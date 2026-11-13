#!/usr/bin/env python3
"""Собирает CSV для ЧК15 (доля занятых в высокотехнологичных и наукоёмких ВЭД).

Занятость по ОКВЭД в открытом парке отсутствует (только «вредные условия» и
неформальный сектор), форма — закрытый контур. Открытая альтернатива — ЕМИСС
№57822 «Средняя численность работников организаций, НЕ относящихся к
субъектам МСП» по видам деятельности: крупные и средние организации, на
которых и сидит основная ВТ-занятость. Скачивает через scripts.fedstat_download
(нужен RU IP), срез: Год=2024, Период=январь-декабрь.

ВТ-набор — классы/группы ОКВЭД2 (канонические названия узлов ЕМИСС):
  21 производство лекарственных средств и материалов
  26 производство компьютеров, электронных и оптических изделий
  30.3 летательные аппараты, включая космические
  32.5 инструменты и приборы для измерения, тестирования и навигации
  62 деятельность в области информационных технологий
  71 архитектура, инженерные изыскания, технические услуги
  72 научные исследования и разработки
Если узел приехал на двух уровнях (группа и подгруппа), остаётся строка с
большим значением — родитель.

Знаменатель — «Всего по обследуемым видам экономической деятельности»
того же среза. Ограничение честно фиксируем: только не-МСП организации.

Выход: data/raw/ht_employment_share_by_region.csv — % + строка РФ.

Запуск (RU IP):  python -m scripts.build_ht_employment_csv
"""
import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.fedstat_download import download  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
INDICATOR = "57822"
OKVED_FILTER = "Классификатор видов экономической деятельности (ОКВЭД2)"
HT_TITLES = {
    "Производство лекарственных средств и материалов, применяемых в медицинских целях и ветеринарии",
    "Производство компьютеров, электронных и оптических изделий",
    "Производство летательных аппаратов, включая космические, и соответствующего оборудования",
    "Деятельность в области информационных технологий",
    "Деятельность в области архитектуры и инженерно-технического проектирования; технических испытаний, исследований и анализа",
    "Научные исследования и разработки",
}
TOTAL_TITLE = "Всего по обследуемым видам экономической деятельности"


def parse_sdmx(data):
    """[(окато_имя, оквед_код, значение)] из SDMX 1.0 ЕМИСС.

    Кодлисты дают коды→названия (ОКВЭД-коды ЕМИСС совпадают с ОКВЭД2 для
    нужных классов; «Всего» = 101.АГ). Серия = s_OKVED2 + s_OKATO + ObsValue
    (десятичная запятая). Отдельно вернём словарь код→название ОКВЭД."""
    s = data.decode("utf-8")
    okved_names = dict(re.findall(
        r'<structure:Code value="([^"]+)">\s*<structure:Description[^>]*>([^<]+)', s))
    okato_names = dict(re.findall(
        r'<structure:CodeList id="s_OKATO">.*?</structure:CodeList>', s, re.S) and [])
    m = re.search(r'<structure:CodeList id="s_OKATO">(.*?)</structure:CodeList>', s, re.S)
    okato_names = dict(re.findall(
        r'<structure:Code value="([^"]+)">\s*<structure:Description[^>]*>([^<]+)', m.group(1))) if m else {}
    rows = []
    for sm in re.finditer(
            r'<generic:SeriesKey>(.*?)</generic:SeriesKey>.*?<generic:ObsValue value="([^"]+)"/>',
            s, re.S):
        attrs = dict(re.findall(r'concept="([^"]+)" value="([^"]+)"', sm.group(1)))
        rows.append((okato_names.get(attrs.get("s_OKATO", ""), attrs.get("s_OKATO", "")),
                     attrs.get("s_OKVED2", ""),
                     float(sm.group(2).replace(",", "."))))
    return rows, okved_names


def main():
    tree, data = download(INDICATOR, only={"Год": ["2024"], "Период": ["январь-декабрь"],
                                           OKVED_FILTER: [TOTAL_TITLE] + sorted(HT_TITLES)})
    open("/tmp/57822.sdmx", "wb").write(data)
    rows, okved_names = parse_sdmx(data)
    print(f"SDMX: {len(data):,} байт, серий {len(rows)}")

    # коды нужных узлов из кодлиста (кратчайший код на название = родитель,
    # чтобы 26 считался без ребёнка 26.51)
    ht_codes = {}
    for code, name in okved_names.items():
        if name in HT_TITLES and (name not in ht_codes or len(code) < len(ht_codes[name])):
            ht_codes[name] = code
    want = set(ht_codes.values())
    print("ВТ-коды:", {n: c for n, c in ht_codes.items()})
    total_codes = {c for c, n in okved_names.items() if n == TOTAL_TITLE}

    ht, tot = {}, {}
    for region, code, val in rows:
        region = region.strip()
        if not region or "Федерация" in region or "округ" in region:
            continue  # РФ и федеральные округа
        if code in total_codes:
            tot[region] = max(tot.get(region, 0), val)
        elif code in want:
            ht[region] = ht.get(region, 0) + val
    out = [(r, round(ht[r] / tot[r] * 100, 1)) for r in tot if r in ht and tot[r] > 0]
    rf = round(sum(ht[r] for r, _ in out) / sum(tot[r] for r, _ in out) * 100, 1)
    with open(RAW / "ht_employment_share_by_region.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["region", "value"])
        for r, v in sorted(out, key=lambda x: -x[1]):
            w.writerow([r, v])
        w.writerow(["Российская Федерация", rf])
    print(f"→ ht_employment_share_by_region.csv ({len(out)} регионов, РФ={rf}%)")


if __name__ == "__main__":
    main()
