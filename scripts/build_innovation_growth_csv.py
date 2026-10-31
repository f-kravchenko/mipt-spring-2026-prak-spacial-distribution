#!/usr/bin/env python3
"""Собирает CSV для ИНВ23 (темп роста затрат на инновационную деятельность).

Темп роста = затраты года t / затраты года t-1 − 1, в % (t = 2023, отчётный
год конфига). Готового кода нет — считается из двух лет одного кода
Y477110102 (затраты на инновационную деятельность организаций, млн руб.) и
грузится через CSV, тот же приём, что у ИНФ15 в build_fixed_assets_csv.py.
Строка «Российская Федерация» — из сумм object_level=Страна за те же годы.

Выход:
  data/raw/innovation_growth_by_region.csv — % к предыдущему году по регионам
  + строка «Российская Федерация» (national_total)

Запуск:
    python -m scripts.build_innovation_growth_csv
"""
import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "Массив Данных в формате Parquet" / "Показатели по регионам.parquet"
OUT = ROOT / "data" / "raw" / "innovation_growth_by_region.csv"

CODE = "Y477110102"  # Затраты на инновационную деятельность организаций, млн руб.
YEAR, PREV = 2023, 2022


def main():
    df = pd.read_parquet(
        PARQUET,
        columns=["object_name", "object_level", "year", "indicator_code", "indicator_value"],
        filters=[("indicator_code", "==", CODE)],
    )
    df = df[df["indicator_value"] > -1e6]  # выкинуть -77777777 (данные закрыты)
    piv = df.pivot_table(index="object_name", columns="year", values="indicator_value", aggfunc="sum")
    ok = piv[YEAR].notna() & piv[PREV].notna() & (piv[PREV] > 0)
    growth = ((piv[YEAR] / piv[PREV] - 1.0) * 100).round(1)

    regions = sorted(r for r in piv[ok].index if r != "Российская Федерация")
    rf = growth.get("Российская Федерация")
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["region", "value"])
        for r in regions:
            w.writerow([r, growth[r]])
        if pd.notna(rf):
            w.writerow(["Российская Федерация", rf])
    print(f"→ {OUT} ({len(regions)} регионов" + (f", РФ={rf}%)" if pd.notna(rf) else ")"))


if __name__ == "__main__":
    main()
