"""
Маска типа территории (§5.5) по OSM landuse.

Классы землепользования различаются по пригодности для хоз. деятельности:
промзона/транспорт/город — высоко, поле — средне, лес/вода — низко/исключение.
Ячейка получает балл класса, в который попадает её центроид (point-in-polygon).
Расчёт оффлайн (osmnx, см. scripts/compute_territory) → data/processed/
territory_<slug>.csv; ETL грузит готовый вес (slim-образ без osmnx).
"""

import numpy as np


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
    "formula": "балл класса землепользования центроида ячейки: промзона 1.0, "
               "транспорт 0.8, город 0.7, поле 0.35, лес 0.1, вода 0.0",
    "applicability": ["промышленность", "строительство", "склады", "услуги"],
    "limitations": "Полнота OSM landuse неравномерна (вне тегированных полигонов "
                   "вес 0); класс по центроиду, без долей площади в ячейке",
}
