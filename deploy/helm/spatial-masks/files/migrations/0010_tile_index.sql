-- 0010_tile_index.sql
-- Тайл-слой для показателей-ИНДЕКСОВ (напр. Индекс качества городской среды).
-- Индекс — не сумма-сохраняемая величина, поэтому tile_composition (он
-- распределяет РЕГИОНАЛЬНЫЙ ИТОГ по маскам, инвариант Σ=rv) для него не годится.
-- В grid_cell.features лежат: value (балл, IDW 14 городов), name (ближайший
-- город) и КОМПОНЕНТЫ масок присутствия 0..1: pop (WorldPop), poi, green (OSM),
-- road, rail, power (близость к линиям OSM), city (затухание к городу). Тайл
-- отдаёт их как есть; итоговую яркость (вес) фронт считает выражением
-- fill-opacity из ползунков — как у других регионов, но мгновенно, без
-- пересчёта тайлов. Регион «индексный», если ячейки несут features->'value'.
-- URL: /tile_index/{z}/{x}/{y}?region=<id>

CREATE OR REPLACE FUNCTION tile_index(z integer, x integer, y integer, query_params json)
RETURNS bytea AS $$
DECLARE
    reg integer := (query_params->>'region')::integer;
    env geometry := ST_TileEnvelope(z, x, y);
    result bytea;
BEGIN
    SELECT ST_AsMVT(t, 'index', 4096, 'mvtgeom') INTO result
    FROM (
        SELECT (gc.features->>'value')::double precision AS value,
               gc.features->>'name'                      AS name,
               (gc.features->>'pop')::real   AS pop,
               (gc.features->>'poi')::real   AS poi,
               (gc.features->>'green')::real AS green,
               (gc.features->>'road')::real  AS road,
               (gc.features->>'rail')::real  AS rail,
               (gc.features->>'power')::real AS power,
               (gc.features->>'city')::real  AS city,
               ST_AsMVTGeom(ST_Transform(gc.geom, 3857), env, 4096, 64, true) AS mvtgeom
        FROM grid_cell gc
        WHERE gc.region_id = reg
          AND gc.features ? 'value'
          AND gc.geom && ST_Transform(env, 4326)
    ) t;
    RETURN result;
END;
$$ LANGUAGE plpgsql STABLE PARALLEL SAFE;
