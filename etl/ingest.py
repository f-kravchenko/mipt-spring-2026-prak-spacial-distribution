"""
ETL: загрузка сетки, масок, композиций и метрик в PostGIS.

Запуск:
    DATABASE_URL=postgresql+psycopg://masks:masks@localhost:5432/masks \
        python -m etl.ingest --config etl/config.yaml

Идемпотентен по региону: перед загрузкой регион с тем же slug удаляется
(каскадом чистятся его сетка, маски, композиции и метрики).
"""

import argparse
import json
import os
import sys

import geopandas as gpd
import pandas as pd
import yaml
from shapely import wkt as shapely_wkt
from sqlalchemy import create_engine, text

# Логика масок переиспользуется как есть — единый источник истины.
from src.masks import (
    baseline, territory, worldpop, regression, distance_to_city, distance_to_center, composition,
    road_network, road_traveltime, railway, power,
)
from src.masks import metrics
from src.masks.pipeline import UndefinedMaskSet

# Реестр масок: pipeline-ключ -> (модуль, зависит ли от показателя).
# Веса считаются в python по столбцам сетки (load_mask_values).
MASK_REGISTRY = {
    'baseline': (baseline, False),
    'worldpop': (worldpop, False),
    'distance_to_city': (distance_to_city, False),
    'distance_to_center': (distance_to_center, False),
    'regression': (regression, True),
}

# Слой-маски: регистрируются как обычные маски (контракт + слой на карте),
# но веса считаются отдельно в PostGIS (по своим данным), а не в python-пайплайне.
LAYER_MASKS = {
    'road_network': road_network,
    'road_traveltime': road_traveltime,
    'railway': railway,
    'power': power,
    'territory': territory,
}

# Маски с предрасчитанным весом в CSV (cell_code, weight): конфиг-ключ -> pipeline-ключ.
# Считаются оффлайн (osmnx), грузятся load_csv_mask — в slim-ETL нет osmnx.
CSV_MASKS = {
    'traveltime': 'road_traveltime',
    'railway': 'railway',
    'power': 'power',
    'territory': 'territory',
}

# RU тип влияния из MASK_DESCRIPTION -> enum influence_kind
INFLUENCE_MAP = {
    'повышающий': 'boost',
    'понижающий': 'damp',
    'исключающий': 'exclude',
    'сглаживающий': 'smooth',
    'нейтральный': 'smooth',
}


def compute_mask_weights(key, grid, indicator_code, params):
    """Веса конкретной маски по ячейкам (в порядке строк grid)."""
    if key == 'baseline':
        return baseline.compute_baseline_mask(grid)
    if key == 'worldpop':
        return worldpop.compute_worldpop_mask(grid)
    if key == 'distance_to_city':
        return distance_to_city.compute_distance_mask(grid, sigma=params['city_sigma_km'])
    if key == 'distance_to_center':
        return distance_to_center.compute_center_mask(grid, sigma=params['center_sigma_km'])
    if key == 'regression':
        return regression.compute_regression_mask(grid, indicator_code)
    raise ValueError(f"Неизвестная маска {key}")


