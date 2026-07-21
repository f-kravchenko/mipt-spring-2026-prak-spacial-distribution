-- 0010_tile_index.sql
-- Тайл-слой для показателей-ИНДЕКСОВ (напр. Индекс качества городской среды).
-- Индекс — не сумма-сохраняемая величина, поэтому tile_composition (он
-- распределяет РЕГИОНАЛЬНЫЙ ИТОГ по маскам, инвариант Σ=rv) для него не годится.
-- Здесь значение и «вес присутствия» уже посчитаны при загрузке сетки и лежат
-- в grid_cell.features ({"value":балл, "weight":плотность 0..1, "name":город}) —
-- тайл лишь отдаёт их. Регион считается «индексным», если его ячейки несут
-- ключ features->'value' (см. /api/regions).
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
        SELECT (gc.features->>'value')::double precision  AS value,
               (gc.features->>'weight')::double precision AS weight,
               gc.features->>'name'                       AS name,
               ST_AsMVTGeom(ST_Transform(gc.geom, 3857), env, 4096, 64, true) AS mvtgeom
        FROM grid_cell gc
        WHERE gc.region_id = reg
          AND gc.features ? 'value'
          AND gc.geom && ST_Transform(env, 4326)
    ) t;
    RETURN result;
END;
$$ LANGUAGE plpgsql STABLE PARALLEL SAFE;
