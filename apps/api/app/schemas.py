from pydantic import BaseModel


class Region(BaseModel):
    id: int
    slug: str
    name: str
    bbox: list[float]  # [minx, miny, maxx, maxy]
    cell_count: int
    cities_tile_url: str | None = None  # шаблон {z}/{x}/{y}, если у региона есть города
    roads_tile_url: str | None = None   # шаблон {z}/{x}/{y}, если у региона есть дороги
    # шаблон тайлов tile_index, если регион — индексный (ячейки несут
    # features->'value'): показатель-индекс, а не сумма-сохраняемый (см. 0010)
    index_tile_url: str | None = None
    # максимум балла ИКГС в регионе — верх линейной шкалы покраски (0..max)
    index_max: float | None = None


class Indicator(BaseModel):
    code: str
    name: str
    unit: str | None = None
    elasticity: float | None = None
    r2: float | None = None
    indicator_type: str | None = None
    # Итог по РФ (та же единица, что у региональных значений) — для режима
    # "Россия": доля ячейки = value / national_total. См. миграцию 0008.
    national_total: float | None = None


class Mask(BaseModel):
    slug: str
    title: str
    source: str | None = None
    signal: str | None = None
    influence: str
    formula: str | None = None
    normalization: str | None = None
    applicability: list[str] | None = None
    limitations: str | None = None
    is_baseline: bool
    indicator_dependent: bool
    tile_url: str  # шаблон {z}/{x}/{y}


class RecomputeRequest(BaseModel):
    region_id: int
    indicator: str
    weights: dict[str, float]  # slug маски -> вес


class RecomputeResult(BaseModel):
    tile_url: str
    value_max: float | None = None
    regional_value: float | None = None
    metrics: dict[str, float]
    # Порог "пика" на суммарном слое (top 5% ненулевых ячеек, percentile_cont
    # в _AGG_SQL). Уже в единицах распределения (масштабирован тем же rv/total, что и
    # value_max) — фронт сравнивает ["get","value"] >= peak_threshold в
    # MapLibre-выражении, отдельно считать пики на фронте не нужно.
    peak_threshold: float | None = None