def register_masks(engine):
    """Заносит контракты масок (§6 ТЗ) в таблицу mask, возвращает slug -> id."""
    modules = {k: v[0] for k, v in MASK_REGISTRY.items()} | dict(LAYER_MASKS)
    slug_to_id = {}
    with engine.begin() as conn:
        for key, module in modules.items():
            d = module.MASK_DESCRIPTION
            row = conn.execute(text("""
                INSERT INTO mask (slug, title, source, signal, influence, formula,
                                  normalization, applicability, limitations, is_baseline)
                VALUES (:slug, :title, :source, :signal, :influence, :formula,
                        :normalization, :applicability, :limitations, :is_baseline)
                ON CONFLICT (slug) DO UPDATE SET
                    title=EXCLUDED.title, source=EXCLUDED.source, signal=EXCLUDED.signal,
                    influence=EXCLUDED.influence, formula=EXCLUDED.formula,
                    normalization=EXCLUDED.normalization, applicability=EXCLUDED.applicability,
                    limitations=EXCLUDED.limitations, is_baseline=EXCLUDED.is_baseline
                RETURNING id
            """), {
                'slug': d['name'],
                'title': d.get('title') or d['name'].replace('_', ' '),
                'source': d.get('source'),
                'signal': d.get('signal'),
                'influence': INFLUENCE_MAP.get(d.get('influence_type', ''), 'smooth'),
                'formula': d.get('formula'),
                'normalization': 'min-max в 0..1',
                'applicability': d.get('applicability', []),
                'limitations': d.get('limitations'),
                'is_baseline': key == 'baseline',
            })
            slug_to_id[key] = row.scalar()
    return slug_to_id


def load_indicators(engine, indicators, parquet_path=None, year=None):
    # national_total — итог по РФ (object_level='Страна' в том же parquet, та же
    # единица измерения, что у региональных строк) — для режима "Россия" на
    # фронте: доля ячейки = cell_abs / national_total, где cell_abs получен
    # через РЕГИОНАЛЬНОЕ значение. См. миграцию 0008.
    with engine.begin() as conn:
        for code, meta in indicators.items():
            national = None
            if parquet_path is not None:
                national = regional_value_lookup(
                    parquet_path, 'Российская Федерация', code, year,
                    subsection=meta.get('subsection'), object_level='Страна')
            conn.execute(text("""
                INSERT INTO indicator (code, name, unit, elasticity, r2, indicator_type,
                                       national_total)
                VALUES (:code, :name, :unit, :elasticity, :r2, :itype, :national)
                ON CONFLICT (code) DO UPDATE SET
                    name=EXCLUDED.name, unit=EXCLUDED.unit, elasticity=EXCLUDED.elasticity,
                    r2=EXCLUDED.r2, indicator_type=EXCLUDED.indicator_type,
                    national_total=EXCLUDED.national_total
            """), {
                'code': code, 'name': meta['name'], 'unit': meta.get('unit'),
                'elasticity': meta.get('elasticity'), 'r2': meta.get('r2'),
                'itype': meta.get('indicator_type'), 'national': national,
            })


def upsert_region(engine, reg):
    """Удаляет регион (каскад) и заново вставляет его границу. Возвращает region_id."""
    border = gpd.read_file(reg['border']).to_crs(4326)
    geom_union = border.union_all() if hasattr(border, 'union_all') else border.unary_union
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM region WHERE slug=:slug"), {'slug': reg['slug']})
        row = conn.execute(text("""
            INSERT INTO region (slug, name, geom)
            VALUES (:slug, :name, ST_Multi(ST_GeomFromText(:wkt, 4326)))
            RETURNING id
        """), {'slug': reg['slug'], 'name': reg['name'], 'wkt': geom_union.wkt})
        return row.scalar()


