"""
Загрузка ИКГС как индекс-слоя для ЛЮБОГО региона (обобщение
ingest_index_novosibirsk.py). Значение = балл реестра по фактической площади НП
(контуры OSM) с затуханием вдвое/5 км, при перекрытии max. Маски присутствия
(яркость) — население/POI/зелень/дороги/ж-д/ЛЭП/близость к городу; те, чьих
данных нет, тихо = 0.

Полигон региона берём из БД по slug (у якутского «центра» он только там). Регион
НЕ удаляем — стираем лишь ИКГС-ячейки (features ? 'value'), Росстат-данные
остаются. Ячейки индекса помечаем cell_code 'ix_*' (не конфликтуют с Росстатом).

Данные: data/processed/<slug>_cities.json (name,lon,lat,score — fetch_index_region),
<slug>_footprint.json, опц. <slug>_osm.json, <slug>_infra.json, <slug>_pop.tif.

Запуск:  DATABASE_URL=... python -m etl.ingest_index --region krasnodar
"""

import argparse
import csv
import io
import json
import math
import os

import numpy as np
import psycopg2
from affine import Affine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "data/processed")
PUB = os.path.join(ROOT, "apps/web/public")
RUSSIA = os.path.join(PUB, "russia.geojson")
MIGRATION = os.path.join(ROOT, "deploy/helm/spatial-masks/files/migrations/0010_tile_index.sql")
KM = 111.32
SIGMA_KM = 15.0   # затухание «присутствия» от города
HALF_KM = 5.0     # полураспад балла вне контура НП (вдвое / 5 км)
CLOSE_CELLS = 3   # смыкание фрагментов застройки в сплошной контур НП (~3 км)
# показатель для regression-маски при переиспользии Росстат-масок (regression
# indicator-dependent; берём «эталон» — Отгруженные товары обрабатывающей пром.)
REG_INDICATOR = "Y477090007"

# Регионы, которые можно СОЗДАТЬ из russia.geojson, если их ещё нет в БД
# (у якутского «центра» полигон только в БД — его сюда не включаем).
CREATE_FROM_GEOJSON = {
    "novosibirsk_ikgs": "Новосибирская область",
    "moscow": "Московская область",
    "krasnodar": "Краснодарский край",
}


def norm95(v):
    pos = v[v > 0]
    ref = np.percentile(pos, 95) if pos.size else 1.0
    return np.clip(np.sqrt(v / ref), 0, 1) if ref > 0 else np.zeros_like(v)


def bin_points(pts, minx, maxy, dlon, dlat, nrow, ncol):
    grid = np.zeros((nrow, ncol))
    if not pts:
        return grid
    a = np.asarray(pts, float)
    cc = np.floor((a[:, 0] - minx) / dlon).astype(int)
    rr = np.floor((maxy - a[:, 1]) / dlat).astype(int)
    ok = (cc >= 0) & (cc < ncol) & (rr >= 0) & (rr < nrow)
    np.add.at(grid, (rr[ok], cc[ok]), 1.0)
    return grid


def line_decay(pts, clon, clat, mlat, sigma_km):
    if not pts:
        return np.zeros(len(clon))
    from scipy.spatial import cKDTree
    kx = KM * math.cos(math.radians(mlat))
    a = np.asarray(pts, float)
    tree = cKDTree(np.column_stack([a[:, 0] * kx, a[:, 1] * KM]))
    d, _ = tree.query(np.column_stack([clon * kx, clat * KM]), k=1)
    return np.exp(-d / sigma_km)


def cell_population(pop_tif, minx, maxy, dlon, dlat, nrow, ncol):
    import rasterio
    with rasterio.open(pop_tif) as src:
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


