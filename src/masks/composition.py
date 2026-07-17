"""
Композиция масок: объединение нескольких аналитических слоёв в одну
итоговую тепловую карту распределения показателя.

Поддерживает два способа (по ТЗ):
1. Взвешенная сумма — линейная комбинация с коэффициентами значимости
2. Мультипликативное гейтирование — для исключающих факторов (вода, лес)

Опционально — энтропийное сглаживание для контроля концентрации.

Пики, линии концентрации и затухание считаются в SQL живого пути
(_PEAK_POINTS_SQL/_AGG_SQL в apps/api/app/main.py, tile_composition в
миграции 0007) — Python-дублей здесь сознательно нет.
"""

import numpy as np


def weighted_sum_composition(masks, weights=None):
    """
    Взвешенная сумма масок.
    
    Параметры:
        masks: dict {имя_маски: numpy array весов}
        weights: dict {имя_маски: коэффициент значимости}.
                 Если None — все маски с равными весами.
    
    Возвращает:
        numpy array итоговых весов (нормированных в 0-1).
    """
    if not masks:
        raise ValueError("Не передано ни одной маски")
    
    if weights is None:
        weights = {name: 1.0 for name in masks}
    
    # Проверка что веса заданы для всех масок
    for name in masks:
        if name not in weights:
            raise ValueError(f"Не задан вес для маски {name}")
    
    # Нормируем веса значимости в сумму 1
    total_weight = sum(weights.values())
    norm_weights = {name: w / total_weight for name, w in weights.items()}
    
    # Линейная комбинация
    arrays = list(masks.values())
    composed = np.zeros_like(arrays[0], dtype=float)
    for name, mask in masks.items():
        composed = composed + norm_weights[name] * mask
    
    # Нормировка результата в 0-1
    if composed.max() > 0:
        composed = composed / composed.max()
    
    return composed


def multiplicative_gating(base_mask, exclusion_masks):
    """
    Мультипликативное гейтирование: исключение ячеек по маскам.
    
    Параметры:
        base_mask: основная маска (numpy array)
        exclusion_masks: dict {имя: numpy array} 
                         где 0 = исключить ячейку, 1 = сохранить
    
    Возвращает:
        numpy array после гейтирования.
    """
    result = base_mask.copy().astype(float)
    
    for name, gate in exclusion_masks.items():
        result = result * gate
    
    return result


def entropy_smoothing(masked_distribution, alpha=0.8):
    """
    Энтропийное сглаживание для контроля чрезмерной концентрации.
    
    Параметры:
        masked_distribution: распределение из композиции масок
        alpha: вес масочного распределения (0-1). 
               1 = чистая маска, 0 = чистое равномерное.
    
    Возвращает:
        Сглаженное распределение.
    """
    n = len(masked_distribution)
    uniform = np.ones(n) / n
    
    smoothed = alpha * masked_distribution + (1 - alpha) * uniform
    return smoothed / smoothed.sum() if smoothed.sum() > 0 else smoothed


def distribute_value(grid_weights, regional_value):
    """
    Распределяет региональное значение пропорционально итоговым весам.
    Гарантирует сохранение суммы.
    
    Параметры:
        grid_weights: numpy array итоговых весов после композиции
        regional_value: официальное значение показателя по региону
    
    Возвращает:
        numpy array значений по ячейкам, сумма равна regional_value
    """
    total = grid_weights.sum()
    if total == 0:
        return np.full(len(grid_weights), regional_value / len(grid_weights))
    return regional_value * grid_weights / total


def check_sum_preservation(cell_values, regional_value, tolerance=1e-6):
    """
    Проверяет инвариант: сумма значений по ячейкам = региональное значение.
    
    Возвращает True если разница в пределах tolerance.
    """
    actual_sum = cell_values.sum()
    diff = abs(actual_sum - regional_value)
    rel_diff = diff / regional_value if regional_value != 0 else diff
    return rel_diff < tolerance


