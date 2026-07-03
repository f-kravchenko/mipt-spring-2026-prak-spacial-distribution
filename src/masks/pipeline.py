"""
Главный пайплайн системы аналитических масок (10 масок).
"""

import numpy as np
from src.masks import (
    baseline, worldpop, regression, distance_to_city, distance_to_center,
    territory, road_network, railway, road_traveltime, power, composition,
    weighting,
)


AVAILABLE_MASKS = {
    'baseline': baseline.compute_baseline_mask,
    'worldpop': worldpop.compute_worldpop_mask,
    'regression': regression.compute_regression_mask,
    'distance_to_city': distance_to_city.compute_distance_mask,
    'distance_to_center': distance_to_center.compute_center_mask,
    'territory': territory.compute_territory_mask,
    'road_network': road_network.compute_road_network_mask,
    'railway': railway.compute_railway_mask,
    'road_traveltime': road_traveltime.compute_traveltime_mask,
    'power': power.compute_power_mask,
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
        elif name == 'territory':
            result[name] = territory.compute_territory_mask(grid)
        elif name == 'road_network':
            result[name] = road_network.compute_road_network_mask(grid)
        elif name == 'railway':
            result[name] = railway.compute_railway_mask(grid)
        elif name == 'road_traveltime':
            result[name] = road_traveltime.compute_traveltime_mask(grid)
        elif name == 'power':
            result[name] = power.compute_power_mask(grid)
    return result


def run_pipeline(grid, regional_value, indicator_code, mask_weights=None, masks_to_use=None,
                 exclusion_masks=None, smoothing_alpha=None, params=None):
    # Автоподбор весов (weighting.resolve_weights, §9 "обоснование весов")
    # включается ТОЛЬКО если не задано ни mask_weights, ни masks_to_use —
    # это случай "дай разумные веса по умолчанию под этот показатель".
    # Если masks_to_use передан явно (например, ablation-конфигурация
    # etl/config.yaml вида {masks: [baseline], weights: null} — "равные веса
    # между явно перечисленными масками", не автодефолт) — оставляем исходное
    # поведение composition.weighted_sum_composition(masks, None) как есть,
    # иначе ломаем ablation-пресеты вроде only_baseline/only_regression.
    if mask_weights is None and masks_to_use is None:
        mask_weights = weighting.resolve_weights(indicator_code)
        masks_to_use = list(mask_weights.keys())
    if params is None:
        params = {}

    masks = compute_all_masks(grid, indicator_code, masks_to_use, params)
    composed = composition.weighted_sum_composition(masks, mask_weights)

    if exclusion_masks:
        composed = composition.multiplicative_gating(composed, exclusion_masks)
    if smoothing_alpha is not None:
        composed = composition.entropy_smoothing(composed, alpha=smoothing_alpha)

    # Пики считаются на итоговом (гейтированном/сглаженном) суммарном слое —
    # после smoothing они уже учитывают контроль концентрации, а не только
    # сырую композицию. method/top_frac/z_thresh/neighbors — из params, чтобы
    # не плодить параметры run_pipeline; см. composition.detect_peaks.
    peaks = composition.detect_peaks(
        composed,
        method=params.get('peak_method', 'percentile'),
        top_frac=params.get('peak_top_frac', 0.05),
        z_thresh=params.get('peak_z_thresh', 2.0),
        neighbors=params.get('peak_neighbors'),
    )

    values = composition.distribute_value(composed, regional_value)
    sum_preserved = composition.check_sum_preservation(values, regional_value)

    return {
        'masks': masks,
        'composed': composed,
        'peaks': peaks,
        'values': values,
        'sum_preserved': sum_preserved,
        'regional_value': regional_value,
        'actual_sum': float(values.sum()),
    }


PIPELINE_DESCRIPTION = {
    "stages": ["1. Маски", "2. Композиция", "3. Гейтирование", "4. Сглаживание", "5. Распределение", "6. Проверка"],
    "invariants": ["sum(cells) == regional_value"],
    "masks_count": 10,
}
