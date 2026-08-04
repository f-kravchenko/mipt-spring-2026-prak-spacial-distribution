"""
Подбор эластичности показателя по населению для регрессионной маски.

Модель маски — value ∝ pop^β (src/masks/regression.compute_regression_mask),
поэтому β оценивается log-log МНК по РЕГИОНАЛЬНОЙ выборке Росстата:
    ln(Y_регион) = a + β · ln(P_регион)
P — городское население (Y477010002, тысяч человек) того же года. Спецификацию
выбрал по совпадению с единственным задокументированным подбором: у
Y477110236 «Объём инновационных товаров» в etl/config.yaml стоит R² 0.53, и
именно городское население за 2023 даёт R² 0.527 (β 1.555 против 1.594).
Полное население и среднегодовое дают R² 0.54-0.60 — мимо.

ВАЖНО про 2.77 у Y477090007: это НЕ подобранная эластичность, а параметр
настройки maxent (URBAN_ELASTICITY_STRICT в notebooks/04_maxent.ipynb, «жёсткий
фокус», рядом лежит SMOOTH = 1.00), попавший в поле elasticity. Тот же log-log
даёт для него β 1.356 при R² 0.652. Скрипт ничего не перезаписывает — печатает
таблицу и готовый YAML-блок, решение о правке конфига за человеком.

Ограничение по смыслу: у УДЕЛЬНЫХ показателей (ставка, «на 1000 чел.») связи с
населением нет по построению — они уже поделены на население. Для них R² выходит
0.02-0.06, а β около нуля или отрицательная: pop^β почти константа, то есть
регрессионная маска перестаёт различать ячейки. Такие показатели скрипт
помечает и в YAML-блок не кладёт.

Запуск:  python scripts/fit_elasticities.py [--min-r2 0.3]
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POP_CODE = "Y477010002"  # Городское население субъектов РФ на 1 января, тыс. чел.


def series(df, code, year, sub=None):
    s = df[(df.indicator_code == code) & (df.year == year) & (df.object_level == "Регион")]
    if sub:
        s = s[s.subsection == sub]
    return s.dropna(subset=["indicator_value"]).groupby("object_name")["indicator_value"].first()


def fit(y, pop, min_n=20):
    """β и R² регрессии ln(y) = a + β·ln(pop). None, если данных мало.
    Отрицательные и нулевые значения выпадают: логарифм не определён — именно
    поэтому не подбирается «естественный прирост» (он бывает отрицательным)."""
    d = pd.concat({"y": y, "p": pop}, axis=1).dropna()
    d = d[(d.y > 0) & (d.p > 0)]
    if len(d) < min_n:
        return None
    x, yy = np.log(d.p.values), np.log(d.y.values)
    beta, a = np.polyfit(x, yy, 1)
    resid = yy - (a + beta * x)
    r2 = 1 - (resid ** 2).sum() / ((yy - yy.mean()) ** 2).sum()
    return float(beta), float(r2), len(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "etl/config.yaml"))
    ap.add_argument("--min-r2", type=float, default=0.3,
                    help="ниже этого R² эластичность не предлагается")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    df = pd.read_parquet(os.path.join(ROOT, cfg["rosstat_parquet"]))
    year = cfg["year"]
    pop = series(df, POP_CODE, year)
    if pop.empty:
        sys.exit(f"нет населения {POP_CODE} за {year}")
    print(f"выборка: {len(pop)} регионов, население {POP_CODE} за {year}\n")

    print(f'{"код":12}{"β":>8}{"R²":>7}{"n":>5}  {"статус":26}показатель')
    good = {}
    for code, meta in cfg["indicators"].items():
        r = fit(series(df, code, year, meta.get("subsection")), pop)
        name = meta["name"][:40]
        if r is None:
            print(f'{code:12}{"—":>8}{"—":>7}{"—":>5}  {"нет данных/отрицательные":26}{name}')
            continue
        beta, r2, n = r
        if beta <= 0:
            status = "β<=0: связи нет"
        elif r2 < a.min_r2:
            status = f"R² < {a.min_r2}: связи нет"
        else:
            status = "годится"
            good[code] = (round(beta, 3), round(r2, 3))
        cur = meta.get("elasticity")
        if cur is not None:
            status += f" (в конфиге {cur})"
        print(f"{code:12}{beta:8.3f}{r2:7.3f}{n:5}  {status:26}{name}")

    print(f"\n--- годных {len(good)}; YAML-блок для etl/config.yaml:")
    for code, (beta, r2) in good.items():
        print(f"  {code}:\n    elasticity: {beta}\n    r2: {r2}")
    print("\nВНИМАНИЕ: ELASTICITIES в src/masks/regression.py — отдельный словарь,")
    print("маска читает его, а не конфиг. Править надо оба.")


if __name__ == "__main__":
    main()