def detect_peaks(composed, method="percentile", top_frac=0.05, z_thresh=2.0, neighbors=None):
    """
    Выделение пиков на суммарном (скомпонованном) слое.

    Порог перцентиля считается только по НЕНУЛЕВЫМ ячейкам: в разреженном
    регионе, где нулей больше (1 - top_frac), перцентиль по всем ячейкам
    схлопывается в 0 — и "пиком" становится любая ненулевая ячейка в
    окружении нулей, т.е. критерий вырождается в шум. Нулевая ячейка пиком
    быть не может.

    Параметры:
        composed: numpy array итоговых весов после composition
                  (выход weighted_sum_composition / multiplicative_gating /
                  entropy_smoothing).
        method:
            "percentile" (по умолчанию) — top_frac (по умолчанию верхние 5%)
                ненулевых ячеек по значению. Не требует данных о соседстве
                ячеек, работает всегда, но на почти однородном фоне может
                выделить ячейки, которые не являются содержательным "пиком" —
                просто оказались чуть выше остальных в плоском распределении.
            "zscore" — ячейки с (value - mean) / std >= z_thresh: статистический
                выброс относительно распределения по региону. Устойчивее
                percentile к разному размеру сетки/региона и к форме
                распределения (в сильно вытянутом хвосте top_frac может
                включать почти нормальные ячейки, zscore — нет).
            "local_max" — истинные локальные максимумы: ячейка — пик, если её
                значение >= значений всех соседей по сетке И проходит порог
                zscore (иначе на плоском фоне пиком считается почти любая
                ячейка чуть выше соседней — шум, а не сигнал). Требует
                neighbors (соседство по geometry, например
                libpysal.weights.Queen.from_dataframe(grid).neighbors) —
                этой информации в composition.py нет по умолчанию, её должен
                передать вызывающий код (он строил grid).
        top_frac: доля ненулевых ячеек для method="percentile" (0-1).
        z_thresh: порог z-оценки для method="zscore"/"local_max".
        neighbors: dict {index: list[index]} — соседние ячейки по сетке.
                   Обязателен для method="local_max".

    Возвращает:
        numpy array bool той же длины, что composed: True = ячейка-пик.
    """
    composed = np.asarray(composed, dtype=float)
    n = len(composed)
    if n == 0:
        return np.zeros(0, dtype=bool)

    if method == "percentile":
        positive = composed[composed > 0]
        if positive.size == 0:
            # все ячейки нулевые — пиков нет
            return np.zeros(n, dtype=bool)
        if positive.std() < 0.01 * positive.mean():
            return np.zeros(n, dtype=bool)
        k = max(1, int(round(positive.size * top_frac)))
        threshold = np.sort(positive)[::-1][k - 1]
        # threshold > 0 по построению — нулевые ячейки не проходят
        return composed >= threshold

    std = composed.std()
    if std == 0:
        # плоское распределение — содержательных пиков нет
        return np.zeros(n, dtype=bool)
    z = (composed - composed.mean()) / std

    if method == "zscore":
        return z >= z_thresh

    if method == "local_max":
        if neighbors is None:
            raise ValueError(
                "method='local_max' требует neighbors (соседство ячеек по сетке)"
            )
        is_local_max = np.zeros(n, dtype=bool)
        for i in range(n):
            nbrs = neighbors.get(i) or neighbors.get(str(i))
            if not nbrs:
                continue
            if composed[i] >= composed[list(nbrs)].max():
                is_local_max[i] = True
        return is_local_max & (z >= z_thresh)

    raise ValueError(f"Неизвестный method для detect_peaks: {method}")


