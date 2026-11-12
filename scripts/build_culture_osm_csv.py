#!/usr/bin/env python3
"""Собирает CSV обеспеченности объектами культуры из OSM (СР63/64/66/67).

Раздел «Культура» Росстата в открытом парке кончается театрами/музеями/
библиотеками (СР61/62/65 уже из парка); кинотеатров, концертных и
выставочных залов и клубов там нет, а stat.mkrf.ru закрыт для не-RU IP.
Прокси — точечные объекты OSM на 100 тыс. жителей, тот же приём, что у
ИНФ21 (build_masts_csv.py): общероссийские bbox-запросы (два, по сторонам
180-го меридиана), разнос по регионам russia.geojson через STRtree.
Способы (way) берём центром (`out center`), объекты культуры почти всегда
точки/полигоны с place-узлом.

Соответствие ТЗ:
  СР63 кинотеатры            amenity=cinema
  СР64 концертные залы       amenity=theatre (прокси: все театрально-
                             концертные площадки, «чистый» тег concert_hall редок)
  СР66 выставочные залы      amenity=gallery + exhibition_centre
  СР67 учреждения клубного   amenity=community_centre (ДК)
       типа

Выход: data/raw/<name>_per_100k_by_region.csv + строка «Российская Федерация».

Запуск:
    python -m scripts.build_culture_osm_csv
"""
import csv
import json
from pathlib import Path

import pandas as pd

from scripts.build_masts_csv import overpass, population_by_region, norm  # общий приём ИНФ21

ROOT = Path(__file__).resolve().parents[1]
GEOJSON = ROOT / "apps" / "web" / "public" / "russia.geojson"
RAW = ROOT / "data" / "raw"

# слаг файла -> набор фильтров Overpass (точки и способы)
FACETS = {
    "cinema": ['node["amenity"="cinema"]', 'way["amenity"="cinema"]'],
    "concert_halls": ['node["amenity"="theatre"]', 'way["amenity"="theatre"]'],
    "exhibition_halls": ['node["amenity"~"^(gallery|exhibition_centre)$"]',
                         'way["amenity"~"^(gallery|exhibition_centre)$"]'],
    "clubs": ['node["amenity"="community_centre"]', 'way["amenity"="community_centre"]'],
    # ИНФ35 КАВФ: коворкинги и фаблабы/хакспейсы (акселераторы в OSM не размечены)
    "kavf": ['node["amenity"~"^(coworking_space|hackerspace)$"]',
             'way["amenity"~"^(coworking_space|hackerspace)$"]',
             'node["leisure"="hackerspace"]', 'way["leisure"="hackerspace"]'],
}
BBOXES = ["(41,19,78,180)", "(41,-180,78,-169)"]  # по сторонам 180-го меридиана


def fetch_points(selectors):
    pts = []
    for bb in BBOXES:
        body = "".join(f"{s}{bb};" for s in selectors)
        for el in overpass(f"[out:json][timeout:300];({body});out center;"):
            c = el.get("center") if el["type"] == "way" else el
            if c and "lat" in c:
                pts.append((c["lon"], c["lat"]))
        print(f"    {bb}: всего {len(pts)}", flush=True)
    return pts


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="через запятую — пересчитать только эти слаги")
    a = ap.parse_args()
    want = set((a.only or ",".join(FACETS)).split(","))

    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree

    pop = population_by_region()
    gj = json.load(open(GEOJSON, encoding="utf-8"))
    regions = [(norm(f["properties"]["name"]), shape(f["geometry"]))
               for f in gj["features"]]
    names = [n for n, _ in regions]
    tree = STRtree([g for _, g in regions])

    for slug, selectors in FACETS.items():
        if slug not in want:
            continue
        print(f"  {slug}:", flush=True)
        counts = {n: 0 for n in names}
        for lon, lat in fetch_points(selectors):
            pt = Point(lon, lat)
            for i in tree.query(pt):
                if regions[i][1].contains(pt):
                    counts[regions[i][0]] += 1
                    break
        rows = [(n, round(c / pop[n] * 100, 1), c)
                for n, c in counts.items() if n in pop.index and c]
        rf = round(sum(r[2] for r in rows) / pop[[r[0] for r in rows]].sum() * 100, 1)
        out = RAW / f"{slug}_per_100k_by_region.csv"
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["region", "value"])
            for name, per, _ in sorted(rows, key=lambda r: -r[1]):
                w.writerow([name, per])
            w.writerow(["Российская Федерация", rf])
        print(f"  → {out.name}: {len(rows)} регионов, РФ={rf} "
              f"(топ: {rows and sorted(rows, key=lambda r: -r[2])[0][0]})", flush=True)


if __name__ == "__main__":
    main()
