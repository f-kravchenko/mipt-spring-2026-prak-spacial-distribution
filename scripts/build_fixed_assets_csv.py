#!/usr/bin/env python3
"""Собирает CSV для ИНФ15 (коэффициент обновления основных фондов) из паркета.

Коэффициент обновления = ввод в действие ОФ (Y477110029) / наличие ОФ на конец
года (Y477110391) × 100. В парке нет готового кода для всей экономики (есть
только с/х Y477120016), поэтому показатель считается из двух кодов и грузится
через CSV — тот же приём, что у ЧК22 в build_researchers_csv.py.

Выход:
  data/raw/fixed_assets_renewal_by_region.csv — % по регионам + строка
  «Российская Федерация» (national_total, из object_level=Страна)

Запуск:
    python -m scripts.build_fixed_assets_csv
"""
import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "Массив Данных в формате Parquet" / "Показатели по регионам.parquet"
OUT = ROOT / "data" / "raw" / "fixed_assets_renewal_by_region.csv"

NUMERATOR = "Y477110029"   # Ввод в действие основных фондов, млн руб.
DENOMINATOR = "Y477110391"  # Стоимость основных фондов на конец года, млн руб.


def read_code(code: str) -> pd.DataFrame:
    """Последний доступный год по каждому объекту (регион + страна)."""
    df = pd.read_parquet(
        PARQUET,
        columns=["object_name", "object_level", "year", "indicator_code",
                 "indicator_value"],
        filters=[("indicator_code", "==", code)],
    )
    df = df[df["indicator_value"] > -1e6]  # отбрасываем сентинелы Росстата
    return (df.sort_values(["object_name", "year"])
              .drop_duplicates("object_name", keep="last")
              .set_index("object_name"))


def main():
    num = read_code(NUMERATOR)
    den = read_code(DENOMINATOR)
    joined = num.join(den, lsuffix="_num", rsuffix="_den", how="inner")
    # Годы числителя и знаменателя должны совпадать (один сборник)
    same_year = joined[joined["year_num"] == joined["year_den"]]
    print(f"Регионов с совпавшими годами: {len(same_year)} из {len(joined)}")
    same_year = same_year.assign(
        value=same_year["indicator_value_num"] / same_year["indicator_value_den"] * 100
    )

    with OUT.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["region", "value"])
        regions = same_year[same_year["object_level_num"] == "Регион"]
        for _, r in regions.sort_values("value", ascending=False).iterrows():
            w.writerow([r.name, round(r["value"], 2)])
        rf = same_year[same_year["object_level_num"] == "Страна"]
        if not rf.empty:
            w.writerow(["Российская Федерация", round(rf["value"].iloc[0], 2)])
    print(f"Записано: {OUT}")


if __name__ == "__main__":
    main()
