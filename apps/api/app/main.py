"""
API системы аналитических масок.

Читает из PostGIS метаданные, контракты масок, композиции и метрики;
отдаёт фронту шаблоны URL векторных тайлов (Martin).
"""

import json
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .db import TILES_BASE_URL, engine
from .index_config import (
    CITY_SCORES, DEFAULT_WEIGHTS, DOMAIN, INDICATOR_NAME, MASK_ORDER,
)
from .schemas import (
    Indicator, Mask, RecomputeRequest, RecomputeResult, Region,
)

# Автоподбор весов (§9 "обоснование весов") — считается в Python
# (src/masks/weighting.py), а не в SQL, в отличие от самого /api/recompute.
# Нужны сами модули масок, чтобы перевести внутренние ключи weighting.py
# (regression, worldpop, ..., territory, power) в mask.slug из таблицы mask —
# слаг для territory и power не следует общему паттерну "{ключ}_mask"
# (territory_type_mask, power_lines_mask), поэтому мэппинг явный, а не f-строка.
from src.masks import (
    baseline, worldpop, regression, distance_to_city, distance_to_center,
    territory, road_network, railway, road_traveltime, power, weighting,
)

_MASK_SLUG = {
    "baseline": baseline.MASK_DESCRIPTION["name"],
    "worldpop": worldpop.MASK_DESCRIPTION["name"],
    "regression": regression.MASK_DESCRIPTION["name"],
    "distance_to_city": distance_to_city.MASK_DESCRIPTION["name"],
    "distance_to_center": distance_to_center.MASK_DESCRIPTION["name"],
    "territory": territory.MASK_DESCRIPTION["name"],
    "road_network": road_network.MASK_DESCRIPTION["name"],
    "railway": railway.MASK_DESCRIPTION["name"],
    "road_traveltime": road_traveltime.MASK_DESCRIPTION["name"],
    "power": power.MASK_DESCRIPTION["name"],
}

