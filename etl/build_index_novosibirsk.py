"""
Пейлоад "Индекс качества городской среды" (Минстрой, 2024) для Новосибирской
области → GeoJSON для фронта.

Источник значений: data/index.pdf (пофасадный рейтинг городов РФ). Из него
взяты 14 городов Новосибирской области с их баллом за 2024 г.

"Виртуальные области" городов = ячейка диаграммы Вороного по 14 городам,
обрезанная ГРАНИЦЕЙ области И кругом радиуса R км вокруг самого города
(Вороной делит всю область, без ограничения ячейки тянутся на сотни км от
города — а показатель городской; круг прижимает область к городу, дальняя
степь остаётся незакрашенной). Граница берётся из уже поставляемого
apps/web/public/russia.geojson — отдельный источник границы не нужен.
Каждый полигон получает имя города и балл; фронт красит по баллу и
показывает имя при наведении.

Это не часть grid/masks-пайплайна (у показателя нет масок — таргет задаётся
прямо по городам), поэтому payload — статический файл, а не таблицы PostGIS.

Запуск:  python -m etl.build_index_novosibirsk [--radius-km 40]
"""

import argparse
import json
import math
import os

from shapely.affinity import scale
from shapely.geometry import MultiPoint, Point, shape
from shapely.ops import voronoi_diagram

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUSSIA = os.path.join(ROOT, "apps/web/public/russia.geojson")
OUT = os.path.join(ROOT, "apps/web/public/novosibirsk_index.geojson")
# Отдельный маленький файл с полигоном области — светлая подложка под
# кругами городов (весь russia.geojson тянуть в главную карту ради одного
# полигона незачем).
OUT_BORDER = os.path.join(ROOT, "apps/web/public/novosibirsk_border.geojson")
REGION_NAME = "Новосибирская область"

# 14 городов Новосибирской области, балл индекса за 2024 (data/index.pdf).
# lon/lat — центры городов (общеизвестные координаты; для "виртуальных"
# областей Вороного этой точности достаточно).
CITIES = [
    ("Новосибирск", 82.9346, 55.0084, 223),
    ("Бердск",      83.1018, 54.7583, 210),
    ("Обь",         82.6931, 54.9963, 216),
    ("Искитим",     83.3075, 54.6350, 179),
    ("Куйбышев",    78.3269, 55.4497, 200),
    ("Барабинск",   78.3439, 55.3506, 178),
    ("Карасук",     78.0403, 53.7317, 205),
    ("Татарск",     75.9836, 55.2144, 195),
    ("Купино",      76.9500, 54.3661, 180),
    ("Черепаново",  83.3733, 54.2206, 180),
    ("Болотное",    84.3894, 55.6717, 178),
    ("Каргат",      80.2831, 55.1936, 178),
    ("Чулым",       80.9600, 55.0900, 173),
    ("Тогучин",     84.4014, 55.2317, 171),
]


def region_polygon():
    data = json.load(open(RUSSIA, encoding="utf-8"))
    for f in data["features"]:
        if f["properties"].get("name") == REGION_NAME:
            return shape(f["geometry"])
    raise SystemExit(f"{REGION_NAME} не найдена в {RUSSIA}")


def km_circle(lon, lat, radius_km):
    """Круг радиуса radius_km вокруг точки в градусах WGS84. Буфер shapely
    планарный (в градусах = эллипс по километрам), поэтому строим круг по
    широте (1° ≈ 111.32 км) и растягиваем по долготе на 1/cos(lat) — тогда
    км-радиус одинаков во все стороны. Для масштаба области (десятки км)
    этого приближения достаточно, отдельная проекция не нужна."""
    r_deg = radius_km / 111.32
    circle = Point(lon, lat).buffer(r_deg, quad_segs=32)
    return scale(circle, xfact=1.0 / math.cos(math.radians(lat)), yfact=1.0,
                 origin=(lon, lat))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius-km", type=float, default=40.0,
                    help="радиус круга вокруг города, ограничивающего ячейку Вороного")
    radius_km = ap.parse_args().radius_km

    border = region_polygon()
    pts = [Point(lon, lat) for _, lon, lat, _ in CITIES]
    # envelope Вороного = bbox границы с запасом, чтобы крайние ячейки
    # полностью накрывали область до обрезки.
    cells = voronoi_diagram(MultiPoint(pts), envelope=border.buffer(1.0))

    features = []
    for name, lon, lat, score in CITIES:
        pt = Point(lon, lat)
        cell = next((c for c in cells.geoms if c.covers(pt)), None)
        if cell is None:
            raise SystemExit(f"нет ячейки Вороного для {name}")
        clipped = cell.intersection(border).intersection(km_circle(lon, lat, radius_km))
        if clipped.is_empty:
            continue
        features.append({
            "type": "Feature",
            "properties": {"name": name, "score": score, "lon": lon, "lat": lat},
            "geometry": clipped.__geo_interface__,
        })

    fc = {"type": "FeatureCollection", "features": features}
    json.dump(fc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)

    border_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": REGION_NAME},
         "geometry": border.__geo_interface__},
    ]}
    json.dump(border_fc, open(OUT_BORDER, "w", encoding="utf-8"), ensure_ascii=False)

    scores = [s for *_, s in CITIES]
    print(f"{len(features)} городов (R={radius_km:g} км) → {OUT}  "
          f"(балл {min(scores)}–{max(scores)})")
    print(f"граница области → {OUT_BORDER}")


if __name__ == "__main__":
    main()
