"""
Сбор данных ИКГС для ЛЮБОГО региона (обобщение fetch_*_novosibirsk.py).
Города+баллы берём из реестра (data/processed/ikgs_scores.json), координаты —
из OSM (place-узлы в bbox региона, матчим по имени), контуры НП — OSM
residential+place по bbox города.

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
from shapely.geometry import shape
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


FOOT_Q = (
    'way["landuse"="residential"]({s},{w},{n},{e});'
    'relation["landuse"="residential"]({s},{w},{n},{e});'
    'way["place"~"^(city|town)$"]["name"]({s},{w},{n},{e});'
)


def fetch_footprints(cities):
    """Контуры застройки вокруг каждого города (bbox ±0.12° шир.), дедуп по id."""
    byid = {}
    for name, lon, lat, _ in cities:
        h = 0.18 if name in ("Москва",) else 0.12
        s, n, w, e = lat - h, lat + h, lon - h * 1.6, lon + h * 1.6
        n0 = len(byid)
        for el in query(FOOT_Q.format(s=s, w=w, n=n, e=e)):
            key = (el["type"], el["id"])
            if key in byid:
                continue
            if el["type"] == "way" and el.get("geometry"):
                byid[key] = [[round(p["lon"], 5), round(p["lat"], 5)] for p in el["geometry"]]
            elif el["type"] == "relation":
                for i, m in enumerate(el.get("members", [])):
                    if m.get("role") == "outer" and m.get("geometry"):
                        byid[("rel", el["id"], i)] = [[round(p["lon"], 5), round(p["lat"], 5)]
                                                      for p in m["geometry"]]
        print(f"  {name:20} +колец={len(byid)-n0}")
        time.sleep(2)
    return [r for r in byid.values() if len(r) >= 3]


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

    polys = fetch_footprints(cities)
    json.dump({"polys": polys}, open(os.path.join(ROOT, f"data/processed/{a.region}_footprint.json"),
                                     "w", encoding="utf-8"))
    print(f"→ контуров {len(polys)}")


if __name__ == "__main__":
    main()
