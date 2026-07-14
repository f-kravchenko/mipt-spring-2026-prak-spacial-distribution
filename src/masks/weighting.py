"""
Автоматический подбор весов композиции по показателю (§9, "обоснование весов").

Раньше веса были одним фиксированным словарём в config.yaml
(regression 0.5, worldpop 0.3, distance_to_city 0.1, distance_to_center 0.1)
без объяснения, откуда взяты числа, и без учёта 5 инфраструктурных масок
(territory, road_network, railway, road_traveltime, power) — они применимы
не ко всем показателям (см. MASK_DESCRIPTION["applicability"] каждой маски).

Методология (полноценной калибровки на муниципальных данных нет — см.
README_part2.md, "Ограничения"):

1. Вес regression = r2 показателя из config.yaml["indicators"]. R² — прямая
   эмпирическая мера объяснённой дисперсии калибровки на 87 регионах.
   Для показателей без калибровки (нет в regression.ELASTICITIES) маска
   regression не участвует.

2. Остаток бюджета (1 - r2, либо 1.0 целиком) делится между:
     - "общими" масками-прокси (worldpop, distance_to_city, distance_to_center)
       — участвуют всегда;
     - "промышленными" (territory, road_network, railway, road_traveltime,
       power) — только для category == "industrial".
   Если применимы обе группы — делятся по industrial_budget_share
   (config.yaml["composition"]) — явное задокументированное допущение.

3. Внутри группы бюджет делится по приорам из config.yaml — обоснование
   каждого числа в комментариях рядом с ним.

4. Если вызывающий код передаёт masks_to_use — веса считаются по полному
   применимому набору, затем фильтруются до пересечения с masks_to_use и
   перенормируются в сумму 1.

Все числа-приоры вынесены в config.yaml, а не хардкожены здесь — по той же
логике, что и в src/territory_config.py (§ рефакторинг territory.py):
менять веса и добавлять категории показателей можно без правки кода.
"""

from pathlib import Path

import yaml

from src.masks.regression import ELASTICITIES

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_weights(indicator_code: str, indicator_meta: dict | None = None,
                     masks_to_use: list[str] | None = None,
                     config: dict | None = None) -> dict[str, float]:
    """
    -> dict {имя_маски: вес}, сумма весов = 1.0.

    indicator_meta: элемент config.yaml["indicators"] (r2, category).
                     Если None — подгружается автоматически по indicator_code.
    masks_to_use: если задан — веса считаются только для этих масок
                  (фильтрация + перенормировка). Иначе — полный применимый набор.
    """
    cfg = config or _load_config()
    comp_cfg = cfg["composition"]
    proxy_priors = comp_cfg["proxy_priors"]
    industrial_priors = comp_cfg["industrial_priors"]
    industrial_budget_share = comp_cfg["industrial_budget_share"]

    if indicator_meta is None:
        indicator_meta = cfg.get("indicators", {}).get(indicator_code, {})

    r2 = indicator_meta.get("r2")
    category = indicator_meta.get("category", "general")
    use_regression = indicator_code in ELASTICITIES and r2 is not None

    weights: dict[str, float] = {}
    if use_regression:
        weights["regression"] = r2
        remaining = 1.0 - r2
    else:
        remaining = 1.0

    use_industrial = category == "industrial"
    industrial_budget = remaining * industrial_budget_share if use_industrial else 0.0
    proxy_budget = remaining - industrial_budget

    for name, prior in proxy_priors.items():
        weights[name] = proxy_budget * prior

    if use_industrial:
        for name, prior in industrial_priors.items():
            weights[name] = industrial_budget * prior

    if masks_to_use is not None:
        weights = {k: v for k, v in weights.items() if k in masks_to_use}
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

    return weights