def load_grid(engine, region_id, grid_path):
    """Грузит ячейки сетки. Возвращает (grid GeoDataFrame, cell_code -> cell_id)."""
    grid = gpd.read_file(grid_path).to_crs(4326).reset_index(drop=True)
    grid = grid.rename_geometry('geom')
    grid['cell_code'] = grid.index.astype(str)

    feature_cols = [c for c in ('dist_to_city_km', 'dist_to_center_km') if c in grid.columns]
    stg = pd.DataFrame({
        'region_id': region_id,
        'cell_code': grid['cell_code'],
        'area_km2': grid.get('area_km2', 0.0),
        'population': grid.get('population'),
        'features_json': grid[feature_cols].apply(
            lambda r: json.dumps({k: _num(r[k]) for k in feature_cols}), axis=1
        ) if feature_cols else '{}',
    })
    stg_gdf = gpd.GeoDataFrame(stg, geometry=grid['geom'], crs=4326)
    stg_gdf = stg_gdf.rename_geometry('geom')
    del stg

    # Пишем сетку в staging батчами: to_postgis конвертирует ВСЮ геометрию в WKB
    # за один вызов, поэтому 140k+ мультиполигонов разом съедают память (OOM).
    # Режем на куски — пик памяти ограничен одним батчем.
    batch = 10000
    n = len(stg_gdf)
    with engine.begin() as conn:
        for i in range(0, n, batch):
            chunk = stg_gdf.iloc[i:i + batch]
            chunk.to_postgis('_stg_grid', conn,
                             if_exists='replace' if i == 0 else 'append', index=False)
            print(f"  staging {min(i + batch, n)}/{n}")
        del stg_gdf
        # геометрия в Python больше не нужна (маски считаются по атрибутам).
        # Освобождаем её ДО тяжёлого серверного INSERT, иначе под давлением
        # памяти OOM-killer убивает Python, пока PostGIS строит индекс.
        grid = pd.DataFrame(grid.drop(columns='geom'))
        conn.execute(text("""
            INSERT INTO grid_cell (region_id, cell_code, geom, area_km2, population, features)
            SELECT region_id, cell_code, ST_Multi(geom), area_km2, population,
                   features_json::jsonb
            FROM _stg_grid
        """))
        conn.execute(text("DROP TABLE _stg_grid"))
        rows = conn.execute(text(
            "SELECT cell_code, id FROM grid_cell WHERE region_id=:r"), {'r': region_id})
        code_to_id = dict(rows.all())
    return grid, code_to_id


def load_mask_values(engine, grid, code_to_id, slug_to_id, indicators, params):
    """Считает и грузит веса всех масок по ячейкам (regression — по показателю)."""
    cell_ids = grid['cell_code'].map(code_to_id).values
    records = []
    for key, (_, indicator_dependent) in MASK_REGISTRY.items():
        mask_id = slug_to_id[key]
        if indicator_dependent:
            for code in indicators:
                # regression = pop^эластичность; у показателей без подобранной
                # эластичности (indicator_type: general) маска не определена —
                # пропускаем её, а не валим ингест целиком. Остальные маски
                # (baseline/worldpop/distance/слой-маски) от показателя не зависят
                # и работают как обычно.
                if key == 'regression' and code not in regression.ELASTICITIES:
                    print(f"  regression: нет эластичности для {code} — маска пропущена")
                    continue
                w = compute_mask_weights(key, grid, code, params)
                records.append(pd.DataFrame({
                    'mask_id': mask_id, 'indicator_code': code,
                    'cell_id': cell_ids, 'weight': w,
                }))
        else:
            w = compute_mask_weights(key, grid, None, params)
            records.append(pd.DataFrame({
                'mask_id': mask_id, 'indicator_code': '',
                'cell_id': cell_ids, 'weight': w,
            }))
    df = pd.concat(records, ignore_index=True)
    del records
    with engine.begin() as conn:
        df.to_sql('mask_cell_value', conn, if_exists='append', index=False,
                  chunksize=10000, method='multi')


