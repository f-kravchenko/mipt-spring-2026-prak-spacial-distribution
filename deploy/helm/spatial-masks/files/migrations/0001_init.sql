-- 0001_init.sql
-- Схема системы аналитических масок для пространственной дезагрегации.
-- PostgreSQL 16 + PostGIS 3. Геометрия хранится в EPSG:4326 (под web-тайлы),
-- площади ячеек считаются в равновеликой проекции на этапе ETL и кэшируются.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ──────────────────────────────────────────────────────────────────────────
-- Справочники
-- ──────────────────────────────────────────────────────────────────────────

-- Субъект РФ (пилотный регион)
CREATE TABLE IF NOT EXISTS region (
    id          serial PRIMARY KEY,
    slug        text NOT NULL UNIQUE,          -- moscow | krasnodar | yakutia_center
    name        text NOT NULL,                 -- человекочитаемое имя
    geom        geometry(MultiPolygon, 4326) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS region_geom_gix ON region USING gist (geom);

-- Статистический показатель (код Росстата)
CREATE TABLE IF NOT EXISTS indicator (
    code         text PRIMARY KEY,             -- Y477090007 и т.п.
    name         text NOT NULL,
    unit         text,
    elasticity   double precision,             -- эластичность населения (из регрессии)
    r2           double precision,
    indicator_type text                        -- industrial | investment | demographic | ...
);

-- Официальное региональное значение показателя (инвариант суммы привязан сюда)
CREATE TABLE IF NOT EXISTS regional_value (
    region_id       integer NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    indicator_code  text    NOT NULL REFERENCES indicator(code) ON DELETE CASCADE,
    year            integer NOT NULL,
    official_value  double precision NOT NULL,
    PRIMARY KEY (region_id, indicator_code, year)
);

-- ──────────────────────────────────────────────────────────────────────────
-- Сетка 1×1 км
-- ──────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS grid_cell (
    id          bigserial PRIMARY KEY,
    region_id   integer NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    cell_code   text NOT NULL,                 -- стабильный ключ ячейки (например "x_y")
    geom        geometry(MultiPolygon, 4326) NOT NULL,
    area_km2    double precision NOT NULL,     -- посчитано в равновеликой проекции
    population  double precision,              -- статичная фича (WorldPop)
    features    jsonb,                          -- прочие статичные фичи ячейки
    UNIQUE (region_id, cell_code)
);
CREATE INDEX IF NOT EXISTS grid_cell_geom_gix ON grid_cell USING gist (geom);
CREATE INDEX IF NOT EXISTS grid_cell_region_ix ON grid_cell (region_id);

-- ──────────────────────────────────────────────────────────────────────────
-- Реестр масок — контракт аналитической маски из §6 ТЗ
-- ──────────────────────────────────────────────────────────────────────────

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'influence_kind') THEN
        CREATE TYPE influence_kind AS ENUM ('boost', 'damp', 'exclude', 'smooth');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS mask (
    id            serial PRIMARY KEY,
    slug          text NOT NULL UNIQUE,        -- night_lights_mask, road_network_mask, ...
    title         text NOT NULL,
    source        text,                        -- §6: источник данных
    signal        text,                        -- §6: смысловой сигнал
    influence     influence_kind NOT NULL,     -- §6: тип влияния
    formula       text,                        -- §6: формула расчёта веса
    normalization text,                        -- §6: как нормируется в 0..1
    applicability text[],                       -- §6: для каких показателей полезна
    limitations   text,                        -- §6: где даёт ошибочный сигнал
    is_baseline   boolean NOT NULL DEFAULT false
);

-- Вес маски по ячейке (нормирован 0..1). Источник тоггл-слоёв на фронте.
-- indicator_code: '' для масок, не зависящих от показателя (baseline, worldpop,
-- distance_*); конкретный код для зависящих (regression — разная эластичность).
CREATE TABLE IF NOT EXISTS mask_cell_value (
    mask_id        integer NOT NULL REFERENCES mask(id) ON DELETE CASCADE,
    indicator_code text   NOT NULL DEFAULT '',
    cell_id        bigint NOT NULL REFERENCES grid_cell(id) ON DELETE CASCADE,
    weight         real   NOT NULL,            -- 0..1
    PRIMARY KEY (mask_id, indicator_code, cell_id)
);
CREATE INDEX IF NOT EXISTS mask_cell_value_cell_ix ON mask_cell_value (cell_id);

-- ──────────────────────────────────────────────────────────────────────────
-- Композиции и итоговое распределение
-- ──────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS composition (
    id              serial PRIMARY KEY,
    region_id       integer NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    indicator_code  text    NOT NULL REFERENCES indicator(code) ON DELETE CASCADE,
    year            integer NOT NULL,
    label           text NOT NULL,             -- "all_5_masks", "ablation_no_regression", ...
    method          text NOT NULL,             -- weighted_sum | gating | smoothing
    weights         jsonb NOT NULL,            -- {regression:0.5, worldpop:0.3, ...}
    smoothing_alpha double precision,
    sum_preserved   boolean,                   -- инвариант §9.1
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (region_id, indicator_code, year, label)
);

-- Итоговое значение показателя по ячейке. SUM(value) = official_value.
CREATE TABLE IF NOT EXISTS distribution_cell (
    composition_id integer NOT NULL REFERENCES composition(id) ON DELETE CASCADE,
    cell_id        bigint  NOT NULL REFERENCES grid_cell(id) ON DELETE CASCADE,
    value          double precision NOT NULL,
    PRIMARY KEY (composition_id, cell_id)
);
CREATE INDEX IF NOT EXISTS distribution_cell_cell_ix ON distribution_cell (cell_id);

-- ──────────────────────────────────────────────────────────────────────────
-- Метрики качества (§9: baseline-сравнение, обратная агрегация, ablation)
-- ──────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS quality_metric (
    id             bigserial PRIMARY KEY,
    composition_id integer NOT NULL REFERENCES composition(id) ON DELETE CASCADE,
    metric         text NOT NULL,             -- gini | top10_share | mae | mape | rmse | corr | sum_error
    value          double precision NOT NULL,
    scope          text                        -- region | municipality_holdout | ...
);
CREATE INDEX IF NOT EXISTS quality_metric_comp_ix ON quality_metric (composition_id);
