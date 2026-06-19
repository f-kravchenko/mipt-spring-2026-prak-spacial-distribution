"""
Главный пайплайн системы аналитических масок (5 масок).
"""

import numpy as np
from src.masks import baseline, worldpop, regression, distance_to_city, distance_to_center, composition


AVAILABLE_MASKS = {
    'baseline': baseline.compute_baseline_mask,
    'worldpop': worldpop.compute_worldpop_mask,
    'regression': regression.compute_regression_mask,
    'distance_to_city': distance_to_city.compute_distance_mask,
    'distance_to_center': distance_to_center.compute_center_mask,
}


def compute_all_masks(grid, indicator_code, masks_to_use=None, params=None):
    if masks_to_use is None:
        masks_to_use = list(AVAILABLE_MASKS.keys())
    if params is None:
        params = {}
    
    result = {}
    for name in masks_to_use:
        if name == 'regression':
            result[name] = regression.compute_regression_mask(grid, indicator_code)
        elif name == 'worldpop':
            result[name] = worldpop.compute_worldpop_mask(grid)
        elif name == 'baseline':
            result[name] = baseline.compute_baseline_mask(grid)
        elif name == 'distance_to_city':
            sigma = params.get('city_sigma', 10.0)
            result[name] = distance_to_city.compute_distance_mask(grid, sigma=sigma)
        elif name == 'distance_to_center':
            sigma = params.get('center_sigma', 30.0)
            result[name] = distance_to_center.compute_center_mask(grid, sigma=sigma)
    return result


def run_pipeline(grid, regional_value, indicator_code, mask_weights=None, masks_to_use=None,
                 exclusion_masks=None, smoothing_alpha=None, params=None):
    masks = compute_all_masks(grid, indicator_code, masks_to_use, params)
    composed = composition.weighted_sum_composition(masks, mask_weights)
    
    if exclusion_masks:
        composed = composition.multiplicative_gating(composed, exclusion_masks)
    if smoothing_alpha is not None:
        composed = composition.entropy_smoothing(composed, alpha=smoothing_alpha)
    
    values = composition.distribute_value(composed, regional_value)
    sum_preserved = composition.check_sum_preservation(values, regional_value)
    
    return {
        'masks': masks,
        'composed': composed,
        'values': values,
        'sum_preserved': sum_preserved,
        'regional_value': regional_value,
        'actual_sum': float(values.sum()),
    }


PIPELINE_DESCRIPTION = {
    "stages": ["1. Маски", "2. Композиция", "3. Гейтирование", "4. Сглаживание", "5. Распределение", "6. Проверка"],
    "invariants": ["sum(cells) == regional_value"],
    "masks_count": 5,
}
