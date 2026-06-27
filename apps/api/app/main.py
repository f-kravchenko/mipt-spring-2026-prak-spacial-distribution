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
from .schemas import (
    Composition, Indicator, Mask, RecomputeRequest, RecomputeResult, Region, ServiceConfig,
)

app = FastAPI(title="Spatial Masks API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev; на проде сузить до домена фронта
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mask_tile_url(slug: str) -> str:
    return f"{TILES_BASE_URL}/tile_mask/{{z}}/{{x}}/{{y}}?mask={slug}"


def _distribution_tile_url(comp_id: int) -> str:
    return f"{TILES_BASE_URL}/tile_distribution/{{z}}/{{x}}/{{y}}?composition={comp_id}"


def _city_tile_url(region_id: int) -> str:
    return f"{TILES_BASE_URL}/tile_city/{{z}}/{{x}}/{{y}}?region={region_id}"


def _road_tile_url(region_id: int) -> str:
    return f"{TILES_BASE_URL}/tile_road/{{z}}/{{x}}/{{y}}?region={region_id}"


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/config", response_model=ServiceConfig)
def config():
    return ServiceConfig(tiles_base_url=TILES_BASE_URL)


@app.get("/api/regions", response_model=list[Region])
def regions():
    sql = text("""
        SELECT r.id, r.slug, r.name,
               ST_XMin(r.geom) AS minx, ST_YMin(r.geom) AS miny,
               ST_XMax(r.geom) AS maxx, ST_YMax(r.geom) AS maxy,
               (SELECT count(*) FROM grid_cell g WHERE g.region_id = r.id) AS cells,
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
            cell_count=r["cells"],
            cities_tile_url=_city_tile_url(r["id"]) if r["has_city"] else None,
            roads_tile_url=_road_tile_url(r["id"]) if r["has_road"] else None,
        )
        for r in rows
    ]


@app.get("/api/indicators", response_model=list[Indicator])
def indicators():
    sql = text("""
        SELECT code, name, unit, elasticity, r2, indicator_type
        FROM indicator ORDER BY code
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return [Indicator(**r) for r in rows]


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


@app.get("/api/compositions", response_model=list[Composition])
def compositions(
    region_id: int | None = Query(None),
    indicator: str | None = Query(None),
):
    where = []
    params: dict = {}
    if region_id is not None:
        where.append("c.region_id = :region_id")
        params["region_id"] = region_id
    if indicator is not None:
        where.append("c.indicator_code = :indicator")
        params["indicator"] = indicator
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    comp_sql = text(f"""
        SELECT c.id, c.region_id, c.indicator_code, c.year, c.label, c.method,
               c.weights, c.smoothing_alpha, c.sum_preserved
        FROM composition c {clause}
        ORDER BY c.region_id, c.indicator_code, c.id
    """)
    with engine.connect() as conn:
        comps = conn.execute(comp_sql, params).mappings().all()
        if not comps:
            return []
        ids = [c["id"] for c in comps]
        m_rows = conn.execute(
            text("SELECT composition_id, metric, value FROM quality_metric "
                 "WHERE composition_id = ANY(:ids)"),
            {"ids": ids},
        ).mappings().all()
        max_rows = conn.execute(
            text("SELECT composition_id, MAX(value) AS vmax FROM distribution_cell "
                 "WHERE composition_id = ANY(:ids) GROUP BY composition_id"),
            {"ids": ids},
        ).mappings().all()

    metrics_by_comp: dict[int, dict[str, float]] = {}
    for mr in m_rows:
        metrics_by_comp.setdefault(mr["composition_id"], {})[mr["metric"]] = mr["value"]
    vmax_by_comp = {r["composition_id"]: r["vmax"] for r in max_rows}

    return [
        Composition(
            id=c["id"], region_id=c["region_id"], indicator_code=c["indicator_code"],
            year=c["year"], label=c["label"], method=c["method"],
            weights=c["weights"] or {}, smoothing_alpha=c["smoothing_alpha"],
            sum_preserved=c["sum_preserved"],
            metrics=metrics_by_comp.get(c["id"], {}),
            value_max=vmax_by_comp.get(c["id"]),
            tile_url=_distribution_tile_url(c["id"]),
        )
        for c in comps
    ]


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
    )
    SELECT a.n AS n, a.s AS total, a.mx AS rawmax, g.g AS gini, top.t10 AS t10
    FROM agg a CROSS JOIN gini g CROSS JOIN top
""")


@app.post("/api/recompute", response_model=RecomputeResult)
def recompute(req: RecomputeRequest):
    """Живой пересчёт распределения по произвольным весам масок.
    Считает regional_value, нормировку и метрики; тайлы рисует tile_composition."""
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
        tile_url=tile_url, value_max=value_max, regional_value=rv, metrics=metrics
    )
