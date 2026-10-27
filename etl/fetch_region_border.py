"""
Граница субъекта и опорные города из OSM — для региона, которого нет в БД и
у которого нет реестра ИКГС (апробация: Воронежская, Нижегородская обл.).

У регионов с ИКГС граница приходит из БД (ingest_index), а опорные города — из
<slug>_cities.json, который пишет etl.fetch_index_region по реестру баллов.
Здесь то же самое без БД и без баллов:
  * border_<name>.gpkg — полигон relation boundary=administrative,
    admin_level=4 с заданным именем, в EPSG:3857 (как пишет build_grid);
  * <slug>_cities.json — [[name, lon, lat, null], ...] в формате реестра ИКГС
    (балл null: сетке он не нужен, build_grid берёт только имя и координаты).
    Первым идёт центр региона — его build_grid берёт опорным по умолчанию.

Список городов задаётся явно: в OSM place=town стоит и у посёлков городского
типа, а опорными должны быть только города (как в реестре ИКГС у НСО — 14
городов, хотя place=city|town внутри области 38).

Запуск:
  python -m etl.fetch_region_border --region voronezh \
      --osm-name "Воронежская область" \
      --cities "Воронеж,Бобров,Богучар,Борисоглебск,..."
"""

import argparse
import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "data/processed")
# те же зеркала, что в scripts/compute_masks_overpass.py
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
HEADERS = {"User-Agent": "mipt-deaggr-etl/1.0 (practicum research)"}


def overpass(q):
    for attempt in range(6):
        url = MIRRORS[attempt % len(MIRRORS)]
        try:
            r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=300)
            if r.status_code == 200:
                return r.json()["elements"]
            print(f"  {url}: HTTP {r.status_code}")
        except requests.RequestException as e:
            print(f"  {url}: {e}")
        time.sleep(10 * (attempt + 1))
    raise SystemExit("Overpass недоступен")


def region_polygon(osm_name):
    """Полигон субъекта: внешние линии relation, склеенные polygonize."""
    from shapely.geometry import LineString
    from shapely.ops import linemerge, polygonize, unary_union
    q = (f'[out:json][timeout:240];relation["boundary"="administrative"]'
         f'["admin_level"="4"]["name"="{osm_name}"];out geom;')
    rels = overpass(q)
    if len(rels) != 1:
        raise SystemExit(f"«{osm_name}»: найдено relation {len(rels)}, нужен ровно один")
    lines = [LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
             for m in rels[0]["members"]
             if m["type"] == "way" and m.get("role", "outer") in ("outer", "")
             and len(m.get("geometry", [])) > 1]
    polys = list(polygonize(linemerge(unary_union(lines))))
    if not polys:
        raise SystemExit(f"«{osm_name}»: контур не замкнулся")
    return unary_union(polys)


def city_points(poly, names):
    """Координаты городов: узел place=city|town с этим именем внутри полигона."""
    from shapely.geometry import Point
    from shapely.prepared import prep
    minx, miny, maxx, maxy = poly.bounds
    q = (f'[out:json][timeout:180];node["place"~"^(city|town)$"]["name"]'
         f'({miny},{minx},{maxy},{maxx});out;')
    pin = prep(poly)
    found = {}
    for e in overpass(q):
        name = e.get("tags", {}).get("name")
        if name in names and name not in found and pin.contains(Point(e["lon"], e["lat"])):
            found[name] = [name, round(e["lon"], 5), round(e["lat"], 5), None]
    missing = [n for n in names if n not in found]
    if missing:
        raise SystemExit(f"в OSM внутри области не нашлись: {', '.join(missing)}")
    return [found[n] for n in names]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, help="slug региона (как в etl/config.yaml)")
    ap.add_argument("--name", help="основа имён файлов (по умолчанию = slug)")
    ap.add_argument("--osm-name", required=True, help="name субъекта в OSM")
    ap.add_argument("--cities", required=True,
                    help="опорные города через запятую, первым — центр региона")
    a = ap.parse_args()
    name = a.name or a.region

    import geopandas as gpd

    poly = region_polygon(a.osm_name)
    border = gpd.GeoDataFrame({"name": [a.region]}, geometry=[poly], crs=4326).to_crs(3857)
    border_path = os.path.join(PROC, f"border_{name}.gpkg")
    border.to_file(border_path, driver="GPKG")
    print(f"граница → {border_path} ({border.geometry.area.sum() / 1e6:,.0f} усл. км²)")

    cities = city_points(poly, [c.strip() for c in a.cities.split(",") if c.strip()])
    cpath = os.path.join(PROC, f"{a.region}_cities.json")
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump(cities, f, ensure_ascii=False)
    print(f"опорные города → {cpath} ({len(cities)} шт.)")


if __name__ == "__main__":
    main()
