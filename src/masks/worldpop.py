"""
WorldPop маска (дасиметрическое распределение по плотности населения).
"""

import numpy as np


def compute_worldpop_mask(grid, population_col='population'):
    pop = grid[population_col].fillna(0).values
    if pop.max() > 0:
        weights = pop / pop.max()
    else:
        weights = np.zeros_like(pop, dtype=float)
    return weights


def distribute_value(grid, regional_value, population_col='population'):
    weights = compute_worldpop_mask(grid, population_col)
    total = weights.sum()
    if total == 0:
        return np.full(len(grid), regional_value / len(grid))
    return regional_value * weights / total


MASK_DESCRIPTION = {
    "name": "worldpop_mask",
    "title": "Население (WorldPop)",
    "source": "WorldPop Russia 2020, растр плотности населения 100 м",
    "signal": "Плотность населения как универсальный прокси человеческой "
              "и экономической активности",
    "influence_type": "повышающий",
    "formula": "вес = сумма_населения_в_ячейке (нормировано в 0-1)",
    "applicability": ["промышленность", "услуги", "торговля", "инновации"],
    "limitations": "Данные 2020 года, могут устаревать; не различает работающих "
                   "и постоянных жителей; в редкозаселённых регионах даёт близкие "
                   "к нулю значения на большой площади",
}
