"""
Маска доступности по времени в пути (§5.3/5.4, «наработки по дорожной сети»).

Сигнал — не евклидово расстояние до города, а время в пути по дорожной сети
(OSM motorway|trunk|primary|secondary, скорости → travel_time) от ячейки до
ближайшего города: учитывает реки, объезды, что активность тянется вдоль трасс.
Время в пути считается оффлайн (osmnx/networkx, см. scripts/compute_traveltime),
кэшируется в data/processed/traveltime_<slug>.csv, а ETL грузит готовый вес.
"""

import numpy as np

SIGMA_MIN = 30.0  # масштаб затухания по времени в пути, минуты


def compute_traveltime_mask(grid, col="traveltime_access"):
    """Фолбэк для python-пайплайна: читает предрасчитанный вес (0..1)."""
    if col not in grid.columns:
        return np.zeros(len(grid))
    return grid[col].fillna(0).to_numpy()


MASK_DESCRIPTION = {
    "name": "road_traveltime_mask",
    "title": "Доступность по времени в пути",
    "source": "Время в пути по магистральной сети OSM до ближайшего города "
              "(скорости по классу дороги → travel_time)",
    "signal": "Сетевая доступность городов: близость по времени в пути как прокси "
              "доступа к рынкам и труду (точнее евклидова расстояния)",
    "influence_type": "повышающий",
    "formula": "norm( e^(−t/σ) ), t — минуты в пути до ближайшего города, σ = 30 мин",
    "applicability": ["промышленность", "торговля", "услуги", "логистика"],
    "limitations": "Зависит от полноты графа OSM; время без пробок; для регионов "
                   "без графа маска не считается",
}
