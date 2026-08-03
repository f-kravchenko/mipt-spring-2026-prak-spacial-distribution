"""
Сборка входных файлов росстатовского ETL (etl.ingest) для региона: сетка 1 км,
граница, города. Нужна регионам, которых не собирали в notebooks/02_grid.ipynb —
например НСО, заведённой только под ИКГС.

Конвенция ПОВТОРЯЕТ ноутбук (им собраны сетки МО/Краснодара/Якутии). Считать
иначе нельзя: маски региона стали бы несопоставимы с остальными, а sigma масок
расстояния в src/masks/config.yaml подобрана под эти самые единицы.
  * сетка строится в EPSG:3857, ячейка ровно 1000×1000 единиц проекции (это НЕ
    настоящий километр: на широте 55° выходит ~573 м по земле), bbox приткнут к
    кратным 1000, обрезка по границе через overlay(intersection);
  * area_km2 = площадь/1e6 — у полной ячейки 1.0, у обрезанной доля;
  * dist_to_center_km — метры EPSG:3857/1000 от центроида до опорного города;
  * dist_to_city_km — то же до ближайшего из КОРОТКОГО списка крупных городов
    (в ноутбуке он захардкожен по 9-10 городов на регион; здесь берём города
    реестра ИКГС из <slug>_cities.json — это ровно тот же смысл);
  * population — сумма WorldPop по ячейке. rasterstats в образе нет, считаем
    rasterio+rasterize: пиксель попадает в ячейку по своему ЦЕНТРУ, ровно как в
    rasterstats.zonal_stats(stats=['sum']) по умолчанию.

cities_<name>.csv — все place=city|town внутри полигона (как у остальных
регионов: у МО в csv 219 строк, хотя dist считался по 9), с населением из тега
OSM. Файл опционален: etl.ingest берёт его только если путь указан в конфиге.

Запуск:
  DATABASE_URL=... python -m etl.build_grid --region novosibirsk_ikgs \
      --name novosibirsk --center Новосибирск
"""

import argparse
import json
import math
import os
import time

CELL = 1000  # единиц EPSG:3857 на сторону ячейки — конвенция ноутбука
OVERPASS = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "mipt-deaggr-etl/1.0 (practicum research)"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "data/processed")


def region_polygon(url, slug, border_path=None):
    """Полигон региона: из БД, иначе из уже сохранённой границы. Фолбэк нужен на
    проде — там сетку собирают ДО ingest_index, то есть регион в БД может ещё не
    существовать, а border_<name>.gpkg лежит в репозитории."""
    import psycopg2
    from shapely.geometry import shape
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT ST_AsGeoJSON(geom) FROM region WHERE slug=%s", (slug,))
        row = cur.fetchone()
    conn.close()
    if row:
        return shape(json.loads(row[0]))
    if border_path and os.path.exists(border_path):
        import geopandas as gpd
        b = gpd.read_file(border_path).to_crs(4326)
        print(f"регион {slug} не в БД — берём границу из {border_path}")
        return b.union_all() if hasattr(b, "union_all") else b.unary_union
    raise SystemExit(f"регион {slug} не найден в БД, и нет {border_path}")


def build_grid(border_gdf):
    """Сетка CELL×CELL в CRS границы, обрезанная по ней (см. ноутбук)."""
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import box
    minx, miny, maxx, maxy = border_gdf.total_bounds
    minx, miny = math.floor(minx / CELL) * CELL, math.floor(miny / CELL) * CELL
    maxx, maxy = math.ceil(maxx / CELL) * CELL, math.ceil(maxy / CELL) * CELL
    cells = [box(x, y, x + CELL, y + CELL)
             for x in np.arange(minx, maxx, CELL) for y in np.arange(miny, maxy, CELL)]
    grid = gpd.GeoDataFrame({"geometry": cells}, crs=border_gdf.crs)
    grid = gpd.overlay(grid, border_gdf[["geometry"]], how="intersection")
    grid["cell_id"] = range(len(grid))
    grid["area_km2"] = grid.geometry.area / 1e6
    return grid


def cell_population(grid_3857, pop_tif):
    """Сумма растра по ячейкам: пиксель относится к ячейке по своему центру."""
    import numpy as np
    import rasterio
    from rasterio.features import rasterize
    with rasterio.open(pop_tif) as src:
        vals = src.read(1).astype("float64")
        nd, transform, shape_ = src.nodata, src.transform, src.shape
        crs = src.crs
    vals = np.where(~np.isfinite(vals) | (vals < 0) | ((nd is not None) & (vals == nd)), 0.0, vals)
    g = grid_3857.to_crs(crs)
    # 0 — «нет ячейки», поэтому жжём cell_id+1 (rasterize: fill=0)
    ids = rasterize(((geom, i + 1) for i, geom in enumerate(g.geometry)),
                    out_shape=shape_, transform=transform, fill=0,
                    all_touched=False, dtype="int32")
    ok = ids > 0
    sums = np.bincount(ids[ok] - 1, weights=vals[ok], minlength=len(g))
    return sums


