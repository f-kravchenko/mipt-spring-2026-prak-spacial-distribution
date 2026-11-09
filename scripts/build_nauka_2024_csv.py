"""
ИНВ14, ИНФ17 и строка 16 таблицы показателей (оплата труда работников НИР) —
из свода формы 2-наука за 2024 г. по ОКАТО, как ИНФ18 в build_tech_age_csv.py
(etl/config.yaml -> csv_indicators). В parquet этих показателей нет.

  ИНВ14    C4771002014  Удельный вес внебюджетных средств во внутренних затратах
           на НИР = (Всего − Бюджетные всех уровней) / Всего × 100. Табл. 7
           «Источники финансирования», лист на субъект: стр. 8 и стр. 41.
  ИНФ17    C4771005017  Техническая вооружённость = стоимость машин и
           оборудования (табл. 6, гр. 2, тыс. руб.) / среднесписочная
           численность работников НИР (табл. 1_2, гр. 2): тыс. руб. на работника.
  стр. 16  C4771001026  Расчётная среднемесячная оплата труда работников НИР
           в % к средней по субъекту: оплата труда работников НИР (табл. 46,
           гр. 5, тыс. руб.) / среднесписочная численность (табл. 1_2, гр. 2) / 12,
           делённая на среднемесячную з/п в субъекте (parquet Y477110378, 2024).

Гр. 5 табл. 46 — оплата труда тех же работников (без совместителей и лиц по
договорам ГПХ), что и в среднесписочной численности. Гр. 4 (все затраты на
оплату труда) завысила бы оценку: по РФ 110,9 тыс. руб. в месяц вместо 95,8 тыс.

Файлы из свода https://rosstat.gov.ru/storage/mediabank/2-nauka_2024.rar:
  ОКАТО/7_ОКАТО_п.xlsx   -> data/raw/nauka_rf/2-nauka_2024_7_okato.xlsx
  ОКАТО/46_ОКАТО_п.xlsx  -> data/raw/nauka_rf/2-nauka_2024_46_okato.xlsx
  ОКАТО/1_2 ОКАТО_п.xlsx -> data/raw/nauka_rf/2-nauka_2024_1_2_okato.xlsx
  ОКАТО/6_ОКАТО_п.xlsx   -> data/raw/nauka_rf/2-nauka_2024_6_okato.xlsx (уже есть, ИНФ18)

Имена регионов пишем как в parquet, сопоставление по ОКАТО — из
build_tech_age_csv. Строка «Российская Федерация» становится
indicator.national_total — базой шкалы режима «Россия» у ставок.

    python -m scripts.build_nauka_2024_csv
"""
import csv

import openpyxl
import pandas as pd

from scripts.build_tech_age_csv import PARQUET, RF, cfg, parquet_names, region_name

XLSX = 'data/raw/nauka_rf/2-nauka_2024_{}_okato.xlsx'
YEAR = 2024
WAGE = 'Y477110378'  # среднемесячная номинальная начисленная з/п работников организаций, руб.


def num(v):
    """Число из ячейки; «...» (данные скрыты, 282-ФЗ) и пустые -> None."""
    return v if isinstance(v, (int, float)) else None


def is_subject(code):
    """РФ (643...) или субъект (11 знаков); федеральные округа пропускаем."""
    return code.startswith('643') or len(code) == 11


def by_region(table, names):
    """Таблица на одном листе: имя региона как в parquet -> строка xlsx."""
    rows = list(openpyxl.load_workbook(XLSX.format(table), read_only=True, data_only=True)
                .worksheets[0].iter_rows(values_only=True))
    assert str(YEAR) in str(rows[0][0]), f"табл. {table}: {rows[0][0]!r}"
    out = {}
    for r in rows:
        code = str(r[1] or '').strip()
        if is_subject(code):
            name = RF if code.startswith('643') else region_name(code, names)
            if name:
                out[name] = r
    return out


def funding(names):
    """Табл. 7 — лист на субъект (имя листа = ОКАТО): номер строки -> гр. 1 «Всего»."""
    wb = openpyxl.load_workbook(XLSX.format(7), read_only=True, data_only=True)
    title = next(wb.worksheets[0].iter_rows(values_only=True))[0]
    assert str(YEAR) in str(title), f"табл. 7: {title!r}"
    out = {}
    for sheet in wb.worksheets:
        code = sheet.title.strip()
        if not is_subject(code):
            continue
        name = RF if code.startswith('643') else region_name(code, names)
        if name:
            out[name] = {str(r[1]).strip(): r[2] for r in sheet.iter_rows(values_only=True)
                         if r[1] is not None}
    return out


def write(path, rows, universe):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['region', 'value'])
        w.writerows(rows)
    values = dict(rows)
    print(f"{path}: {len(rows)} строк, РФ = {values.get(RF)}")
    print(f"  без значения («...»): {sorted(set(universe) - set(values))}")
    pilots = [r['object_name'] for r in cfg['regions']]
    missing = [p for p in pilots if p not in values]
    assert RF in values and not missing, f"{path}: нет РФ или пилотов {missing}"


def main():
    names = parquet_names()
    staff = by_region('1_2', names)  # гр. 2 — среднесписочная численность работников НИР
    costs = by_region(46, names)     # гр. 5 — оплата труда работников НИР, тыс. руб.
    assets = by_region(6, names)     # гр. 2 — машины и оборудование, тыс. руб.
    sources = funding(names)
    wages = pd.read_parquet(PARQUET, columns=['object_name', 'indicator_value'],
                            filters=[('indicator_code', '=', WAGE), ('year', '=', YEAR)])
    wages = dict(zip(wages['object_name'], wages['indicator_value']))

    offbudget = []
    for name, lines in sources.items():
        total, budget = num(lines.get('8')), num(lines.get('41'))
        if total and budget is not None:
            offbudget.append((name, round((total - budget) / total * 100, 1)))

    equipment, pay_ratio = [], []
    for name, r in staff.items():
        workers = num(r[3])
        if not workers:
            continue
        machines = num(assets[name][3]) if name in assets else None
        if machines:
            equipment.append((name, round(machines / workers, 1)))
        pay = num(costs[name][6]) if name in costs else None
        if pay and wages.get(name):
            monthly = pay * 1000 / workers / 12
            pay_ratio.append((name, round(monthly / wages[name] * 100, 1)))

    write('data/raw/rd_offbudget_share_by_region.csv', offbudget, sources)
    write('data/raw/rd_equipment_per_worker_by_region.csv', equipment, staff)
    write('data/raw/rd_pay_to_regional_wage_by_region.csv', pay_ratio, staff)


if __name__ == '__main__':
    main()
