"""
Оффлайн-расчёт веса маски доступности по времени в пути (road_traveltime).

Требует osmnx/networkx (поэтому отдельно от slim-ETL). Для каждого региона с
кэшированным графом считает время в пути от ячейки до ближайшего города и пишет
data/processed/traveltime_<slug>.csv (cell_code, weight 0..1). ETL грузит готовое.

Запуск (в venv с osmnx):
    python scripts/compute_traveltime.py
"""

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.spatial_decay as sd
from src.masks.road_traveltime import SIGMA_MIN

BUFFER_KM = 30.0  # города в пределах буфера от сетки (центр-в-дырке сохраняется)

# (slug в БД, ключ spatial_decay, файл сетки-фич)
REGIONS = [
    ("moscow", "moscow", "data/processed/grid_moscow_1km_features.gpkg"),
    ("yakutia", "yakutia", "data/processed/grid_yakutia_center_1km_features.gpkg"),
    ("krasnodar", "krasnodar", "data/processed/grid_krasnodar_1km_features.gpkg"),
]


def main():
    for slug, key, grid_path in REGIONS:
        graph = sd.GRAPH_DIR / f"roads_{key}.graphml"
        if not graph.exists():
            print(f"[{slug}] нет графа {graph.name} — пропуск", flush=True)
            continue
        print(f"[{slug}] чтение сетки…", flush=True)
        grid = gpd.read_file(grid_path).to_crs(3857).reset_index(drop=True)
        cell_code = grid.index.astype(str)

        cities = sd.cities_gdf(key, crs="EPSG:3857", grid=grid, buffer_km=BUFFER_KM)
        print(f"[{slug}] городов в буфере: {len(cities)}; граф из кэша…", flush=True)
        G = sd.load_road_graph(key)

        print(f"[{slug}] время в пути по сети ({len(grid)} ячеек)…", flush=True)
        net = sd.network_minutes(grid, cities, G)
        t = net.min(axis=1)                       # минуты до ближайшего города
        w = np.exp(-t / SIGMA_MIN)                # недостижимые (inf) -> 0
        w = np.where(np.isfinite(w), w, 0.0)
        m = w.max()
        if m > 0:
            w = w / m

        out_slug = "yakutia" if slug == "yakutia" else slug
        out = Path(f"data/processed/traveltime_{out_slug}.csv")
        pd.DataFrame({"cell_code": cell_code, "weight": w}).to_csv(out, index=False)
        reach = float(np.isfinite(t).mean())
        print(f"[{slug}] -> {out}  достижимо={reach:.2f}  w(avg)={w.mean():.3f}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
