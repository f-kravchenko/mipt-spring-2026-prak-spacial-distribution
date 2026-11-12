#!/usr/bin/env python3
"""Собирает CSV для НР24 (инновации с использованием ИС) — патентоёмкость.

Форма 4-инновация (строки 305-307: инновационные товары с использованием
охранных документов) закрыта. Прокси-составной из двух открытых кодов парка:
выдано патентов на изобретения (Y477110307) на млрд руб. инновационных
товаров (Y477110236) — «ИС-интенсивность» инновационной продукции региона.
Интерпретация осторожная: выдача патентов и выпуск инновационных товаров
относятся к разным совокупностям организаций.

Выход:
  data/raw/innovation_patent_intensity_by_region.csv — патентов на млрд руб.
  + строка «Российская Федерация»

Запуск:
    python -m scripts.build_patent_intensity_csv
"""
import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "Массив Данных в формате Parquet" / "Показатели по регионам.parquet"
OUT = ROOT / "data" / "raw" / "innovation_patent_intensity_by_region.csv"

PATENTS = "Y477110307"   # Выдано патентов на изобретения, штук
INNOV = "Y477110236"     # Объём инновационных товаров, млн руб.
YEAR = 2023


def read(code: str) -> pd.Series:
    df = pd.read_parquet(
        PARQUET,
        columns=["object_name", "object_level", "year", "indicator_code", "indicator_value"],
        filters=[("indicator_code", "==", code), ("year", "<=", YEAR)],
    )
    df = df[df["indicator_value"] > -1e6]
    return (df.sort_values(["object_name", "year"])
              .drop_duplicates("object_name", keep="last")
              .set_index("object_name")["indicator_value"])


def main():
    patents, innov = read(PATENTS), read(INNOV)
    regions = [r for r in patents.index if r != "Российская Федерация" and r in innov.index]
    rows = [(r, round(patents[r] / innov[r] * 1000, 1)) for r in regions if innov[r] > 0]
    rf = round(patents.get("Российская Федерация", 0)
               / innov.get("Российская Федерация", 1) * 1000, 1)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["region", "value"])
        for r, v in sorted(rows, key=lambda x: -x[1]):
            w.writerow([r, v])
        w.writerow(["Российская Федерация", rf])
    print(f"→ {OUT} ({len(rows)} регионов, РФ={rf})")


if __name__ == "__main__":
    main()
