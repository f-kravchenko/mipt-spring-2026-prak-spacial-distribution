#!/usr/bin/env python3
"""Выгружает полный список Y-кодов из паркета Росстата с названиями.
Нужен чтобы быстро найти показатель по ключевому слову и подключить его
в etl/config.yaml без ручного поиска в сборнике.

Выход:
  data/raw/parquet_indicators_list.csv — colonka: code, name, unit, sections

Запуск:
    python -m scripts.list_parquet_indicators

Опционально фильтрует по подстроке в названии:
    python -m scripts.list_parquet_indicators --search театр
    python -m scripts.list_parquet_indicators --search "выброс в атмосферу"
"""
import argparse
import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "Массив Данных в формате Parquet" / "Показатели по регионам.parquet"
OUT = ROOT / "data" / "raw" / "parquet_indicators_list.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", help="фильтр по подстроке в названии (регистр не важен)")
    args = ap.parse_args()

    print(f"Читаю {PARQUET}...")
    df = pd.read_parquet(
        PARQUET,
        columns=["indicator_code", "indicator_name", "indicator_unit", "subsection"],
    )
    print(f"Всего строк в паркете: {len(df):,}")

    agg = (
        df.groupby(["indicator_code", "indicator_name"], as_index=False)
          .agg(subsections=("subsection", lambda s: sorted(set(x for x in s if x))),
               unit=("indicator_unit", lambda s: next(iter(set(x for x in s if x)), "")))
    )
    print(f"Уникальных пар (код + имя): {len(agg):,}")

    if args.search:
        key = args.search.lower()
        agg = agg[agg["indicator_name"].str.lower().str.contains(key, na=False)]
        print(f"После фильтра «{args.search}»: {len(agg):,}")

    agg = agg.sort_values("indicator_code")
    agg["subsections"] = agg["subsections"].map(lambda ss: "; ".join(ss) if ss else "")

    agg.to_csv(OUT, index=False, encoding="utf-8")
    print(f"Записано: {OUT}")

    if args.search and len(agg) < 60:
        print("\n--- Найденные показатели ---")
        for _, r in agg.iterrows():
            subs = f"  [{r['subsections']}]" if r["subsections"] else ""
            print(f"  {r['indicator_code']}  {r['indicator_name']}{subs}")


if __name__ == "__main__":
    main()
