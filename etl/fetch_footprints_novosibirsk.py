"""
Фактические контуры (площади) 14 населённых пунктов НСО из OSM — нужны для
затухания ИКГС: внутри контура держим балл из реестра, наружу — затухание.

Берём застроенную ткань landuse=residential + именованные place-полигоны
(city/town) — это и есть «фактическая площадь населённого пункта». bbox вокруг
городов (по всей области Overpass таймаутит), дедуп по id. out geom — сразу
полная геометрия кольца. Кэш → data/processed/osm_nso_footprint.json.

Запуск (разово):  python -m etl.fetch_footprints_novosibirsk
"""

import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/processed/osm_nso_footprint.json")
OVERPASS = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "mipt-deaggr-etl/1.0 (practicum research)"}

# city, lon, lat, полуразмер bbox по широте (Новосибирск-агломерация — шире)
CITIES = [
    ("Новосибирск", 82.9346, 55.0084, 0.35),
    ("Бердск",      83.1018, 54.7583, 0.12),
    ("Обь",         82.6931, 54.9963, 0.10),
    ("Искитим",     83.3075, 54.6350, 0.10),
    ("Куйбышев",    78.3269, 55.4497, 0.10),
    ("Барабинск",   78.3439, 55.3506, 0.10),
    ("Карасук",     78.0403, 53.7317, 0.10),
    ("Татарск",     75.9836, 55.2144, 0.10),
    ("Купино",      76.9500, 54.3661, 0.08),
    ("Черепаново",  83.3733, 54.2206, 0.08),
    ("Болотное",    84.3894, 55.6717, 0.08),
    ("Каргат",      80.2831, 55.1936, 0.08),
    ("Чулым",       80.9600, 55.0900, 0.08),
    ("Тогучин",     84.4014, 55.2317, 0.08),
]

Q = (
    'way["landuse"="residential"]({s},{w},{n},{e});'
    'relation["landuse"="residential"]({s},{w},{n},{e});'
    'way["place"~"^(city|town)$"]["name"]({s},{w},{n},{e});'
)


def query(body, retries=4):
    q = f"[out:json][timeout:90];({body});out geom;"
    for a in range(retries):
        r = requests.post(OVERPASS, data={"data": q}, headers=HEADERS, timeout=120)
        if r.status_code == 200:
            return r.json()["elements"]
        time.sleep(6 * (a + 1))
    print(f"  пропуск: Overpass {r.status_code}")
    return []


def rings(elems, byid):
    """Кольца из ways (geometry) и outer-членов relations; дедуп по (type,id)."""
    for el in elems:
        key = (el["type"], el["id"])
        if key in byid:
            continue
        if el["type"] == "way" and el.get("geometry"):
            g = el["geometry"]
            byid[key] = [[round(p["lon"], 5), round(p["lat"], 5)] for p in g]
        elif el["type"] == "relation":
            for m in el.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    mkey = ("relmemb", el["id"], id(m))
                    byid[mkey] = [[round(p["lon"], 5), round(p["lat"], 5)]
                                  for p in m["geometry"]]


def main():
    byid = {}
    for name, lon, lat, h in CITIES:
        n0 = len(byid)
        s, n, w, e = lat - h, lat + h, lon - h * 1.6, lon + h * 1.6
        rings(query(Q.format(s=s, w=w, n=n, e=e)), byid)
        print(f"{name:12} +колец={len(byid)-n0:4}  (всего {len(byid)})")
        time.sleep(3)
    polys = [r for r in byid.values() if len(r) >= 3]
    json.dump({"polys": polys}, open(OUT, "w", encoding="utf-8"))
    print(f"→ {OUT}: контуров {len(polys)}")


if __name__ == "__main__":
    main()
