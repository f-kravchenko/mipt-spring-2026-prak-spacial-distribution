#!/usr/bin/env python3
"""Собирает CSV удельных показателей (на жителя / на 100 тыс.) из паркета Росстата.

Замечания заказчика (ОШИБКИ, задачи 11, 14, 16): абсолютные объёмы подменяли
кг/чел и «на 100 тыс. жителей». Здесь значение делится на численность населения
региона (Y477110461, тыс. человек) за тот же год.

Выход (data/raw/):
  emissions_per_capita_by_region.csv          СР41  кг/чел
  waste_generated_per_capita_by_region.csv    СР43  кг/чел
  waste_utilized_per_capita_by_region.csv     СР44  кг/чел
  sport_halls_per_100k_by_region.csv          СР52  залов на 100 тыс.
  sport_grounds_per_100k_by_region.csv        СР53  площадок и полей на 100 тыс.

Запуск:
    python -m scripts.build_per_capita_csv
"""
import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "Массив Данных в формате Parquet" / "Показатели по регионам.parquet"
RAW = ROOT / "data" / "raw"
YEAR = 2023
POPULATION = "Y477110461"  # Численность населения, тыс. человек, на конец года

# (файл, код, подраздел или None, множитель к value / население_тыс)
# тыс. т / тыс. чел = т/чел -> *1000 = кг/чел
# штук / тыс. чел -> *100 = на 100 тыс. чел
OUTPUTS = [
    ("emissions_per_capita_by_region.csv", "Y477110053", None, 1000),
    ("waste_generated_per_capita_by_region.csv", "Y477070020",
     "Образование отходов производства и потребления", 1000),
    ("waste_utilized_per_capita_by_region.csv", "Y477070020",
     "Утилизация и обезвреживание отходов производства и потребления: Всего", 1000),
    ("sport_halls_per_100k_by_region.csv", "Y477110502", None, 100),
    ("sport_grounds_per_100k_by_region.csv", "Y477110501", None, 100),
]


def read(code, subsection=None):
    filters = [("indicator_code", "==", code), ("year", "<=", YEAR),
               ("object_level", "in", ["Регион", "Страна"])]
    if subsection:
        filters.append(("subsection", "==", subsection))
    df = pd.read_parquet(
        PARQUET,
        columns=["object_name", "object_level", "year", "indicator_value", "subsection"],
        filters=filters,
    )
    df = df[df["indicator_value"] > -1e6]  # сентинелы Росстата
    return df


def main():
    print(f"Читаю {PARQUET}...")
    pop = read(POPULATION)
    pop = pop[pop["indicator_value"] > 0]
    for fname, code, sub, mult in OUTPUTS:
        val = read(code, sub)
        if val.empty:
            raise SystemExit(f"{code} ({sub}): нет строк — проверь подраздел")
        val = val.sort_values(["object_name", "year"]).drop_duplicates("object_name", keep="last")
        rows = []
        for _, r in val.iterrows():
            p = pop[(pop["object_name"] == r["object_name"]) & (pop["year"] <= r["year"])]
            if p.empty:
                continue
            p_val = p.sort_values("year")["indicator_value"].iloc[-1]
            rows.append((r["object_name"], round(r["indicator_value"] / p_val * mult, 3)))
        # Итог по РФ в паркете иногда в других единицах (Y477110053: 0.116 кг/чел
        # вместо ~116). Если РФ выпадает из диапазона регионов — строку РФ не пишем,
        # national_total тогда не задаётся (как у других CSV без итога по РФ).
        reg = [v for name, v in rows if name != "Российская Федерация"]
        rows = [(name, v) for name, v in rows
                if name != "Российская Федерация" or min(reg) <= v <= max(reg)]
        out = RAW / fname
        with out.open("w", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["region", "value"])
            for region, v in sorted(rows, key=lambda x: -x[1]):
                w.writerow([region, v])
        rf = [v for reg, v in rows if reg == "Российская Федерация"]
        print(f"{fname}: {len(rows)} строк, РФ = {rf[0] if rf else 'нет'}")


if __name__ == "__main__":
    main()
