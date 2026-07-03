from pydantic import BaseModel


class Region(BaseModel):
    id: int
    slug: str
    name: str
    bbox: list[float]  # [minx, miny, maxx, maxy]
    cell_count: int
    cities_tile_url: str | None = None  # шаблон {z}/{x}/{y}, если у региона есть города
    roads_tile_url: str | None = None   # шаблон {z}/{x}/{y}, если у региона есть дороги


class Indicator(BaseModel):
    code: str
    name: str
    unit: str | None = None
    elasticity: float | None = None
    r2: float | None = None
    indicator_type: str | None = None


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


class Composition(BaseModel):
    id: int
    region_id: int
    indicator_code: str
    year: int
    label: str
    method: str
    weights: dict
    smoothing_alpha: float | None = None
    sum_preserved: bool | None = None
    metrics: dict[str, float]
    value_max: float | None = None  # макс. значение ячейки — для цветовой шкалы
    tile_url: str  # шаблон {z}/{x}/{y}


class ServiceConfig(BaseModel):
    tiles_base_url: str


class RecomputeRequest(BaseModel):
    region_id: int
    indicator: str
    weights: dict[str, float]  # slug маски -> вес


class RecomputeResult(BaseModel):
    tile_url: str
    value_max: float | None = None
    regional_value: float | None = None
    metrics: dict[str, float]
    # Порог "пика" на суммарном слое (top 5% ячеек, см. §9 "пики на суммарном
    # слое" / composition.detect_peaks(method="percentile", top_frac=0.05)).
    # Уже в единицах распределения (масштабирован тем же rv/total, что и
    # value_max) — фронт сравнивает ["get","value"] >= peak_threshold в
    # MapLibre-выражении, отдельно считать пики на фронте не нужно.
    peak_threshold: float | None = None
