"""
Композиция масок: объединение нескольких аналитических слоёв в одну
итоговую тепловую карту распределения показателя.

Поддерживает два способа (по ТЗ):
1. Взвешенная сумма — линейная комбинация с коэффициентами значимости
2. Мультипликативное гейтирование — для исключающих факторов (вода, лес)

Опционально — энтропийное сглаживание для контроля концентрации.

Пики, линии концентрации и затухание считаются в SQL живого пути
(_PEAK_POINTS_SQL/_AGG_SQL в apps/api/app/main.py, tile_composition в
миграции 0007) — Python-дублей здесь сознательно нет.
"""

import numpy as np


def weighted_sum_composition(masks, weights=None):
    """
    Взвешенная сумма масок.
    
    Параметры:
        masks: dict {имя_маски: numpy array весов}
        weights: dict {имя_маски: коэффициент значимости}.
                 Если None — все маски с равными весами.
    
    Возвращает:
        numpy array итоговых весов (нормированных в 0-1).
    """
    if not masks:
        raise ValueError("Не передано ни одной маски")
    
    if weights is None:
        weights = {name: 1.0 for name in masks}
    
    # Проверка что веса заданы для всех масок
    for name in masks:
        if name not in weights:
            raise ValueError(f"Не задан вес для маски {name}")
    
    # Нормируем веса значимости в сумму 1
    total_weight = sum(weights.values())
    norm_weights = {name: w / total_weight for name, w in weights.items()}
    
    # Линейная комбинация
    arrays = list(masks.values())
    composed = np.zeros_like(arrays[0], dtype=float)
    for name, mask in masks.items():
        composed = composed + norm_weights[name] * mask
    
    # Нормировка результата в 0-1
    if composed.max() > 0:
        composed = composed / composed.max()
    
    return composed


def multiplicative_gating(base_mask, exclusion_masks):
    """
    Мультипликативное гейтирование: исключение ячеек по маскам.
    
    Параметры:
        base_mask: основная маска (numpy array)
        exclusion_masks: dict {имя: numpy array} 
                         где 0 = исключить ячейку, 1 = сохранить
    
    Возвращает:
        numpy array после гейтирования.
    """
    result = base_mask.copy().astype(float)
    
    for name, gate in exclusion_masks.items():
        result = result * gate
    
    return result


def entropy_smoothing(masked_distribution, alpha=0.8):
    """
    Энтропийное сглаживание для контроля чрезмерной концентрации.
    
    Параметры:
        masked_distribution: распределение из композиции масок
        alpha: вес масочного распределения (0-1). 
               1 = чистая маска, 0 = чистое равномерное.
    
    Возвращает:
        Сглаженное распределение.
    """
    n = len(masked_distribution)
    uniform = np.ones(n) / n
    
    smoothed = alpha * masked_distribution + (1 - alpha) * uniform
    return smoothed / smoothed.sum() if smoothed.sum() > 0 else smoothed


def distribute_value(grid_weights, regional_value):
    """
    Распределяет региональное значение пропорционально итоговым весам.
    Гарантирует сохранение суммы.
    
    Параметры:
        grid_weights: numpy array итоговых весов после композиции
        regional_value: официальное значение показателя по региону
    
    Возвращает:
        numpy array значений по ячейкам, сумма равна regional_value
    """
    total = grid_weights.sum()
    if total == 0:
        return np.full(len(grid_weights), regional_value / len(grid_weights))
    return regional_value * grid_weights / total


def check_sum_preservation(cell_values, regional_value, tolerance=1e-6):
    """
    Проверяет инвариант: сумма значений по ячейкам = региональное значение.
    
    Возвращает True если разница в пределах tolerance.
    """
    actual_sum = cell_values.sum()
    diff = abs(actual_sum - regional_value)
    rel_diff = diff / regional_value if regional_value != 0 else diff
    return rel_diff < tolerance
