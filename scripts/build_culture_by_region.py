#!/usr/bin/env python3
"""Собирает три CSV для СР61 (театры), СР62 (библиотеки), СР63 (кинотеатры)
из открытых данных Минкультуры (opendata.mkrf.ru).

Выход:
  data/raw/theaters_by_region.csv       — СР61, число мест в театрах
  data/raw/libraries_by_region.csv      — СР62, число общедоступных библиотек
  data/raw/cinemas_by_region.csv        — СР63, число мест в кинозалах

Все CSV в формате `region,value`. Регион берётся из поля адреса / субъекта.
Для СР61 и СР63 суммируется число посадочных мест по объектам региона.
Для СР62 — подсчёт объектов по региону.

Запуск:
    python -m scripts.build_culture_by_region

Требуется RU IP для доступа к opendata.mkrf.ru.
"""
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"

DATASETS = {
    "theaters": {
        "slug": "7705851331-stat_theaters",
        "out": "theaters_by_region.csv",
        "value_fields": ["halls_capacity", "capacity", "seats"],
    },
    "libraries": {
        "slug": "7705851331-stat_libraries",
        "out": "libraries_by_region.csv",
        "value_fields": None,  # просто счётчик объектов
    },
    "cinemas": {
        "slug": "7705851331-cinema_halls",
        "out": "cinemas_by_region.csv",
        "value_fields": ["seats", "capacity"],
    },
}

API_BASE = "https://opendata.mkrf.ru/opendata"
PAGE_SIZE = 500


def fetch_dataset(slug):
    """Качает все записи датасета постранично."""
    rows = []
    page = 1
    while True:
        url = f"{API_BASE}/{slug}/data?size={PAGE_SIZE}&from={(page-1)*PAGE_SIZE}&f={{}}&s=[]"
        print(f"  {slug} страница {page}...", end="", flush=True)
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            print(f" FAIL {r.status_code}")
            break
        data = r.json()
        chunk = data.get("data", [])
        if not chunk:
            print(" пусто")
            break
        rows.extend(chunk)
        print(f" {len(chunk)}")
        if len(chunk) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.3)
    return rows


def pick_value(rec, fields):
    """Из записи достаёт числовое значение по первому попавшемуся полю,
    рекурсивно ищет внутри вложенных dict/list."""
    if fields is None:
        return 1  # счётчик объектов

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in [f.lower() for f in fields]:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
                res = walk(v)
                if res is not None:
                    return res
        elif isinstance(obj, list):
            for item in obj:
                res = walk(item)
                if res is not None:
                    return res
        return None
    return walk(rec) or 0


def pick_region(rec):
    """Из записи вытаскивает имя субъекта РФ."""
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in ("subject", "region", "region_name", "subject_name",
                                "окто", "субъект"):
                    if isinstance(v, str):
                        return v
                    if isinstance(v, dict):
                        for kk in ("name", "value"):
                            if kk in v and isinstance(v[kk], str):
                                return v[kk]
                res = walk(v)
                if res:
                    return res
        elif isinstance(obj, list):
            for item in obj:
                res = walk(item)
                if res:
                    return res
        return None
    return walk(rec)


def build_csv(slug, value_fields, out_name):
    print(f"\n=== {out_name} ({slug}) ===")
    rows = fetch_dataset(slug)
    print(f"Всего записей: {len(rows)}")
    by_region = defaultdict(float)
    for r in rows:
        reg = pick_region(r.get("data", r))
        if not reg:
            continue
        val = pick_value(r.get("data", r), value_fields)
        by_region[reg.strip()] += val
    print(f"Регионов с данными: {len(by_region)}")

    out_path = RAW / out_name
    with out_path.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["region", "value"])
        for reg, val in sorted(by_region.items(), key=lambda x: -x[1]):
            w.writerow([reg, val if isinstance(val, int) else round(val, 2)])
    print(f"Записано: {out_path}")


def main():
    for name, cfg in DATASETS.items():
        try:
            build_csv(cfg["slug"], cfg["value_fields"], cfg["out"])
        except Exception as e:
            print(f"ERROR {name}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
