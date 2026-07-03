-- 0007_tile_composition_decay.sql
-- Добавляет к tile_composition (0006) опциональное затухание от пиков/линий
-- концентрации (ТЗ п.6). Пики и MST-линии НЕ пересчитываются здесь — это
-- было бы неприемлемо дорого на каждый z/x/y тайл (MST на весь регион при
-- каждом пане/зуме карты). Вместо этого фронт один раз получает готовые
-- точки-пики и линии от /api/concentration-structure (вместе с /api/recompute,
-- на том же наборе весов) и передаёт их сюда query-параметрами — тайл только
-- считает расстояние до ближайшей точки/линии (дёшево: обычно единицы-
-- десятки пиков) и подмешивает exp(-d/sigma).
--
-- Обратная совместимость: beta по умолчанию 0 (эффект выключен) — старые
-- вызовы tile_composition без новых параметров работают ровно как раньше,
-- POI/линии даже не парсятся (CASE WHEN beta > 0 отсекает их до
-- json_array_elements).
--
-- value = raw_value + [beta > 0] beta * vmax * exp(-d_nearest / sigma)
-- где raw_value — прежняя формула (взвешенная сумма * rv / total),
-- d_nearest — расстояние (метры, geography) до ближайшего пика ИЛИ до
-- ближайшей точки на отрезке линии (LEAST по обоим), vmax — максимальное
-- значение ячейки в регионе (§9, тот же value_max, что уже возвращает
-- /api/recompute — чтобы вклад декея был в тех же абсолютных единицах,
-- что и сам показатель, а не произвольным числом 0..1).
--
-- sigma_km/beta — НЕ константы: см. обоснование дефолта sigma_km=10 в
-- composition.decay_from_structure (docstring) — открыты параметрами
-- специально для эмпирического подбора и визуальной проверки (ТЗ п.6).
--
-- URL: /tile_composition/{z}/{x}/{y}
--   ?region=18&ind=Y..&rv=..&total=..&w=<json {slug:w}>
--   &peaks=<json [{"lon":..,"lat":..}, ...]>      -- опционально
--   &lines=<json [[[lon,lat],[lon,lat]], ...]>     -- опционально
--   &sigma_km=10&beta=0.3&vmax=<value_max из /api/recompute>

CREATE OR REPLACE FUNCTION tile_composition(z integer, x integer, y integer, query_params json)
RETURNS bytea AS $$
DECLARE
    reg   integer := (query_params->>'region')::integer;
    ind   text := coalesce(query_params->>'ind', '');
    rv    double precision := (query_params->>'rv')::double precision;
    total double precision := (query_params->>'total')::double precision;
    weights json := (query_params->>'w')::json;
    peaks_json json := coalesce((query_params->>'peaks')::json, '[]'::json);
    lines_json json := coalesce((query_params->>'lines')::json, '[]'::json);
    sigma_m double precision := coalesce((query_params->>'sigma_km')::double precision, 10.0) * 1000.0;
    beta    double precision := coalesce((query_params->>'beta')::double precision, 0.0);
    vmax    double precision := coalesce((query_params->>'vmax')::double precision, 0.0);
    env   geometry := ST_TileEnvelope(z, x, y);
    result bytea;
BEGIN
    SELECT ST_AsMVT(t, 'distribution', 4096, 'mvtgeom') INTO result
    FROM (
        SELECT
            base.raw_value
            + CASE WHEN beta > 0 AND sigma_m > 0
                   THEN beta * vmax * exp(-decay.min_dist_m / sigma_m)
                   ELSE 0
              END AS value,
            ST_AsMVTGeom(ST_Transform(base.geom, 3857), env, 4096, 64, true) AS mvtgeom
        FROM (
            SELECT gc.id, gc.geom,
                   sum(wt.w * mcv.weight) * rv / NULLIF(total, 0) AS raw_value
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
        ) base
        CROSS JOIN LATERAL (
            -- min_dist_m считается ТОЛЬКО если beta > 0 — иначе не парсим
            -- peaks_json/lines_json вообще (дефолтный beta=0 не платит за это)
            SELECT CASE WHEN beta > 0 THEN LEAST(
                COALESCE((
                    SELECT min(ST_Distance(
                        base.geom::geography,
                        ST_SetSRID(ST_MakePoint(
                            (p->>'lon')::double precision, (p->>'lat')::double precision
                        ), 4326)::geography
                    ))
                    FROM json_array_elements(peaks_json) p
                ), 1e12),
                COALESCE((
                    SELECT min(ST_Distance(
                        base.geom::geography,
                        ST_SetSRID(ST_MakeLine(
                            ST_MakePoint((l->0->>0)::double precision, (l->0->>1)::double precision),
                            ST_MakePoint((l->1->>0)::double precision, (l->1->>1)::double precision)
                        ), 4326)::geography
                    ))
                    FROM json_array_elements(lines_json) l
                ), 1e12)
            ) ELSE 1e12 END AS min_dist_m
        ) decay
    ) t;
    RETURN result;
END;
$$ LANGUAGE plpgsql STABLE PARALLEL SAFE;