def fetch_cities(poly):
    """place=city|town внутри полигона: имя, координаты, население из тега OSM."""
    import requests
    from shapely.geometry import Point
    from shapely.prepared import prep
    minx, miny, maxx, maxy = poly.bounds
    q = (f'[out:json][timeout:180];node["place"~"^(city|town)$"]["name"]'
         f'({miny},{minx},{maxy},{maxx});out tags center;')
    els = []
    for attempt in range(4):
        r = requests.post(OVERPASS, data={"data": q}, headers=HEADERS, timeout=240)
        if r.status_code == 200:
            els = r.json()["elements"]
            break
        time.sleep(12 * (attempt + 1))
    else:
        print("  Overpass недоступен — cities.csv не пишем (файл опционален)")
        return []
    pin = prep(poly)
    out = []
    for e in els:
        lon, lat, t = e.get("lon"), e.get("lat"), e.get("tags", {})
        if lon is None or not pin.contains(Point(lon, lat)):
            continue
        try:
            pop = float(str(t.get("population", "")).replace(" ", "")) or None
        except ValueError:
            pop = None
        out.append({"name": t["name"], "lon": round(lon, 6), "lat": round(lat, 6),
                    "population": pop, "place": t.get("place")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, help="slug региона в БД")
    ap.add_argument("--name", help="основа имён файлов (по умолчанию = slug)")
    ap.add_argument("--center", help="опорный город для dist_to_center_km "
                                     "(по умолчанию первый в <slug>_cities.json)")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    a = ap.parse_args()
    url = (a.database_url or "").replace("postgresql+psycopg://", "postgresql://") \
                               .replace("postgresql+psycopg2://", "postgresql://")
    if not url:
        raise SystemExit("Задайте DATABASE_URL")
    slug, name = a.region, a.name or a.region

    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point

    border_path = os.path.join(PROC, f"border_{name}.gpkg")
    poly = region_polygon(url, slug, border_path)
    border = gpd.GeoDataFrame({"name": [slug]}, geometry=[poly], crs=4326).to_crs(3857)
    border.to_file(border_path, driver="GPKG")
    print(f"граница → {border_path} ({border.geometry.area.sum()/1e6:,.0f} усл. км²)")

    grid = build_grid(border)
    print(f"сетка: ячеек {len(grid):,}, сумма area_km2 {grid.area_km2.sum():,.0f}")

    # опорные города для расстояний — реестр ИКГС региона
    reg_cities = json.load(open(os.path.join(PROC, f"{slug}_cities.json"), encoding="utf-8"))
    cpts = gpd.GeoDataFrame(
        {"name": [c[0] for c in reg_cities]},
        geometry=[Point(c[1], c[2]) for c in reg_cities], crs=4326).to_crs(3857)
    center_name = a.center or reg_cities[0][0]
    sel = cpts[cpts.name == center_name]
    if sel.empty:
        raise SystemExit(f"опорный город «{center_name}» не найден среди "
                         f"{', '.join(cpts.name)}")
    cen = grid.geometry.centroid
    grid["dist_to_center_km"] = cen.distance(sel.geometry.iloc[0]) / 1000
    grid["dist_to_city_km"] = [
        min(p.distance(q) for q in cpts.geometry) / 1000 for p in cen]
    print(f"  опорный город {center_name}; dist_to_city_km "
          f"{grid.dist_to_city_km.min():.1f}..{grid.dist_to_city_km.max():.1f}")

    pop_tif = os.path.join(PROC, f"{slug}_pop.tif")
    if os.path.exists(pop_tif):
        grid["population"] = cell_population(grid, pop_tif)
        print(f"  население: сумма {grid.population.sum():,.0f}, "
              f"ячеек с людьми {(grid.population > 0).sum():,}")
    else:
        grid["population"] = 0.0
        print(f"  {pop_tif} нет — population=0 (worldpop/regression будут пустыми)")

    grid_path = os.path.join(PROC, f"grid_{name}_1km_features.gpkg")
    grid[["cell_id", "area_km2", "population", "dist_to_center_km",
          "dist_to_city_km", "geometry"]].to_file(grid_path, driver="GPKG")
    print(f"сетка → {grid_path}")

    cities = fetch_cities(poly)
    if cities:
        cpath = os.path.join(PROC, f"cities_{name}.csv")
        pd.DataFrame(cities).to_csv(cpath, index=False)
        print(f"города → {cpath} ({len(cities)} шт.)")


if __name__ == "__main__":
    main()
