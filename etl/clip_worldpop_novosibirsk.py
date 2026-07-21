"""
Вырезка растра населения WorldPop по Новосибирской области.

Тот же принцип, что у других регионов (см. src/maxent_io.py: pop_mo_z.tif и
пр. — национальный WorldPop, заранее обрезанный по региону). Идеально — не качать национальный файл РФ (1 км, 194 МБ) целиком, а оконно
прочитать bbox области через GDAL /vsicurl/ (range-запросы). Но сервер
WorldPop Range на GET не отдаёт («Range downloading not supported»), поэтому
источник качается разово и передаётся сюда через --src (или переменную
WORLDPOP_SRC). Результат — маленький data/processed/pop_nso_z.tif, из которого
ingest_index_novosibirsk.py берёт население по ячейкам.

Запуск:  python -m etl.clip_worldpop_novosibirsk --src /path/rus_ppp_2020_1km.tif
"""

import argparse
import os

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

import rasterio
from rasterio.windows import from_bounds

# 1 км агрегированный WorldPop по РФ (unconstrained, 2020).
URL = ("/vsicurl/https://data.worldpop.org/GIS/Population/"
       "Global_2000_2020_1km/2020/RUS/rus_ppp_2020_1km_Aggregated.tif")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/processed/pop_nso_z.tif")
# bbox Новосибирской области (из russia.geojson) + небольшой запас
BBOX = (75.0, 53.2, 85.2, 57.3)  # minlon, minlat, maxlon, maxlat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.environ.get("WORLDPOP_SRC", URL),
                    help="национальный растр (локальный файл или /vsicurl/URL)")
    src_path = ap.parse_args().src
    with rasterio.open(src_path) as src:
        win = from_bounds(*BBOX, transform=src.transform).round_offsets().round_lengths()
        data = src.read(1, window=win)
        profile = src.profile.copy()
        profile.update(height=data.shape[0], width=data.shape[1],
                       transform=src.window_transform(win), compress="lzw")
        with rasterio.open(OUT, "w", **profile) as dst:
            dst.write(data, 1)
    print(f"{data.shape[1]}×{data.shape[0]} пикс → {OUT}  "
          f"(nodata={profile.get('nodata')})")


if __name__ == "__main__":
    main()
