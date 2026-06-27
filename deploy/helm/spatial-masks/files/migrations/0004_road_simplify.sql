-- 0004_road_simplify.sql
-- Дороги: упрощаем геометрию пропорционально зуму (на обзоре не нужны все изгибы).
-- Линии — упрощение незаметно, но заметно меньше вершин в тайле. Ячейки сетки
-- (распределение/маски) НЕ агрегируем: на обзорном зуме это давало блочный,
-- размытый хороплет — оставляем честные 1-км ячейки (как в 0002).

CREATE OR REPLACE FUNCTION tile_road(z integer, x integer, y integer, query_params json)
RETURNS bytea AS $$
DECLARE
    reg integer := (query_params->>'region')::integer;
    env  geometry := ST_TileEnvelope(z, x, y);
    env4326 geometry := ST_Transform(env, 4326);
    tol double precision := 40075016.6855785 / power(2, z) / 512.0;  -- ~8 px в метрах
    result bytea;
BEGIN
    SELECT ST_AsMVT(t, 'road', 4096, 'mvtgeom') INTO result
    FROM (
        SELECT r.highway,
            ST_AsMVTGeom(
                ST_SimplifyPreserveTopology(ST_Transform(r.geom, 3857), tol),
                env, 4096, 64, true
            ) AS mvtgeom
        FROM road r
        WHERE r.region_id = reg
          AND r.geom && env4326
    ) t;
    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
