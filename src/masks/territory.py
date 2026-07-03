"""
Маска типа территории (§5.5) по OSM landuse.

Классы землепользования различаются по пригодности для хоз. деятельности:
промзона/транспорт/город — высоко, поле — средне, лес/вода — низко/исключение.
Ячейка получает балл класса, в который попадает её центроид (point-in-polygon).
Расчёт оффлайн (osmnx, см. scripts/compute_territory) → data/processed/
territory_<slug>.csv; ETL грузит готовый вес (slim-образ без osmnx).

Баллы классов — в src/masks/territory_scores.yaml, единственном источнике правды
(используется и здесь, и в scripts/compute_territory.py через
src/territory_config.py). Здесь veса не хардкодятся — только формула-описание
собирается из конфига, чтобы не расходиться с фактическими расчётами.
"""

import numpy as np

from src.territory_config import build_formula


def compute_territory_mask(grid, col="territory_score"):
    if col not in grid.columns:
        return np.zeros(len(grid))
    return grid[col].fillna(0).to_numpy()


MASK_DESCRIPTION = {
    "name": "territory_type_mask",
    "title": "Тип территории",
    "source": "OSM landuse/natural/aeroway: industrial, railway/aeroway, "
              "residential/commercial, farmland, forest/wood, water",
    "signal": "Тип территории как пригодность под экономическую активность: "
              "промзона и транспорт — высоко, поле — средне, лес и вода — низко",
    "influence_type": "повышающий",
    "formula": build_formula(),
    "applicability": ["промышленность", "строительство", "склады", "услуги"],
    "limitations": "Полнота OSM landuse неравномерна (вне тегированных полигонов "
                   "вес 0); класс по центроиду, без долей площади в ячейке",
}
