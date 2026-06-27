"""
Оффлайн-расчёт маски типа территории (§5.5) по OSM landuse.

Каждой ячейке присваивается «пригодность для хоз. деятельности» по классу
землепользования, в который попадает её центроид: промзона/транспорт/город —
высоко, поле — средне, лес/вода — низко. Качает один запрос landuse/natural/
aeroway на регион (зеркало mail.ru), классифицирует полигоны, делает
point-in-polygon и пишет data/processed/territory_<slug>.csv (cell_code, weight).

Запуск (venv с osmnx):
    python scripts/compute_territory.py
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
ox.settings.overpass_url = "https://maps.mail.ru/osm/tools/overpass/api"
ox.settings.overpass_rate_limit = False
ox.settings.requests_timeout = 300

# значение тега -> балл пригодности (0..1)
LANDUSE_SCORE = {
    "industrial": 1.00,
    "railway": 0.80,
    "residential": 0.70, "commercial": 0.70, "retail": 0.70,
    "farmland": 0.35, "meadow": 0.35, "orchard": 0.35, "vineyard": 0.35,
    "forest": 0.10,
    "reservoir": 0.0, "basin": 0.0,
}
NATURAL_SCORE = {"water": 0.0, "wood": 0.10}
AEROWAY_SCORE = {"aerodrome": 0.80}

TAGS = {
    "landuse": list(LANDUSE_SCORE),
    "natural": list(NATURAL_SCORE),
    "aeroway": list(AEROWAY_SCORE),
}

REGIONS = [
    ("moscow", "moscow", "data/processed/grid_moscow_1km_features.gpkg"),
    ("yakutia", "yakutia", "data/processed/grid_yakutia_center_1km_features.gpkg"),
    ("krasnodar", "krasnodar", "data/processed/grid_krasnodar_1km_features.gpkg"),
]


def _score(row):
    vals = [
        LANDUSE_SCORE.get(row.get("landuse")),
        NATURAL_SCORE.get(row.get("natural")),
        AEROWAY_SCORE.get(row.get("aeroway")),
    ]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def main():
    for slug, key, grid_path in REGIONS:
        print(f"[{slug}] сетка…", flush=True)
        grid = gpd.read_file(grid_path).to_crs(3857).reset_index(drop=True)
        cents = gpd.GeoDataFrame(geometry=grid.geometry.centroid, crs=3857)
        cell_code = grid.index.astype(str)
        poly = sd.region_polygon(key)
        try:
            f = ox.features_from_polygon(poly, TAGS)
            f = f[f.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()
            for col in ("landuse", "natural", "aeroway"):
                if col not in f.columns:
                    f[col] = None
            f["score"] = f.apply(_score, axis=1)
            f = f[f["score"].notna()].to_crs(3857)[["geometry", "score"]]
            print(f"[{slug}] landuse-полигонов: {len(f)}", flush=True)

            j = gpd.sjoin(cents[["geometry"]], f, predicate="within", how="left")
            w = j["score"].groupby(j.index).max().reindex(cents.index).fillna(0.0).to_numpy()
            pd.DataFrame({"cell_code": cell_code, "weight": w}).to_csv(
                f"data/processed/territory_{slug}.csv", index=False)
            print(f"[{slug}] -> territory_{slug}.csv  покрыто={np.mean(w>0):.2f}  "
                  f"w(avg)={w.mean():.3f}", flush=True)
        except Exception as e:
            print(f"[{slug}] FAIL: {type(e).__name__}: {str(e)[:120]}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
