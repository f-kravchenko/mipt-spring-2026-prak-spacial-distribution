-- 0003_places.sql
-- Справочные слои карты: города (точки) и дорожная сеть (линии).
-- Геометрия в EPSG:4326, как и остальные слои. Тайлы — функции для Martin.

CREATE TABLE IF NOT EXISTS city (
    id         bigserial PRIMARY KEY,
    region_id  integer NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    name       text,
    population double precision,
    place      text,                              -- city | town | village | ...
    geom       geometry(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS city_geom_gix ON city USING gist (geom);
CREATE INDEX IF NOT EXISTS city_region_ix ON city (region_id);

CREATE TABLE IF NOT EXISTS road (
    id         bigserial PRIMARY KEY,
    region_id  integer NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    highway    text,                              -- класс дороги из OSM (motorway, trunk, ...)
    geom       geometry(LineString, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS road_geom_gix ON road USING gist (geom);
CREATE INDEX IF NOT EXISTS road_region_ix ON road (region_id);

-- Тайл городов региона. URL: /tile_city/{z}/{x}/{y}?region=18
CREATE OR REPLACE FUNCTION tile_city(z integer, x integer, y integer, query_params json)
RETURNS bytea AS $$
DECLARE
    reg integer := (query_params->>'region')::integer;
    result bytea;
BEGIN
    SELECT ST_AsMVT(t, 'city', 4096, 'mvtgeom') INTO result
    FROM (
        SELECT c.name, c.population, c.place,
            ST_AsMVTGeom(
                ST_Transform(c.geom, 3857),
                ST_TileEnvelope(z, x, y),
                4096, 64, true
            ) AS mvtgeom
        FROM city c
        WHERE c.region_id = reg
          AND c.geom && ST_Transform(ST_TileEnvelope(z, x, y), 4326)
    ) t;
    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;

-- Тайл дорог региона. URL: /tile_road/{z}/{x}/{y}?region=18
CREATE OR REPLACE FUNCTION tile_road(z integer, x integer, y integer, query_params json)
RETURNS bytea AS $$
DECLARE
    reg integer := (query_params->>'region')::integer;
    result bytea;
BEGIN
    SELECT ST_AsMVT(t, 'road', 4096, 'mvtgeom') INTO result
    FROM (
        SELECT r.highway,
            ST_AsMVTGeom(
                ST_Transform(r.geom, 3857),
                ST_TileEnvelope(z, x, y),
                4096, 64, true
            ) AS mvtgeom
        FROM road r
        WHERE r.region_id = reg
          AND r.geom && ST_Transform(ST_TileEnvelope(z, x, y), 4326)
    ) t;
    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
