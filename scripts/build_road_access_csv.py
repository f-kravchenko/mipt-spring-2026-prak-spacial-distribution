#!/usr/bin/env python3
"""Собирает CSV доступности автодорог федерального/регионального значения (СР32/33).

ТЗ: доля населения не дальше 30 км от дороги соответствующего класса
(«30 мин езды»). Классы — прокси по OSM, как разведено в indicators.csv:
  СР32 федеральные  highway=motorway|trunk|primary
  СР33 региональные highway=primary|secondary
(primary входит в оба — так в разведке ТЗ, «сверка по перечням» дорог
недоступна).

Дорожная геометрия — экстракты Geofabrik по федеральным округам
(cache/geofabrik/*-latest.osm.pbf, ~3 ГБ): Overpass такие объёмы по стране
не отдаёт. Парсим osmium, вершины округляем до 0.05° (~5 км) и дедуплицируем
— для буфера 30 км этого достаточно.

Население — национальный WorldPop 1 км (data/raw/worldpop/…Aggregated.tif,
см. etl/clip_worldpop_novosibirsk). Пиксель относится к региону по центру
(rasterize), доля = сумма населения пикселей в 30 км от ближайшей вершины
дороги / всё население региона. Расстояние — по локальной проекции
(км/градус на широте пикселя, для 30-км буфера хватает).

Выход:
  data/raw/sr32_federal_road_access_by_region.csv — % в 30 км, регионы + РФ
  data/raw/sr33_regional_road_access_by_region.csv

Запуск:
    python -m scripts.build_road_access_csv
"""
import csv
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GEOJSON = ROOT / "apps" / "web" / "public" / "russia.geojson"
PBF_DIR = ROOT / "cache" / "geofabrik"
POP_TIF = ROOT / "data" / "raw" / "worldpop" / "rus_ppp_2020_1km_Aggregated.tif"
RAW = ROOT / "data" / "raw"
OUT = {"federal": RAW / "sr32_federal_road_access_by_region.csv",
       "regional": RAW / "sr33_regional_road_access_by_region.csv"}
FEDERAL = {"motorway", "trunk", "primary"}
REGIONAL = {"primary", "secondary"}
RANGE_KM = 30.0


def road_points():
    """Вершины дорог из pbf-экстрактов, округлённые до 0.05° и уникализированные.
    Кэш в cache/roads_points.npz — парсинг 3.8 ГБ pbf занимает ~7 минут."""
    cache = ROOT / "cache" / "roads_points.npz"
    if cache.exists():
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    import osmium

    pts = {"federal": set(), "regional": set()}

    class Handler(osmium.SimpleHandler):
        def way(self, w):
            hw = w.tags.get("highway")
            fed, reg = hw in FEDERAL, hw in REGIONAL
            if not (fed or reg):
                return
            for n in w.nodes:
                p = (round(n.lon, 2), round(n.lat, 2))  # ~0.5-1.1 км
                if fed:
                    pts["federal"].add(p)
                if reg:
                    pts["regional"].add(p)

    h = Handler()
    for pbf in sorted(PBF_DIR.glob("*-latest.osm.pbf")):
        h.apply_file(str(pbf), locations=True, idx="flex_mem")
        print(f"  {pbf.name}: federal {len(pts['federal']) :,} regional {len(pts['regional']) :,}", flush=True)
    out = {k: np.array(sorted(v)) for k, v in pts.items()}
    np.savez_compressed(cache, **out)
    return out


def sphere_xyz(lon, lat):
    """Единичная сфера: хорда × R ≈ длина дуги — без широтных искажений.
    (Плоская проекция с фиксированным cos(56°) врала на юге: Ставрополь
    получал 0% при федеральной трассе через край.)"""
    lo, la = np.radians(lon), np.radians(lat)
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]


def main():
    from rasterio.features import rasterize
    from scipy.spatial import cKDTree
    from shapely.geometry import shape

    roads = road_points()
    trees = {k: cKDTree(sphere_xyz(v[:, 0], v[:, 1])) for k, v in roads.items()}
    R_KM = 6371.0

    import rasterio
    with rasterio.open(POP_TIF) as src:
        vals = src.read(1).astype("float64")
        nd, transform, shape_ = src.nodata, src.transform, src.shape
    vals = np.where(~np.isfinite(vals) | (vals < 0) | (vals == nd), 0.0, vals)
    rows, cols = np.nonzero(vals > 0)
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e
    pop = vals[rows, cols]

    gj = json.load(open(GEOJSON, encoding="utf-8"))
    out_rows = {"federal": [], "regional": []}
    for f in gj["features"]:
        geom = shape(f["geometry"])
        mask = rasterize([(geom, 1)], out_shape=shape_, transform=transform,
                         fill=0, all_touched=False).astype(bool)
        sel = mask[rows, cols]
        if not sel.any():
            continue
        plon, plat, ppop = lon[sel], lat[sel], pop[sel]
        total = ppop.sum()
        xy = sphere_xyz(plon, plat)
        for kind, tree in trees.items():
            d, _ = tree.query(xy, k=1)
            share = round(float(ppop[d * R_KM <= RANGE_KM].sum() / total * 100), 1)
            out_rows[kind].append((f["properties"]["name"], share))
        print(f"  {f['properties']['name']}: "
              f"{out_rows['federal'][-1][1]}% / {out_rows['regional'][-1][1]}%", flush=True)

    for kind, rr in out_rows.items():
        rf = round(sum(v for _, v in rr) / len(rr), 1)  # среднее по регионам
        with open(OUT[kind], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["region", "value"])
            # имена регионов берём как в russia.geojson (короткие) — сводим к паркету
            from scripts.build_masts_csv import norm
            for name, v in sorted(((norm(n), v) for n, v in rr), key=lambda r: -r[1]):
                w.writerow([name, v])
            w.writerow(["Российская Федерация", rf])
        print(f"→ {OUT[kind].name}: {len(rr)} регионов, РФ={rf}")


if __name__ == "__main__":
    main()