def cluster_peaks(peak_mask, neighbors, values, mass_share=0.90):
    """
    Группирует ячейки-пики (detect_peaks) в кластеры смежности (BFS по
    neighbors — тот же формат {index: [соседние индексы]}, что в
    detect_peaks(method="local_max")) и берёт представительную ячейку
    каждого кластера — с МАКСИМАЛЬНЫМ значением внутри кластера, а не
    геометрический центроид (на невыпуклой форме кластера центроид может
    попасть в ячейку с низким значением — например, подковообразный кластер
    вокруг реки/леса).

    Правило Парето: оставляются сильнейшие кластеры, суммарно накрывающие
    mass_share массы пиковых ячеек (масса кластера — sum значений его ячеек,
    чтобы крупный центр из многих сильных ячеек выигрывал у одиночной яркой).
    В отличие от фиксированного топ-K число пиков адаптируется к структуре
    концентрации (моноцентричный регион — единицы, полицентричный — десятки)
    и не зависит от масштаба карты: при переходе регион->страна отбор везде
    идёт по одной доле массы — общий "уровень моря" без перекоса высот.
    Кластер, пересекающий границу share, включается. То же правило в
    SQL-пути API (_PEAK_POINTS_SQL в apps/api/app/main.py).

    Параметры:
        peak_mask: (N,) bool — выход detect_peaks
        neighbors: dict {index: list[index]} — соседство ячеек по сетке
        values: (N,) numpy array — значения (обычно composed)
        mass_share: доля массы пиков, которую накрывают кластеры (None — все)

    Возвращает:
        list[int] — индексы ячеек-представителей, по одной на кластер
    """
    peak_idx = np.flatnonzero(peak_mask)
    peak_set = set(peak_idx.tolist())
    visited = set()
    clusters = []
    for i in peak_idx:
        i = int(i)
        if i in visited:
            continue
        stack, component = [i], []
        visited.add(i)
        while stack:
            cur = stack.pop()
            component.append(cur)
            for nb in neighbors.get(cur, []):
                if nb in peak_set and nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        clusters.append(component)
    if mass_share is not None and clusters:
        clusters.sort(key=lambda c: -values[c].sum())
        total = sum(values[c].sum() for c in clusters)
        kept, acc = [], 0.0
        for c in clusters:
            if acc >= mass_share * total:
                break
            kept.append(c)
            acc += values[c].sum()
        clusters = kept
    return [max(c, key=lambda k: values[k]) for c in clusters]


def build_concentration_lines(peak_coords):
    """
    Минимальное остовное дерево (MST) между точками-пиками — "линии
    концентрации" (ТЗ п.5). Почему MST, а не связи "все со всеми": при K
    пиках это K-1 линий без циклов и с минимальной суммарной длиной, вместо
    K*(K-1)/2 связей одинаковой "силы" независимо от расстояния — MST не
    добавляет параметров для подбора (результат однозначно определяется
    координатами), это стандартный приём для задачи "связать точки
    минимальной осмысленной сетью" в пространственном анализе (транспортные/
    экономические коридоры).

    Параметры:
        peak_coords: (K, 2) координаты точек-пиков в метрах (проекция, не
                     градусы — иначе расстояния некорректны)

    Возвращает:
        list[((x1,y1),(x2,y2))] — рёбра MST. Пустой список, если K < 2
        (ТЗ п.5: линии строятся только "если пиков более 1").
    """
    peak_coords = np.asarray(peak_coords, dtype=float)
    k = len(peak_coords)
    if k < 2:
        return []

    from scipy.sparse.csgraph import minimum_spanning_tree
    from scipy.spatial.distance import cdist

    dist = cdist(peak_coords, peak_coords)
    mst = minimum_spanning_tree(dist).toarray()
    edges = []
    for i in range(k):
        for j in range(k):
            if mst[i, j] > 0:
                edges.append((tuple(peak_coords[i]), tuple(peak_coords[j])))
    return edges


def _point_segment_distance(points, seg_a, seg_b):
    """Расстояние от массива точек (N,2) до одного отрезка [seg_a, seg_b]."""
    seg_a = np.asarray(seg_a, dtype=float)
    seg_b = np.asarray(seg_b, dtype=float)
    d = seg_b - seg_a
    len2 = d @ d
    if len2 == 0:
        return np.linalg.norm(points - seg_a, axis=1)
    t = np.clip(((points - seg_a) @ d) / len2, 0.0, 1.0)
    proj = seg_a + t[:, None] * d
    return np.linalg.norm(points - proj, axis=1)