app = FastAPI(title="Spatial Masks API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev; на проде сузить до домена фронта
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mask_tile_url(slug: str) -> str:
    return f"{TILES_BASE_URL}/tile_mask/{{z}}/{{x}}/{{y}}?mask={slug}"


def _city_tile_url(region_id: int) -> str:
    return f"{TILES_BASE_URL}/tile_city/{{z}}/{{x}}/{{y}}?region={region_id}"


def _road_tile_url(region_id: int) -> str:
    return f"{TILES_BASE_URL}/tile_road/{{z}}/{{x}}/{{y}}?region={region_id}"


def _index_tile_url(region_id: int) -> str:
    return f"{TILES_BASE_URL}/tile_index/{{z}}/{{x}}/{{y}}?region={region_id}"


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/regions", response_model=list[Region])
def regions():
    sql = text("""
        SELECT r.id, r.slug, r.name,
               ST_XMin(r.geom) AS minx, ST_YMin(r.geom) AS miny,
               ST_XMax(r.geom) AS maxx, ST_YMax(r.geom) AS maxy,
               (SELECT count(*) FROM grid_cell g WHERE g.region_id = r.id) AS all_cells,
               -- индексные ячейки несут features->'value' (0010); у индекс-региона
               -- в счётчике показываем именно их (Росстат-ячейки могут лежать рядом)
               (SELECT count(*) FROM grid_cell g
                WHERE g.region_id = r.id AND g.features ? 'value') AS index_cells,
               (SELECT max((g.features->>'value')::float) FROM grid_cell g
                WHERE g.region_id = r.id AND g.features ? 'value') AS index_max,
               -- имена городов региона (подписи ИКГС-ячеек) — по ним ниже берём
               -- минимальный балл из реестра CITY_SCORES: сам value содержит ещё
               -- и затухание (дробные значения вплоть до ~0), он для низа шкалы не годится
               (SELECT array_agg(DISTINCT g.features->>'name') FROM grid_cell g
                WHERE g.region_id = r.id AND g.features ? 'value') AS index_names,
               EXISTS(SELECT 1 FROM city  c WHERE c.region_id = r.id) AS has_city,
               EXISTS(SELECT 1 FROM road  d WHERE d.region_id = r.id) AS has_road
        FROM region r ORDER BY r.id
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return [
        Region(
            id=r["id"], slug=r["slug"], name=r["name"],
            bbox=[r["minx"], r["miny"], r["maxx"], r["maxy"]],
            cell_count=r["index_cells"] if r["index_cells"] else r["all_cells"],
            cities_tile_url=_city_tile_url(r["id"]) if r["has_city"] else None,
            roads_tile_url=_road_tile_url(r["id"]) if r["has_road"] else None,
            index_tile_url=_index_tile_url(r["id"]) if r["index_cells"] else None,
            index_max=r["index_max"],
            # низ шкалы = наименьший балл города региона (по реестру CITY_SCORES)
            index_min=min((CITY_SCORES[n] for n in (r["index_names"] or [])
                           if n in CITY_SCORES), default=None),
        )
        for r in rows
    ]


@app.get("/api/indicators", response_model=list[Indicator])
def indicators():
    sql = text("""
        SELECT code, name, unit, elasticity, r2, indicator_type, national_total
        FROM indicator ORDER BY code
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return [Indicator(**r) for r in rows]


@app.get("/api/index-config")
def get_index_config():
    """Справочник индекс-региона (ИКГС): маски присутствия (= словарь отгруженных
    товаров, контракты из таблицы mask), баллы городов, домен, имя показателя."""
    rows = {r["slug"]: r for r in engine.connect().execute(text(
        "SELECT slug, title, source, signal, formula, normalization, influence::text AS influence "
        "FROM mask WHERE slug = ANY(:slugs)"
    ), {"slugs": MASK_ORDER}).mappings().all()}
    masks = []
    for slug in MASK_ORDER:
        r = rows.get(slug)
        if not r:
            continue
        masks.append({"key": slug, "title": r["title"], "source": r["source"],
                      "signal": r["signal"], "formula": r["formula"],
                      "influence": r["influence"], "default_weight": DEFAULT_WEIGHTS.get(slug, 0.0)})
    return {"indicator_name": INDICATOR_NAME, "domain": DOMAIN,
            "city_scores": CITY_SCORES, "masks": masks}


@app.get("/api/masks", response_model=list[Mask])
def masks():
    sql = text("""
        SELECT m.slug, m.title, m.source, m.signal, m.influence::text AS influence,
               m.formula, m.normalization, m.applicability, m.limitations, m.is_baseline,
               COALESCE(d.dep, false) AS indicator_dependent
        FROM mask m
        LEFT JOIN (
            SELECT mask_id, bool_or(indicator_code <> '') AS dep
            FROM mask_cell_value GROUP BY mask_id
        ) d ON d.mask_id = m.id
        ORDER BY m.is_baseline DESC, m.slug
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return [
        Mask(**{**r, "tile_url": _mask_tile_url(r["slug"])})
        for r in rows
    ]


@app.get("/api/mask-peaks")
def mask_peaks(
    region_id: int = Query(...),
    mask: str = Query(...),
    indicator: str | None = Query(None),
    frac: float = Query(0.05, ge=0.0, le=0.5),
):
    """
    Порог "пика" для ОТДЕЛЬНОГО слоя (ТЗ п.2 — тепловая карта с пиками
    топ 5-10% для каждого слоя, не только для суммарного, см. п.4 в
    /api/recompute -> peak_threshold). Тот же приём: percentile_cont по
    значениям слоя в регионе, фронт сравнивает ["get","weight"] >= threshold
    MapLibre-выражением — отдельно считать пики на фронте не нужно.

    indicator обязателен только для indicator_dependent масок (сейчас — только
    regression); для остальных не передаётся, mcv.indicator_code = ''.
    """
    sql = text("""
        SELECT CASE
            WHEN stddev(mcv.weight) < 0.01 * NULLIF(avg(mcv.weight), 0)
            THEN NULL
            ELSE percentile_cont(:p) WITHIN GROUP (ORDER BY mcv.weight)
        END AS threshold
        FROM mask_cell_value mcv
        JOIN grid_cell gc ON gc.id = mcv.cell_id
        JOIN mask m ON m.id = mcv.mask_id
        WHERE gc.region_id = :r AND m.slug = :slug
          AND mcv.indicator_code IN ('', COALESCE(:i, ''))
          AND mcv.weight > 0  -- порог по ненулевым, см. _PEAK_POINTS_SQL
    """)
    with engine.connect() as conn:
        row = conn.execute(
            sql, {"p": 1 - frac, "r": region_id, "slug": mask, "i": indicator}
        ).mappings().first()
    if row is None or row["threshold"] is None:
        raise HTTPException(404, "Нет данных маски для региона/показателя")
    return {"peak_threshold": float(row["threshold"])}


@app.get("/api/default-weights", response_model=dict[str, float])
def default_weights(indicator: str = Query(...)):
    """
    Автоподбор весов композиции под показатель (§9 "обоснование весов"):
    вес regression = r2 калибровки, остаток делится между общими и
    промышленными масками — см. src/masks/weighting.resolve_weights.

    r2/indicator_type берутся из БД (та же таблица, что отдаёт /api/indicators) —
    единственный источник истины для рантайма; src/masks/config.yaml используется
    weighting.py только для приоров (proxy_priors/industrial_priors), которым
    негде больше храниться, но не для r2/category — их сюда передаём явно,
    чтобы не тянуть в API ещё один рассинхронизированный источник данных.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT r2, indicator_type FROM indicator WHERE code = :c"),
            {"c": indicator},
        ).mappings().first()
    if row is None:
        raise HTTPException(404, f"Показатель {indicator} не найден")

    indicator_meta = {"r2": row["r2"], "category": row["indicator_type"]}
    weights_internal = weighting.resolve_weights(indicator, indicator_meta=indicator_meta)

    # internal key -> mask.slug; неизвестные ключи молча пропускаем, а не падаем,
    # чтобы новая маска без записи в _MASK_SLUG не роняла весь эндпоинт
    return {_MASK_SLUG[k]: v for k, v in weights_internal.items() if k in _MASK_SLUG}


# Региональное значение показателя = сумма распределения любой его композиции
# (все они сохраняют сумму). regional_value-таблица в этой сборке не заполнена.
_RV_SQL = text("""
    SELECT sum(dc.value) FROM distribution_cell dc
    WHERE dc.composition_id = (
        SELECT id FROM composition
        WHERE region_id = :r AND indicator_code = :i ORDER BY id LIMIT 1
    )
""")

# Взвешенная сумма масок по ячейкам + агрегаты для нормировки и метрик.
# peak.p95 — 95-й перцентиль raw по ненулевым ячейкам региона (top 5%) —
# используется ниже, чтобы посчитать peak_threshold в тех же единицах, что value_max.
_AGG_SQL = text("""
    WITH wt AS (
        SELECT m.id AS mask_id, (e.value)::double precision AS w
        FROM json_each_text(CAST(:w AS json)) e JOIN mask m ON m.slug = e.key
        WHERE (e.value)::double precision <> 0
    ),
    cell AS (
        SELECT mcv.cell_id, sum(wt.w * mcv.weight) AS raw
        FROM mask_cell_value mcv
        JOIN wt ON wt.mask_id = mcv.mask_id
        JOIN grid_cell gc ON gc.id = mcv.cell_id
        WHERE gc.region_id = :r AND mcv.indicator_code IN ('', :i)
        GROUP BY mcv.cell_id
    ),
    agg AS (SELECT count(*) n, sum(raw) s, max(raw) mx FROM cell),
    ordr AS (SELECT raw, row_number() OVER (ORDER BY raw) rn FROM cell),
    gini AS (
        SELECT (2.0 * sum(o.rn * o.raw) / NULLIF(a.n * a.s, 0) - (a.n + 1.0) / a.n) AS g
        FROM ordr o CROSS JOIN agg a GROUP BY a.n, a.s
    ),
    top AS (
        SELECT sum(raw) AS t10 FROM (
            SELECT raw FROM cell ORDER BY raw DESC
            LIMIT GREATEST((SELECT ceil(0.1 * n)::int FROM agg), 1)
        ) z
    ),
    peak AS (
        -- по ненулевым ячейкам — см. комментарий в _PEAK_POINTS_SQL
        SELECT CASE
            WHEN stddev(raw) < 0.01 * NULLIF(avg(raw), 0)
            THEN NULL
            ELSE percentile_cont(0.95) WITHIN GROUP (ORDER BY raw)
        END AS p95
        FROM cell WHERE raw > 0
    )
    SELECT a.n AS n, a.s AS total, a.mx AS rawmax, g.g AS gini, top.t10 AS t10,
           peak.p95 AS p95
    FROM agg a CROSS JOIN gini g CROSS JOIN top CROSS JOIN peak
""")


@app.post("/api/recompute", response_model=RecomputeResult)
def recompute(req: RecomputeRequest):
    """Живой пересчёт распределения по произвольным весам масок.
    Считает regional_value, нормировку, метрики и порог пиков; тайлы рисует
    tile_composition."""
    weights = {k: v for k, v in req.weights.items() if v}
    if not weights:
        raise HTTPException(400, "Задайте хотя бы один ненулевой вес")
    w_json = json.dumps(weights, ensure_ascii=False)

    with engine.connect() as conn:
        rv = conn.execute(_RV_SQL, {"r": req.region_id, "i": req.indicator}).scalar()
        if rv is None:
            raise HTTPException(404, "Нет композиции (regional_value) для региона/показателя")
        row = conn.execute(
            _AGG_SQL, {"w": w_json, "r": req.region_id, "i": req.indicator}
        ).mappings().first()

    if not row or not row["n"] or not row["total"] or row["total"] <= 0:
        raise HTTPException(422, "Пустой результат: нет масок с весами для этого региона")

    rv = float(rv)
    total = float(row["total"])
    value_max = float(row["rawmax"]) * rv / total
    peak_threshold = float(row["p95"]) * rv / total if row["p95"] is not None else None
    metrics = {
        "gini": float(row["gini"]) if row["gini"] is not None else 0.0,
        "top10_share": float(row["t10"]) / total if total else 0.0,
        "sum_error": 0.0,  # инвариант суммы выполнен по построению
    }
    tile_url = (
        f"{TILES_BASE_URL}/tile_composition/{{z}}/{{x}}/{{y}}"
        f"?region={req.region_id}&ind={req.indicator}&rv={rv}&total={total}"
        f"&w={quote(w_json)}"
    )
    return RecomputeResult(
        tile_url=tile_url, value_max=value_max, regional_value=rv, metrics=metrics,
        peak_threshold=peak_threshold,
    )


# Глобальная шкала для режима отображения "Россия": p99 абсолютных значений
# ячеек (raw/total_региона * rv_региона — та же нормализация, что в
# tile_composition) по ВСЕМ загруженным регионам при данных весах. p99, а не
# max — шкала не должна определяться одной аномальной ячейкой. Только
# ненулевые ячейки (см. пороги пиков — то же схлопывание перцентиля в 0).
# rv каждого региона — сумма его хранимого распределения (как _RV_SQL).
_GLOBAL_SCALE_SQL = text("""
    WITH wt AS (
        SELECT m.id AS mask_id, (e.value)::double precision AS w
        FROM json_each_text(CAST(:w AS json)) e JOIN mask m ON m.slug = e.key
        WHERE (e.value)::double precision <> 0
    ),
    rv AS (
        SELECT c.region_id, sum(dc.value) AS rv
        FROM distribution_cell dc
        JOIN composition c ON c.id = dc.composition_id
        WHERE c.id IN (
            SELECT min(id) FROM composition WHERE indicator_code = :i GROUP BY region_id
        )
        GROUP BY c.region_id
    ),
    cell AS (
        SELECT gc.region_id, mcv.cell_id, sum(wt.w * mcv.weight) AS raw
        FROM mask_cell_value mcv
        JOIN wt ON wt.mask_id = mcv.mask_id
        JOIN grid_cell gc ON gc.id = mcv.cell_id
        WHERE mcv.indicator_code IN ('', :i)
        GROUP BY gc.region_id, mcv.cell_id
    ),
    tot AS (SELECT region_id, sum(raw) AS total FROM cell GROUP BY region_id),
    vals AS (
        SELECT cell.raw / NULLIF(tot.total, 0) * rv.rv AS v
        FROM cell JOIN tot USING (region_id) JOIN rv USING (region_id)
    )
    SELECT (SELECT percentile_cont(0.99) WITHIN GROUP (ORDER BY v)
            FROM vals WHERE v > 0) AS p99,
           (SELECT max(v) FROM vals) AS vmax,
           (SELECT count(*) FROM cell) AS n,
           -- "базовая концентрация" по всем загруженным регионам: значение
           -- ячейки при РАВНОМЕРНОМ размазывании (Σ показателей / Σ ячеек,
           -- нули включены) — точка отсчёта дивергентной шкалы (LQ = v/base)
           (SELECT sum(rv) FROM rv) / NULLIF((SELECT count(*) FROM cell), 0) AS base_cell
""")


@app.get("/api/global-scale")
def global_scale(
    indicator: str = Query(...),
    weights: str = Query(..., description="JSON slug->вес, тот же формат, что POST /api/recompute"),
):
    """
    Фиксированная шкала показателя для режима "Россия": одинаковый цвет —
    одинаковое абсолютное значение в одних единицах, независимо от выбранной
    территории. Считается по всем загруженным регионам с данными весами;
    возвращает также national_total (итог по РФ из indicator, миграция 0008)
    для подписи долей.
    """
    try:
        weights_dict = {k: v for k, v in json.loads(weights).items() if v}
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(400, "weights должен быть JSON-объектом slug->вес")
    if not weights_dict:
        raise HTTPException(400, "Задайте хотя бы один ненулевой вес")
    w_json = json.dumps(weights_dict, ensure_ascii=False)

    with engine.connect() as conn:
        row = conn.execute(
            _GLOBAL_SCALE_SQL, {"w": w_json, "i": indicator}
        ).mappings().first()
        national = conn.execute(
            text("SELECT national_total FROM indicator WHERE code = :c"), {"c": indicator}
        ).scalar()

    if not row or row["p99"] is None:
        raise HTTPException(404, "Нет данных для расчёта глобальной шкалы")
    return {
        "p99": float(row["p99"]),
        "value_max": float(row["vmax"]),
        "cells": int(row["n"]),
        "base_cell": float(row["base_cell"]) if row["base_cell"] is not None else None,
        "national_total": float(national) if national is not None else None,
    }


# Точки-пики для линий концентрации (ТЗ п.5): те же взвешенные значения
# ячеек, что _AGG_SQL, но дальше кластеризуем смежные ячейки-пики через
# ST_ClusterDBSCAN(eps=1) — единица измерения после ST_Transform(...,3857)
# это метры, eps=1 метр means "физически соприкасаются" (у полигонов ячеек,
# делящих общую границу, расстояние между геометриями = 0) — это ТОЧНАЯ
# проверка смежности, а не произвольный порог расстояния, поэтому eps здесь
# не вынесен в параметр (в отличие от sigma_km/beta ниже — те действительно
# требуют эмпирического подбора). DISTINCT ON (cid) ... ORDER BY raw DESC
# берёт ячейку с максимальным значением в каждом кластере как точку-пик —
# не центроид кластера (на невыпуклой форме кластера центроид может попасть
# в ячейку с низким значением — например, подковообразный кластер вокруг реки).
_PEAK_POINTS_SQL = text("""
    WITH wt AS (
        SELECT m.id AS mask_id, (e.value)::double precision AS w
        FROM json_each_text(CAST(:w AS json)) e JOIN mask m ON m.slug = e.key
        WHERE (e.value)::double precision <> 0
    ),
    cell AS (
        SELECT mcv.cell_id, sum(wt.w * mcv.weight) AS raw, gc.geom
        FROM mask_cell_value mcv
        JOIN wt ON wt.mask_id = mcv.mask_id
        JOIN grid_cell gc ON gc.id = mcv.cell_id
        WHERE gc.region_id = :r AND mcv.indicator_code IN ('', :i)
        GROUP BY mcv.cell_id, gc.geom
    ),
    thr AS (
        -- Порог только по ненулевым ячейкам: в разреженном регионе перцентиль
        -- по всем ячейкам схлопывается в 0, и "пиком" становится любая
        -- ненулевая ячейка в окружении нулей. threshold > 0 по построению.
        SELECT CASE
            WHEN stddev(raw) < 0.01 * NULLIF(avg(raw), 0)
            THEN NULL
            ELSE percentile_cont(1 - :frac) WITHIN GROUP (ORDER BY raw)
        END AS t
        FROM cell WHERE raw > 0
    ),
    peak_cells AS (
        SELECT c.cell_id, c.raw, c.geom
        FROM cell c CROSS JOIN thr
        WHERE c.raw >= thr.t
    ),
    clustered AS (
        SELECT cell_id, raw, geom,
               ST_ClusterDBSCAN(ST_Transform(geom, 3857), eps := 1, minpoints := 1)
                   OVER () AS cid
        FROM peak_cells
    ),
    -- Правило Парето вместо фиксированного топ-K: сильнейшие кластеры,
    -- суммарно накрывающие :share массы пиковых ячеек (масса кластера —
    -- sum(raw), чтобы крупный центр из многих сильных ячеек выигрывал у
    -- одиночной яркой). Число пиков адаптируется к структуре концентрации
    -- (моноцентричная Якутия — 3, полицентричное Подмосковье — ~60 на
    -- worldpop) и не зависит от масштаба карты: при переходе регион->страна
    -- отбор везде идёт по одной доле массы — общий "уровень моря" без
    -- перекоса высот. Кластер, пересекающий границу share, включается.
    mass AS (
        SELECT cid, sum(raw) AS m FROM clustered GROUP BY cid
    ),
    strongest AS (
        SELECT cid FROM (
            SELECT cid,
                   sum(m) OVER (ORDER BY m DESC, cid) - m AS mass_before,
                   sum(m) OVER () AS total
            FROM mass
        ) z
        WHERE mass_before < :share * total
    )
    SELECT DISTINCT ON (c.cid)
           ST_X(ST_Centroid(c.geom)) AS lon, ST_Y(ST_Centroid(c.geom)) AS lat, c.raw
    FROM clustered c JOIN strongest s USING (cid)
    ORDER BY c.cid, c.raw DESC
""")


@app.get("/api/concentration-structure")
def concentration_structure(
    region_id: int = Query(...),
    indicator: str = Query(...),
    weights: str = Query(..., description="JSON slug->вес, тот же формат, что POST /api/recompute"),
    peak_frac: float = Query(0.10, ge=0.01, le=0.5, description="ТЗ п.4: топ 10% по умолчанию"),
    peak_mass_share: float = Query(0.90, ge=0.5, le=0.99, description="Парето: оставить кластеры, накрывающие эту долю массы пиков"),
    decay_sigma_km: float = Query(10.0, gt=0, description="см. composition.decay_from_structure"),
):
    """
    Линии концентрации между пиками (ТЗ п.5) — GeoJSON FeatureCollection:
    Point-фичи для точек-пиков, LineString-фичи для рёбер MST между ними.

    MST считается в Python (scipy), не в SQL — для типичного числа пиков
    (единицы-десятки после кластеризации) это тривиально быстро, а
    реализация MST на чистом SQL сложнее и не даёт выгоды на таком объёме.

    decay_sigma_km возвращается в ответе как есть (не применяется здесь к
    per-cell values) — сам per-cell расчёт затухания делает SQL-функция
    tile_composition (миграция 0007): фронт передаёт эти пики/линии и sigma/beta
    query-параметрами тайла.
    """
    try:
        weights_dict = {k: v for k, v in json.loads(weights).items() if v}
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(400, "weights должен быть JSON-объектом slug->вес")
    if not weights_dict:
        raise HTTPException(400, "Задайте хотя бы один ненулевой вес")
    w_json = json.dumps(weights_dict, ensure_ascii=False)

    with engine.connect() as conn:
        rows = conn.execute(
            _PEAK_POINTS_SQL,
            {"w": w_json, "r": region_id, "i": indicator, "frac": peak_frac,
             "share": peak_mass_share}
        ).mappings().all()

    if not rows:
        raise HTTPException(404, "Нет данных для построения пиков")

    points = [(float(r["lon"]), float(r["lat"]), float(r["raw"])) for r in rows]
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"value": raw, "kind": "peak"},
        }
        for lon, lat, raw in points
    ]

    if len(points) > 1:
        import numpy as np
        from scipy.sparse.csgraph import minimum_spanning_tree
        from scipy.spatial.distance import cdist

        # плоское приближение градусов в метры для построения MST (не для
        # итоговых метрических расстояний) — точнее делать через ST_Transform
        # в SQL, но для топологии дерева на масштабе одного региона (десятки-
        # сотни км) этой точности достаточно, и не нужен второй SQL-запрос
        lat0 = sum(p[1] for p in points) / len(points)
        coords_m = np.array([
            ((lon - points[0][0]) * 111320 * np.cos(np.radians(lat0)),
             (lat - points[0][1]) * 110540)
            for lon, lat, _ in points
        ])
        mst = minimum_spanning_tree(cdist(coords_m, coords_m)).toarray()
        for i in range(len(points)):
            for j in range(len(points)):
                if mst[i, j] > 0:
                    features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [points[i][0], points[i][1]],
                                [points[j][0], points[j][1]],
                            ],
                        },
                        "properties": {"kind": "concentration_line"},
                    })

    return {"type": "FeatureCollection", "features": features, "decay_sigma_km": decay_sigma_km}
