"""
Маска близости к городам.
Принцип: чем ближе к ближайшему городу, тем выше вес.
"""

import numpy as np


def compute_distance_mask(grid, distance_col='dist_to_city_km', sigma=10.0):
    d = grid[distance_col].fillna(grid[distance_col].max()).values
    weights = np.exp(-d / sigma)
    if weights.max() > 0:
        weights = weights / weights.max()
    return weights


MASK_DESCRIPTION = {
    "name": "distance_to_city_mask",
    "title": "Близость к городам",
    "source": "Расстояние до ближайшего города (populated place) из OSM",
    "signal": "Близость к городам как прокси агломерационного эффекта: доступ "
              "к рынкам сбыта, трудовым ресурсам и городской инфраструктуре",
    "influence_type": "повышающий",
    "formula": "вес = e^(-d/σ), d — км до ближайшего города, σ = 10 км",
    "applicability": ["промышленность", "услуги", "торговля", "инновации"],
    "limitations": "Не учитывает размер города (Москва и посёлок с 5 тыс. жителей "
                   "формально равнозначны); зависит от полноты OSM",
}
