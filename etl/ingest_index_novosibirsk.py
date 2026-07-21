"""
Загрузка «Индекса качества городской среды» (Новосибирская область) как
ОБЫЧНОГО региона в PostGIS: сетка 1 км в grid_cell + значение/вес в
features, отдаётся векторными тайлами tile_index (миграция 0010) — как
у других регионов, а не статическим geojson.

Индекс — не сумма, поэтому в отличие от Росстат-показателей значение ячейки
НЕ распределяется из регионального итога. Спатиализация:
  value  — балл реестра держится по всей ФАКТИЧЕСКОЙ площади НП (контуры OSM,
           fetch_footprints_novosibirsk.py); вне контура затухает вдвое каждые
           5 км, при перекрытии контуров/затуханий берём max (доминирующий НП);
  weight — «присутствие» = населениеWorldPop + затухание к городу + пол
           (baseline), нормировано 0..1 (яркость; сплошное покрытие без дыр);
  name   — ближайший город (для подписи при наведении).

Источники: apps/web/public/russia.geojson (граница), data/processed/pop_nso_z.tif
(WorldPop 1 км, готовит etl/clip_worldpop_novosibirsk.py).

Запуск:  DATABASE_URL=postgresql://masks:masks@localhost:5575/masks \
             python -m etl.ingest_index_novosibirsk
"""

import argparse
import csv
import io
import json
import math
import os

import numpy as np
import psycopg2
import rasterio
from rasterio.features import rasterize, shapes
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUSSIA = os.path.join(ROOT, "apps/web/public/russia.geojson")
# Светлая подложка области под сеткой на фронте (см. App.jsx nsk-region).
OUT_BORDER = os.path.join(ROOT, "apps/web/public/novosibirsk_border.geojson")
# Контуры НП (площадь под баллом реестра) — слой «Границы НП» на фронте.
OUT_CITIES = os.path.join(ROOT, "apps/web/public/novosibirsk_cities.geojson")
POP_TIF = os.path.join(ROOT, "data/processed/pop_nso_z.tif")
OSM_JSON = os.path.join(ROOT, "data/processed/osm_nso.json")  # etl/fetch_osm_novosibirsk.py
FOOT_JSON = os.path.join(ROOT, "data/processed/osm_nso_footprint.json")  # etl/fetch_footprints_novosibirsk.py
INFRA_JSON = os.path.join(ROOT, "data/processed/osm_nso_infra.json")  # etl/fetch_infra_novosibirsk.py
MIGRATION = os.path.join(ROOT, "deploy/helm/spatial-masks/files/migrations/0010_tile_index.sql")
SLUG = "novosibirsk_ikgs"
NAME = "Новосибирская область"
KM = 111.32

CITIES = [
    ("Новосибирск", 82.9346, 55.0084, 223),
    ("Бердск",      83.1018, 54.7583, 210),
    ("Обь",         82.6931, 54.9963, 216),
    ("Искитим",     83.3075, 54.6350, 179),
    ("Куйбышев",    78.3269, 55.4497, 200),
    ("Барабинск",   78.3439, 55.3506, 178),
    ("Карасук",     78.0403, 53.7317, 205),
    ("Татарск",     75.9836, 55.2144, 195),
    ("Купино",      76.9500, 54.3661, 180),
    ("Черепаново",  83.3733, 54.2206, 180),
    ("Болотное",    84.3894, 55.6717, 178),
    ("Каргат",      80.2831, 55.1936, 178),
    ("Чулым",       80.9600, 55.0900, 173),
    ("Тогучин",     84.4014, 55.2317, 171),
]
SIGMA_KM = 15.0  # затухание «присутствия» от города
HALF_KM = 5.0    # период полураспада балла ИКГС вне контура НП (вдвое / 5 км)
CLOSE_CELLS = 3  # смыкание фрагментов застройки в сплошной контур НП (~3 км)


def region_polygon():
    for f in json.load(open(RUSSIA, encoding="utf-8"))["features"]:
        if f["properties"].get("name") == NAME:
            return shape(f["geometry"])
    raise SystemExit(f"{NAME} не найдена в {RUSSIA}")


