"""
Метрики качества распределения (§9 ТЗ).

Без муниципальных данных доступны метрики концентрации и проверка
сохранения суммы. MAE/MAPE/RMSE/корреляция считаются при наличии факта.
"""

import numpy as np


def gini(values):
    """Коэффициент Джини концентрации значений по ячейкам (0 = равномерно)."""
    v = np.sort(np.asarray(values, dtype=float))
    n = len(v)
    if n == 0 or v.sum() == 0:
        return 0.0
    cum = np.cumsum(v)
    return float((n + 1 - 2 * np.sum(cum) / cum[-1]) / n)


def top_share(values, frac=0.1):
    """Доля показателя, приходящаяся на верхние frac ячеек."""
    v = np.sort(np.asarray(values, dtype=float))[::-1]
    if v.sum() == 0:
        return 0.0
    k = max(1, int(len(v) * frac))
    return float(v[:k].sum() / v.sum())


def sum_error(values, regional_value):
    """Относительная ошибка сохранения суммы (§9.1)."""
    if regional_value == 0:
        return float(abs(np.sum(values)))
    return float(abs(np.sum(values) - regional_value) / regional_value)


def mae(pred, actual):
    return float(np.mean(np.abs(np.asarray(pred) - np.asarray(actual))))


def rmse(pred, actual):
    return float(np.sqrt(np.mean((np.asarray(pred) - np.asarray(actual)) ** 2)))


def smape(pred, actual):
    p, a = np.asarray(pred, dtype=float), np.asarray(actual, dtype=float)
    denom = np.abs(p) + np.abs(a)
    mask = denom > 0
    if not mask.any():
        return 0.0
    return float(np.mean(2 * np.abs(p - a)[mask] / denom[mask]))
