#!/usr/bin/env python3
"""Собирает CSV для ИНФ12 (вузы на 100 тыс. жителей) из Wikidata + паркета.

Число организаций ВО по субъектам РФ — из Wikidata SPARQL: сущности-подклассы
университета (Q3918) со страной P17=Россия, регион — цепочка P131 до субъекта
Федерации (Q10864048), максимум 2 промежуточных уровня (вуз → город → субъект).
Рособрнадзор (официальный реестр) недоступен без российского IP и без гарантий
отдачи, поэтому Wikidata — повторяемый прокси: покрывает головные организации
ВО, филиалы — неполно.

Знаменатель — среднегодовая численность населения Y477110373 (тыс. чел.) из
паркета Росстата, 2023.

Выход:
  data/raw/universities_per_100k_by_region.csv — вузов на 100 тыс. жителей
  по регионам + строка «Российская Федерация»

Запуск:
    python -m scripts.build_universities_csv
"""
import csv
import re
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "Массив Данных в формате Parquet" / "Показатели по регионам.parquet"
OUT = ROOT / "data" / "raw" / "universities_per_100k_by_region.csv"

POPULATION = "Y477110373"  # Среднегодовая численность населения, тыс. человек

SPARQL = """
SELECT ?region ?regionLabel (COUNT(DISTINCT ?uni) AS ?cnt) WHERE {
  ?uni wdt:P31/wdt:P279* wd:Q3918 .
  ?uni wdt:P17 wd:Q159 .
  ?region wdt:P31/wdt:P279* wd:Q10864048 .
  ?region wdt:P17 wd:Q159 .
  ?uni wdt:P131/wdt:P131?/wdt:P131? ?region .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ru". }
}
GROUP BY ?region ?regionLabel
"""

# Названия Wikidata, не совпадающие с именами регионов в паркете Росстата:
# короткие имена республик, «Город Санкт-Петербург», Югра
W2R = {
    "Город Санкт-Петербург": "Санкт-Петербург",
    "Ханты-Мансийский автономный округ": "Ханты-Мансийский автономный округ — Югра",
    "Адыгея": "Республика Адыгея",
    "Башкортостан": "Республика Башкортостан",
    "Бурятия": "Республика Бурятия",
    "Дагестан": "Республика Дагестан",
    "Кабардино-Балкария": "Кабардино-Балкарская Республика",
    "Калмыкия": "Республика Калмыкия",
    "Карачаево-Черкесия": "Карачаево-Черкесская Республика",
    "Марий Эл": "Республика Марий Эл",
    "Мордовия": "Республика Мордовия",
    "Северная Осетия": "Республика Северная Осетия — Алания",
    "Татарстан": "Республика Татарстан",
    "Тыва": "Республика Тыва",
    "Удмуртия": "Удмуртская Республика",
    "Хакасия": "Республика Хакасия",
    "Чечня": "Чеченская Республика",
    "Чувашия": "Чувашская Республика",
    "Якутия": "Республика Саха (Якутия)",
}


def norm(name: str) -> str:
    """Приводим имя субъекта к каноническому виду: без «г.», «(без автономного округа)»."""
    name = W2R.get(name, name)
    name = re.sub(r"^г\.\s*", "", name)
    name = re.sub(r"\s*\(без автономн[а-я]+ округ[а-я]*\)", "", name)
    name = name.replace("—", "—").strip()
    return name


def population_by_region() -> pd.DataFrame:
    df = pd.read_parquet(
        PARQUET,
        columns=["object_name", "year", "indicator_code", "indicator_value"],
        filters=[("indicator_code", "==", POPULATION), ("object_level", "==", "Регион")],
    )
    df = df[df["indicator_value"] > -1e6]
    return (df.sort_values(["object_name", "year"])
              .drop_duplicates("object_name", keep="last")
              .set_index("object_name")["indicator_value"])


def main():
    pop = population_by_region()
    pop.index = [norm(n) for n in pop.index]
    print(f"Население: {len(pop)} регионов, {pop.index[0]}…")

    r = requests.get(
        "https://query.wikidata.org/sparql",
        params={"query": SPARQL, "format": "json"},
        headers={"User-Agent": "mipt-spatial-distribution/1.0 (research)"},
        timeout=90,
    )
    r.raise_for_status()
    rows = r.json()["results"]["bindings"]
    print(f"Строк от Wikidata: {len(rows)}")

    counts = {}
    for row in rows:
        region = norm(row["regionLabel"]["value"])
        cnt = int(row["cnt"]["value"])
        if region in pop.index:
            counts[region] = counts.get(region, 0) + cnt
        else:
            print(f"  не сопоставлено: {region} ({cnt} вузов)")

    total_unis, total_pop = 0, 0.0
    with OUT.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["region", "value"])
        for region, cnt in sorted(counts.items(), key=lambda kv: -kv[1] / pop[kv[0]]):
            # вузов на 100 тыс.: cnt / (тыс. чел. * 1000) * 100_000
            w.writerow([region, round(cnt / pop[region] * 100, 3)])
            total_unis += cnt
            total_pop += pop[region]
        w.writerow(["Российская Федерация", round(total_unis / total_pop * 100, 3)])
    print(f"Записано: {OUT} ({len(counts)} регионов, {total_unis} вузов)")


if __name__ == "__main__":
    main()
