"""
ЧК22 и ЧК24 — CSV-показатели по субъектам РФ (etl/config.yaml -> csv_indicators),
год — cfg['year'] (2023).

ЧК22  Dст = (Чдн + Чкн) / Чиссл × 100 — из росстатовского parquet:
      Y477110454 (исследователи с учёными степенями = Y477110455 доктора +
      Y477110456 кандидаты) / Y477110473 (исследователи).

ЧК24  Кобн = Ч принятых исследователей / Ч исследователей на конец года × 1000 —
      форма 2-наука, таблица 4а_2 «Движение исследователей» по ОКАТО. В parquet
      её нет; Росстат выкладывает своды формы архивом, файл (19 КБ) взят оттуда:
        https://rosstat.gov.ru/storage/mediabank/2-nauka_2023.rar
          -> ОКАТО/4а_2_ОКАТО_п.xlsx = data/raw/nauka_rf/2-nauka_2023_4a_2_okato.xlsx
      Вариант ТЗ «из других субъектов РФ» (ЧК242) открытыми данными не считается:
      признака территории прибытия в форме нет, только ведомственный запрос.

Имена регионов пишем как в parquet (ingest ищет по object_name); xlsx
сопоставляется с parquet по ОКАТО. Строка «Российская Федерация» становится
indicator.national_total — базой шкалы режима «Россия» у ставок.

    python -m scripts.build_researchers_csv
"""
import openpyxl
import pandas as pd
import yaml

cfg = yaml.safe_load(open('etl/config.yaml', encoding='utf-8'))
PARQUET, YEAR = cfg['rosstat_parquet'], cfg['year']
XLSX = 'data/raw/nauka_rf/2-nauka_2023_4a_2_okato.xlsx'
RF = 'Российская Федерация'


def parquet_values(code):
    """object_name -> (object_okato, indicator_value). Численности неотрицательны,
    отрицательное — сентинел пропуска Росстата (-77777777 / -99999999)."""
    df = pd.read_parquet(PARQUET, columns=['object_name', 'object_okato', 'indicator_value'],
                         filters=[('indicator_code', '=', code), ('year', '=', YEAR),
                                  ('object_level', 'in', ['Регион', 'Страна']),
                                  ('indicator_value', '>=', 0)])
    return df.set_index('object_name')


def movement():
    """(ОКАТО, принято, на конец) по строкам 4а_2; РФ -> '00000000', как в parquet."""
    rows = list(openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
                .worksheets[0].iter_rows(values_only=True))
    assert str(YEAR) in rows[0][0], f"{XLSX}: {rows[0][0]!r}, а год конфига {YEAR}"
    head = [str(c or '') for c in rows[2]]
    hired = next(i for i, h in enumerate(head) if h.startswith('Принято'))  # подгр. «Всего»
    end = next(i for i, h in enumerate(head) if 'на конец' in h)
    for r in rows[5:]:
        okato = str(r[1] or '')
        if okato.startswith('643'):
            okato = '00000000'
        if len(okato) == 11 or okato == '00000000':
            yield okato, r[hired], r[end]


def main():
    researchers = parquet_values('Y477110473')
    res = researchers['indicator_value']
    chk22 = (parquet_values('Y477110454')['indicator_value'] / res * 100).dropna().round(2)

    chk24, skipped, unmatched = {}, [], []
    for code, hired, end in movement():
        # «…» — данные скрыты (конфиденциальность, 282-ФЗ), пусто — явления нет
        if not isinstance(hired, (int, float)) or not isinstance(end, (int, float)) or not end:
            skipped.append(code)
            continue
        # Код ОКАТО однозначно не сопоставить: «Архангельская/Тюменская область
        # с АО и без АО» в parquet делят один код, а в xlsx у «без АО» свой
        # (11001…). Файл и parquet — один свод 2-наука, так что берём регион с
        # тем же префиксом ОКАТО и той же численностью исследователей на конец года.
        name = [n for n, o in researchers['object_okato'].items()
                if o[:2] == code[:2] and res[n] == end]
        if len(name) == 1:
            chk24[name[0]] = round(hired / end * 1000, 2)
        else:
            unmatched.append(code)

    for path, s in (('data/raw/researchers_degree_share_by_region.csv', chk22),
                    ('data/raw/researchers_renewal_per_1k_by_region.csv', pd.Series(chk24))):
        s.rename_axis('region').rename('value').to_csv(path)
        print(f"{path}: {len(s)} строк, РФ = {s[RF]}")
    print(f"ЧК24 без значения («…» или пусто), ОКАТО: {skipped}")
    print(f"ЧК24 не сопоставлены с parquet, ОКАТО: {unmatched}")

    pilots = [r['object_name'] for r in cfg['regions']]
    missing = [p for p in pilots if p not in chk22 or p not in chk24]
    assert not missing, f"нет значений для пилотов: {missing}"


if __name__ == '__main__':
    main()
