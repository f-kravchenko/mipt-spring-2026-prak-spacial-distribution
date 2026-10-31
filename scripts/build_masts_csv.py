#!/usr/bin/env python3
"""Собирает CSV для ИНФ21 (мобильная связь) из OSM + паркета.

ТЗ хочет «охват населения мобильной связью, %»: карты покрытия операторов и
реестр РЭС Роскомнадзора закрыты, честный процент не считается. Прокси —
плотность сети базовых станций на 100 тыс. жителей: вышки OSM
(man_made=mast + tower с tower:type=communication, как предложено в
разведке indicators.csv) / население (Y477110373, тыс. чел, паркет).

Счёт — двумя запросами Overpass на всю страну (bbox западнее и восточнее
180-го меридиана, `(poly:...)` на субъект оказался в 40 раз медленнее и
ловил деградацию зеркал), дальше вышки разносятся по регионам локально,
point-in-polygon по russia.geojson (STRtree). Пути берём как центроиды —
вышки-мультиполигоны редкость. Имена geojson короткие («Башкортостан»),
в паркете полные — словарь G2P ниже.

Выход:
  data/raw/mobile_masts_per_100k_by_region.csv — вышек на 100 тыс. жителей
  по регионам + строка «Российская Федерация»

Запуск:
    python -m scripts.build_masts_csv
"""
import csv
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "Массив Данных в формате Parquet" / "Показатели по регионам.parquet"
GEOJSON = ROOT / "apps" / "web" / "public" / "russia.geojson"
OUT = ROOT / "data" / "raw" / "mobile_masts_per_100k_by_region.csv"

POPULATION = "Y477110373"  # Среднегодовая численность населения, тыс. человек

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
HEADERS = {"User-Agent": "mipt-deaggr-etl/1.0 (practicum research)"}

# geojson -> паркет: короткие имена республик и прочие несовпадения
G2P = {
    "Город Санкт-Петербург": "Санкт-Петербург",
    "Ханты-Мансийский автономный округ": "Ханты-Мансийский автономный округ — Югра",
    "Адыгея": "Республика Адыгея",
    "Башкортостан": "Республика Башкортостан",
    "Бурятия": "Республика Бурятия",
    "Дагестан": "Республика Дагестан",
    "Кабардино-Балкарская республика": "Кабардино-Балкарская Республика",
    "Калмыкия": "Республика Калмыкия",
    "Карачаево-Черкесская республика": "Карачаево-Черкесская Республика",
    "Марий Эл": "Республика Марий Эл",
    "Мордовия": "Республика Мордовия",
    "Северная Осетия": "Республика Северная Осетия — Алания",
    "Северная Осетия — Алания": "Республика Северная Осетия — Алания",
    "Татарстан": "Республика Татарстан",
    "Тыва": "Республика Тыва",
    "Удмуртия": "Удмуртская Республика",
    "Удмуртская республика": "Удмуртская Республика",
    "Хакасия": "Республика Хакасия",
    "Чечня": "Чеченская Республика",
    "Чеченская республика": "Чеченская Республика",
    "Чувашия": "Чувашская Республика",
    "Якутия": "Республика Саха (Якутия)",
    "Алтай": "Республика Алтай",
    "Ингушетия": "Республика Ингушетия",
}


def norm(name: str) -> str:
    # в geojson тире бывает дефисом («... - Югра»), в парке — длинное тире
    name = name.replace(" - ", " — ")
    name = G2P.get(name, name)
    return re.sub(r"\s*\(без автономн[а-я]+ округ[а-я]*\)", "", name).strip()


def population_by_region() -> pd.Series:
    df = pd.read_parquet(
        PARQUET,
        columns=["object_name", "year", "indicator_code", "indicator_value"],
        filters=[("indicator_code", "==", POPULATION), ("object_level", "==", "Регион")],
    )
    df = df[df["indicator_value"] > -1e6]
    df["object_name"] = df["object_name"].map(norm)
    # у Архангельской два object_name («с» и «без» округа) сворачиваются в один —
    # берём строку с бОльшим годом, при равенстве последнюю
    return (df.sort_values(["object_name", "year"])
              .drop_duplicates("object_name", keep="last")
              .set_index("object_name")["indicator_value"])


def overpass(query: str, retries: int = 6):
    for a in range(retries):
        url = MIRRORS[a % len(MIRRORS)]
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=180)
            if r.status_code == 200:
                return r.json()["elements"]
            code = r.status_code
        except requests.RequestException as e:
            code = type(e).__name__
        print(f"    {url.split('/')[2]}: {code}, попытка {a + 1}/{retries}", flush=True)
        time.sleep(8 * (a + 1))
    raise SystemExit("Overpass недоступен на всех зеркалах")


def fetch_masts() -> list:
    """Все вышки РФ двумя bbox-запросами (по обе стороны 180-го меридиана)."""
    els = []
    for s, w, n, e in [(41, 19, 78, 180), (41, -180, 78, -169)]:
        bb = f"({s},{w},{n},{e})"
        q = (f'[out:json][timeout:300];('
             f'node["man_made"="mast"]{bb};'
             f'node["man_made"="tower"]["tower:type"="communication"]{bb};'
             f');out;')
        got = overpass(q, retries=9)
        els += got
        print(f"  bbox {bb}: {len(got)} вышек", flush=True)
    return els


def main():
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree

    pop = population_by_region()
    gj = json.load(open(GEOJSON, encoding="utf-8"))
    regions = [(norm(f["properties"]["name"]), shape(f["geometry"]))
               for f in gj["features"]]
    tree = STRtree([g for _, g in regions])

    counts = {name: 0 for name, _ in regions}
    for el in fetch_masts():
        if el["type"] == "node":
            pt = Point(el["lon"], el["lat"])
        else:  # way: геометрии не просили — пропускаем (вышки-линии редки)
            continue
        for i in tree.query(pt):
            if regions[i][1].contains(pt):
                counts[regions[i][0]] += 1
                break

    rows = []
    for name, n in counts.items():
        if name not in pop.index:
            print(f"  {name}: нет населения в паркете — пропуск", flush=True)
            continue
        rows.append((name, round(n / pop[name] * 100, 1), n))
        print(f"  {name}: {n} вышек, {rows[-1][1]} на 100 тыс.", flush=True)

    total_n = sum(r[2] for r in rows)
    rf = round(total_n / pop[[r[0] for r in rows]].sum() * 100, 1)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["region", "value"])
        for name, per100k, _ in sorted(rows, key=lambda r: -r[1]):
            w.writerow([name, per100k])
        w.writerow(["Российская Федерация", rf])
    print(f"→ {OUT} ({len(rows)} регионов, РФ={rf})")


if __name__ == "__main__":
    main()
