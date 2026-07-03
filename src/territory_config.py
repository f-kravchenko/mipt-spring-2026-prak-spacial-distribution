"""
Загрузчик конфига маски типа территории (§5.5) — src/masks/territory_scores.yaml.

Общий модуль для двух мест, которые раньше дублировали баллы классов
землепользования по отдельности:
  - scripts/compute_territory.py (оффлайн-расчёт, venv с osmnx)
  - src/masks/territory.py (рантайм-описание маски, slim-образ)

Зависит только от PyYAML (лёгкая зависимость) — никакого osmnx/geopandas,
поэтому безопасно импортировать из slim-образа.
"""

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "masks" / "territory_scores.yaml"


def load_classes(path: Path = CONFIG_PATH) -> list[dict]:
    """Читает список классов землепользования из YAML-конфига."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["classes"]


def build_score_maps(classes: list[dict] | None = None):
    """
    -> (LANDUSE_SCORE, NATURAL_SCORE, AEROWAY_SCORE, TAGS)

    Используется в scripts/compute_territory.py вместо литеральных словарей.
    """
    classes = classes if classes is not None else load_classes()

    landuse_score: dict[str, float] = {}
    natural_score: dict[str, float] = {}
    aeroway_score: dict[str, float] = {}

    for c in classes:
        score = c["score"]
        tags = c.get("tags", {})
        for v in tags.get("landuse", []):
            landuse_score[v] = score
        for v in tags.get("natural", []):
            natural_score[v] = score
        for v in tags.get("aeroway", []):
            aeroway_score[v] = score

    tags_for_query = {
        "landuse": list(landuse_score),
        "natural": list(natural_score),
        "aeroway": list(aeroway_score),
    }

    return landuse_score, natural_score, aeroway_score, tags_for_query


def _fmt_score(score: float) -> str:
    """0.7 -> '0.7', 1.0 -> '1.0', 0.0 -> '0.0' (без лишних/недостающих нулей)."""
    s = f"{score:.2f}".rstrip("0").rstrip(".")
    return s if "." in s else f"{s}.0"


def build_formula(classes: list[dict] | None = None) -> str:
    """
    -> строка для MASK_DESCRIPTION["formula"] в src/masks/territory.py.

    Классы сортируются по убыванию балла — тот же порядок, что и в исходном
    тексте формулы ("промзона 1.0, транспорт 0.8, ... вода 0.0").
    """
    classes = classes if classes is not None else load_classes()
    ordered = sorted(classes, key=lambda c: -c["score"])
    parts = [f'{c["label"]} {_fmt_score(c["score"])}' for c in ordered]
    return "балл класса землепользования центроида ячейки: " + ", ".join(parts)
