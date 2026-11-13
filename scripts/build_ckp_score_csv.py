#!/usr/bin/env python3
"""Собирает CSV для ИНФ31 (наличие ЦКПНО) из счётчика ЦКП.

ТЗ: балл 0/1/2 по числу центров коллективного пользования научным
оборудованием на территории. С ноября считаем по раздельному счётчику ЦКП
(ckp_objects_by_region.csv, scripts/build_ckp_csv.py, нужен RU IP) — раньше
балл выводили из суммы ЦКП+УНУ+ПИШ. Пороги наши (ТЗ даёт только шкалу):
0 — нет, 1 — 1-3, 2 — 4 и больше (медиана распределения ЦКП = 4).

Регионы без ЦКП (в т.ч. не сопоставившиеся при сборе: Крым, Севастополь…)
получают честный 0 — для 0/1/2-показателя «нет строки» и «0» разные вещи.

Выход:
  data/raw/ckp_score_by_region.csv — балл 0/1/2 по всем регионам парка
  + строка «Российская Федерация» (максимальный балл = 2)

Запуск:
    python -m scripts.build_ckp_score_csv
"""
import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "Массив Данных в формате Parquet" / "Показатели по регионам.parquet"
CKP = ROOT / "data" / "raw" / "ckp_objects_by_region.csv"
OUT = ROOT / "data" / "raw" / "ckp_score_by_region.csv"


def main():
    regions = (pd.read_parquet(PARQUET, columns=["object_name", "object_level"])
                 .query("object_level == 'Регион'")["object_name"].unique())
    ckp = pd.read_csv(CKP).set_index("region")["value"].to_dict()
    rows = []
    for r in sorted(regions):
        n = int(ckp.get(r, 0))
        rows.append((r, 0 if n == 0 else 1 if n <= 3 else 2))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["region", "value"])
        w.writerows(rows)
        w.writerow(["Российская Федерация", 2])
    dist = pd.Series([v for _, v in rows]).value_counts().to_dict()
    print(f"→ {OUT} ({len(rows)} регионов: {dist})")


if __name__ == "__main__":
    main()
