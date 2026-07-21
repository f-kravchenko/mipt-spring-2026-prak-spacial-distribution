"""
Линейная инфраструктура OSM по Новосибирской области для маски присутствия:
  road  — крупные дороги (motorway/trunk/primary/secondary) → близость к дороге
          (аналог маски road_network других регионов);
  rail  — ж/д линии (аналог railway);
  power — ЛЭП (аналог power).

Эти слои разрежённые, поэтому берутся ОДНИМ региональным запросом (в отличие
от плотных POI). osmnx не нужен — тянем геометрию через Overpass и семплим
точки вдоль линий; расстояние-затухание считает ingest (та же формула, что в
src/masks/road_network|railway|power, но по сетке индекса в features.weight).

Кэш: data/processed/osm_nso_infra.json. Запуск:  python -m etl.fetch_infra_novosibirsk
"""

import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/processed/osm_nso_infra.json")
OVERPASS = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "mipt-deaggr-etl/1.0 (practicum research)"}
BBOX = "53.2,75.0,57.3,85.2"  # НСО с запасом

LAYERS = {
    "road":  f'way["highway"~"^(motorway|trunk|primary|secondary)$"]({BBOX});',
    "rail":  f'way["railway"="rail"]({BBOX});',
    "power": f'way["power"="line"]({BBOX});',
}


def fetch(selector):
    q = f"[out:json][timeout:120];({selector});out geom;"
    for a in range(4):
        r = requests.post(OVERPASS, data={"data": q}, headers=HEADERS, timeout=180)
        if r.status_code == 200:
            return r.json()["elements"]
        time.sleep(8 * (a + 1))
    raise SystemExit(f"Overpass {r.status_code}")


def sample(elems):
    """Точки вдоль линий, округлённые до ~100 м и дедуплицированные."""
    pts = set()
    for el in elems:
        for nd in el.get("geometry", []):
            pts.add((round(nd["lon"], 3), round(nd["lat"], 3)))
    return sorted(pts)


def lines(elems):
    """Полные линии (для слоя-оверлея «Дороги»): highway + список координат."""
    out = []
    for el in elems:
        g = el.get("geometry", [])
        if len(g) >= 2:
            out.append({"h": el.get("tags", {}).get("highway", ""),
                        "c": [[round(n["lon"], 5), round(n["lat"], 5)] for n in g]})
    return out


def main():
    out = {}
    for name, sel in LAYERS.items():
        elems = fetch(sel)
        out[name] = sample(elems)
        if name == "road":
            out["road_lines"] = lines(elems)  # для слоя-оверлея «Дороги»
        print(f"{name:6}: {len(out[name])} точек"
              + (f", {len(out['road_lines'])} линий" if name == "road" else ""))
        time.sleep(3)
    json.dump(out, open(OUT, "w", encoding="utf-8"))
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
