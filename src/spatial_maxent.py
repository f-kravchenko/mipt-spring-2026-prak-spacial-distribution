"""
Модель максимальной энтропии для разнесения регионального показателя по ячейкам.

Идея: найти распределение p_i (доли регионального объёма в ячейке i), которое
максимизирует энтропию H = -sum p_i log p_i при ограничениях на средние значения
пространственных признаков E[f_k] = sum p_i f_ki.

Веса: p_i ∝ exp(lambda · f_i), lambda подбирается из ограничений (двойственная задача).
Целевые средние mu_k задаются по опорному распределению (население^alpha * доступность^beta),
где alpha — эластичность по городскому населению из регрессии.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp


def _center_distance_km(grid) -> np.ndarray:
    if "dist_to_center_km" in grid.columns:
        return grid["dist_to_center_km"].values.astype(float)
    if "dist_to_moscow_km" in grid.columns:
        return grid["dist_to_moscow_km"].values.astype(float)
    return grid["dist_to_city_km"].values.astype(float)


def build_cell_features(grid) -> tuple[np.ndarray, list[str]]:
    """Стандартизированные признаки ячейки для maxent."""
    pop = grid["population"].values.astype(float)
    d_city = grid["dist_to_city_km"].values.astype(float)
    d_center = _center_distance_km(grid)

    raw = np.column_stack(
        [
            np.log1p(np.maximum(pop, 0.0)),
            np.log1p(1.0 / (d_city + 1.0)),
            np.log1p(1.0 / (d_center + 1.0)),
        ]
    )
    names = ["log_pop", "log_access_city", "log_access_center"]
    mean = raw.mean(axis=0)
    std = raw.std(axis=0)
    std[std < 1e-9] = 1.0
    features = (raw - mean) / std
    return features, names


def reference_weights(
    grid,
    urban_elasticity: float = 2.77,
    accessibility_power: float = 0.35,
) -> np.ndarray:
    """
    Опорные веса для задания ограничений maxent:
    население^alpha * (близость к центру и городу)^beta, нормированные на 1.
    """
    pop = grid["population"].values.astype(float)
    d_city = grid["dist_to_city_km"].values.astype(float)
    d_center = _center_distance_km(grid)

    pop_term = np.power(pop, urban_elasticity, where=(pop > 0), out=np.zeros_like(pop))
    acc = (1.0 / (d_center + 1.0)) ** 0.7 * (1.0 / (d_city + 1.0)) ** 0.3
    weights = pop_term * np.power(acc, accessibility_power)

    total = weights.sum()
    if total <= 0:
        n = len(weights)
        return np.full(n, 1.0 / n)
    return weights / total


def constraint_targets(features: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Целевые средние признаков E[f_k] под опорным распределением."""
    return features.T @ weights


def maxent_weights(
    features: np.ndarray,
    targets: np.ndarray,
    max_iter: int = 500,
) -> np.ndarray:
    """
    Решение maxent: p_i ∝ exp(lambda · f_i), E_p[f] = targets.
    Возвращает вектор вероятностей длины n_cells.
    """
    n, k = features.shape
    if n == 0:
        return np.array([])

    targets = np.asarray(targets, dtype=float)
    uniform = np.full(n, 1.0 / n)

    def dual_objective(lam: np.ndarray) -> float:
        logits = features @ lam
        log_z = logsumexp(logits)
        return log_z - float(lam @ targets)

    def dual_gradient(lam: np.ndarray) -> np.ndarray:
        logits = features @ lam
        log_p = logits - logsumexp(logits)
        p = np.exp(log_p)
        return p @ features - targets

    res = minimize(
        dual_objective,
        x0=np.zeros(k),
        jac=dual_gradient,
        method="L-BFGS-B",
        options={"maxiter": max_iter},
    )

    if not res.success:
        # запасной вариант: экспоненциальные веса по признакам с lambda = 0
        logits = features @ res.x
        log_p = logits - logsumexp(logits)
        p = np.exp(log_p)
        if not np.isfinite(p).all() or p.sum() <= 0:
            return uniform
        return p / p.sum()

    logits = features @ res.x
    log_p = logits - logsumexp(logits)
    p = np.exp(log_p)
    return p / p.sum()


def distribute_maxent(
    grid,
    X_region: float,
    urban_elasticity: float = 2.77,
    accessibility_power: float = 0.35,
) -> np.ndarray:
    """Разнести X_region по ячейкам методом максимальной энтропии."""
    features, _ = build_cell_features(grid)
    w_ref = reference_weights(
        grid,
        urban_elasticity=urban_elasticity,
        accessibility_power=accessibility_power,
    )
    targets = constraint_targets(features, w_ref)
    p = maxent_weights(features, targets)
    return X_region * p
