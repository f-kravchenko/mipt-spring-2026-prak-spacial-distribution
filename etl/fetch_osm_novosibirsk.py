"""
Сбор OSM-сигналов для маски ИКГС по 14 городам Новосибирской области:
  POI    — узлы amenity/shop (плотность инфраструктуры; критерии индекса
           «социально-досуговая» и «общественно-деловая»);
  green  — городская зелень: parks/gardens/grass/recreation (критерий
           «озеленённые пространства»). Лес/тайгу НЕ берём — это не городская
           среда и она забила бы область.

Индекс — про города, поэтому качаем bbox вокруг городов, а не всю область
(по всей области Overpass таймаутит). Результат кэшируется в
data/processed/osm_nso.json, чтобы ingest не дёргал Overpass каждый раз.

Запуск (разово):  python -m etl.fetch_osm_novosibirsk
"""

import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/processed/osm_nso.json")
OVERPASS = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "mipt-deaggr-etl/1.0 (practicum research)"}

# city, lon, lat, полуразмер bbox по градусам (Новосибирск — агломерация, шире)
CITIES = [
    ("Новосибирск", 82.9346, 55.0084, 0.45),
    ("Бердск",      83.1018, 54.7583, 0.15),
    ("Обь",         82.6931, 54.9963, 0.12),
    ("Искитим",     83.3075, 54.6350, 0.12),
    ("Куйбышев",    78.3269, 55.4497, 0.12),
    ("Барабинск",   78.3439, 55.3506, 0.12),
    ("Карасук",     78.0403, 53.7317, 0.12),
    ("Татарск",     75.9836, 55.2144, 0.12),
    ("Купино",      76.9500, 54.3661, 0.10),
    ("Черепаново",  83.3733, 54.2206, 0.10),
    ("Болотное",    84.3894, 55.6717, 0.10),
    ("Каргат",      80.2831, 55.1936, 0.10),
    ("Чулым",       80.9600, 55.0900, 0.10),
    ("Тогучин",     84.4014, 55.2317, 0.10),
]

POI_Q = ('node["amenity"]({s},{w},{n},{e});node["shop"]({s},{w},{n},{e});')
GREEN_Q = (
    'way["leisure"~"^(park|garden|playground|pitch|recreation_ground|dog_park)$"]({s},{w},{n},{e});'
    'way["landuse"~"^(grass|recreation_ground|village_green)$"]({s},{w},{n},{e});'
    'node["leisure"~"^(park|garden|playground)$"]({s},{w},{n},{e});'
)


STEP = 0.2  # сторона под-тайла (град.): большой bbox Overpass таймаутит


def query(body, retries=4):
    q = f"[out:json][timeout:50];({body});out center qt;"
    for a in range(retries):
        r = requests.post(OVERPASS, data={"data": q}, headers=HEADERS, timeout=70)
        if r.status_code == 200:
            return r.json()["elements"]
        time.sleep(6 * (a + 1))
    print(f"  пропуск под-тайла: Overpass {r.status_code}")
    return []


def coords(elems):
    out = []
    for el in elems:
        if "lon" in el:
            out.append((round(el["lon"], 5), round(el["lat"], 5)))
        elif "center" in el:
            out.append((round(el["center"]["lon"], 5), round(el["center"]["lat"], 5)))
    return out


def subtiles(lon, lat, h):
    """bbox города (± h по широте, ± h*1.6 по долготе) → сетка под-тайлов STEP."""
    s, n, w, e = lat - h, lat + h, lon - h * 1.6, lon + h * 1.6
    la = s
    while la < n:
        lo = w
        while lo < e:
            yield dict(s=la, w=lo, n=min(la + STEP, n), e=min(lo + STEP, e))
            lo += STEP
        la += STEP


def main():
    pois, green = set(), set()
    for name, lon, lat, h in CITIES:
        tiles = list(subtiles(lon, lat, h))
        p0, g0 = len(pois), len(green)
        for bbox in tiles:
            pois.update(coords(query(POI_Q.format(**bbox)))); time.sleep(2)
            green.update(coords(query(GREEN_Q.format(**bbox)))); time.sleep(2)
        print(f"{name:12} тайлов={len(tiles):2}  +POI={len(pois)-p0:5} +green={len(green)-g0:5}"
              f"  (всего POI={len(pois)}, green={len(green)})")
    json.dump({"poi": sorted(pois), "green": sorted(green)},
              open(OUT, "w", encoding="utf-8"))
    print(f"→ {OUT}: POI={len(pois)}, green={len(green)}")


if __name__ == "__main__":
    main()
