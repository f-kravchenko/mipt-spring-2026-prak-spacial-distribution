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
  rd_personnel_per_10k_by_region.csv          ЧК21  персонал НИР на 10 тыс.
  rd_expenditure_per_1k_by_region.csv         ИНВ13 тыс. руб. на 1000 чел
  patent_applications_per_10k_by_region.csv   НР11  заявок на 10 тыс.
  vtno_investments_per_1k_by_region.csv       ИНВ11 тыс. руб. на 1000 чел
  manufacturing_investment_per_1k_by_region.csv ИНВ12 тыс. руб. на 1000 чел
  budget_revenue_per_1k_by_region.csv         ИНВ31 тыс. руб. на 1000 чел

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
    # Замечания ОШИБКИ 2, 3, 7 (показатели блоков ЧК, ИНВ, НР):
    # человек / тыс. чел -> *10 = на 10 тыс. чел
    ("rd_personnel_per_10k_by_region.csv", "Y477110471", None, 10),       # ЧК21
    # млн руб. / тыс. чел -> *1000 = тыс. руб. на 1000 чел
    ("rd_expenditure_per_1k_by_region.csv", "Y477110039", None, 1000),    # ИНВ13
    # заявок / тыс. чел -> *10 = на 10 тыс. чел
    ("patent_applications_per_10k_by_region.csv", "Y477110309", None, 10),  # НР11
    # ОШИБКИ, задача 17: инвестиции и доходы на 1000 жителей (млн руб. -> тыс. руб.)
    ("manufacturing_investment_per_1k_by_region.csv", "Y477050016",
     "В том числе по видам экономической деятельности: обрабатывающие производства", 1000),  # ИНВ12
    ("budget_revenue_per_1k_by_region.csv", "Y477110071", None, 1000),  # ИНВ31
    # СР51 (парки/зелень): тыс. га / тыс. чел = га/чел -> *10 000 = кв.м/чел
    ("green_space_per_capita_by_region.csv", "Y477030026",
     "Общая площадь зеленых насаждений в пределах городской черты, тыс. га: всего", 10000),
]

# Итог по РФ в паркете в других единицах, чем у регионов. Без строки РФ у ставки
# нет national_total: режим «Россия» отдаёт 404, а 13.10 из-за этого падала
# публикация (503). Y477110071: у РФ млрд руб. (22 325), у регионов млн руб.
RF_UNIT_FIX = {"Y477110071": 1000}

# Числитель из готового CSV (region,value в млн руб.), знаменатель — население.
CSV_OUTPUTS = [
    ("vtno_investments_per_1k_by_region.csv", "vtno_investments_by_region.csv", 1000),  # ИНВ11
]

# Для итога по РФ из суммы регионов: эти строки уже входят в «...(с автономным
# округом)» / «...(с автономными округами)», иначе посчитаются дважды.
NESTED_REGIONS = {
    "Архангельская область (без автономного округа)",
    "Ненецкий автономный округ",
    "Тюменская область (без автономных округов)",
    "Ханты-Мансийский автономный округ — Югра",
    "Ямало-Ненецкий автономный округ",
}

# НР21 берётся напрямую из паркета: Y477110235 (официальный удельный вес, %).


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
        # У РФ бывает несколько строк за год в разных единицах (Y477110053:
        # 16 952 тыс. т и 17 млн т) — берём все, write() выберет подходящую.
        rf_val = val[val["object_level"] == "Страна"]
        rf_val = rf_val[rf_val["year"] == rf_val["year"].max()]
        val = val[val["object_level"] == "Регион"]
        val = val.sort_values(["object_name", "year"]).drop_duplicates("object_name", keep="last")
        rows = []
        for _, r in val.iterrows():
            p = pop[(pop["object_name"] == r["object_name"]) & (pop["year"] <= r["year"])]
            if p.empty:
                continue
            p_val = p.sort_values("year")["indicator_value"].iloc[-1]
            rows.append((r["object_name"], round(r["indicator_value"] / p_val * mult, 3)))
        rf_pop = pop[(pop["object_name"] == "Российская Федерация")
                     & (pop["year"] <= rf_val["year"].max())].sort_values("year")
        rf_candidates = []
        if not rf_val.empty and not rf_pop.empty:
            p_val = rf_pop["indicator_value"].iloc[-1]
            rf_candidates = [round(v * RF_UNIT_FIX.get(code, 1) / p_val * mult, 3)
                             for v in rf_val["indicator_value"]]
        write(fname, rows, rf_candidates)

    latest_pop = pop.sort_values("year").drop_duplicates("object_name", keep="last")
    pop_by_name = dict(zip(latest_pop["object_name"], latest_pop["indicator_value"]))
    for fname, src, mult in CSV_OUTPUTS:
        src_df = pd.read_csv(RAW / src)
        # В исходном CSV у части регионов остались сентинелы Росстата
        # (-77 777 777 = «нет данных»), отрицательных инвестиций не бывает.
        bad = src_df[src_df["value"] < 0]
        if len(bad):
            print(f"{src}: пропущено {len(bad)} регионов с сентинелом: {', '.join(bad['region'])}")
        src_df = src_df[src_df["value"] >= 0]
        rows = [(r["region"], round(r["value"] / pop_by_name[r["region"]] * mult, 3))
                for _, r in src_df.iterrows() if r["region"] in pop_by_name]
        if len(rows) < len(src_df):
            print(f"{src}: {len(src_df) - len(rows)} регионов без населения пропущено")
        # Строки РФ в исходном CSV нет: сумма регионов / население РФ. Регионы
        # с сентинелом в сумму не входят, итог по РФ немного занижен.
        total = src_df[~src_df["region"].isin(NESTED_REGIONS)]["value"].sum()
        rf_pop = pop_by_name.get("Российская Федерация")
        rf_candidates = [round(total / rf_pop * mult, 3)] if rf_pop else []
        write(fname, rows, rf_candidates)


def write(fname, rows, rf_candidates=()):
    # Итог по РФ: первый кандидат, попавший в диапазон регионов. Вне диапазона
    # — значит, другие единицы; если не подошёл ни один, строку РФ не пишем,
    # national_total тогда не задаётся (как у других CSV без итога по РФ).
    rows = [(name, v) for name, v in rows if name != "Российская Федерация"]
    reg = [v for _, v in rows]
    rf = next((v for v in rf_candidates if min(reg) <= v <= max(reg)), None)
    if rf is not None:
        rows.append(("Российская Федерация", rf))
    out = RAW / fname
    with out.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["region", "value"])
        for region, v in sorted(rows, key=lambda x: -x[1]):
            w.writerow([region, v])
    rf = [v for name, v in rows if name == "Российская Федерация"]
    print(f"{fname}: {len(rows)} строк, РФ = {rf[0] if rf else 'нет'}")


if __name__ == "__main__":
    main()