def load_road_network_mask(engine, region_id, mask_id):
    """Маска дорожной сети (§5.3): веса по близости к дорогам разных классов.
    Считается в PostGIS по таблице road (KNN <-> + geography-расстояние).
    Возвращает число загруженных ячеек (0, если у региона нет дорог)."""
    tiers = road_network.ROAD_TIERS
    # d_<tier> — км до ближайшей дороги класса; пусто -> большое расстояние (вес ~0).
    # Для 1 км ячеек считаем доступность от внутренней точки ячейки: это заметно
    # дешевле polygon->geography distance и достаточно точно для аналитической маски.
    # regex-предикат подставляется ЛИТЕРАЛОМ (а не bind-параметром), чтобы планер
    # использовал частичный composite GiST-индекс road_region_geom_*_gix (см. 0005);
    # иначе KNN превращается в bitmap scan + сортировку дорог для каждой ячейки.
    # Паттерны — доверенные константы из ROAD_TIERS, инъекции нет.
    dist_cols = ",\n".join(
        f"""COALESCE((SELECT ST_DistanceSphere(c.pt, r.geom)
                      FROM road r
                      WHERE r.region_id = :rid AND r.highway ~ '{spec[0]}'
                      ORDER BY c.pt <-> r.geom LIMIT 1), 1e9) / 1000.0 AS d_{t}"""
        for t, spec in tiers.items()
    )
    score = " + ".join(
        f"{w}*exp(-d_{t}/{sigma})" for t, (_re, sigma, w) in tiers.items()
    )
    sql = text(f"""
        WITH cells AS (
            SELECT gc.id AS cell_id, ST_PointOnSurface(gc.geom) AS pt
            FROM grid_cell gc
            WHERE gc.region_id = :rid
        ),
        d AS (
            SELECT c.cell_id,
                {dist_cols}
            FROM cells c
        ),
        w AS (SELECT cell_id, ({score}) AS raw FROM d)
        INSERT INTO mask_cell_value (mask_id, indicator_code, cell_id, weight)
        SELECT :mid, '', cell_id, (raw / NULLIF((SELECT max(raw) FROM w), 0))::real
        FROM w
    """)
    params = {'rid': region_id, 'mid': mask_id}
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        for old_index in ('road_geom_fed_gix', 'road_geom_reg_gix', 'road_geom_loc_gix'):
            conn.execute(text(f"DROP INDEX IF EXISTS {old_index}"))
        for tier, (pattern, _sigma, _weight) in tiers.items():
            suffix = {'federal': 'fed', 'regional': 'reg', 'local': 'loc'}[tier]
            conn.execute(text(f"""
                CREATE INDEX IF NOT EXISTS road_region_geom_{suffix}_gix
                ON road USING gist (region_id, geom)
                WHERE highway ~ '{pattern}'
            """))
        conn.execute(text("ANALYZE road"))
        conn.execute(text("""
            DELETE FROM mask_cell_value
            WHERE mask_id = :mid
              AND cell_id IN (SELECT id FROM grid_cell WHERE region_id = :rid)
        """), {'mid': mask_id, 'rid': region_id})
        return conn.execute(sql, params).rowcount


