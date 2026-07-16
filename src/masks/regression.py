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
    "source": "Логарифмическая регрессия на 87 регионах России (Росстат, 2023)",
    "signal": "Концентрация показателя в городах с учётом эффекта агломераций: "
              "чем выше эластичность, тем сильнее показатель стягивается в крупные центры",
    "influence_type": "повышающий",
    "formula": "вес = население_ячейки^эластичность (нормировано в 0-1). "
               "Эластичность подобрана отдельно для каждого показателя",
    "applicability": ["отгрузка обрабатывающей промышленности",
                      "внутренние затраты на НИОКР",
                      "объём инновационных товаров"],
    "limitations": "Модель обучена только на трёх показателях; периферия получает "
                   "минимальный вес; регионы с нетипичной экономикой (Дальний Восток, "
                   "Северный Кавказ) описываются хуже",
}