def cell_population(minx, maxy, dlon, dlat, nrow, ncol):
    """Сумма населения WorldPop в каждую ячейку сетки (zonal sum, numpy-биннинг)."""
    with rasterio.open(POP_TIF) as src:
        a = src.read(1).astype("float64")
        nd, t = src.nodata, src.transform
    a = np.where(~np.isfinite(a) | (a < 0) | ((nd is not None) & (a == nd)), 0.0, a)
    rows, cols = np.nonzero(a > 0)
    lon = t.c + (cols + 0.5) * t.a
    lat = t.f + (rows + 0.5) * t.e
    cc = np.floor((lon - minx) / dlon).astype(int)
    rr = np.floor((maxy - lat) / dlat).astype(int)
    ok = (cc >= 0) & (cc < ncol) & (rr >= 0) & (rr < nrow)
    pop = np.zeros((nrow, ncol))
    np.add.at(pop, (rr[ok], cc[ok]), a[rows, cols][ok])
    return pop


def bin_points(pts, minx, maxy, dlon, dlat, nrow, ncol):
    """Счётчик точек (lon,lat) по ячейкам сетки — плотность OSM-объектов."""
    grid = np.zeros((nrow, ncol))
    if not pts:
        return grid
    a = np.asarray(pts, float)
    cc = np.floor((a[:, 0] - minx) / dlon).astype(int)
    rr = np.floor((maxy - a[:, 1]) / dlat).astype(int)
    ok = (cc >= 0) & (cc < ncol) & (rr >= 0) & (rr < nrow)
    np.add.at(grid, (rr[ok], cc[ok]), 1.0)
    return grid


def norm95(v):
    """Нормировка 0..1 через sqrt(v/p95) — устойчива к выбросам плотности."""
    pos = v[v > 0]
    ref = np.percentile(pos, 95) if pos.size else 1.0
    return np.clip(np.sqrt(v / ref), 0, 1) if ref > 0 else np.zeros_like(v)