def load_csv_mask(engine, region_id, mask_id, csv_path, code_to_id):
    """Грузит слой-маску с предрасчитанным весом (cell_code, weight) из CSV в
    mask_cell_value. Вес считается оффлайн (osmnx), т.к. ETL-образ без osmnx.
    Возвращает число загруженных ячеек."""
    df = pd.read_csv(csv_path, dtype={'cell_code': str})
    df['cell_id'] = df['cell_code'].map(code_to_id)
    df = df.dropna(subset=['cell_id'])
    df['cell_id'] = df['cell_id'].astype('int64')
    out = pd.DataFrame({
        'mask_id': mask_id, 'indicator_code': '',
        'cell_id': df['cell_id'], 'weight': df['weight'].astype(float),
    })
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM mask_cell_value
            WHERE mask_id = :mid
              AND cell_id IN (SELECT id FROM grid_cell WHERE region_id = :rid)
        """), {'mid': mask_id, 'rid': region_id})
        out.to_sql('mask_cell_value', conn, if_exists='append', index=False,
                   chunksize=10000, method='multi')
    return len(out)


def load_compositions(engine, region_id, grid, code_to_id, regional_values, cfg,
                      store_cells=False):
    """Прогон ablation-конфигураций -> composition + quality_metric (+ по флагу
    distribution_cell). Per-cell значения не читает никто: живая карта считает их
    на лету (tile_composition), метрики ablation лежат в quality_metric, а
    региональный итог — на самой композиции (см. миграцию 0012). Хранение стоило
    7.35 ГБ из 10.2, поэтому по умолчанию не пишем."""
    cell_ids = grid['cell_code'].map(code_to_id).values
    params = {'city_sigma': cfg['distances']['city_sigma_km'],
              'center_sigma': cfg['distances']['center_sigma_km']}
    year = cfg['year']

    for code, regional_value in regional_values.items():
        for conf in cfg['ablation']:
            try:
                res = composition_run(grid, regional_value, code, conf, params)
            except UndefinedMaskSet as e:
                print(f"  {code}/{conf['label']}: пропуск — {e}")
                continue
            values = res['values']
            with engine.begin() as conn:
                comp_id = conn.execute(text("""
                    INSERT INTO composition (region_id, indicator_code, year, label, method,
                                             weights, smoothing_alpha, sum_preserved,
                                             regional_value)
                    VALUES (:r, :ic, :y, :label, :method, :weights, :alpha, :sp, :rv)
                    RETURNING id
                """), {
                    'r': region_id, 'ic': code, 'y': year, 'label': conf['label'],
                    'method': 'weighted_sum',
                    'weights': json.dumps(conf['weights']) if conf['weights'] else json.dumps({}),
                    'alpha': None, 'sp': bool(res['sum_preserved']),
                    'rv': float(regional_value),
                }).scalar()

                if store_cells:
                    pd.DataFrame({
                        'composition_id': comp_id, 'cell_id': cell_ids, 'value': values,
                    }).to_sql('distribution_cell', conn, if_exists='append', index=False,
                              chunksize=10000, method='multi')

                qm = {
                    'gini': metrics.gini(values),
                    'top10_share': metrics.top_share(values, 0.1),
                    'sum_error': metrics.sum_error(values, regional_value),
                }
                pd.DataFrame([
                    {'composition_id': comp_id, 'metric': k, 'value': v, 'scope': 'region'}
                    for k, v in qm.items()
                ]).to_sql('quality_metric', conn, if_exists='append', index=False)


def load_cities(engine, region_id, csv_path):
    """Грузит города региона (точки + население) из CSV в таблицу city."""
    df = pd.read_csv(csv_path).dropna(subset=['lon', 'lat'])
    cols = {c: c for c in ('name', 'population', 'place') if c in df.columns}
    gdf = gpd.GeoDataFrame(
        df[list(cols)].rename(columns=cols),
        geometry=gpd.points_from_xy(df['lon'], df['lat']), crs=4326,
    ).rename_geometry('geom')
    gdf['region_id'] = region_id
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM city WHERE region_id=:r"), {'r': region_id})
        gdf.to_postgis('city', conn, if_exists='append', index=False)
    return len(gdf)


def load_roads_infra(engine, region_id, infra_path):
    """Дороги из <slug>_infra.json (road_lines: {h: highway, c: [[lon,lat],…]}).
    Тот же формат таблицы road, что у graphml-пути — маска дорожной сети
    считается в PostGIS по классам highway и не различает источник."""
    lines = json.load(open(infra_path, encoding='utf-8')).get('road_lines', [])
    rows = [{'highway': ln.get('h') or None,
             'geom': shapely_wkt.loads(
                 'LINESTRING(' + ','.join(f'{x} {y}' for x, y in ln['c']) + ')')}
            for ln in lines if len(ln.get('c', [])) >= 2]
    if not rows:
        return 0
    gdf = gpd.GeoDataFrame(rows, geometry='geom', crs=4326)
    gdf['highway'] = gdf['highway'].astype('string')
    gdf['region_id'] = region_id
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM road WHERE region_id=:r"), {'r': region_id})
        gdf.to_postgis('road', conn, if_exists='append', index=False, chunksize=5000)
    return len(gdf)


def load_roads(engine, region_id, graphml_path):
    """Грузит дорожную сеть из osmnx-graphml (без osmnx — парсим XML stdlib)."""
    import xml.etree.ElementTree as ET
    from shapely.geometry import LineString

    ns = {'g': 'http://graphml.graphdrawing.org/xmlns'}
    root = ET.parse(graphml_path).getroot()

    # key id -> (домен, имя атрибута); ищем node x/y и edge geometry/highway
    keys = {k.get('id'): (k.get('for'), k.get('attr.name')) for k in root.findall('g:key', ns)}
    kid = lambda dom, nm: next((i for i, (d, n) in keys.items() if d == dom and n == nm), None)
    nx, ny = kid('node', 'x'), kid('node', 'y')
    kgeo, khw = kid('edge', 'geometry'), kid('edge', 'highway')

    graph = root.find('g:graph', ns)
    nodes = {}
    for nd in graph.findall('g:node', ns):
        vals = {d.get('key'): d.text for d in nd.findall('g:data', ns)}
        if vals.get(nx) and vals.get(ny):
            nodes[nd.get('id')] = (float(vals[nx]), float(vals[ny]))

    rows = []
    for ed in graph.findall('g:edge', ns):
        vals = {d.get('key'): d.text for d in ed.findall('g:data', ns)}
        geom = None
        if vals.get(kgeo):
            try:
                geom = shapely_wkt.loads(vals[kgeo])
            except Exception:
                geom = None
        if geom is None:
            s, t = nodes.get(ed.get('source')), nodes.get(ed.get('target'))
            if s and t:
                geom = LineString([s, t])
        if geom is not None and geom.geom_type == 'LineString':
            rows.append({'region_id': region_id, 'highway': vals.get(khw), 'geom': geom})

    if not rows:
        return 0
    gdf = gpd.GeoDataFrame(rows, geometry='geom', crs=4326)
    gdf['highway'] = gdf['highway'].astype('string')
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM road WHERE region_id=:r"), {'r': region_id})
        gdf.to_postgis('road', conn, if_exists='append', index=False, chunksize=5000)
    return len(gdf)


def composition_run(grid, regional_value, code, conf, params):
    from src.masks.pipeline import run_pipeline
    return run_pipeline(
        grid=grid, regional_value=regional_value, indicator_code=code,
        masks_to_use=conf['masks'], mask_weights=conf['weights'], params=params,
    )


def regional_value_lookup(parquet_path, object_name, code, year,
                          subsection=None, object_level='Регион'):
    # Predicate/column pushdown: читаем только подходящие строки, а не весь
    # массив (~2 млн строк, ~2 ГБ в памяти) — иначе OOM при чтении 3 раза.
    #
    # subsection обязателен для показателей с несколькими подразделами
    # (Y477090007 — четыре секции ОКВЭД на один object_name): без фильтра
    # sel[0] возвращает первую попавшуюся строку файла ("Водоснабжение..."
    # вместо "Обрабатывающие производства") — так в БД попадали значения
    # чужого подраздела. Задаётся в etl/config.yaml -> indicators.<code>.subsection.
    df = pd.read_parquet(
        parquet_path,
        columns=['object_name', 'object_level', 'year', 'indicator_code',
                 'indicator_value', 'subsection'],
        filters=[('indicator_code', '==', code), ('object_level', '==', object_level),
                 ('year', '==', year), ('object_name', '==', object_name)],
    )
    if subsection is not None:
        df = df[df['subsection'] == subsection]
    if len(df) > 1:
        print(f"  ВНИМАНИЕ: {code}/{object_name}: {len(df)} строк "
              f"(подразделы: {df['subsection'].unique().tolist()}) — задайте "
              f"indicators.{code}.subsection в конфиге; взята первая")
    sel = df['indicator_value'].values
    return float(sel[0]) if len(sel) else None


def _num(x):
    try:
        f = float(x)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='etl/config.yaml')
    ap.add_argument('--database-url', default=os.environ.get('DATABASE_URL'))
    # Правка метаданных показателя (indicator_type, unit, эластичность) не требует
    # перезаливки сеток и композиций — а полный прогон это ~15 минут и снос
    # ИКГС-ячеек (регион пересоздаётся). load_indicators идемпотентен (upsert).
    ap.add_argument('--indicators-only', action='store_true',
                    help='обновить только справочник показателей и выйти')
    # Прогон региона переливает его целиком (регион пересоздаётся), поэтому
    # обычно нужен ровно один — не трогая уже загруженные.
    ap.add_argument('--region', action='append', metavar='SLUG',
                    help='грузить только эти регионы (можно повторять)')
    ap.add_argument('--store-cells', action='store_true',
                    help='писать distribution_cell (артефакт §9.5 ТЗ; ~7 ГБ на '
                         'четыре региона, живому сервису не нужен)')
    args = ap.parse_args()

    if not args.database_url:
        sys.exit("Задайте DATABASE_URL или --database-url")

    cfg = yaml.safe_load(open(args.config, encoding='utf-8'))
    engine = create_engine(args.database_url)

    load_indicators(engine, cfg['indicators'], cfg['rosstat_parquet'], cfg['year'])
    if args.indicators_only:
        print(f"справочник показателей обновлён ({len(cfg['indicators'])}), выход")
        return
    slug_to_id = register_masks(engine)
    params = {'city_sigma_km': cfg['distances']['city_sigma_km'],
              'center_sigma_km': cfg['distances']['center_sigma_km']}

    todo = cfg['regions']
    if args.region:
        todo = [r for r in todo if r['slug'] in args.region]
        missing = set(args.region) - {r['slug'] for r in todo}
        if missing:
            sys.exit(f"нет в конфиге: {', '.join(sorted(missing))}")
    for reg in todo:
        print(f"[{reg['slug']}] загрузка границы и сетки…")
        region_id = upsert_region(engine, reg)
        grid, code_to_id = load_grid(engine, region_id, reg['grid'])

        regional_values = {}
        for code in cfg['indicators']:
            val = regional_value_lookup(cfg['rosstat_parquet'], reg['object_name'], code,
                                        cfg['year'], cfg['indicators'][code].get('subsection'))
            if val is None:
                print(f"  нет значения {code} для {reg['object_name']} — пропуск показателя")
                continue
            regional_values[code] = val

        load_mask_values(engine, grid, code_to_id, slug_to_id, list(regional_values), params)
        load_compositions(engine, region_id, grid, code_to_id, regional_values, cfg,
                          store_cells=args.store_cells)
        print(f"  ячеек: {len(grid)}, показателей: {len(regional_values)}")

        if reg.get('cities'):
            print(f"  городов: {load_cities(engine, region_id, reg['cities'])}")
        # дороги: osmnx-graphml либо road_lines из infra.json (его собирает
        # etl.fetch_infra_* для ИКГС-регионов — те же way с тегом highway,
        # graphml для них не готовили)
        if reg.get('roads'):
            print(f"  дорог: {load_roads(engine, region_id, reg['roads'])}")
        elif reg.get('roads_infra'):
            print(f"  дорог: {load_roads_infra(engine, region_id, reg['roads_infra'])}")
        if reg.get('roads') or reg.get('roads_infra'):
            # маска дорожной сети считается по таблице road — только если дороги есть
            n = load_road_network_mask(engine, region_id, slug_to_id['road_network'])
            print(f"  маска дорог: {n} ячеек")
        for cfg_key, mask_key in CSV_MASKS.items():
            path = reg.get(cfg_key)
            if path and os.path.exists(path):
                n = load_csv_mask(engine, region_id, slug_to_id[mask_key], path, code_to_id)
                print(f"  маска {mask_key}: {n} ячеек")

    print("Готово.")


if __name__ == '__main__':
    main()
