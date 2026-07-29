"""Нормировка показателей в шкалу 0-100 (индекс ВНТП)."""
import numpy as np


def normalize_indicator(values, direction='direct'):
    values = np.asarray(values, dtype=float)
    v_min = np.nanmin(values)
    v_max = np.nanmax(values)
    if v_max == v_min:
        return np.full_like(values, 50.0)
    if direction == 'direct':
        return 100.0 * (values - v_min) / (v_max - v_min)
    elif direction == 'inverse':
        return 100.0 * (v_max - values) / (v_max - v_min)
    else:
        raise ValueError(f"direction: 'direct' или 'inverse', получено {direction}")


def compute_vntp_index(indicator_arrays, directions=None):
    if not indicator_arrays:
        raise ValueError("Не передано ни одного показателя")
    directions = directions or {}
    normalized = []
    for code, values in indicator_arrays.items():
        d = directions.get(code, 'direct')
        normalized.append(normalize_indicator(values, d))
    return np.sum(normalized, axis=0)
