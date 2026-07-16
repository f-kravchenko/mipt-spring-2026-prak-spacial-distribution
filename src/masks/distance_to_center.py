"""
Маска близости к региональному центру.
"""

import numpy as np


def compute_center_mask(grid, distance_col='dist_to_center_km', sigma=30.0):
    d = grid[distance_col].fillna(grid[distance_col].max()).values
    weights = np.exp(-d / sigma)
    if weights.max() > 0:
        weights = weights / weights.max()
    return weights


MASK_DESCRIPTION = {
    "name": "distance_to_center_mask",
    "title": "Близость к центру региона",
    "source": "Расстояние до административного центра региона",
    "signal": "Близость к региональной столице как прокси управленческой, "
              "финансовой и административной активности",
    "influence_type": "повышающий",
    "formula": "вес = e^(-d/σ), d — км до центра региона, σ = 30 км",
    "applicability": ["государственное управление", "финансы и страхование",
                      "услуги", "торговля"],
    "limitations": "Не учитывает другие крупные города в регионе; в редкозаселённых "
                   "регионах затухание слишком плавное относительно реальной структуры",
}
