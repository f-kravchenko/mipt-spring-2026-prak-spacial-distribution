"""
v1 — метод затухания (gravity / distance-decay) для разнесения регионального
показателя Росстата по ячейкам сетки.

Идея: вес ячейки i — это потенциал доступности к городам,
    w_i = sum_j  pop_j * f(d_ij),
где d_ij — расстояние от центроида ячейки до города j (км), pop_j — население
города, f — ядро затухания. Затем X_region разносится пропорционально весам.

Это обобщает прежний baseline-вариант `1/(d+1)`:
- учитывается население городов (Москва и пригород вносят разный вклад),
- сравниваются три формы ядра (exp / gauss / power),
- параметр ядра подбирается по корреляции веса с растром населения (WorldPop).

Поддерживаемые ядра (см. `decay_kernel`):
- "exp"   : exp(-d / sigma)            — sigma в км, масштаб затухания
- "gauss" : exp(-d^2 / (2 sigma^2))    — sigma в км
- "power" : 1 / (d + 1)^sigma          — sigma безразмерный показатель степени
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import Point

# Города пилотных регионов: имя -> (lon, lat, население).
# Население — округлённые оценки Росстата (перепись 2021 / текущие оценки).
# Первый город в каждом списке считается "центром" региона.
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


def cities_gdf(region_key: str, crs="EPSG:3857"):
    """GeoDataFrame городов региона с колонками city, population (в нужной CRS)."""
    import geopandas as gpd

    rows = CITIES[region_key]
    gdf = gpd.GeoDataFrame(
        {
            "city": list(rows.keys()),
            "population": [v[2] for v in rows.values()],
        },
        geometry=[Point(v[0], v[1]) for v in rows.values()],
        crs="EPSG:4326",
    )
    return gdf.to_crs(crs)


def city_distances_km(grid, cities) -> np.ndarray:
    """
    Матрица расстояний (n_cells, n_cities) в км между центроидами ячеек и
    городами. Обе геометрии должны быть в одной проекционной CRS (метры).
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


def gravity_weights(
    dist_km: np.ndarray,
    populations: np.ndarray,
    sigma: float,
    kind: str = "exp",
    use_population: bool = True,
) -> np.ndarray:
    """
    Потенциал доступности ячеек: w_i = sum_j pop_j * f(d_ij).
    При use_population=False население городов игнорируется (w_i = sum_j f(d_ij)).
    """
    f = decay_kernel(dist_km, sigma, kind)
    if use_population:
        return f @ np.asarray(populations, dtype=float)
    return f.sum(axis=1)


def distribute_decay(
    grid,
    X_region: float,
    region_key: str,
    sigma: float,
    kind: str = "exp",
    use_population: bool = True,
) -> np.ndarray:
    """Разнести X_region по ячейкам сетки методом затухания (v1)."""
    cities = cities_gdf(region_key, grid.crs)
    dist = city_distances_km(grid, cities)
    w = gravity_weights(
        dist, cities["population"].to_numpy(), sigma, kind, use_population
    )
    total = w.sum()
    if not np.isfinite(total) or total <= 0:
        w = np.ones(len(w))
        total = w.sum()
    return X_region * w / total


def tune_sigma(
    grid,
    region_key: str,
    kind: str,
    sigmas,
    use_population: bool = True,
) -> tuple[tuple[float, float], list[tuple[float, float]]]:
    """
    Подобрать sigma, максимизируя ранговую корреляцию (Spearman) веса затухания
    с растром населения WorldPop (`grid['population']`) как прокси истины.

    Возвращает ((best_sigma, best_rho), [(sigma, rho), ...]).
    """
    from scipy.stats import spearmanr

    cities = cities_gdf(region_key, grid.crs)
    dist = city_distances_km(grid, cities)
    pops = cities["population"].to_numpy()
    target = grid["population"].to_numpy().astype(float)

    results: list[tuple[float, float]] = []
    best: tuple[float, float] | None = None
    for s in sigmas:
        w = gravity_weights(dist, pops, s, kind, use_population)
        rho = spearmanr(w, target).correlation
        rho = 0.0 if not np.isfinite(rho) else float(rho)
        results.append((float(s), rho))
        if best is None or rho > best[1]:
            best = (float(s), rho)
    assert best is not None
    return best, results


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
