#!/usr/bin/env python3
"""Собирает CSV для СР44 (отходы утилизированные) из паркета Росстата.

Берём Y477070020 с subsection «Утилизация и обезвреживание отходов производства
и потребления: Всего». В etl/config.yaml этот Y-код уже используется с другим
subsection для СР43 (образование отходов), поэтому СР44 грузится через CSV.

Выход:
  data/raw/waste_utilization_by_region.csv — тыс. тонн по регионам

Запуск:
    python -m scripts.build_waste_utilization_csv
"""
import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "Массив Данных в формате Parquet" / "Показатели по регионам.parquet"
OUT = ROOT / "data" / "raw" / "waste_utilization_by_region.csv"

INDICATOR = "Y477070020"
SUBSECTION = "Утилизация и обезвреживание отходов производства и потребления: Всего"
YEAR = 2023


def main():
    print(f"Читаю {PARQUET}...")
    df = pd.read_parquet(
        PARQUET,
        columns=["object_name", "object_level", "year", "indicator_code",
                 "indicator_value", "subsection"],
        filters=[
            ("indicator_code", "==", INDICATOR),
            ("object_level", "==", "Регион"),
            ("year", "<=", YEAR),
            ("subsection", "==", SUBSECTION),
        ],
    )
    print(f"Строк по {INDICATOR}: {len(df):,}")
    # Берём последний доступный год по каждому региону
    df = df.sort_values(["object_name", "year"]).drop_duplicates(
        "object_name", keep="last")
    # Отбрасываем сентинелы Росстата
    df = df[df["indicator_value"] > -1e6]
    print(f"После фильтра значений: {len(df):,}")

    with OUT.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["region", "value"])
        for _, r in df.sort_values("indicator_value", ascending=False).iterrows():
            w.writerow([r["object_name"], r["indicator_value"]])
    print(f"Записано: {OUT}")


if __name__ == "__main__":
    main()