def decay_from_structure(cell_coords, peak_coords, lines, sigma_km=10.0):
    """
    Убывающая модель затухания от пиков и линий концентрации (ТЗ п.6):
    для каждой ячейки — расстояние до БЛИЖАЙШЕЙ точки структуры (пик или
    точка на отрезке линии), затем exp(-d/sigma).

    Почему exp(-d/sigma), а не другая форма: та же функция уже используется
    в проекте для каждого сигнала "затухание от точки" (distance_to_city_mask,
    distance_to_center_mask, railway_mask, power_lines_mask,
    road_traveltime_mask) — сохраняем единообразие модели вместо того, чтобы
    изобретать новую форму специально для пиков.

    Почему sigma_km=10.0 по умолчанию, а не "2-кратное затухание на 1 км" из
    черновика ТЗ (эквивалент sigma≈1.44 км): при таком sigma вклад падает
    ниже 5% уже за 4-5 км — на сетке 1×1 км это значит, что даже соседняя
    ячейка получает сильно урезанный сигнал. Во ВСЕХ остальных масках проекта
    с той же семантикой ("затухание от точки экономической активности")
    sigma лежит в диапазоне 8-30 км (distance_to_city=10, railway/power=8-10,
    distance_to_center=30). Пик суммарного слоя — по построению точка
    устойчивой концентрации, прошедшая через несколько масок и взвешивание,
    то есть по значимости ближе к городу, чем к отдельному объекту
    инфраструктуры — поэтому 10 км взято по аналогии с distance_to_city.
    ЭТО ОТПРАВНАЯ ТОЧКА для эмпирической проверки "на реальной карте глазами",
    как и просит ТЗ, — не финальное откалиброванное значение. Параметр
    специально не зашит константой внутрь функции без возможности переопределить.

    Параметры:
        cell_coords: (N,2) координаты центроидов ячеек, метры
        peak_coords: (K,2) координаты точек-пиков, метры
        lines: список рёбер (см. build_concentration_lines)
        sigma_km: масштаб затухания, км

    Возвращает:
        (N,) numpy array весов затухания 0..1
    """
    cell_coords = np.asarray(cell_coords, dtype=float)
    if len(peak_coords) == 0:
        return np.zeros(len(cell_coords))

    from scipy.spatial import cKDTree
    tree = cKDTree(np.asarray(peak_coords, dtype=float))
    dist, _ = tree.query(cell_coords)

    for a, b in lines:
        seg_dist = _point_segment_distance(cell_coords, a, b)
        dist = np.minimum(dist, seg_dist)

    return np.exp(-dist / (sigma_km * 1000.0))


def blend_decay(composed, decay, beta=0.3):
    """
    Подмешивает модель затухания от структуры пиков/линий (ТЗ п.6) к
    суммарному слою. Та же идея, что entropy_smoothing выше (подмешивание
    через один коэффициент), только источник сигнала другой.

    Параметры:
        composed: (N,) исходный суммарный слой
        decay: (N,) выход decay_from_structure
        beta: доля вклада затухания от структуры (0 = composed без изменений,
              1 = заменить composed чистым затуханием). beta=0.3 — отправная
              точка для визуальной проверки вместе с sigma_km выше, не
              откалиброванное значение.

    Возвращает:
        (N,) numpy array, нормированный в 0..1
    """
    blended = (1 - beta) * composed + beta * decay
    return blended / blended.max() if blended.max() > 0 else blended


COMPOSITION_DESCRIPTION = {
    "methods": [
        {
            "name": "weighted_sum",
            "description": "Линейная комбинация масок с коэффициентами значимости",
            "use_case": "Базовая композиция для большинства показателей"
        },
        {
            "name": "multiplicative_gating",
            "description": "Умножение базовой маски на исключающие маски (вода, лес)",
            "use_case": "Когда нужно полностью обнулить ячейки определённого типа"
        },
        {
            "name": "entropy_smoothing",
            "description": "Микс масочного распределения с равномерным через alpha",
            "use_case": "Контроль чрезмерной концентрации (alpha < 1)"
        },
        {
            "name": "detect_peaks",
            "description": "Выделение пиков на суммарном слое: percentile (топ N% "
                           "ненулевых ячеек по значению), zscore (статистический "
                           "выброс) или local_max (локальный максимум по соседям)",
            "use_case": "Подсветка хотспотов на карте композиции — отдельным слоем "
                        "поверх непрерывной заливки, чтобы пики не терялись в общей "
                        "палитре"
        },
        {
            "name": "cluster_peaks + build_concentration_lines",
            "description": "Группировка смежных ячеек-пиков в точки-представители "
                           "и минимальное остовное дерево (MST) между ними",
            "use_case": "Линии концентрации между пиками (ТЗ п.5)"
        },
        {
            "name": "decay_from_structure + blend_decay",
            "description": "exp(-d/sigma) от ближайшей точки структуры (пик или "
                           "линия), подмешивается к composed через beta",
            "use_case": "Убывающая модель затухания от пиков/линий (ТЗ п.6); "
                        "sigma_km и beta — открытые параметры для эмпирического "
                        "подбора и визуальной проверки, не константы"
        }
    ],
    "invariants": ["Сохранение суммы: sum(cells) = regional_value"]
}
