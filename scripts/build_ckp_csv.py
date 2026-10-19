#!/usr/bin/env python3
"""Собирает CSV для ИНФ13 (объекты научно-технологической инфраструктуры).

Три слагаемых по рецепту ТЗ (Nлаб + NПИШ + NЦКП):
- ЦКП и УНУ — скрапинг каталога ckp-rf.ru (/catalog/ckp/, /catalog/usu/):
  в карточках списка регион лежит в div.catalogue-item-city. Сайт доступен
  только с российского IP (см. SCRAPE_FROM_RU.md).
- ПИШ — 50 передовых инженерных школ (30 в 2022 + 20 в 2023), перечень
  зафиксирован ниже как словарь по регионам (источники: релиз Социоцентра
  о рейтинге второй волны, сводка ФСРАР по группам первой волны).
- Лаборатории — единого открытого реестра нет, в счёт не входят.

Выход:
  data/raw/nti_objects_by_region.csv — объектов на регион + строка
  «Российская Федерация» (сумма)

Запуск (с российским IP):
    python -m scripts.build_ckp_csv
"""
import csv
import re
import time
from collections import Counter
from pathlib import Path

import requests

from scripts.build_universities_csv import norm

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "nti_objects_by_region.csv"

BASE = "https://ckp-rf.ru/catalog/{cat}/?PAGEN_1={page}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
SLEEP = 0.4  # щадящий интервал, как у build_culture_by_region.py

TITLE_RE = re.compile(r'catalogue-item-title">(.*?)</div>', re.S)
CITY_RE = re.compile(r'catalogue-address"></use></svg>\s*([^<]+?)\s*</div>', re.S)
PAGINATION_RE = re.compile(r"PAGEN_1=(\d+)")

# 50 передовых инженерных школ по субъектам РФ.
# Первая волна (2022, 30): сводка ФСРАР news/view?id=5953 (по government.ru/news/50917).
# Вторая волна (2023, 20): релиз Социоцентра «Опубликован рейтинг ПИШ второй волны».
PISH_BY_REGION = {
    "Москва": 12,
    "Санкт-Петербург": 6,
    "Республика Татарстан": 5,
    "Самарская область": 3,
    "Томская область": 3,
    "Воронежская область": 2,
    "Нижегородская область": 2,
    "Ростовская область": 2,
    "Республика Башкортостан": 1,
    "Новосибирская область": 1,
    "Свердловская область": 1,
    "Приморский край": 1,
    "Пермский край": 1,
    "Новгородская область": 1,
    "Псковская область": 1,
    "Челябинская область": 1,
    "Тульская область": 1,
    "Ярославская область": 1,
    "Чеченская Республика": 1,
    "Ульяновская область": 1,
    "Вологодская область": 1,
    "Омская область": 1,
    "Сахалинская область": 1,
}


def fetch_page(cat: str, page: int) -> str:
    r = requests.get(BASE.format(cat=cat, page=page), headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def scrape_catalog(cat: str) -> Counter:
    """Обходим все страницы каталога, считаем объекты по регионам."""
    counts: Counter = Counter()
    page, max_page = 1, 1
    while page <= max_page:
        html = fetch_page(cat, page)
        max_page = max(max_page,
                       max((int(n) for n in PAGINATION_RE.findall(html)), default=1))
        titles = TITLE_RE.findall(html)
        cities = CITY_RE.findall(html)
        if len(titles) != len(cities):
            print(f"  {cat} стр.{page}: {len(titles)} названий vs {len(cities)} регионов — пропуск")
        for city in cities:
            counts[norm(city)] += 1
        print(f"  {cat} стр.{page}/{max_page}: +{len(cities)}")
        page += 1
        time.sleep(SLEEP)
    return counts


def main():
    print("ckp-rf.ru: ЦКП")
    ckp = scrape_catalog("ckp")
    print("ckp-rf.ru: УНУ")
    usu = scrape_catalog("usu")

    total = Counter()
    for c in (ckp, usu):
        total.update(c)
    total.update({k: v for k, v in PISH_BY_REGION.items()})
    print(f"ЦКП: {sum(ckp.values())}, УНУ: {sum(usu.values())}, "
          f"ПИШ: {sum(PISH_BY_REGION.values())}")

    with OUT.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["region", "value"])
        for region, cnt in sorted(total.items(), key=lambda kv: -kv[1]):
            w.writerow([region, cnt])
        w.writerow(["Российская Федерация", sum(total.values())])
    print(f"Записано: {OUT} ({len(total)} регионов)")


if __name__ == "__main__":
    main()
