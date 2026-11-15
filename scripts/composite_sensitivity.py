#!/usr/bin/env python3
"""Чувствительность комплексной оценки к схеме весов (валидация весов свода).

Весов по данным не подобрать: внешнего по-ячеечного эталона нет ни для одного
показателя, а маски НЕ коллинеарны (НН: медиана |ρ|=0.13, ни одной пары
>0.7 — см. анализ в composite_config.py), то есть выбор весов реально меняет
географию. Единственный не-произвольный выбор без данных — равные веса
(принцип безразличия); всё остальное требует экспертной сессии.

Что проверяем: насколько «свод» вообще чувствителен к схеме весов. Считаем
взвешенные суммы масок (агрегат свода по всем показателям) в трёх схемах:
равные веса, текущие DEFAULT_MASKS-подобные (население-тяжёлые) и
инфраструктурно-тяжёлые, и сравниваем ранги ячеек (Spearman). Высокий ρ —
свод устойчив, весовой «черновик» не врёт направлением; низкий — малейший
перекос весов меняет карту, нужна экспертная сессия.

Локально доступно 8 из 10 масок (road_network/regression считаются в БД);
traveltime ≈ road_network по смыслу — структура репрезентативна.

Запуск:
    python -m scripts.composite_sensitivity [--region nizhny_novgorod]
"""
import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"

# три схемы весов над масками: безразличие / «люди» / «инфраструктура»
SCHEMES = {
    "равные": None,  # всем по 1
    "население": {"worldpop": 3, "dist_city": 2, "traveltime": 1, "science": 1},
    "инфраструктура": {"power": 3, "railway": 2, "territory": 2, "traveltime": 1},
}


def masks_frame(slug):
    g = gpd.read_file(PROC / f"grid_{slug}_1km_features.gpkg")
    m = pd.DataFrame({
        "worldpop": np.sqrt(g.population.clip(lower=0)
                            / np.percentile(g.population[g.population > 0], 95)).clip(0, 1),
        "dist_city": np.exp(-g.dist_to_city_km / 10),
        "dist_center": np.exp(-g.dist_to_center_km / 30),
    })
    for name in ("railway", "power", "territory", "traveltime", "science"):
        m[name] = pd.read_csv(PROC / f"{name}_{slug}.csv")["weight"]
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="nizhny_novgorod")
    a = ap.parse_args()
    m = masks_frame(a.region)
    sums = {}
    for scheme, weights in SCHEMES.items():
        w = pd.Series(1.0, index=m.columns) if weights is None \
            else pd.Series(0.0, index=m.columns).fillna(0).add(pd.Series(weights))
        w = w / w.sum()
        sums[scheme] = (m * w).sum(axis=1)
    schemes = list(sums)
    print(f"свод «{a.region}»: ранговая устойчивость (Spearman) между схемами весов")
    for i in range(len(schemes)):
        for j in range(i + 1, len(schemes)):
            rho = spearmanr(sums[schemes[i]], sums[schemes[j]]).statistic
            print(f"  {schemes[i]:16} × {schemes[j]:16} ρ = {rho:.2f}")
    med = np.median([abs(spearmanr(sums[s], sums[t]).statistic)
                     for s in schemes for t in schemes if s < t])
    print(f"медианная |ρ| = {med:.2f} — {'устойчив' if med > 0.8 else 'ЧУВСТВИТЕЛЕН'} к схеме весов")


if __name__ == "__main__":
    main()
