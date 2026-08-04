-- 0011_tile_composition_offset.sql
-- Смещение нуля в tile_composition: value = (Σ w_m · mask_weight - off) · rv / total
--
-- Зачем. Прежняя формула ЛИНЕЙНА по взвешенной сумме, и этого хватало для
-- объёмных показателей (инвариант Σ по ячейкам = региональный итог: rv/total).
-- Удельные показатели (indicator_type: rate — коэффициент рождаемости, кв.м на
-- жителя, мощность на 10000 чел.) складывать по ячейкам нельзя: ставка не
-- аддитивна. Для них API считает не долю от итога, а нормировку в 0..100
-- (src/masks/normalization.normalize_indicator, min-max) — а это АФФИННОЕ
-- преобразование, линейной формулой не выражается: нужно вычесть минимум.
--
-- off приходит query-параметром, по умолчанию 0 — тогда функция ведёт себя
-- в точности как в 0007, объёмные показатели ничего не замечают.
--
-- СТАТУС: сейчас off никто не передаёт. Отдельные показатели решено выводить в
-- абсолютных величинах (доля регионального итога), нормировка у них снята —
-- см. историю: indicator_type: rate и value_kind убраны. Параметр оставлен под
-- результирующий слой, которому нормированные значения понадобятся; откатывать
-- определение функции ради этого смысла нет — при off=0 поведение прежнее.
--
-- URL (объёмные, как раньше):
--   ?region=18&ind=Y..&rv=..&total=..&w=<json {slug:w}>
-- URL (удельные, нормировка 0..100): rv=100, total=rawmax-rawmin, off=rawmin
--   ?region=18&ind=Y..&rv=100&total=..&off=..&w=<json {slug:w}>
-- Опциональная зона затухания (0007) не изменилась:
--   &peaks=..&lines=..&sigma_km=10&beta=0.3&vmax=..

CREATE OR REPLACE FUNCTION tile_composition(z integer, x integer, y integer, query_params json)
RETURNS bytea AS $$
DECLARE
    reg   integer := (query_params->>'region')::integer;
    ind   text := coalesce(query_params->>'ind', '');
    rv    double precision := (query_params->>'rv')::double precision;
    total double precision := (query_params->>'total')::double precision;
    off   double precision := coalesce((query_params->>'off')::double precision, 0.0);
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
                   (sum(wt.w * mcv.weight) - off) * rv / NULLIF(total, 0) AS raw_value
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
