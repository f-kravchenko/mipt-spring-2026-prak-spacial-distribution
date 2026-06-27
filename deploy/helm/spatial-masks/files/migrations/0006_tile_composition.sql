-- 0006_tile_composition.sql
-- Живой пересчёт распределения: взвешенная сумма масок с произвольными весами,
-- нормированная на региональное значение (инвариант суммы). Веса, regional_value
-- (rv) и нормировочная сумма (total) приходят query-параметрами; total и rv
-- считает API один раз на набор весов (см. /api/recompute), тайл лишь масштабирует.
-- value = (Σ w_m · mask_weight) · rv / total
-- URL: /tile_composition/{z}/{x}/{y}?region=18&ind=Y..&rv=..&total=..&w=<json {slug:w}>

CREATE OR REPLACE FUNCTION tile_composition(z integer, x integer, y integer, query_params json)
RETURNS bytea AS $$
DECLARE
    reg   integer := (query_params->>'region')::integer;
    ind   text := coalesce(query_params->>'ind', '');
    rv    double precision := (query_params->>'rv')::double precision;
    total double precision := (query_params->>'total')::double precision;
    weights json := (query_params->>'w')::json;
    env   geometry := ST_TileEnvelope(z, x, y);
    result bytea;
BEGIN
    SELECT ST_AsMVT(t, 'distribution', 4096, 'mvtgeom') INTO result
    FROM (
        SELECT c.value,
            ST_AsMVTGeom(ST_Transform(c.geom, 3857), env, 4096, 64, true) AS mvtgeom
        FROM (
            SELECT gc.geom,
                   sum(wt.w * mcv.weight) * rv / NULLIF(total, 0) AS value
            FROM grid_cell gc
            JOIN mask_cell_value mcv ON mcv.cell_id = gc.id
            JOIN (
                SELECT m.id AS mask_id, (e.value)::double precision AS w
                FROM json_each_text(weights) e
                JOIN mask m ON m.slug = e.key
            ) wt ON wt.mask_id = mcv.mask_id
            WHERE gc.region_id = reg
              AND mcv.indicator_code IN ('', ind)
              AND gc.geom && ST_Transform(env, 4326)
            GROUP BY gc.id, gc.geom
        ) c
    ) t;
    RETURN result;
END;
$$ LANGUAGE plpgsql STABLE PARALLEL SAFE;
