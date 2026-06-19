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
    "source": "Расстояние до регионального центра",
    "signal": "Близость к столице как прокси активности",
    "influence_type": "повышающий",
    "formula": "exp(-d/sigma), sigma = 30 км",
    "applicability": ["административно-экономические показатели"],
    "limitations": "Не учитывает другие крупные города в регионе",
}