def get_region(cur, slug):
    """Полигон+id региона из БД; если нет — создаём из russia.geojson по имени."""
    cur.execute("SELECT id, name, ST_AsGeoJSON(geom) FROM region WHERE slug=%s", (slug,))
    row = cur.fetchone()
    if row:
        from shapely.geometry import shape
        return row[0], row[1], shape(json.loads(row[2]))
    name = CREATE_FROM_GEOJSON.get(slug)
    if not name:
        raise SystemExit(f"регион {slug} не в БД и не создаётся из geojson")
    from shapely.geometry import shape
    poly = next((shape(f["geometry"]) for f in json.load(open(RUSSIA, encoding="utf-8"))["features"]
                 if f["properties"].get("name") == name), None)
    if poly is None:
        raise SystemExit(f"{name} не найдена в russia.geojson")
    cur.execute("INSERT INTO region (slug, name, geom) VALUES "
                "(%s,%s,ST_Multi(ST_GeomFromText(%s,4326))) RETURNING id", (slug, name, poly.wkt))
    return cur.fetchone()[0], name, poly


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--step-km", type=float, default=1.0)
    a = ap.parse_args()
    url = (a.database_url or "").replace("postgresql+psycopg://", "postgresql://") \
                               .replace("postgresql+psycopg2://", "postgresql://")
    if not url:
        raise SystemExit("Задайте DATABASE_URL")
    slug, step = a.region, a.step_km
    p = lambda suf: os.path.join(PROC, f"{slug}_{suf}")

    from rasterio.features import rasterize, shapes
    from scipy.ndimage import binary_closing, binary_fill_holes, distance_transform_edt
    from shapely.geometry import Polygon, shape
    from shapely.ops import unary_union

    conn = psycopg2.connect(url)
    conn.autocommit = False
    cur = conn.cursor()
    if os.path.exists(MIGRATION):
        cur.execute(open(MIGRATION, encoding="utf-8").read())
    reg, name, border = get_region(cur, slug)

    # светлая подложка + контуры НП для фронта (слои nsk-region / nsk-cities)
    json.dump({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": name}, "geometry": border.__geo_interface__}]},
        open(os.path.join(PUB, f"{slug}_border.geojson"), "w", encoding="utf-8"), ensure_ascii=False)

    cities = json.load(open(p("cities.json"), encoding="utf-8"))
    cx = np.array([c[1] for c in cities]); cy = np.array([c[2] for c in cities])
    cs = np.array([c[3] for c in cities], float)

    minx, miny, maxx, maxy = border.bounds
    mlat = (miny + maxy) / 2
    dlat = step / KM
    dlon = step / (KM * math.cos(math.radians(mlat)))
    ncol = int(math.ceil((maxx - minx) / dlon))
    nrow = int(math.ceil((maxy - miny) / dlat))
    transform = Affine(dlon, 0, minx, 0, -dlat, maxy)
    inmask = rasterize([(border, 1)], out_shape=(nrow, ncol), transform=transform,
                       fill=0, all_touched=True).astype(bool)

    rr, cc = np.nonzero(inmask)
    clon = minx + (cc + 0.5) * dlon
    clat = maxy - (rr + 0.5) * dlat
    coslat = np.cos(np.radians(clat)); coslat0 = math.cos(math.radians(mlat))

    # ближайший город (подпись) + затухание присутствия к нему
    dmin = np.full(len(rr), np.inf); nearest = np.zeros(len(rr), int)
    for i in range(len(cities)):
        d2 = ((clon - cx[i]) * KM * coslat) ** 2 + ((clat - cy[i]) * KM) ** 2
        closer = d2 < dmin; dmin = np.where(closer, d2, dmin); nearest = np.where(closer, i, nearest)
    citydecay = np.exp(-dmin / (2 * SIGMA_KM ** 2))

    # value: балл по контуру НП + затухание, max при перекрытии
    masks = [np.zeros((nrow, ncol), bool) for _ in cities]
    polys = json.load(open(p("footprint.json"), encoding="utf-8"))["polys"] if os.path.exists(p("footprint.json")) else []
    for ring in polys:
        poly = Polygon(ring)
        if not poly.is_valid or poly.is_empty:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        ctr = poly.centroid
        j = int(np.argmin(((cx - ctr.x) * coslat0) ** 2 + (cy - ctr.y) ** 2))
        masks[j] |= rasterize([(poly, 1)], out_shape=(nrow, ncol), transform=transform,
                              fill=0, all_touched=True).astype(bool)
    for j in range(len(cities)):  # гарантия присутствия: диск ~2 км в точке
        rj = int((maxy - cy[j]) / dlat); cj = int((cx[j] - minx) / dlon)
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                r_, c_ = rj + dr, cj + dc
                if 0 <= r_ < nrow and 0 <= c_ < ncol and dr * dr + dc * dc <= 4:
                    masks[j][r_, c_] = True
    for j in range(len(cities)):  # смыкаем фрагменты в сплошной контур
        masks[j] = binary_fill_holes(binary_closing(masks[j], iterations=CLOSE_CELLS))
    value_grid = np.zeros((nrow, ncol))
    for j in range(len(cities)):
        if masks[j].any():
            d_km = distance_transform_edt(~masks[j], sampling=(step, step))
            value_grid = np.maximum(value_grid, cs[j] * 0.5 ** (d_km / HALF_KM))
    value = value_grid[rr, cc]

    # контуры НП → geojson (слой «Границы НП»)
    feats = []
    for j, c in enumerate(cities):
        if masks[j].any():
            geoms = [shape(g) for g, _ in shapes(masks[j].astype("uint8"), mask=masks[j], transform=transform)]
            feats.append({"type": "Feature", "properties": {"name": c[0], "value": c[3]},
                          "geometry": unary_union(geoms).__geo_interface__})
    json.dump({"type": "FeatureCollection", "features": feats},
              open(os.path.join(PUB, f"{slug}_npcontours.geojson"), "w", encoding="utf-8"), ensure_ascii=False)

    # маски присутствия (яркость) = 10 масок словаря отгруженных товаров, слаги
    # таблицы mask. На своей сетке считаем геометрические/OSM-маски; regression/
    # traveltime/territory = 0 (нет данных на сетке ИКГС). Для Росстат-регионов
    # ниже ПЕРЕЗАПИШЕМ все 10 их же mask_cell_value (пространственный джойн).
    cpop = np.zeros(len(rr))
    worldpop = road_c = rail_c = powr_c = np.zeros(len(rr))
    if os.path.exists(p("pop.tif")):
        pop = cell_population(p("pop.tif"), minx, maxy, dlon, dlat, nrow, ncol)
        cpop = pop[rr, cc]; worldpop = norm95(pop)[rr, cc]
    if os.path.exists(p("infra.json")):
        inf = json.load(open(p("infra.json"), encoding="utf-8"))
        road_c = line_decay(inf.get("road", []), clon, clat, mlat, 5.0)
        rail_c = line_decay(inf.get("rail", []), clon, clat, mlat, 6.0)
        powr_c = line_decay(inf.get("power", []), clon, clat, mlat, 8.0)
    ctr = border.centroid
    dctr = np.sqrt(((clon - ctr.x) * KM * coslat) ** 2 + ((clat - ctr.y) * KM) ** 2)
    dist_center = np.exp(-dctr / 50.0)  # близость к центру региона (масштаб региона)
    zero = np.zeros(len(rr))
    comp = {
        "baseline_mask": np.ones(len(rr)), "distance_to_center_mask": dist_center,
        "distance_to_city_mask": citydecay, "power_lines_mask": powr_c,
        "railway_mask": rail_c, "regression_mask": zero, "road_network_mask": road_c,
        "road_traveltime_mask": zero, "territory_type_mask": zero, "worldpop_mask": worldpop,
    }
    cols = list(comp)
    r2 = lambda arr, k: round(float(arr[k]), 2)

    # только ИКГС-ячейки региона; Росстат-данные (без features->'value') остаются
    cur.execute("DELETE FROM grid_cell WHERE region_id=%s AND features ? 'value'", (reg,))
    buf = io.StringIO(); wtr = csv.writer(buf)
    for k in range(len(rr)):
        x0 = round(clon[k] - dlon / 2, 5); x1 = round(clon[k] + dlon / 2, 5)
        y0 = round(clat[k] - dlat / 2, 5); y1 = round(clat[k] + dlat / 2, 5)
        wkt = f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"
        wtr.writerow([f"ix_{rr[k]}_{cc[k]}", wkt, float(cpop[k]),
                      round(float(value[k]), 2), cities[nearest[k]][0]] + [r2(comp[c], k) for c in cols])
    buf.seek(0)
    cur.execute("CREATE TEMP TABLE _stg (cell_code text, wkt text, popcnt float8, value float8, "
                "name text, " + ", ".join(f"{c} float8" for c in cols) + ") ON COMMIT DROP")
    cur.copy_expert("COPY _stg FROM STDIN WITH CSV", buf)
    comp_json = ", ".join(f"'{c}', {c}" for c in cols)
    cur.execute(f"""
        INSERT INTO grid_cell (region_id, cell_code, geom, area_km2, population, features)
        SELECT %s, cell_code, ST_Multi(ST_GeomFromText(wkt,4326)), %s, popcnt,
               jsonb_build_object('value', value, 'name', name, {comp_json}) FROM _stg
    """, (reg, step * step))

    # Росстат-регион: у него уже посчитаны те же 10 масок (mask_cell_value).
    # Берём их как АВТОРИТЕТНЫЕ — пространственный джойн ИКГС-ячейка→Росстат-ячейка
    # (по центроиду) перезаписывает все 10 компонент их же значениями. У НСО
    # Росстат-масок нет → остаются свои 7 (см. выше).
    cur.execute("""SELECT EXISTS(SELECT 1 FROM mask_cell_value mcv JOIN grid_cell g
                   ON g.id=mcv.cell_id WHERE g.region_id=%s AND NOT (g.features ? 'value'))""", (reg,))
    if cur.fetchone()[0]:
        cur.execute("""
            UPDATE grid_cell ix SET features = ix.features || sub.w
            FROM (
              SELECT ix2.id, jsonb_object_agg(m.slug, round(mcv.weight::numeric, 4)) AS w
              FROM grid_cell ix2
              JOIN grid_cell ro ON ro.region_id=%s AND NOT (ro.features ? 'value')
                   AND ST_Contains(ro.geom, ST_Centroid(ix2.geom))
              JOIN mask_cell_value mcv ON mcv.cell_id = ro.id
              JOIN mask m ON m.id = mcv.mask_id
              WHERE ix2.region_id=%s AND ix2.features ? 'value'
                AND mcv.indicator_code IN ('', %s)
              GROUP BY ix2.id
            ) sub WHERE ix.id = sub.id
        """, (reg, reg, REG_INDICATOR))
        print(f"  переиспользованы Росстат-маски ({cur.rowcount} ячеек)")

    # дороги в road только если у региона их ещё нет (Росстат-регионы уже с дорогами)
    cur.execute("SELECT count(*) FROM road WHERE region_id=%s", (reg,))
    rlines = (json.load(open(p("infra.json"), encoding="utf-8")).get("road_lines", [])
              if os.path.exists(p("infra.json")) else [])
    if cur.fetchone()[0] == 0 and rlines:
        rbuf = io.StringIO(); rw = csv.writer(rbuf)
        for ln in rlines:
            rw.writerow([ln.get("h") or "", "LINESTRING(" + ",".join(f"{x} {y}" for x, y in ln["c"]) + ")"])
        rbuf.seek(0)
        cur.execute("CREATE TEMP TABLE _rd (highway text, wkt text) ON COMMIT DROP")
        cur.copy_expert("COPY _rd FROM STDIN WITH CSV", rbuf)
        cur.execute("INSERT INTO road (region_id, highway, geom) "
                    "SELECT %s, NULLIF(highway,''), ST_GeomFromText(wkt,4326) FROM _rd", (reg,))

    conn.commit(); conn.close()
    print(f"{slug}: регион id={reg} «{name}», ячеек ИКГС {len(rr)}, городов {len(cities)}, "
          f"балл {value.min():.0f}–{value.max():.0f}, контуров {len(polys)}")


if __name__ == "__main__":
    main()
