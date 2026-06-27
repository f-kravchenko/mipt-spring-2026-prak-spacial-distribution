"""
Регрессионная маска для пространственной дезагрегации показателей.
"""

import numpy as np

ELASTICITIES = {
    "Y477110236": 1.594,  # Объём инновационных товаров (R² 0.53)
    "Y477090007": 2.77,
    "Y477110039": 1.39,
}


def compute_regression_mask(grid, indicator_code, population_col='population'):
    if indicator_code not in ELASTICITIES:
        raise ValueError(f"Эластичность не подобрана для {indicator_code}")
    
    elasticity = ELASTICITIES[indicator_code]
    pop = grid[population_col].values
    weights = np.power(pop, elasticity, where=(pop > 0), out=np.zeros_like(pop, dtype=float))
    
    if weights.max() > 0:
        weights = weights / weights.max()
    
    return weights


def distribute_value(grid, regional_value, indicator_code, population_col='population'):
    weights = compute_regression_mask(grid, indicator_code, population_col)
    total = weights.sum()
    if total == 0:
        return np.full(len(grid), regional_value / len(grid))
    return regional_value * weights / total


MASK_DESCRIPTION = {
    "name": "regression_mask",
    "title": "Регрессия по населению",
    "source": "Регрессия на 87 регионах России (Росстат 2023)",
    "signal": "Концентрация показателя в городских центрах",
    "influence_type": "повышающий",
    "formula": "вес = население_ячейки^эластичность (нормировано в 0-1)",
    "applicability": list(ELASTICITIES.keys()),
}