def line_decay(pts, clon, clat, mlat, sigma_km):
    """Близость к линейной инфраструктуре: exp(-d_нач/σ) до ближайшей точки
    сети (та же форма, что в src/masks/road_network|railway|power). cKDTree в
    локальных км, поэтому расстояние сразу в километрах."""
    if not pts:
        return np.zeros(len(clon))
    from scipy.spatial import cKDTree
    kx = KM * math.cos(math.radians(mlat))
    a = np.asarray(pts, float)
    tree = cKDTree(np.column_stack([a[:, 0] * kx, a[:, 1] * KM]))
    d, _ = tree.query(np.column_stack([clon * kx, clat * KM]), k=1)
    return np.exp(-d / sigma_km)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--step-km", type=float, default=1.0)
    url = ap.parse_args().database_url
    if not url:
        raise SystemExit("Задайте DATABASE_URL")
    # psycopg2 не понимает префикс SQLAlchemy-диалекта
    url = url.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    step = ap.parse_args().step_km

    border = region_polygon()
    json.dump({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": NAME},
         "geometry": border.__geo_interface__}]},
              open(OUT_BORDER, "w", encoding="utf-8"), ensure_ascii=False)
    minx, miny, maxx, maxy = border.bounds
    mlat = (miny + maxy) / 2
    dlat = step / KM
    dlon = step / (KM * math.cos(math.radians(mlat)))
    ncol = int(math.ceil((maxx - minx) / dlon))
    nrow = int(math.ceil((maxy - miny) / dlat))

    # сетка сверху-вниз (row 0 = maxy) — как аффинное преобразование растра
    from affine import Affine
    transform = Affine(dlon, 0, minx, 0, -dlat, maxy)
    inmask = rasterize([(border, 1)], out_shape=(nrow, ncol), transform=transform,
                       fill=0, all_touched=True).astype(bool)
    pop = cell_population(minx, maxy, dlon, dlat, nrow, ncol)

    rr, cc = np.nonzero(inmask)
    clon = minx + (cc + 0.5) * dlon
    clat = maxy - (rr + 0.5) * dlat
    coslat = np.cos(np.radians(clat))
    cpop = pop[rr, cc]

    # ближайший город (подпись) + затухание «присутствия» к ближайшему
    cx = np.array([c[1] for c in CITIES]); cy = np.array([c[2] for c in CITIES])
    cs = np.array([c[3] for c in CITIES], float)
    dmin = np.full(len(rr), np.inf); nearest = np.zeros(len(rr), int)
    for i in range(len(CITIES)):
        dx = (clon - cx[i]) * KM * coslat
        dy = (clat - cy[i]) * KM
        d2 = dx * dx + dy * dy
        closer = d2 < dmin
        dmin = np.where(closer, d2, dmin)
        nearest = np.where(closer, i, nearest)

    # value: балл реестра по всей фактической площади НП (контуры OSM), вне
    # контура — затухание вдвое каждые HALF_KM, при перекрытии max (доминирующий
    # НП). На сетке: маска-контур на город → EDT-дистанция до неё → score·0.5^(d/H).
    from scipy.ndimage import binary_closing, binary_fill_holes, distance_transform_edt
    coslat0 = math.cos(math.radians(mlat))
    masks = [np.zeros((nrow, ncol), bool) for _ in CITIES]
    polys = json.load(open(FOOT_JSON, encoding="utf-8"))["polys"] if os.path.exists(FOOT_JSON) else []
    for ring in polys:
        poly = Polygon(ring)
        if not poly.is_valid or poly.is_empty:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        ctr = poly.centroid  # владелец кольца — ближайший город к центроиду
        j = int(np.argmin(((cx - ctr.x) * coslat0) ** 2 + (cy - ctr.y) ** 2))
        masks[j] |= rasterize([(poly, 1)], out_shape=(nrow, ncol), transform=transform,
                              fill=0, all_touched=True).astype(bool)
    # каждый город присутствует гарантированно: диск ~2 км в его точке (на случай
    # дыр в OSM — Купино/Обь), иначе его балл вовсе не попал бы на карту.
    for j in range(len(CITIES)):
        rj = int((maxy - cy[j]) / dlat); cj = int((cx[j] - minx) / dlon)
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                r_, c_ = rj + dr, cj + dc
                if 0 <= r_ < nrow and 0 <= c_ < ncol and dr * dr + dc * dc <= 4:
                    masks[j][r_, c_] = True
    # OSM residential — фрагментарная ткань (кварталы через улицы/реки/промзоны):
    # на сетке 1 км это «зернистая» маска, и промежутки между кусками падают в
    # затухание. Смыкаем в сплошную огибающую (closing ~3 км) и заливаем дыры —
    # город становится единым пятном под баллом, а не крапом.
    for j in range(len(CITIES)):
        masks[j] = binary_fill_holes(binary_closing(masks[j], iterations=CLOSE_CELLS))
    value_grid = np.zeros((nrow, ncol))
    for j in range(len(CITIES)):
        if masks[j].any():
            d_km = distance_transform_edt(~masks[j], sampling=(step, step))
            value_grid = np.maximum(value_grid, cs[j] * 0.5 ** (d_km / HALF_KM))
    value = value_grid[rr, cc]
    print(f"контуров OSM {len(polys)}, ячеек в контурах {sum(int(m.sum()) for m in masks)}")

    # Контуры НП (границы площади под баллом) → geojson для слоя «Границы НП».
    # Векторизуем ту же маску, что держит балл — контур совпадает с окраской.
    feats = []
    for j, c in enumerate(CITIES):
        if not masks[j].any():
            continue
        geoms = [shape(g) for g, _ in shapes(masks[j].astype("uint8"),
                 mask=masks[j], transform=transform)]
        feats.append({"type": "Feature",
                      "properties": {"name": c[0], "value": c[3]},
                      "geometry": unary_union(geoms).__geo_interface__})
    json.dump({"type": "FeatureCollection", "features": feats},
              open(OUT_CITIES, "w", encoding="utf-8"), ensure_ascii=False)

    popnorm = norm95(cpop)
    citydecay = np.exp(-dmin / (2 * SIGMA_KM ** 2))

    # OSM-маски (если есть кэш etl/fetch_osm_novosibirsk.py): плотность POI
    # (инфраструктура) и городской зелени — прокси критериев самого индекса.
    # Есть только вокруг городов; в селе poi/green = 0, вес держат pop+decay.
    poinorm = greennorm = np.zeros(len(rr))
    if os.path.exists(OSM_JSON):
        osm = json.load(open(OSM_JSON, encoding="utf-8"))
        poi = bin_points(osm.get("poi", []), minx, maxy, dlon, dlat, nrow, ncol)
        grn = bin_points(osm.get("green", []), minx, maxy, dlon, dlat, nrow, ncol)
        poinorm = norm95(poi)[rr, cc]
        greennorm = norm95(grn)[rr, cc]
        print(f"OSM: POI={len(osm.get('poi', []))}, green={len(osm.get('green', []))}")
    else:
        print(f"нет {OSM_JSON} — вес без OSM (только население+затухание)")

    # Линейная инфраструктура OSM (etl/fetch_infra_novosibirsk.py): близость к
    # крупным дорогам / ж-д / ЛЭП — те же дистанционные маски, что у других
    # регионов (road_network/railway/power), но по сетке индекса. Держим
    # компоненты РАЗДЕЛЬНО (не сливаем) — веса выставляет пользователь ползунками.
    road_c = rail_c = powr_c = np.zeros(len(rr))
    if os.path.exists(INFRA_JSON):
        inf = json.load(open(INFRA_JSON, encoding="utf-8"))
        road_c = line_decay(inf.get("road", []), clon, clat, mlat, 5.0)
        rail_c = line_decay(inf.get("rail", []), clon, clat, mlat, 6.0)
        powr_c = line_decay(inf.get("power", []), clon, clat, mlat, 8.0)
        print(f"инфраструктура: road={len(inf.get('road', []))}, "
              f"rail={len(inf.get('rail', []))}, power={len(inf.get('power', []))} точек")
    else:
        print(f"нет {INFRA_JSON} — без дорожно-инфраструктурных масок")

    # Компоненты масок присутствия (0..1) на ячейку — вес (яркость) собирает
    # фронт из ползунков. value (цвет) = IDW балла, маски задают только яркость.
    comp = {"pop": popnorm, "poi": poinorm, "green": greennorm,
            "road": road_c, "rail": rail_c, "power": powr_c, "city": citydecay}
    r2 = lambda a, k: round(float(a[k]), 2)

    conn = psycopg2.connect(url)
    conn.autocommit = False
    with conn, conn.cursor() as cur:
        # Локально создаём tile_index сами (миграции ещё не накатаны); в проде
        # файла миграций в etl-образе нет — функцию накатывает migrate-Job/initdb.
        if os.path.exists(MIGRATION):
            cur.execute(open(MIGRATION, encoding="utf-8").read())
        cur.execute("DELETE FROM region WHERE slug=%s", (SLUG,))
        cur.execute("INSERT INTO region (slug, name, geom) VALUES "
                    "(%s,%s, ST_Multi(ST_GeomFromText(%s,4326))) RETURNING id",
                    (SLUG, NAME, border.wkt))
        reg = cur.fetchone()[0]

        cols = list(comp)  # pop,poi,green,road,rail,power,city
        buf = io.StringIO()
        wtr = csv.writer(buf)
        for k in range(len(rr)):
            x0 = round(clon[k] - dlon / 2, 5); x1 = round(clon[k] + dlon / 2, 5)
            y0 = round(clat[k] - dlat / 2, 5); y1 = round(clat[k] + dlat / 2, 5)
            wkt = f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"
            wtr.writerow([f"{rr[k]}_{cc[k]}", wkt, float(cpop[k]),
                          round(float(value[k]), 2), CITIES[nearest[k]][0]]
                         + [r2(comp[c], k) for c in cols])
        buf.seek(0)
        cur.execute("CREATE TEMP TABLE _stg (cell_code text, wkt text, popcnt float8, "
                    "value float8, name text, " + ", ".join(f"{c} float8" for c in cols) +
                    ") ON COMMIT DROP")
        cur.copy_expert("COPY _stg FROM STDIN WITH CSV", buf)
        comp_json = ", ".join(f"'{c}', {c}" for c in cols)
        cur.execute(f"""
            INSERT INTO grid_cell (region_id, cell_code, geom, area_km2, population, features)
            SELECT %s, cell_code, ST_Multi(ST_GeomFromText(wkt,4326)), %s, popcnt,
                   jsonb_build_object('value', value, 'name', name, {comp_json})
            FROM _stg
        """, (reg, step * step))

        # Слой дорог НСО в таблицу road → тот же tile_road и слой «Дороги», что
        # у других регионов (/api/regions вернёт roads_tile_url автоматически).
        rlines = (json.load(open(INFRA_JSON, encoding="utf-8")).get("road_lines", [])
                  if os.path.exists(INFRA_JSON) else [])
        if rlines:
            rbuf = io.StringIO(); rw = csv.writer(rbuf)
            for ln in rlines:
                coords = ",".join(f"{x} {y}" for x, y in ln["c"])
                rw.writerow([ln.get("h") or "", f"LINESTRING({coords})"])
            rbuf.seek(0)
            cur.execute("CREATE TEMP TABLE _rd (highway text, wkt text) ON COMMIT DROP")
            cur.copy_expert("COPY _rd FROM STDIN WITH CSV", rbuf)
            cur.execute("INSERT INTO road (region_id, highway, geom) "
                        "SELECT %s, NULLIF(highway,''), ST_GeomFromText(wkt,4326) FROM _rd",
                        (reg,))
    conn.close()
    print(f"регион id={reg}, ячеек {len(rr)}, балл {value.min():.0f}–{value.max():.0f}, "
          f"население Σ≈{int(cpop.sum()):,}, дорог {len(rlines)}")


if __name__ == "__main__":
    main()
