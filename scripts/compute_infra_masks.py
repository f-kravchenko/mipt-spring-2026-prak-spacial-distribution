"""
Оффлайн-расчёт инфраструктурных масок (§5.3/5.4): ЖД-узлы и ЛЭП.

Качает из OSM (mail.ru Overpass — рабочее зеркало для РФ) ЖД-станции/узлы
(railway=station|halt|yard) и линии электропередачи (power=line), считает для
каждой ячейки расстояние до ближайшего объекта и вес exp(-d/σ). Пишет
data/processed/{railway,power}_<slug>.csv (cell_code, weight). ETL грузит готовое
(в slim-образе нет osmnx).

Запуск (в venv с osmnx):
    python scripts/compute_infra_masks.py
"""

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.spatial_decay as sd

ox.settings.use_cache = True
ox.settings.cache_folder = "cache/osmnx"
ox.settings.overpass_url = "https://maps.mail.ru/osm/tools/overpass/api"  # зеркало РФ
ox.settings.overpass_rate_limit = False
ox.settings.requests_timeout = 300

SIGMA_RAIL_KM = 10.0   # доступность ЖД-узлов
SIGMA_POWER_KM = 8.0   # близость к магистральным ЛЭП

# (slug, ключ spatial_decay, файл сетки)
REGIONS = [
    ("moscow", "moscow", "data/processed/grid_moscow_1km_features.gpkg"),
    ("yakutia", "yakutia", "data/processed/grid_yakutia_center_1km_features.gpkg"),
    ("krasnodar", "krasnodar", "data/processed/grid_krasnodar_1km_features.gpkg"),
]


def _weights_to_features(cents, feats, sigma_km):
    """Вес exp(-d/σ), нормированный 0..1, по расстоянию до ближайшего объекта."""
    if feats is None or len(feats) == 0:
        return np.zeros(len(cents))
    near = gpd.sjoin_nearest(cents[["geometry"]], feats[["geometry"]], distance_col="d")
    d = near["d"].groupby(near.index).min().reindex(cents.index).to_numpy()  # метры
    w = np.exp(-(d / 1000.0) / sigma_km)
    w = np.where(np.isfinite(w), w, 0.0)
    return w / w.max() if w.max() > 0 else w


def main():
    for slug, key, grid_path in REGIONS:
        print(f"[{slug}] сетка…", flush=True)
        grid = gpd.read_file(grid_path).to_crs(3857).reset_index(drop=True)
        cents = gpd.GeoDataFrame(geometry=grid.geometry.centroid, crs=3857)
        cell_code = grid.index.astype(str)
        poly = sd.region_polygon(key)

        try:
            rail = ox.features_from_polygon(poly, {"railway": ["station", "halt", "yard"]})
            rail = rail[rail.geometry.notna()].to_crs(3857)
            rail["geometry"] = rail.geometry.centroid  # полигоны узлов -> точки
            wr = _weights_to_features(cents, rail, SIGMA_RAIL_KM)
            pd.DataFrame({"cell_code": cell_code, "weight": wr}).to_csv(
                f"data/processed/railway_{slug}.csv", index=False)
            print(f"[{slug}] railway: {len(rail)} узлов, w(avg)={wr.mean():.3f}", flush=True)
        except Exception as e:
            print(f"[{slug}] railway FAIL: {type(e).__name__}: {str(e)[:100]}", flush=True)

        try:
            pw = ox.features_from_polygon(poly, {"power": "line"})
            pw = pw[pw.geometry.type.isin(["LineString", "MultiLineString"])].to_crs(3857)
            wp = _weights_to_features(cents, pw, SIGMA_POWER_KM)
            pd.DataFrame({"cell_code": cell_code, "weight": wp}).to_csv(
                f"data/processed/power_{slug}.csv", index=False)
            print(f"[{slug}] power: {len(pw)} линий, w(avg)={wp.mean():.3f}", flush=True)
        except Exception as e:
            print(f"[{slug}] power FAIL: {type(e).__name__}: {str(e)[:100]}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
