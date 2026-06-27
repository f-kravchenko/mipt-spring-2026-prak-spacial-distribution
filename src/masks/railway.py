"""
Маска близости к ЖД-узлам (§5.4).

Станции, остановочные пункты и грузовые дворы (OSM railway=station|halt|yard)
как прокси доступа к железнодорожной логистике. Вес = затухание по расстоянию
до ближайшего узла. Расстояния считаются оффлайн (osmnx, см.
scripts/compute_infra_masks), кэшируются в data/processed/railway_<slug>.csv.
"""

import numpy as np

SIGMA_KM = 10.0


def compute_railway_mask(grid, col="railway_access"):
    if col not in grid.columns:
        return np.zeros(len(grid))
    return grid[col].fillna(0).to_numpy()


MASK_DESCRIPTION = {
    "name": "railway_mask",
    "title": "ЖД-узлы",
    "source": "ЖД-узлы OSM: railway=station|halt|yard",
    "signal": "Доступ к железнодорожной логистике — близость к станциям и "
              "грузовым дворам как прокси перевозок и промышленных площадок",
    "influence_type": "повышающий",
    "formula": "norm( e^(−d/σ) ), d — км до ближайшего ЖД-узла, σ = 10 км",
    "applicability": ["промышленность", "логистика", "добыча", "склады"],
    "limitations": "Узел ≠ реальный грузооборот; полнота тегов OSM; нет учёта "
                   "электрификации/пропускной способности линии",
}
