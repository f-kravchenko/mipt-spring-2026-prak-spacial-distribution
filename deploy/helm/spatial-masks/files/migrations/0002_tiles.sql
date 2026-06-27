-- 0002_tiles.sql
-- Функции-источники векторных тайлов (MVT) для Martin.
-- Тоггл слоёв на фронте = смена query-параметра источника, пересчёта нет.

-- Тайл одной маски: вес ячейки 0..1 как свойство для раскраски.
-- indicator опционален (нужен для зависящих масок, напр. regression).
-- URL: /tile_mask/{z}/{x}/{y}?mask=night_lights_mask&indicator=Y477090007
CREATE OR REPLACE FUNCTION tile_mask(z integer, x integer, y integer, query_params json)
RETURNS bytea AS $$
DECLARE
    mask_slug text := query_params->>'mask';
    ind       text := coalesce(query_params->>'indicator', '');
    result bytea;
BEGIN
    SELECT ST_AsMVT(t, 'mask', 4096, 'mvtgeom') INTO result
    FROM (
        SELECT
            mcv.weight,
            gc.population,
            ST_AsMVTGeom(
                ST_Transform(gc.geom, 3857),
                ST_TileEnvelope(z, x, y),
                4096, 64, true
            ) AS mvtgeom
        FROM grid_cell gc
        JOIN mask_cell_value mcv ON mcv.cell_id = gc.id
        JOIN mask m ON m.id = mcv.mask_id
        WHERE m.slug = mask_slug
          AND mcv.indicator_code IN ('', ind)
          AND gc.geom && ST_Transform(ST_TileEnvelope(z, x, y), 4326)
    ) t;
    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;

-- Тайл итогового распределения для конкретной композиции.
-- URL: /tile_distribution/{z}/{x}/{y}?composition=12
CREATE OR REPLACE FUNCTION tile_distribution(z integer, x integer, y integer, query_params json)
RETURNS bytea AS $$
DECLARE
    comp_id integer := (query_params->>'composition')::integer;
    result bytea;
BEGIN
    SELECT ST_AsMVT(t, 'distribution', 4096, 'mvtgeom') INTO result
    FROM (
        SELECT
            dc.value,
            ST_AsMVTGeom(
                ST_Transform(gc.geom, 3857),
                ST_TileEnvelope(z, x, y),
                4096, 64, true
            ) AS mvtgeom
        FROM grid_cell gc
        JOIN distribution_cell dc ON dc.cell_id = gc.id
        WHERE dc.composition_id = comp_id
          AND gc.geom && ST_Transform(ST_TileEnvelope(z, x, y), 4326)
    ) t;
    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
