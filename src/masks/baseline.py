"""
Baseline маска (равномерное распределение по площади ячеек).

Нижняя граница качества — эталон для сравнения с осмысленными методами.
"""

import numpy as np


def compute_baseline_mask(grid, area_col='area_km2'):
    """
    Вес ячейки пропорционален её площади.
    Нормирован в 0-1.
    """
    area = grid[area_col].fillna(0).values
    if area.max() > 0:
        weights = area / area.max()
    else:
        weights = np.ones_like(area, dtype=float)
    return weights


def distribute_value(grid, regional_value, area_col='area_km2'):
    """
    Распределяет региональное значение равномерно по площади.
    Сумма по ячейкам = regional_value.
    """
    weights = compute_baseline_mask(grid, area_col)
    total = weights.sum()
    if total == 0:
        return np.full(len(grid), regional_value / len(grid))
    return regional_value * weights / total


MASK_DESCRIPTION = {
    "name": "baseline_mask",
    "source": "Геометрия сетки",
    "signal": "Эталон равномерного распределения",
    "influence_type": "нейтральный",
    "formula": "вес = площадь_ячейки (нормировано в 0-1)",
    "applicability": ["все показатели как baseline"],
    "limitations": "Не учитывает никакую пространственную неоднородность. Используется только для сравнения",
}
