"""
Расчёт масок railway / power / territory / traveltime для региона БЕЗ osmnx.

Зачем отдельный скрипт. scripts/compute_infra_masks.py, compute_territory.py и
compute_traveltime.py требуют osmnx (а traveltime — ещё и заранее скачанный
graphml), поэтому НСО осталась без этих четырёх масок: у неё 6 из 10. Здесь то
же самое считается прямыми запросами в Overpass и scipy, которые есть в
slim-образе ETL.

Конвенции повторяют существующие скрипты — иначе маска НСО несопоставима с
масками остальных регионов:
  * сетка читается в EPSG:3857, вес пишется на cell_code = индекс строки сетки
    (так его сопоставляет load_csv_mask в etl/ingest.py);
  * railway: станции/платформы/грузовые дворы (railway=station|halt|yard),
    w = norm(exp(-d/10км)), d — расстояние в метрах EPSG:3857 (как в
    compute_infra_masks._weights_to_features);
  * power:   линии power=line, w = norm(exp(-d/8км));
  * territory: балл класса landuse/natural/aeroway полигона, в который попал
    ЦЕНТРОИД ячейки (баллы из src/masks/territory_scores.yaml), иначе 0;
  * traveltime: t = время по сети + подъезд от ячейки и от города по прямой на
    25 км/ч (OFFROAD_KMH), w = norm(exp(-t/30мин)) при SIGMA_MIN=30.

Отличие от osmnx-версии, сознательное: скорости берём по классу дороги из
SPEED_KMH ниже. osmnx использовал бы тег maxspeed, а при его отсутствии —
среднее по классу в этом же графе. Разброс скоростей внутри класса на
магистральной сети мал, а тег maxspeed в РФ заполнен редко.

Многоисточниковый Dijkstra без networkx: в граф добавляется ВИРТУАЛЬНЫЙ
супер-источник, соединённый с узлом каждого города ребром веса off_city_j.
Кратчайший путь от него до узла = min_j(off_city_j + сеть(город_j → узел)) —
ровно то, что считает network_minutes перебором городов.

Запуск:
  python -m scripts.compute_masks_overpass --grid data/processed/grid_novosibirsk_1km_features.gpkg \
      --cities data/processed/cities_novosibirsk.csv --out-slug novosibirsk
"""

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.territory_config import build_score_maps  # noqa: E402

# Публичный Overpass душит серию запросов (429), а на больших тайлах отдаёт 504.
# Ротируем зеркала: на каждую попытку берём следующее. Зеркало mail.ru, которое
# используют osmnx-скрипты, отдаёт 403 на прямой POST — здесь его нет.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
HEADERS = {"User-Agent": "mipt-deaggr-etl/1.0 (practicum research)"}
SIGMA_RAIL_KM = 10.0
SIGMA_POWER_KM = 8.0
SIGMA_MIN = 30.0     # = src.masks.road_traveltime.SIGMA_MIN
OFFROAD_KMH = 25.0   # = src.spatial_decay.OFFROAD_KMH
BUFFER_KM = 30.0     # города в этом буфере от сетки (как в compute_traveltime)
# км/ч по классу дороги; osmnx взял бы maxspeed, см. докстринг
SPEED_KMH = {"motorway": 110, "motorway_link": 80, "trunk": 90, "trunk_link": 70,
             "primary": 70, "primary_link": 55, "secondary": 55, "secondary_link": 45,
             "tertiary": 45, "tertiary_link": 40}


def overpass(body, timeout=300, retries=9, out="geom"):
    q = f"[out:json][timeout:{timeout}];({body});out {out};"
    for a in range(retries):
        url = MIRRORS[a % len(MIRRORS)]
        try:
            r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=timeout + 120)
            if r.status_code == 200:
                return r.json()["elements"]
            code = r.status_code
        except requests.RequestException as e:
            code = type(e).__name__
        print(f"    {url.split('/')[2]}: {code}, попытка {a + 1}/{retries}", flush=True)
        time.sleep(8 * (a + 1))
    raise SystemExit("Overpass недоступен на всех зеркалах")


def tiles(bounds, step=1.5):
    """Дробим bbox: один запрос на всю область (НСО ~10°x4°) ловит 504."""
    minx, miny, maxx, maxy = bounds
    xs = np.arange(minx, maxx, step)
    ys = np.arange(miny, maxy, step)
    return [(y, x, min(y + step, maxy), min(x + step, maxx)) for y in ys for x in xs]


def decay_to_points(cents, pts_3857, sigma_km):
    """norm(exp(-d/σ)) до ближайшей точки; d в метрах CRS сетки (EPSG:3857)."""
    if len(pts_3857) == 0:
        return np.zeros(len(cents))
    from scipy.spatial import cKDTree
    d, _ = cKDTree(pts_3857).query(np.c_[cents.x.to_numpy(), cents.y.to_numpy()], k=1)
    w = np.exp(-(d / 1000.0) / sigma_km)
    w = np.where(np.isfinite(w), w, 0.0)
    return w / w.max() if w.max() > 0 else w


