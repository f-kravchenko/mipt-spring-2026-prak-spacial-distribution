"""
Загрузка «Индекса качества городской среды» (Новосибирская область) как
ОБЫЧНОГО региона в PostGIS: сетка 1 км в grid_cell + значение/вес в
features, отдаётся векторными тайлами tile_index (миграция 0010) — как
у других регионов, а не статическим geojson.

Индекс — не сумма, поэтому в отличие от Росстат-показателей значение ячейки
НЕ распределяется из регионального итога. Спатиализация:
  value  — IDW-интерполяция балла 14 городов (плавный непрерывный цвет);
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
from rasterio.features import rasterize
from shapely.geometry import shape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUSSIA = os.path.join(ROOT, "apps/web/public/russia.geojson")
# Светлая подложка области под сеткой на фронте (см. App.jsx nsk-region).
OUT_BORDER = os.path.join(ROOT, "apps/web/public/novosibirsk_border.geojson")
POP_TIF = os.path.join(ROOT, "data/processed/pop_nso_z.tif")
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

    # IDW балла + ближайший город + затухание к ближайшему
    cx = np.array([c[1] for c in CITIES]); cy = np.array([c[2] for c in CITIES])
    cs = np.array([c[3] for c in CITIES], float)
    num = np.zeros(len(rr)); den = np.zeros(len(rr))
    dmin = np.full(len(rr), np.inf); nearest = np.zeros(len(rr), int)
    eps = 0.7 ** 2
    for i in range(len(CITIES)):
        dx = (clon - cx[i]) * KM * coslat
        dy = (clat - cy[i]) * KM
        d2 = dx * dx + dy * dy
        w = 1.0 / (d2 + eps)
        num += w * cs[i]; den += w
        closer = d2 < dmin
        dmin = np.where(closer, d2, dmin)
        nearest = np.where(closer, i, nearest)
    value = num / den

    p95 = np.percentile(cpop[cpop > 0], 95) if (cpop > 0).any() else 1.0
    popnorm = np.clip(np.sqrt(cpop / p95), 0, 1)
    citydecay = np.exp(-dmin / (2 * SIGMA_KM ** 2))
    # пол 0.12 (как baseline-маска у других регионов — сплошное покрытие),
    # ярче там, где плотнее население/ближе город
    weight = np.clip(0.12 + 0.88 * (0.7 * popnorm + 0.3 * citydecay), 0, 1)

    conn = psycopg2.connect(url)
    conn.autocommit = False
    with conn, conn.cursor() as cur:
        cur.execute(open(MIGRATION, encoding="utf-8").read())  # tile_index (идемпотентно)
        cur.execute("DELETE FROM region WHERE slug=%s", (SLUG,))
        cur.execute("INSERT INTO region (slug, name, geom) VALUES "
                    "(%s,%s, ST_Multi(ST_GeomFromText(%s,4326))) RETURNING id",
                    (SLUG, NAME, border.wkt))
        reg = cur.fetchone()[0]

        buf = io.StringIO()
        wtr = csv.writer(buf)
        for k in range(len(rr)):
            x0 = round(clon[k] - dlon / 2, 5); x1 = round(clon[k] + dlon / 2, 5)
            y0 = round(clat[k] - dlat / 2, 5); y1 = round(clat[k] + dlat / 2, 5)
            wkt = f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"
            wtr.writerow([f"{rr[k]}_{cc[k]}", wkt, float(cpop[k]),
                          round(float(value[k]), 2), round(float(weight[k]), 3),
                          CITIES[nearest[k]][0]])
        buf.seek(0)
        cur.execute("CREATE TEMP TABLE _stg (cell_code text, wkt text, pop float8, "
                    "value float8, weight float8, name text) ON COMMIT DROP")
        cur.copy_expert("COPY _stg FROM STDIN WITH CSV", buf)
        cur.execute("""
            INSERT INTO grid_cell (region_id, cell_code, geom, area_km2, population, features)
            SELECT %s, cell_code, ST_Multi(ST_GeomFromText(wkt,4326)), %s, pop,
                   jsonb_build_object('value', value, 'weight', weight, 'name', name)
            FROM _stg
        """, (reg, step * step))
    conn.close()
    print(f"регион id={reg}, ячеек {len(rr)}, балл IDW {value.min():.0f}–{value.max():.0f}, "
          f"население Σ≈{int(cpop.sum()):,}")


if __name__ == "__main__":
    main()
