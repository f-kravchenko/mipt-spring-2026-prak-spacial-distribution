"""
ИНФ18 — удельный вес машин и оборудования в возрасте до 5 лет в общей стоимости
машин и оборудования организаций, выполнявших научные исследования и разработки,
по субъектам РФ (etl/config.yaml -> csv_indicators).

Уд = Стоимость машин и оборудования до 5 лет / Стоимость машин и оборудования × 100 —
форма 2-наука, таблица 6 «Среднегодовая полная учётная стоимость основных фондов»
по ОКАТО за 2024 г.: графа 3 / графа 2. В parquet её нет; файл (17 КБ) взят из
свода формы:
  https://rosstat.gov.ru/storage/mediabank/2-nauka_2024.rar
    -> ОКАТО/6_ОКАТО_п.xlsx = data/raw/nauka_rf/2-nauka_2024_6_okato.xlsx
Прежний CSV (собран вручную) не совпадал с этой таблицей ни за один год
2021–2024, его источник не восстановить — поэтому пересобран отсюда.

Имена регионов пишем как в parquet (ingest ищет по object_name); xlsx
сопоставляется с parquet по ОКАТО, как в build_researchers_csv.py. Строка
«Российская Федерация» становится indicator.national_total — базой шкалы
режима «Россия» у ставок.

    python -m scripts.build_tech_age_csv
"""
import csv

import openpyxl
import pandas as pd
import yaml

cfg = yaml.safe_load(open('etl/config.yaml', encoding='utf-8'))
PARQUET = cfg['rosstat_parquet']
XLSX = 'data/raw/nauka_rf/2-nauka_2024_6_okato.xlsx'
OUT = 'data/raw/tech_under_5_years_by_region.csv'
RF = 'Российская Федерация'


def parquet_names():
    """ОКАТО (8 знаков) -> имена регионов в parquet. У «Архангельской/Тюменской
    области с АО и без АО» код общий, их различаем ниже."""
    df = pd.read_parquet(PARQUET, columns=['object_name', 'object_okato'],
                         filters=[('indicator_code', '=', 'Y477110473'),
                                  ('object_level', '=', 'Регион')]).drop_duplicates()
    names = {}
    for name, okato in zip(df['object_name'], df['object_okato']):
        names.setdefault(okato, []).append(name)
    return names


def region_name(code, names):
    """Код ОКАТО строки xlsx (11 знаков) -> имя как в parquet или None."""
    if code[2:5] == '001':  # «Архангельская область (кроме НАО)», «Тюменская (кроме АО)»
        cand = [n for n in names.get(code[:2] + '000000', []) if '(без ' in n]
    else:
        cand = names.get(code[:8], [])
        if len(cand) > 1:  # тот же код у «с АО» и «без АО» — у xlsx это «с АО»
            cand = [n for n in cand if '(с ' in n]
    return cand[0] if len(cand) == 1 else None


def main():
    names = parquet_names()
    rows = list(openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
                .worksheets[0].iter_rows(values_only=True))
    assert '2024' in str(rows[0][0]), f"{XLSX}: {rows[0][0]!r}"
    out, skipped, unmatched = [], [], []
    for r in rows:
        code = str(r[1] or '').strip()
        if not (code.startswith('643') or len(code) == 11):
            continue  # шапка, федеральные округа
        machines, young = r[3], r[4]
        # «...» — данные скрыты (конфиденциальность, 282-ФЗ)
        if not isinstance(machines, (int, float)) or not isinstance(young, (int, float)) \
                or not machines:
            skipped.append(str(r[0]).strip())
            continue
        name = RF if code.startswith('643') else region_name(code, names)
        if name is None:
            unmatched.append(str(r[0]).strip())
            continue
        out.append((name, round(young / machines * 100, 1)))

    with open(OUT, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['region', 'value'])
        w.writerows(out)
    rf = dict(out).get(RF)
    print(f"{OUT}: {len(out)} строк, РФ = {rf}")
    print(f"ИНФ18 без значения («...»): {skipped}")
    print(f"ИНФ18 не сопоставлены с parquet: {unmatched}")
    pilots = [r['object_name'] for r in cfg['regions']]
    missing = [p for p in pilots if p not in dict(out)]
    assert not missing, f"нет значений для пилотов: {missing}"


if __name__ == '__main__':
    main()
