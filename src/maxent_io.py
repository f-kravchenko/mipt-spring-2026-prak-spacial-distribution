"""Загрузка и подготовка сеток для метода максимальной энтропии."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
from rasterstats import zonal_stats
from shapely.geometry import Point, box as shp_box


CITY_POINTS_WGS84: dict[str, dict[str, tuple[float, float]]] = {
    "mo": {
        "Москва": (37.6173, 55.7558),
        "Балашиха": (37.9384, 55.7964),
        "Подольск": (37.5447, 55.4297),
        "Химки": (37.4448, 55.8970),
        "Мытищи": (37.7295, 55.9117),
        "Люберцы": (37.9534, 55.6760),
        "Электросталь": (38.4445, 55.7847),
        "Коломна": (38.7544, 55.0794),
        "Серпухов": (37.4216, 54.9156),
    },
    "kk": {
        "Краснодар": (38.9753, 45.0355),
        "Сочи": (39.7233, 43.5853),
        "Новороссийск": (37.7615, 44.7239),
        "Армавир": (41.1289, 44.9892),
        "Анапа": (37.3158, 44.8946),
        "Геленджик": (38.0699, 44.5622),
        "Туапсе": (39.0779, 44.0974),
        "Ейск": (38.2766, 46.7104),
        "Тихорецк": (40.1283, 45.8536),
    },
    "ya": {
        "Якутск": (129.7322, 62.0355),
        "Нерюнгри": (124.7125, 56.6609),
        "Мирный": (113.9667, 62.5353),
        "Алдан": (125.3889, 58.6031),
    },
}


@dataclass(frozen=True)
class RegionConfig:
    key: str
    title: str
    grid_path: Path
    features_path: Path
    border_path: Path
    pop_raster: Path
    x_otgr: float
    geocode: str
    regional_center: tuple[float, float]  # lon, lat (WGS84)


DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"
CITIES_DIR = DATA_DIR / "cities"

REGIONS: dict[str, RegionConfig] = {
    "mo": RegionConfig(
        key="mo",
        title="Московская область",
        grid_path=DATA_DIR / "grid_moscow_1km.gpkg",
        features_path=DATA_DIR / "grid_moscow_1km_features.gpkg",
        border_path=DATA_DIR / "border_mo.gpkg",
        pop_raster=DATA_DIR / "pop_mo_z.tif",
        x_otgr=209.0,
        geocode="Moscow Oblast, Russia",
        regional_center=(37.6173, 55.7558),
    ),
    "kk": RegionConfig(
        key="kk",
        title="Краснодарский край",
        grid_path=DATA_DIR / "grid_krasnodar_1km.gpkg",
        features_path=DATA_DIR / "grid_krasnodar_1km_features.gpkg",
        border_path=DATA_DIR / "border_krasnodar.gpkg",
        pop_raster=DATA_DIR / "pop_kk_z.tif",
        x_otgr=59.7,
        geocode="Krasnodar Krai, Russia",
        regional_center=(38.9753, 45.0355),
    ),
    "ya": RegionConfig(
        key="ya",
        title="Якутия (центр)",
        grid_path=DATA_DIR / "grid_yakutia_center_1km.gpkg",
        features_path=DATA_DIR / "grid_yakutia_center_1km_features.gpkg",
        border_path=DATA_DIR / "border_ya_center.gpkg",
        pop_raster=DATA_DIR / "pop_ya_z.tif",
        x_otgr=11.2,
        geocode="Sakha Republic, Russia",
        regional_center=(129.7322, 62.0355),
    ),
}


def _require(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"Нет файла {label}: {path}\n"
            "Сначала выполните 02_grid.ipynb (сетка, WorldPop) и 03_worldpop.ipynb (растры)."
        )


def _log(msg: str) -> None:
    print(msg, flush=True)


def _city_xy(cities: gpd.GeoDataFrame) -> np.ndarray:
    geoms = cities.geometry
    if geoms.geom_type.eq("Point").all():
        return np.column_stack([geoms.x.to_numpy(), geoms.y.to_numpy()])
    reps = geoms.representative_point()
    return np.column_stack([reps.x.to_numpy(), reps.y.to_numpy()])


def _min_dist_to_cities_km(centroids: gpd.GeoSeries, cities: gpd.GeoDataFrame) -> np.ndarray:
    city_xy = _city_xy(cities)
    cell_xy = np.column_stack([centroids.x.to_numpy(), centroids.y.to_numpy()])
    diff = cell_xy[:, None, :] - city_xy[None, :, :]
    return np.sqrt((diff * diff).sum(axis=2)).min(axis=1) / 1000.0


def attach_population(grid: gpd.GeoDataFrame, pop_raster: Path, region_key: str = "") -> gpd.GeoDataFrame:
    grid = grid.copy()
    _log(f"  [{region_key}] zonal_stats: {len(grid):,} ячеек (обычно 10–40 мин)...")
    grid_4326 = grid.to_crs(epsg=4326)
    stats = zonal_stats(
        grid_4326.geometry,
        str(pop_raster),
        stats=["sum"],
        nodata=-99999,
    )
    grid["population"] = [s["sum"] if s and s["sum"] is not None else 0.0 for s in stats]
    _log(f"  [{region_key}] население: pop>0 = {(grid['population'] > 0).sum():,}")
    return grid


def _ensure_border(region: RegionConfig) -> gpd.GeoDataFrame:
    if region.border_path.exists():
        return gpd.read_file(region.border_path)

    if region.key == "ya":
        ya_full = ox.geocode_to_gdf(region.geocode).to_crs(epsg=3857)
        center = gpd.GeoSeries([Point(region.regional_center)], crs="EPSG:4326").to_crs(
            epsg=3857
        )[0]
        half = 200_000
        bbox = shp_box(
            center.x - half,
            center.y - half,
            center.x + half,
            center.y + half,
        )
        border = gpd.overlay(
            ya_full,
            gpd.GeoDataFrame(geometry=[bbox], crs="EPSG:3857"),
            how="intersection",
        )
    else:
        border = ox.geocode_to_gdf(region.geocode).to_crs(epsg=3857)

    region.border_path.parent.mkdir(parents=True, exist_ok=True)
    border.to_file(region.border_path, driver="GPKG")
    return border


def _load_cities(region: RegionConfig, grid_crs) -> gpd.GeoDataFrame:
    CITIES_DIR.mkdir(parents=True, exist_ok=True)
    cities_path = CITIES_DIR / f"cities_{region.key}.gpkg"
    if cities_path.exists():
        return gpd.read_file(cities_path)

    if region.key in CITY_POINTS_WGS84:
        points = CITY_POINTS_WGS84[region.key]
        geoms = [Point(lon, lat) for lon, lat in points.values()]
        cities = gpd.GeoDataFrame(geometry=geoms, crs="EPSG:4326").to_crs(grid_crs)
    else:
        border = _ensure_border(region).to_crs(epsg=4326)
        bbox = border.total_bounds
        tags = {"place": ["city", "town"]}
        cities = ox.features_from_bbox(bbox=bbox, tags=tags)
        cities = cities[cities.geometry.type.isin(["Point", "Polygon", "MultiPolygon"])].copy()
        cities = cities.to_crs(grid_crs)

    cities[["geometry"]].to_file(cities_path, driver="GPKG")
    return cities


def attach_distances(grid: gpd.GeoDataFrame, region: RegionConfig) -> gpd.GeoDataFrame:
    grid = grid.copy()
    centroids = grid.geometry.centroid

    center = gpd.GeoSeries(
        [Point(region.regional_center)], crs="EPSG:4326"
    ).to_crs(grid.crs)[0]
    grid["dist_to_center_km"] = centroids.distance(center) / 1000

    if region.key == "mo":
        moscow = gpd.GeoSeries([Point(37.6173, 55.7558)], crs="EPSG:4326").to_crs(grid.crs)[0]
        grid["dist_to_moscow_km"] = centroids.distance(moscow) / 1000

    cities = _load_cities(region, grid.crs)
    _log(f"  [{region.key}] расстояния до {len(cities)} городов...")
    grid["dist_to_city_km"] = _min_dist_to_cities_km(centroids, cities)
    if "centroid" in grid.columns:
        grid = grid.drop(columns=["centroid"])
    return grid


def build_features_grid(region: RegionConfig, force: bool = False) -> gpd.GeoDataFrame:
    """Собрать сетку с population и расстояниями; сохранить в features_path."""
    if region.features_path.exists() and not force:
        return gpd.read_file(region.features_path)

    _require(region.grid_path, "сетка")
    _require(region.pop_raster, "растр WorldPop")
    _log(f"[{region.key}] загрузка сетки {region.grid_path.name}...")
    grid = gpd.read_file(region.grid_path)
    _ensure_border(region)
    grid = attach_population(grid, region.pop_raster, region_key=region.key)
    grid = attach_distances(grid, region)
    _log(f"[{region.key}] сохранение {region.features_path.name}...")

    region.features_path.parent.mkdir(parents=True, exist_ok=True)
    out = grid.drop(columns=["centroid"], errors="ignore")
    out.to_file(region.features_path, driver="GPKG")
    _log(f"[{region.key}] готово.")
    return out


def load_region(region_key: str, force: bool = False) -> tuple[gpd.GeoDataFrame, RegionConfig]:
    region = REGIONS[region_key]
    grid = build_features_grid(region, force=force)
    return grid, region