def fetch_railway(bbox_tiles):
    """railway=station|halt|yard — узлы, way и relation; полигоны → центроиды."""
    pts = []
    for i, (s, w, n, e) in enumerate(bbox_tiles, 1):
        els = overpass(
            f'node["railway"~"^(station|halt|yard)$"]({s},{w},{n},{e});'
            f'way["railway"~"^(station|halt|yard)$"]({s},{w},{n},{e});')
        for el in els:
            if el["type"] == "node":
                pts.append((el["lon"], el["lat"]))
            elif el.get("geometry"):
                g = [(p["lon"], p["lat"]) for p in el["geometry"]]
                pts.append((float(np.mean([p[0] for p in g])), float(np.mean([p[1] for p in g]))))
        print(f"  ЖД тайл {i}/{len(bbox_tiles)}: всего точек {len(pts)}", flush=True)
        time.sleep(4)
    return pts


def fetch_power(bbox_tiles):
    """power=line — вершины линий (расстояние до вершины ≈ до линии: шаг вершин
    много меньше σ=8 км)."""
    pts = []
    for i, (s, w, n, e) in enumerate(bbox_tiles, 1):
        for el in overpass(f'way["power"="line"]({s},{w},{n},{e});'):
            pts += [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
        print(f"  ЛЭП тайл {i}/{len(bbox_tiles)}: вершин {len(pts)}", flush=True)
        time.sleep(4)
    return pts


def fetch_landuse(bbox_tiles, tags):
    """Полигоны нужных классов: way + relation (outer-сегменты сшиваем)."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import linemerge, polygonize, unary_union
    LU, NA, AE, _ = build_score_maps()
    sel = []
    for key, vals in tags.items():
        sel.append(f'["{key}"~"^({"|".join(vals)})$"]')
    polys = []
    for i, (s, w, n, e) in enumerate(bbox_tiles, 1):
        body = "".join(f'way{f}({s},{w},{n},{e});relation{f}({s},{w},{n},{e});' for f in sel)
        for el in overpass(body):
            t = el.get("tags", {})
            score = max([v for v in (LU.get(t.get("landuse")), NA.get(t.get("natural")),
                                     AE.get(t.get("aeroway"))) if v is not None], default=None)
            if score is None:
                continue
            if el["type"] == "way" and el.get("geometry"):
                ring = [(p["lon"], p["lat"]) for p in el["geometry"]]
                if len(ring) >= 4:
                    g = Polygon(ring).buffer(0)
                    if not g.is_empty:
                        polys.append((g, score))
            elif el["type"] == "relation":
                segs = [[(p["lon"], p["lat"]) for p in m["geometry"]]
                        for m in el.get("members", [])
                        if m.get("role") == "outer" and m.get("geometry")]
                segs = [x for x in segs if len(x) >= 2]
                if not segs:
                    continue
                merged = unary_union(list(polygonize(linemerge([LineString(x) for x in segs]))))
                if not merged.is_empty:
                    polys.append((merged, score))
        print(f"  landuse тайл {i}/{len(bbox_tiles)}: полигонов {len(polys)}", flush=True)
        time.sleep(4)
    return polys


def fetch_roads(bbox_tiles):
    """Магистральная сеть: класс + геометрия (списки координат)."""
    ways, seen = [], set()
    cls = "|".join(k for k in SPEED_KMH if not k.endswith("_link"))
    for i, (s, w, n, e) in enumerate(bbox_tiles, 1):
        for el in overpass(f'way["highway"~"^({cls})(_link)?$"]({s},{w},{n},{e});'):
            if el["id"] in seen or not el.get("geometry"):
                continue
            seen.add(el["id"])
            ways.append((el["tags"].get("highway", ""),
                         [(p["lon"], p["lat"]) for p in el["geometry"]]))
        print(f"  дороги тайл {i}/{len(bbox_tiles)}: линий {len(ways)}", flush=True)
        time.sleep(4)
    return ways


def traveltime_weights(cents_3857, cities_3857, ways, to3857):
    """Минуты до ближайшего города по сети + подъезды; -> norm(exp(-t/σ))."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra
    from scipy.spatial import cKDTree

    # узлы графа — вершины линий, склеенные по совпадению координат
    node_id, xs, ys = {}, [], []
    rows, cols, secs = [], [], []
    for hw, coords in ways:
        kmh = SPEED_KMH.get(hw, 40)
        prev = None
        for lon, lat in coords:
            key = (round(lon, 6), round(lat, 6))
            if key not in node_id:
                node_id[key] = len(xs)
                x, y = to3857(lon, lat)
                xs.append(x); ys.append(y)
            cur = node_id[key]
            if prev is not None and prev != cur:
                d = np.hypot(xs[cur] - xs[prev], ys[cur] - ys[prev])
                t = d / 1000.0 / kmh * 3600.0
                rows.append(prev); cols.append(cur); secs.append(t)
            prev = cur
    n = len(xs)
    if n == 0:
        return np.zeros(len(cents_3857))
    nx_, ny_ = np.array(xs), np.array(ys)
    tree = cKDTree(np.c_[nx_, ny_])

    cell_d, cell_node = tree.query(np.c_[cents_3857.x.to_numpy(), cents_3857.y.to_numpy()], k=1)
    city_d, city_node = tree.query(np.c_[cities_3857.x.to_numpy(), cities_3857.y.to_numpy()], k=1)
    off_cell_min = cell_d / 1000.0 / OFFROAD_KMH * 60.0
    off_city_min = city_d / 1000.0 / OFFROAD_KMH * 60.0

    # ВИРТУАЛЬНЫЙ супер-источник n: ребро до узла города весом off_city_j.
    # Dijkstra от него = min_j(off_city_j + сеть(город_j → узел)).
    rows2 = rows + [n] * len(city_node)
    cols2 = cols + list(city_node)
    secs2 = secs + list(off_city_min * 60.0)
    g = coo_matrix((np.array(secs2), (np.array(rows2), np.array(cols2))), shape=(n + 1, n + 1))
    sec = dijkstra(g.tocsr(), directed=False, indices=n)

    t = sec[cell_node] / 60.0 + off_cell_min
    w = np.exp(-t / SIGMA_MIN)
    w = np.where(np.isfinite(w), w, 0.0)
    reach = float(np.isfinite(t).mean())
    print(f"  время в пути: узлов {n}, достижимо {reach:.2f}, "
          f"медиана {np.nanmedian(t[np.isfinite(t)]):.0f} мин", flush=True)
    return w / w.max() if w.max() > 0 else w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", required=True)
    ap.add_argument("--cities", required=True)
    ap.add_argument("--out-slug", required=True)
    ap.add_argument("--only", help="через запятую: railway,power,territory,traveltime")
    ap.add_argument("--tile-deg", type=float, default=1.5)
    a = ap.parse_args()
    want = set((a.only or "railway,power,territory,traveltime").split(","))

    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import Point

    grid = gpd.read_file(os.path.join(ROOT, a.grid)).to_crs(3857).reset_index(drop=True)
    cents = grid.geometry.centroid
    cell_code = grid.index.astype(str)
    bounds = gpd.GeoSeries(grid.geometry, crs=3857).to_crs(4326).total_bounds
    bt = tiles(bounds, a.tile_deg)
    print(f"сетка {len(grid)} ячеек, тайлов Overpass {len(bt)}", flush=True)
    tr = Transformer.from_crs(4326, 3857, always_xy=True)
    to3857 = lambda lon, lat: tr.transform(lon, lat)

    def save(name, w):
        p = os.path.join(ROOT, f"data/processed/{name}_{a.out_slug}.csv")
        pd.DataFrame({"cell_code": cell_code, "weight": w}).to_csv(p, index=False)
        print(f"-> {p}  покрыто={np.mean(w > 0):.2f}  w(avg)={w.mean():.3f}", flush=True)

    if "railway" in want:
        pts = fetch_railway(bt)
        arr = np.array([to3857(x, y) for x, y in pts]) if pts else np.empty((0, 2))
        save("railway", decay_to_points(cents, arr, SIGMA_RAIL_KM))
    if "power" in want:
        pts = fetch_power(bt)
        arr = np.array([to3857(x, y) for x, y in pts]) if pts else np.empty((0, 2))
        save("power", decay_to_points(cents, arr, SIGMA_POWER_KM))
    if "territory" in want:
        _, _, _, TAGS = build_score_maps()
        polys = fetch_landuse(bt, TAGS)
        if polys:
            f = gpd.GeoDataFrame({"score": [s for _, s in polys]},
                                 geometry=[g for g, _ in polys], crs=4326).to_crs(3857)
            cg = gpd.GeoDataFrame(geometry=cents, crs=3857)
            j = gpd.sjoin(cg[["geometry"]], f, predicate="within", how="left")
            w = j["score"].groupby(j.index).max().reindex(cg.index).fillna(0.0).to_numpy()
        else:
            w = np.zeros(len(grid))
        save("territory", w)
    if "traveltime" in want:
        cdf = pd.read_csv(os.path.join(ROOT, a.cities)).dropna(subset=["lon", "lat"])
        cities = gpd.GeoDataFrame(cdf, geometry=[Point(x, y) for x, y in zip(cdf.lon, cdf.lat)],
                                  crs=4326).to_crs(3857)
        keep = cities.geometry.within(grid.union_all().buffer(BUFFER_KM * 1000))
        cities = cities[keep]
        print(f"  городов в буфере {BUFFER_KM:.0f} км: {len(cities)}", flush=True)
        ways = fetch_roads(bt)
        save("traveltime", traveltime_weights(cents, cities.geometry, ways, to3857))
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
