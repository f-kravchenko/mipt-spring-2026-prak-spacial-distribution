#!/usr/bin/env python3
"""Собирает CSV для ИНФ31 (наличие ЦКПНО) из уже собранного реестра объектов НТИ.

ТЗ: балл 0/1/2 по числу центров коллективного пользования на территории.
Раздельного счёта ЦКП нет (да и реестр ckp-rf.ru закрыт для не-RU IP), но
ИНФ13 уже собрал nti_objects_by_region.csv = ЦКП + УНУ + ПИШ по регионам
(scripts/build_ckp_csv.py) — балл выводим из него. Пороги наши (ТЗ даёт
только шкалу 0/1/2): 0 — объектов нет, 1 — 1-3, 2 — 4 и больше.

Регионы без объектов (в т.ч. не сопоставившиеся при сборе ИНФ13: Крым,
Севастополь, ЛНР...) получают честный 0 — для 0/1/2-показателя «нет строки»
и «0» разные вещи.

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
NTI = ROOT / "data" / "raw" / "nti_objects_by_region.csv"
OUT = ROOT / "data" / "raw" / "ckp_score_by_region.csv"


def main():
    regions = (pd.read_parquet(PARQUET, columns=["object_name", "object_level"])
                 .query("object_level == 'Регион'")["object_name"].unique())
    nti = pd.read_csv(NTI).set_index("region")["value"].to_dict()
    rows = []
    for r in sorted(regions):
        n = int(nti.get(r, 0))
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
