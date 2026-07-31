-- 0012_composition_regional_value.sql
-- Региональный итог хранится на самой композиции, а не восстанавливается
-- суммой 83 млн строк distribution_cell.
--
-- Зачем. distribution_cell занимала 7.35 ГБ из 10.2 ГБ базы (4.1 данных +
-- 3.2 индексы) — по строке на ячейку × 8 ablation-пресетов × 11 показателей ×
-- 4 региона. При этом per-cell значения не читал НИКТО:
--   * _RV_SQL и _GLOBAL_SCALE_SQL (apps/api/app/main.py) берут только
--     sum(dc.value) — а он по построению равен региональному значению
--     (композиция сохраняет сумму, sum_preserved);
--   * метрики ablation (gini/top10_share/sum_error) считаются в ETL в памяти
--     и лежат в quality_metric (960 строк) — они переживают очистку;
--   * tile_distribution (0002) не вызывает ни фронт, ни API — живая карта
--     рисуется tile_composition, который считает значения на лету из
--     mask_cell_value. Функция остаётся, но без данных вернёт пустой тайл.
--
-- Итог: сумма переезжает в composition.regional_value, ETL больше не пишет
-- per-cell строки (флаг --store-cells возвращает запись, если понадобится
-- воспроизвести артефакт §9.5 ТЗ), таблица очищается.

ALTER TABLE composition ADD COLUMN IF NOT EXISTS regional_value double precision;

-- Заполняем из того, что уже загружено: миграции гоняются на каждом деплое,
-- и на проде колонка должна получить значения ДО того, как таблицу очистят —
-- иначе /api/recompute отдаст 404 «нет композиции». Свежий ETL пишет
-- regional_value сам, поэтому условие IS NULL делает шаг самоотключаемым.
UPDATE composition c SET regional_value = s.total
FROM (SELECT composition_id, sum(value) AS total
      FROM distribution_cell GROUP BY composition_id) s
WHERE s.composition_id = c.id AND c.regional_value IS NULL;

CREATE INDEX IF NOT EXISTS composition_region_ind_ix
    ON composition (region_id, indicator_code, id);
