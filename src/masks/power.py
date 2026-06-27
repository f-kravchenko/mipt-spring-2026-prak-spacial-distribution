"""
Маска близости к ЛЭП (§5.3).

Магистральные линии электропередачи (OSM power=line) как прокси доступной
электрической мощности — важной для промышленности и энергоёмких производств.
Вес = затухание по расстоянию до ближайшей линии. Расстояния считаются оффлайн
(osmnx, см. scripts/compute_infra_masks) → data/processed/power_<slug>.csv.
"""

import numpy as np

SIGMA_KM = 8.0


def compute_power_mask(grid, col="power_access"):
    if col not in grid.columns:
        return np.zeros(len(grid))
    return grid[col].fillna(0).to_numpy()


MASK_DESCRIPTION = {
    "name": "power_lines_mask",
    "title": "ЛЭП (электросети)",
    "source": "Линии электропередачи OSM: power=line",
    "signal": "Доступ к электрической мощности — близость к магистральным ЛЭП "
              "как прокси возможности разместить энергоёмкое производство",
    "influence_type": "повышающий",
    "formula": "norm( e^(−d/σ) ), d — км до ближайшей ЛЭП, σ = 8 км",
    "applicability": ["промышленность", "энергоёмкие производства", "добыча"],
    "limitations": "Без напряжения/мощности линии; полнота тегов OSM; близость "
                   "≠ доступ к подключению",
}
