"""
Главный пайплайн системы аналитических масок (11 масок).
"""

import numpy as np
from src.masks import (
    baseline, worldpop, regression, distance_to_city, distance_to_center,
    territory, road_network, railway, road_traveltime, power, science, composition,
    weighting,
)


class UndefinedMaskSet(ValueError):
    """Ни одна маска набора не определена для этого показателя (нечем считать)."""


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
    'science': science.compute_science_mask,
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
        elif name == 'science':
            result[name] = science.compute_science_mask(grid)
    return result


def rate_base_values(grid, rate_base):
    """Знаменатель удельного показателя по ячейкам: 'population' — население,
    'area' — площадь ячейки (см. composition.distribute_rate)."""
    column = {'population': 'population', 'area': 'area_km2'}.get(rate_base)
    if column is None:
        raise ValueError(f"неизвестный rate_base: {rate_base}")
    if column not in grid:
        raise ValueError(f"в сетке нет столбца {column} для rate_base={rate_base}")
    return np.nan_to_num(np.asarray(grid[column], dtype=float), nan=0.0)


def run_pipeline(grid, regional_value, indicator_code, mask_weights=None, masks_to_use=None,
                 exclusion_masks=None, smoothing_alpha=None, params=None, rate_base=None):
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

    # regression = pop^эластичность: без подобранной эластичности (показатели
    # indicator_type: general) маска не определена. resolve_weights её и не
    # берёт, но явный masks_to_use (ablation-пресеты etl/config.yaml) перечисляет
    # маски жёстко — выкидываем её и перенормируем остаток, чтобы Σ весов = 1.
    if 'regression' in (masks_to_use or ()) and indicator_code not in regression.ELASTICITIES:
        masks_to_use = [m for m in masks_to_use if m != 'regression']
        if not masks_to_use:
            raise UndefinedMaskSet(
                f"набор масок пуст для {indicator_code}: только regression, "
                "а эластичность не подобрана")
        if mask_weights:
            mask_weights = {k: v for k, v in mask_weights.items() if k != 'regression'}
            total = sum(mask_weights.values())
            mask_weights = {k: v / total for k, v in mask_weights.items()} if total else None

    masks = compute_all_masks(grid, indicator_code, masks_to_use, params)
    composed = composition.weighted_sum_composition(masks, mask_weights)

    if exclusion_masks:
        composed = composition.multiplicative_gating(composed, exclusion_masks)
    if smoothing_alpha is not None:
        composed = composition.entropy_smoothing(composed, alpha=smoothing_alpha)

    # Пики здесь не считаются: живой путь — percentile_cont в SQL
    # (_AGG_SQL/_PEAK_POINTS_SQL в apps/api/app/main.py).
    #
    # rate_base задан -> показатель удельный (ставка), у него сохраняется
    # взвешенное среднее, а не сумма: ставка не аддитивна (см. миграцию 0013 и
    # composition.distribute_rate). sum_preserved в обоих случаях означает
    # «инвариант выполнен» — свой у каждого типа показателя.
    if rate_base:
        base = rate_base_values(grid, rate_base)
        values = composition.distribute_rate(composed, regional_value, base)
        invariant_ok = composition.check_mean_preservation(values, base, regional_value)
    else:
        values = composition.distribute_value(composed, regional_value)
        invariant_ok = composition.check_sum_preservation(values, regional_value)

    return {
        'masks': masks,
        'composed': composed,
        'values': values,
        'sum_preserved': invariant_ok,
        'regional_value': regional_value,
        'actual_sum': float(values.sum()),
    }
