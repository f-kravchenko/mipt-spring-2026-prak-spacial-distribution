"""
Сбор данных ИКГС для ЛЮБОГО региона (обобщение fetch_*_novosibirsk.py).
Города+баллы берём из реестра (data/processed/ikgs_scores.json), координаты —
из OSM (place-узлы в bbox региона, матчим по имени), границы НП — полигоны OSM
place=city|town того же имени.

Полигон региона читаем из БД по slug (у якутского «центра» он только там).
Оставляем города, попавшие ВНУТРЬ полигона (для «центра» отсекает дальние НП).

Пишет data/processed/<slug>_cities.json (name,lon,lat,score) и
<slug>_footprint.json ({polys}). WorldPop/POI/зелень/инфра — отдельно/позже
(маски-яркость), значение ИКГС от них не зависит.

Запуск:  DATABASE_URL=... python -m etl.fetch_index_region --region krasnodar
"""

import argparse
import json
import os
import time

import psycopg2
import requests
from shapely.geometry import LineString, shape
from shapely.ops import linemerge, polygonize, unary_union
from shapely.prepared import prep

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCORES = os.path.join(ROOT, "data/processed/ikgs_scores.json")
OVERPASS = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "mipt-deaggr-etl/1.0 (practicum research)"}


def norm(s):
    return s.lower().replace("ё", "е").replace("-", " ").strip()


def query(body, timeout=90, retries=4):
    q = f"[out:json][timeout:{timeout}];({body});out geom;"
    for a in range(retries):
        r = requests.post(OVERPASS, data={"data": q}, headers=HEADERS, timeout=timeout + 40)
        if r.status_code == 200:
            return r.json()["elements"]
        time.sleep(6 * (a + 1))
    print(f"  Overpass {r.status_code} — пропуск")
    return []


def region_polygon(url, slug):
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT ST_AsGeoJSON(geom) FROM region WHERE slug=%s", (slug,))
        row = cur.fetchone()
    conn.close()
    if not row:
        raise SystemExit(f"регион {slug} не найден в БД")
    return shape(json.loads(row[0]))


def fetch_cities(scores, poly):
    """place-узлы city/town в bbox → координаты; матч к реестру по имени;
    оставляем внутри полигона."""
    minx, miny, maxx, maxy = poly.bounds
    els = query(f'node["place"~"^(city|town)$"]({miny},{minx},{maxy},{maxx});', timeout=120)
    byname = {}
    for el in els:
        nm = el.get("tags", {}).get("name")
        if nm and "lon" in el:
            byname[norm(nm)] = (round(el["lon"], 5), round(el["lat"], 5))
    pinp = prep(poly)
    out, miss = [], []
    from shapely.geometry import Point
    for name, sc in scores.items():
        c = byname.get(norm(name))
        if c and pinp.contains(Point(c)):
            out.append([name, c[0], c[1], sc])
        elif c:
            pass  # вне полигона (для «центра» — норм)
        else:
            miss.append(name)
    if miss:
        print(f"  не нашли координат OSM: {', '.join(miss)}")
    return out


def fetch_footprints(cities, poly):
    """Границы НП = полигоны OSM place=city|town, сматченные к городам реестра по
    имени. Раньше брали ВСЮ landuse=residential в боксе ±13 км вокруг города и
    вешали на ближайший город — в контур попадали окрестные сёла и дачи, после
    смыкания НП раздувался до сотен км² (Верея 481 км² при реальных 7). Теперь
    источник — сама граница НП, и кольцо сразу знает, чей оно (owner)."""
    minx, miny, maxx, maxy = poly.bounds
    bb = f"{miny},{minx},{maxy},{maxx}"
    els = query(f'way["place"~"^(city|town)$"]["name"]({bb});'
                f'relation["place"~"^(city|town)$"]["name"]({bb});', timeout=180)
    # outer-мемберы отношения — это СЕГМЕНТЫ контура, а не готовые кольца:
    # замыкание каждого по отдельности давало обрезки (Новосибирск 48 км² вместо
    # 501). Сшиваем сегменты города линкой linemerge → polygonize.
    bysegs = {}
    for el in els:
        segs = ([[(p["lon"], p["lat"]) for p in el["geometry"]]]
                if el["type"] == "way" and el.get("geometry") else
                [[(p["lon"], p["lat"]) for p in m["geometry"]]
                 for m in el.get("members", []) if m.get("role") == "outer" and m.get("geometry")])
        bysegs.setdefault(norm(el["tags"]["name"]), []).extend(s for s in segs if len(s) >= 2)
    byname = {}
    for nm, segs in bysegs.items():
        merged = unary_union(list(polygonize(linemerge([LineString(s) for s in segs]))))
        geoms = merged.geoms if merged.geom_type == "MultiPolygon" else ([merged] if not merged.is_empty else [])
        byname[nm] = [[[round(x, 5), round(y, 5)] for x, y in g.exterior.coords] for g in geoms]
    polys, owner, miss = [], [], []
    for j, (name, lon, lat, _) in enumerate(cities):
        rings = byname.get(norm(name)) or []
        if not rings:
            miss.append(name)  # ingest подставит опорный диск ~2 км в точке города
        polys += rings
        owner += [j] * len(rings)
    if miss:
        print(f"  без полигона place (будет диск ~2 км): {', '.join(miss)}")
    return polys, owner


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    a = ap.parse_args()
    url = (a.database_url or "").replace("postgresql+psycopg://", "postgresql://") \
                               .replace("postgresql+psycopg2://", "postgresql://")
    if not url:
        raise SystemExit("Задайте DATABASE_URL")

    scores = json.load(open(SCORES, encoding="utf-8"))[a.region]
    poly = region_polygon(url, a.region)
    cities = fetch_cities(scores, poly)
    print(f"{a.region}: городов с координатами внутри полигона {len(cities)}/{len(scores)}")
    json.dump(cities, open(os.path.join(ROOT, f"data/processed/{a.region}_cities.json"),
                           "w", encoding="utf-8"), ensure_ascii=False)

    polys, owner = fetch_footprints(cities, poly)
    json.dump({"polys": polys, "owner": owner},
              open(os.path.join(ROOT, f"data/processed/{a.region}_footprint.json"), "w",
                   encoding="utf-8"))
    print(f"→ контуров {len(polys)} у {len(set(owner))}/{len(cities)} НП")


if __name__ == "__main__":
    main()
