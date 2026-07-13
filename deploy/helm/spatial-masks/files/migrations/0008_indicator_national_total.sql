-- 0008_indicator_national_total.sql
-- Итог показателя по РФ (строка object_level='Страна' в исходном parquet) —
-- для режима отображения "Россия": доля ячейки в общероссийском показателе
-- cell_national_share = cell_abs / national_total, где cell_abs получен через
-- РЕГИОНАЛЬНОЕ значение (raw/total_региона × показатель_региона). Умножать
-- вес ячейки сразу на национальный показатель нельзя — веса нормированы
-- внутри региона и между регионами не сопоставимы.
-- Заполняется ETL (etl/ingest.py, load_indicators) из той же таблицы Росстата,
-- что и региональные значения — единицы измерения согласованы по построению.

ALTER TABLE indicator ADD COLUMN IF NOT EXISTS national_total double precision;

COMMENT ON COLUMN indicator.national_total IS
    'Значение показателя по РФ в целом (object_level=Страна в исходных данных), '
    'та же единица измерения, что у региональных значений';
