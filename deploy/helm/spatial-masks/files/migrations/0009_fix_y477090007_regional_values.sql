-- 0009_fix_y477090007_regional_values.sql
-- Одноразовая починка данных Y477090007 (см. README_part3.md §9).
--
-- Причина: regional_value_lookup в ETL брал первую строку parquet без фильтра
-- по subsection — у Y477090007 четыре подраздела ОКВЭД, и в БД попадало
-- значение "Водоснабжение..." вместо "Обрабатывающие производства"
-- (Москва: 195 вместо 5275 млрд руб.). ETL исправлен (etl/config.yaml ->
-- indicators.Y477090007.subsection), эта миграция чинит УЖЕ ЗАГРУЖЕННЫЕ данные.
--
-- ВАЖНО: миграции прогоняются целиком на каждом деплое (helm pre-upgrade job /
-- compose-сервис migrate), поэтому каждый шаг самоотключаем:
--   - срабатывает только если данные в заведомо старом состоянии;
--   - после починки — no-op;
--   - будущий ETL-прогон (пишет уже корректные значения) не затирается.

-- 1) Единица измерения: в parquet "миллиардов рублей" — правим только
--    заведомо старое значение, ничего другого не трогаем.
UPDATE indicator SET unit = 'млрд руб.'
WHERE code = 'Y477090007' AND unit = 'млн руб.';

-- 2) Итог по РФ (object_level='Страна', 2023) — только если ещё не заполнен
--    (свежий ETL заполняет сам, см. load_indicators; его значения не трогаем).
UPDATE indicator i SET national_total = v.total
FROM (VALUES ('Y477090007', 74574.0),
             ('Y477110039', 1649788.0),
             ('Y477110236', 8323885.5019)) AS v(code, total)
WHERE i.code = v.code AND i.national_total IS NULL;

-- 3) Масштаб распределений: значения линейны по rv, поэтому точная починка —
--    умножение на new_rv/old_rv. Регион масштабируется ТОЛЬКО если сумма его
--    эталонной композиции совпадает со СТАРЫМ (ошибочным) rv с относительным
--    допуском 1e-6 (float-дрейф суммирования) — защита и от повторного
--    умножения на следующем деплое, и от порчи данных будущего ETL
--    (у него сумма сразу равна новому rv -> условие не выполняется).
WITH fix AS (
    SELECT r.id AS region_id, f.old_rv, f.new_rv
    FROM region r
    JOIN (VALUES ('moscow',         195.0, 5275.0),
                 ('krasnodar',      50.8,  1607.0),
                 ('yakutia_center', 10.8,  54.9)) AS f(slug, old_rv, new_rv)
      ON f.slug = r.slug
),
cur AS (
    SELECT c.region_id, sum(dc.value) AS s
    FROM distribution_cell dc
    JOIN composition c ON c.id = dc.composition_id
    WHERE c.indicator_code = 'Y477090007'
      AND c.id IN (SELECT min(id) FROM composition
                   WHERE indicator_code = 'Y477090007' GROUP BY region_id)
    GROUP BY c.region_id
),
todo AS (
    SELECT fix.region_id, fix.new_rv / fix.old_rv AS k
    FROM fix JOIN cur USING (region_id)
    WHERE abs(cur.s - fix.old_rv) / fix.old_rv < 1e-6
)
UPDATE distribution_cell dc
SET value = dc.value * todo.k
FROM composition c, todo
WHERE dc.composition_id = c.id
  AND c.region_id = todo.region_id
  AND c.indicator_code = 'Y477090007';
