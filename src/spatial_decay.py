"""
v1 — метод затухания (gravity / distance-decay) для разнесения регионального
показателя Росстата по ячейкам сетки.

Идея: вес ячейки i — потенциал доступности к городам,
    w_i = sum_j  m_j * f(d_ij),
где d_ij — расстояние от центроида ячейки до города j (км), f — ядро затухания,
m_j — «масса» города. Масса = (pop_j / max_pop)^beta — нормированное население в
степени beta (гравитационная эластичность). Затем X_region разносится
пропорционально весам.

Что улучшено по сравнению с наивным `1/(d+1)`:
- города берутся из OSM (city/town с населением), а не 6-9 точек вручную;
- города фильтруются по близости к региону (out-of-region агломерации не тянут
  отгрузку в чужие углы), см. `buffer_km`;
- масса города нелинейна по населению: `pop^beta` (beta=1 — линейно, beta>1 —
  концентрация в крупных городах, ср. эластичность регрессии v3 ≈ 2.77);
- сравниваются три ядра (exp / gauss / power), параметры (sigma, beta)
  подбираются по корреляции веса с растром населения WorldPop.

Источник городов: `data/processed/cities_<region>.csv` (готовится Overpass-
запросом, см. ноутбук). При отсутствии файла используется встроенный список
`CITIES` (минимальный fallback).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from shapely.geometry import Point

# Папка с кэшем городов (cities_<region>.csv). Рассчитывается относительно пакета.
_ROOT = Path(__file__).resolve().parents[1]
_DATA = _ROOT / "data" / "processed"

# Минимальный встроенный список (fallback, если нет cities_<region>.csv).
# Имя -> (lon, lat, население). Первый город — «центр» региона.
CITIES: dict[str, dict[str, tuple[float, float, int]]] = {
    "moscow": {
        "Москва": (37.6173, 55.7558, 12655050),
        "Балашиха": (37.9384, 55.7964, 507366),
        "Подольск": (37.5447, 55.4297, 308130),
        "Химки": (37.4448, 55.8970, 259550),
        "Мытищи": (37.7295, 55.9117, 235504),
        "Люберцы": (37.9534, 55.6760, 205769),
        "Электросталь": (38.4445, 55.7847, 156261),
        "Коломна": (38.7544, 55.0794, 140129),
        "Серпухов": (37.4216, 54.9156, 125817),
    },
    "krasnodar": {
        "Краснодар": (38.9753, 45.0355, 948827),
        "Сочи": (39.7233, 43.5853, 466078),
        "Новороссийск": (37.7615, 44.7239, 275795),
        "Армавир": (41.1289, 44.9892, 186725),
        "Анапа": (37.3158, 44.8946, 92047),
        "Ейск": (38.2766, 46.7104, 82888),
        "Геленджик": (38.0699, 44.5622, 75730),
        "Туапсе": (39.0779, 44.0974, 62269),
        "Тихорецк": (40.1283, 45.8536, 56474),
    },
    "yakutia": {
        "Якутск": (129.7322, 62.0355, 355443),
        "Нерюнгри": (124.7283, 56.6589, 57009),
        "Мирный": (113.9881, 62.5350, 35311),
        "Алдан": (125.3914, 58.6017, 20131),
        "Вилюйск": (121.6450, 63.7475, 11095),
        "Покровск": (129.1486, 61.4856, 9620),
    },
}

# Региональное значение отгрузки (млрд руб), как в 02_grid.ipynb.
X_SHIPPING: dict[str, float] = {
    "moscow": 209.0,
    "krasnodar": 59.7,
    "yakutia": 11.2,
}

KERNELS = ("exp", "gauss", "power")

# Степень массы по умолчанию: эластичность по городскому населению из регрессии v3.
DEFAULT_BETA = 2.77
# Радиус, в пределах которого город считается влияющим на регион (км).
# 30 км перекрывает «дырку» Москвы (федеральный город в 25 км от ячеек области),
# но отсекает города соседних регионов (ближайший — Рязань, 47 км).
DEFAULT_BUFFER_KM = 30.0

# --- параметры дорожной сети (v1-net) ---
# Граница региона для скачивания графа (заполняем дырки, чтобы дороги через
# Москву не выпадали и сеть не рвалась).
_BORDERS = {
    "moscow": "border_mo.gpkg",
    "krasnodar": "border_krasnodar.gpkg",
    "yakutia": "border_ya_center.gpkg",
}
# Магистральная сеть: для межгородской доступности крупные дороги важнее, чем
# каждый дворовый проезд (и граф на порядки меньше).
ROAD_FILTER = '["highway"~"motorway|trunk|primary|secondary"]'
# Скорость «подъезда» от ячейки/города до ближайшего узла сети (км/ч).
OFFROAD_KMH = 25.0
GRAPH_DIR = _DATA / "graphs"


def load_cities(region_key: str):
    """
    Список городов региона как DataFrame с колонками name, lon, lat, population.
    Читает кэш `data/processed/cities_<region>.csv`; при отсутствии — fallback
    на встроенный CITIES. Города без населения отбрасываются (нужны для массы).
    """
    import pandas as pd

    path = _DATA / f"cities_{region_key}.csv"
    if path.exists():
        df = pd.read_csv(path)
        df = df.dropna(subset=["population"]).copy()
        df["population"] = df["population"].astype(float)
        return df[["name", "lon", "lat", "population"]].reset_index(drop=True)

    rows = CITIES[region_key]
    return pd.DataFrame(
        {
            "name": list(rows.keys()),
            "lon": [v[0] for v in rows.values()],
            "lat": [v[1] for v in rows.values()],
            "population": [float(v[2]) for v in rows.values()],
        }
    )


def cities_gdf(region_key: str, crs="EPSG:3857", grid=None, buffer_km: float | None = None):
    """
    GeoDataFrame городов региона (city, population, geometry) в нужной CRS.

    Если передан `grid` и `buffer_km`, оставляем только города, чья дистанция до
    ближайшего центроида ячейки <= buffer_km. Так центр-в-дырке (напр. Москва)
    сохраняется (расстояние ~0), а города соседних регионов в углах bbox
    отбрасываются.
    """
    import geopandas as gpd

    df = load_cities(region_key)
    gdf = gpd.GeoDataFrame(
        {"city": df["name"].to_numpy(), "population": df["population"].to_numpy()},
        geometry=[Point(lon, lat) for lon, lat in zip(df["lon"], df["lat"])],
        crs="EPSG:4326",
    ).to_crs(crs)

    if grid is not None and buffer_km is not None:
        dmat = city_distances_km(grid, gdf)
        near = dmat.min(axis=0) <= buffer_km
        gdf = gdf[near].reset_index(drop=True)
    return gdf


def city_distances_km(grid, cities) -> np.ndarray:
    """
    Матрица расстояний (n_cells, n_cities) в км между центроидами ячеек и
    городами. Обе геометрии — в одной проекционной CRS (метры).
    """
    cents = grid.geometry.centroid
    cx = cents.x.to_numpy()
    cy = cents.y.to_numpy()
    px = cities.geometry.x.to_numpy()
    py = cities.geometry.y.to_numpy()
    dx = cx[:, None] - px[None, :]
    dy = cy[:, None] - py[None, :]
    return np.sqrt(dx * dx + dy * dy) / 1000.0


def decay_kernel(d_km: np.ndarray, sigma: float, kind: str = "exp") -> np.ndarray:
    """Ядро затухания f(d). sigma — масштаб в км (exp/gauss) или степень (power)."""
    d_km = np.asarray(d_km, dtype=float)
    if kind == "exp":
        return np.exp(-d_km / sigma)
    if kind == "gauss":
        return np.exp(-(d_km**2) / (2.0 * sigma**2))
    if kind == "power":
        return 1.0 / np.power(d_km + 1.0, sigma)
    raise ValueError(f"неизвестное ядро: {kind!r}, ожидалось одно из {KERNELS}")


def city_mass(populations: np.ndarray, beta: float = 1.0) -> np.ndarray:
    """Масса города: (pop / max_pop)^beta. Нормировка убирает переполнение."""
    pop = np.asarray(populations, dtype=float)
    pmax = pop.max() if len(pop) and pop.max() > 0 else 1.0
    return np.power(pop / pmax, beta)


def gravity_weights(
    dist_km: np.ndarray,
    populations: np.ndarray,
    sigma: float,
    kind: str = "exp",
    beta: float = 1.0,
    use_population: bool = True,
) -> np.ndarray:
    """
    Потенциал доступности ячеек: w_i = sum_j m_j * f(d_ij),
    где m_j = (pop_j/max_pop)^beta (или 1, если use_population=False).
    """
    f = decay_kernel(dist_km, sigma, kind)
    if use_population:
        return f @ city_mass(populations, beta)
    return f.sum(axis=1)


def distribute_decay(
    grid,
    X_region: float,
    region_key: str,
    sigma: float,
    kind: str = "exp",
    beta: float = 1.0,
    buffer_km: float | None = DEFAULT_BUFFER_KM,
    use_population: bool = True,
) -> np.ndarray:
    """Разнести X_region по ячейкам сетки методом затухания (v1)."""
    cities = cities_gdf(region_key, grid.crs, grid=grid, buffer_km=buffer_km)
    dist = city_distances_km(grid, cities)
    w = gravity_weights(
        dist, cities["population"].to_numpy(), sigma, kind, beta, use_population
    )
    total = w.sum()
    if not np.isfinite(total) or total <= 0:
        w = np.ones(len(w))
        total = w.sum()
    return X_region * w / total


def tune_on_matrix(dist, populations, target, kind, sigmas, betas=(1.0,)):
    """
    Перебор (sigma, beta) для произвольной матрицы «расстояний» dist (евклид в км
    ИЛИ время по сети в минутах), максимизируя Spearman веса с `target`.
    Возвращает ((best_sigma, best_beta, best_rho), [(sigma, beta, rho), ...]).
    """
    from scipy.stats import spearmanr

    target = np.asarray(target, dtype=float)
    results: list[tuple[float, float, float]] = []
    best: tuple[float, float, float] | None = None
    for beta in betas:
        mass = city_mass(populations, beta)
        for s in sigmas:
            w = decay_kernel(dist, s, kind) @ mass
            rho = spearmanr(w, target).correlation
            rho = 0.0 if not np.isfinite(rho) else float(rho)
            results.append((float(s), float(beta), rho))
            if best is None or rho > best[2]:
                best = (float(s), float(beta), rho)
    assert best is not None
    return best, results


def tune_gravity(
    grid,
    region_key: str,
    kind: str,
    sigmas,
    betas=(1.0,),
    buffer_km: float | None = DEFAULT_BUFFER_KM,
):
    """
    Подобрать (sigma, beta) для ядра `kind` по евклидову расстоянию, максимизируя
    ранговую корреляцию веса с растром населения WorldPop (`grid['population']`).
    Матрица расстояний считается один раз.
    """
    cities = cities_gdf(region_key, grid.crs, grid=grid, buffer_km=buffer_km)
    dist = city_distances_km(grid, cities)
    return tune_on_matrix(
        dist, cities["population"].to_numpy(), grid["population"].to_numpy(), kind, sigmas, betas
    )


def tune_sigma(grid, region_key: str, kind: str, sigmas, buffer_km=DEFAULT_BUFFER_KM):
    """Частный случай tune_gravity при beta=1 (обратная совместимость)."""
    (s, _b, rho), res = tune_gravity(grid, region_key, kind, sigmas, betas=(1.0,), buffer_km=buffer_km)
    return (s, rho), [(sig, r) for sig, _bb, r in res]


# ----------------------------------------------------------------------------
# v1-net: затухание по дорожной сети (время в пути вместо евклидова расстояния)
# ----------------------------------------------------------------------------


def configure_osmnx():
    """
    Вежливые настройки osmnx, снижающие риск бана Overpass и лишних запросов:
    - стабильный абсолютный HTTP-кэш (иначе ./cache зависит от CWD и не
      переиспользуется между ноутбуком и скриптами → повторные загрузки);
    - ожидание свободного слота сервера (overpass_rate_limit);
    - щадящий таймаут и понятный User-Agent.
    Сам полный drive-граф (все улицы) мы НИКОГДА не качаем — только магистрали.
    """
    import osmnx as ox

    cache = _ROOT / "cache" / "osmnx"  # под игнорируемым cache/
    cache.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache)
    ox.settings.overpass_rate_limit = True
    ox.settings.requests_timeout = 300
    ox.settings.http_user_agent = "mipt-prak-spatial/1.0 (student project)"


def region_polygon(region_key: str, simplify_tol_deg: float = 0.01):
    """
    Граница региона (EPSG:4326) с заполненными дырками — для скачивания графа.
    Полигон упрощается (по умолчанию ~1 км): к Overpass уходит короткий `poly:`-
    фильтр вместо тысяч вершин границы — запрос меньше и парсится быстрее.
    """
    import geopandas as gpd
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union

    b = gpd.read_file(_DATA / _BORDERS[region_key]).to_crs(4326)
    geom = unary_union(b.geometry.values)
    if isinstance(geom, Polygon):
        geom = Polygon(geom.exterior)
    else:
        geom = MultiPolygon([Polygon(g.exterior) for g in geom.geoms])
    if simplify_tol_deg:
        geom = geom.simplify(simplify_tol_deg)
    return geom


def load_road_graph(region_key: str, force: bool = False, custom_filter: str = ROAD_FILTER):
    """
    Граф магистральных дорог региона с временем в пути на рёбрах (travel_time, сек).
    Кэшируется в `data/processed/graphs/roads_<region>.graphml`.
    Скачивание идёт через Overpass (медленно), повторный вызов читает кэш;
    к тому же osmnx кэширует и сырой ответ Overpass (см. `configure_osmnx`).
    """
    import osmnx as ox

    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    path = GRAPH_DIR / f"roads_{region_key}.graphml"
    if path.exists() and not force:
        return ox.io.load_graphml(path)

    configure_osmnx()
    G = ox.graph_from_polygon(
        region_polygon(region_key), custom_filter=custom_filter, simplify=True, retain_all=False
    )
    G = ox.routing.add_edge_speeds(G)
    G = ox.routing.add_edge_travel_times(G)
    ox.io.save_graphml(G, path)
    return G


def network_minutes(grid, cities, G, offroad_kmh: float = OFFROAD_KMH) -> np.ndarray:
    """
    Матрица времени в пути (минуты) (n_cells, n_cities): время по дорожной сети
    между ближайшими узлами + «подъезд» от ячейки и от города до своих узлов
    по прямой со скоростью offroad_kmh. Недостижимые пары -> inf.
    """
    import networkx as nx
    import osmnx as ox

    grid4326 = grid.to_crs(4326)
    cities4326 = cities.to_crs(4326)
    ccx = grid4326.geometry.centroid.x.to_numpy()
    ccy = grid4326.geometry.centroid.y.to_numpy()
    cell_nodes = np.asarray(ox.distance.nearest_nodes(G, ccx, ccy))
    city_nodes = np.asarray(
        ox.distance.nearest_nodes(G, cities4326.geometry.x.to_numpy(), cities4326.geometry.y.to_numpy())
    )

    # off-road подъезд (в метрах -> минуты) считаем в проекции grid.crs (метры)
    import geopandas as gpd

    nodes = list(G.nodes)
    idx = {n: k for k, n in enumerate(nodes)}
    node_pts = gpd.GeoSeries(
        [Point(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodes], crs="EPSG:4326"
    ).to_crs(grid.crs)
    nx_arr = node_pts.x.to_numpy()
    ny_arr = node_pts.y.to_numpy()

    cents = grid.geometry.centroid
    cell_idx = np.array([idx[n] for n in cell_nodes])
    off_cell_min = (
        np.hypot(cents.x.to_numpy() - nx_arr[cell_idx], cents.y.to_numpy() - ny_arr[cell_idx])
        / 1000.0 / offroad_kmh * 60.0
    )

    cpx = cities.geometry.x.to_numpy()
    cpy = cities.geometry.y.to_numpy()
    city_idx = np.array([idx[n] for n in city_nodes])
    off_city_min = (
        np.hypot(cpx - nx_arr[city_idx], cpy - ny_arr[city_idx]) / 1000.0 / offroad_kmh * 60.0
    )

    # неориентированный вид: для доступности направление одностороннего движения
    # несущественно, а связность графа выше (иначе из города по out-рёбрам
    # достижима лишь часть сети).
    UG = ox.convert.to_undirected(G)

    n_cells, n_cities = len(grid), len(cities)
    out = np.full((n_cells, n_cities), np.inf)
    for j in range(n_cities):
        lengths = nx.single_source_dijkstra_path_length(UG, int(city_nodes[j]), weight="travel_time")
        sec = np.full(len(nodes), np.inf)
        for n, s in lengths.items():
            sec[idx[n]] = s
        net_min = sec[cell_idx] / 60.0
        out[:, j] = net_min + off_cell_min + off_city_min[j]
    return out


def distribute_decay_network(
    grid,
    X_region: float,
    cities,
    net_min: np.ndarray,
    sigma_min: float,
    kind: str = "exp",
    beta: float = 1.0,
) -> np.ndarray:
    """Разнести X_region по ячейкам, используя время в пути по сети (v1-net)."""
    w = gravity_weights(net_min, cities["population"].to_numpy(), sigma_min, kind, beta)
    total = w.sum()
    if not np.isfinite(total) or total <= 0:
        w = np.ones(len(w))
        total = w.sum()
    return X_region * w / total


def gini(values) -> float:
    """Коэффициент Джини (та же формула, что в ноутбуках проекта)."""
    x = np.sort(np.asarray(values, dtype=float))
    n = len(x)
    if n == 0:
        return 0.0
    cumsum = np.cumsum(x)
    if cumsum[-1] <= 0:
        return 0.0
    return float((n + 1 - 2 * np.sum(cumsum) / cumsum[-1]) / n)


def top_share(values, frac: float = 0.10) -> float:
    """Доля суммарного объёма, приходящаяся на верхние frac ячеек."""
    x = np.sort(np.asarray(values, dtype=float))[::-1]
    total = x.sum()
    if total <= 0:
        return 0.0
    k = max(1, int(round(len(x) * frac)))
    return float(x[:k].sum() / total)
