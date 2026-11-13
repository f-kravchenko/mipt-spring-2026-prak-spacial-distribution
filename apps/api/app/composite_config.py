"""
Результирующий слой (комплексная оценка) — справочник на бэке.

Модель. У каждого показателя СВОЙ вектор весов над 10 масками, заданный
экспертно: какая-то маска на показатель не влияет (вес 0), какая-то влияет
сильно. Именно это разводит слои — общие маски дают один и тот же рельеф, а
разные веса делают из него разную географию. Дальше §4.1 методики: слой
показателя нормируется в 0..100 (min-max по ячейкам региона) с учётом
направления, и слои складываются с весами показателей.

    Sᵢ = 100·(rawᵢ − minᵢ)/(maxᵢ − minᵢ)        прямое действие
    Sᵢ = 100·(maxᵢ − rawᵢ)/(maxᵢ − minᵢ)        обратное действие
    Результат = Σᵢ wᵢ·Sᵢ,   Σ wᵢ = 1  →  результат тоже в 0..100

Почему не нужна новая тайл-функция. Свод ЛИНЕЕН по маскам:
    Σᵢ wᵢ·Sᵢ = Σₘ Aₘ·maskₘ − off,
    Aₘ = Σᵢ kᵢ·W[i][m],   off = Σᵢ kᵢ·bᵢ,   kᵢ = 100·wᵢ·sᵢ/spanᵢ,
    sᵢ = +1 и bᵢ = minᵢ для прямого, sᵢ = −1 и bᵢ = maxᵢ для обратного.
То есть API считает эффективный вектор весов и сдвиг, а рисует уже имеющийся
tile_composition (миграция 0011 добавила ему off ровно под этот случай).

ponytail: линеаризация точна, пока ни у одного показателя нет веса на
regression_mask. Эта маска единственная зависит от показателя (своя на каждый
код), и в один коэффициент Aₘ её не свернуть. Сейчас у всех видимых
показателей эластичности нет — regression-строк в БД для них не существует, и
вес на неё был бы весом в пустоту. /api/composite это проверяет и отказывается
считать, а не выдаёт молча неверный слой. Когда эластичности появятся —
обобщать join в tile_composition до пары (slug, indicator_code).

ВЕСА НИЖЕ — ЧЕРНОВИК. Расставлены по смыслу показателя, не подобраны по данным;
их и надо заменить экспертными. Формат: слаг маски -> вес, суммировать до 1 не
обязательно (нормируется при расчёте), отсутствующая маска = 0.
"""

# Направление по §4.2/§4.1: "direct" — больше значит лучше/выше,
# "inverse" — больше значит хуже (балл считается от максимума).
# Вес показателя в своде — indicator_weight; 0 исключает показатель.
COMPOSITE = {
    "Y477110231": {  # Общие коэффициенты рождаемости
        "direction": "direct", "indicator_weight": 0.15,
        "masks": {"worldpop_mask": 0.50, "distance_to_city_mask": 0.20,
                  "territory_type_mask": 0.15, "road_network_mask": 0.10,
                  "baseline_mask": 0.05},
    },
    "Y477110196": {  # Коэффициенты естественного прироста населения
        "direction": "direct", "indicator_weight": 0.15,
        "masks": {"worldpop_mask": 0.45, "distance_to_city_mask": 0.25,
                  "territory_type_mask": 0.15, "road_network_mask": 0.10,
                  "baseline_mask": 0.05},
    },
    "Y477110197": {  # Коэффициенты миграционного прироста
        "direction": "direct", "indicator_weight": 0.10,
        "masks": {"distance_to_city_mask": 0.35, "worldpop_mask": 0.30,
                  "road_network_mask": 0.15, "road_traveltime_mask": 0.10,
                  "distance_to_center_mask": 0.10},
    },
    "Y477110217": {  # Обеспеченность детей местами в дошкольных организациях
        "direction": "direct", "indicator_weight": 0.15,
        "masks": {"worldpop_mask": 0.50, "territory_type_mask": 0.20,
                  "distance_to_city_mask": 0.20, "baseline_mask": 0.10},
    },
    "Y477140017": {  # Ввод в действие мощностей общеобразовательных организаций
        "direction": "direct", "indicator_weight": 0.10,
        "masks": {"worldpop_mask": 0.45, "distance_to_city_mask": 0.25,
                  "territory_type_mask": 0.20, "road_network_mask": 0.10},
    },
    "Y477110071": {  # Доходы консолидированных бюджетов
        "direction": "direct", "indicator_weight": 0.15,
        "masks": {"worldpop_mask": 0.35, "distance_to_center_mask": 0.25,
                  "territory_type_mask": 0.15, "road_network_mask": 0.15,
                  "railway_mask": 0.10},
    },
    "Y477110226": {  # Общая площадь жилых помещений на одного жителя.
        # Обратное действие: в плотной городской застройке метража на человека
        # МЕНЬШЕ, поэтому городские маски работают на понижение балла.
        "direction": "inverse", "indicator_weight": 0.10,
        "masks": {"worldpop_mask": 0.45, "distance_to_city_mask": 0.30,
                  "territory_type_mask": 0.25},
    },
    "Y477110211": {  # Мощность амбулаторно-поликлинических организаций
        "direction": "direct", "indicator_weight": 0.10,
        "masks": {"worldpop_mask": 0.40, "distance_to_city_mask": 0.30,
                  "road_network_mask": 0.15, "territory_type_mask": 0.15},
    },
    # Научно-технологический блок. Ведущая маска — science_mask (НИИ, вузовские
    # кампусы, академгородки): у науки есть конкретные точки, и до появления
    # этой маски её приходилось приближать «где центр региона» + «где живут
    # люди», что для патентов и НИОКР заведомо неверно. Используемые технологии
    # и затраты на инновации — это уже производство, у них ведут
    # territory/road/railway/power, а science идёт добавкой.
    "Y477110309": {  # Подано патентных заявок на изобретения
        "direction": "direct", "indicator_weight": 0.10,
        "masks": {"science_mask": 0.40, "worldpop_mask": 0.30,
                  "distance_to_center_mask": 0.20, "distance_to_city_mask": 0.10},
    },
    "Y477110473": {  # Персонал НИОКР: исследователи
        "direction": "direct", "indicator_weight": 0.10,
        "masks": {"science_mask": 0.45, "worldpop_mask": 0.30,
                  "distance_to_center_mask": 0.15, "distance_to_city_mask": 0.10},
    },
    "Y477110259": {  # Организации, выполнявшие научные исследования
        "direction": "direct", "indicator_weight": 0.08,
        "masks": {"science_mask": 0.45, "worldpop_mask": 0.25,
                  "distance_to_center_mask": 0.20, "road_network_mask": 0.10},
    },
    "Y477110176": {  # Используемые передовые производственные технологии
        "direction": "direct", "indicator_weight": 0.08,
        "masks": {"territory_type_mask": 0.28, "road_network_mask": 0.18,
                  "worldpop_mask": 0.17, "railway_mask": 0.14,
                  "power_lines_mask": 0.13, "science_mask": 0.10},
    },
    "Y477110352": {  # Разработанные передовые производственные технологии
        "direction": "direct", "indicator_weight": 0.07,
        "masks": {"science_mask": 0.35, "worldpop_mask": 0.25,
                  "distance_to_center_mask": 0.20, "territory_type_mask": 0.20},
    },
    "Y477110102": {  # Затраты на инновационную деятельность организаций
        "direction": "direct", "indicator_weight": 0.10,
        "masks": {"science_mask": 0.30, "territory_type_mask": 0.25,
                  "worldpop_mask": 0.20, "road_network_mask": 0.15,
                  "railway_mask": 0.10},
    },
    "Y477110295": {  # Плотность автодорог с твёрдым покрытием
        # единственный показатель, у которого есть прямой пространственный
        # аналог среди масок — road_network и берёт половину веса
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"road_network_mask": 0.50, "road_traveltime_mask": 0.25,
                  "worldpop_mask": 0.15, "distance_to_city_mask": 0.10},
    },
    # Блок 5 ТЗ — инфраструктура (ИНФ12–ИНФ18)
    "Y477110390": {  # ИНФ16 — степень износа основных фондов (обратный)
        "direction": "inverse", "indicator_weight": 0.05,
        "masks": {"territory_type_mask": 0.30, "road_network_mask": 0.20,
                  "power_lines_mask": 0.20, "worldpop_mask": 0.15,
                  "distance_to_city_mask": 0.15},
    },
    "C4771005012": {  # ИНФ12 — вузы на 100 тыс. жителей
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"science_mask": 0.35, "worldpop_mask": 0.30,
                  "distance_to_city_mask": 0.20, "territory_type_mask": 0.15},
    },
    "C4771005013": {  # ИНФ13 — объекты НТИ (ЦКП, УНУ, ПИШ)
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"science_mask": 0.50, "worldpop_mask": 0.25,
                  "distance_to_center_mask": 0.15, "territory_type_mask": 0.10},
    },
    "C4771005015": {  # ИНФ15 — коэффициент обновления основных фондов
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"territory_type_mask": 0.30, "road_network_mask": 0.20,
                  "power_lines_mask": 0.20, "worldpop_mask": 0.15,
                  "distance_to_city_mask": 0.15},
    },
    "C4771005018": {  # ИНФ18 — оборудование до 5 лет
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"science_mask": 0.40, "territory_type_mask": 0.20,
                  "power_lines_mask": 0.15, "worldpop_mask": 0.15,
                  "distance_to_city_mask": 0.10},
    },
    # ИНФ23/ИНФ2 — цифровая инфраструктура (ИКТ в организациях, 2024). Цифровые
    # технологии — городская и организационная история: население + близость к
    # городу + дороги; ИИ дополнительно leaning к science-маске (наукоёмкие
    # центры внедряют первыми).
    "Y477110170": {  # технологии ИИ
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"science_mask": 0.30, "worldpop_mask": 0.30,
                  "distance_to_city_mask": 0.20, "road_network_mask": 0.20},
    },
    "Y477110167": {  # облачные сервисы
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.35, "distance_to_city_mask": 0.25,
                  "road_network_mask": 0.20, "territory_type_mask": 0.20},
    },
    "Y477110172": {  # цифровые платформы
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.35, "distance_to_city_mask": 0.25,
                  "road_network_mask": 0.20, "territory_type_mask": 0.20},
    },
    "Y477110260": {  # организации с веб-сайтом
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.35, "distance_to_city_mask": 0.20,
                  "road_network_mask": 0.25, "territory_type_mask": 0.20},
    },
    "C4771002023": {  # ИНВ23 — темп роста затрат на инновационную деятельность
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"science_mask": 0.30, "territory_type_mask": 0.25,
                  "worldpop_mask": 0.20, "road_network_mask": 0.15,
                  "railway_mask": 0.10},
    },
    "Y477130022": {  # ЧК25 — з/п научных сотрудников к средней по региону
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"science_mask": 0.40, "worldpop_mask": 0.25,
                  "distance_to_center_mask": 0.20, "territory_type_mask": 0.15},
    },
    "C4771006051": {  # СР51 — городская зелень на жителя
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.40, "distance_to_city_mask": 0.25,
                  "territory_type_mask": 0.20, "road_network_mask": 0.15},
    },
    "C4771005021": {  # ИНФ21 — базовые станции мобильной связи на 100 тыс.
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.30, "distance_to_city_mask": 0.25,
                  "road_network_mask": 0.25, "territory_type_mask": 0.20},
    },
    "C4771005036": {  # ИНФ36 — действующие ОЭЗ (ППТ/ТВТ/портовые)
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"territory_type_mask": 0.30, "power_lines_mask": 0.20,
                  "road_network_mask": 0.20, "railway_mask": 0.15,
                  "distance_to_city_mask": 0.15},
    },
    "C4771005031": {  # ИНФ31 — балл ЦКПНО 0-2
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"science_mask": 0.50, "worldpop_mask": 0.20,
                  "distance_to_center_mask": 0.15, "territory_type_mask": 0.15},
    },
    # СР63/64/66/67 — культура из OSM. Городские объекты: население + центр
    # города; клубы (ДК) массовые и сельские — им ближе дороги и территория.
    "C4771006063": {  # СР63 — кинотеатры
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.40, "distance_to_city_mask": 0.30,
                  "road_network_mask": 0.15, "territory_type_mask": 0.15},
    },
    "C4771006064": {  # СР64 — концертные залы
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.40, "distance_to_center_mask": 0.25,
                  "distance_to_city_mask": 0.20, "territory_type_mask": 0.15},
    },
    "C4771006066": {  # СР66 — выставочные залы
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.40, "distance_to_city_mask": 0.30,
                  "territory_type_mask": 0.20, "road_network_mask": 0.10},
    },
    "C4771006067": {  # СР67 — клубные учреждения (ДК)
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.25, "road_network_mask": 0.25,
                  "territory_type_mask": 0.25, "distance_to_city_mask": 0.25},
    },
    # СР32/33 — доступность дорог: прямой пространственный аналог — маска
    # дорожной сети; доля населения тоже ведёт
    "C4771006032": {  # СР32 — федеральные дороги в 30 км
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"road_network_mask": 0.40, "worldpop_mask": 0.25,
                  "road_traveltime_mask": 0.20, "territory_type_mask": 0.15},
    },
    "C4771006033": {  # СР33 — региональные дороги в 30 км
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"road_network_mask": 0.40, "worldpop_mask": 0.25,
                  "road_traveltime_mask": 0.20, "territory_type_mask": 0.15},
    },
    "C4771003024": {  # НР24 — патентоёмкость инновационной продукции
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"science_mask": 0.45, "worldpop_mask": 0.20,
                  "distance_to_center_mask": 0.20, "territory_type_mask": 0.15},
    },
    "C4771005035": {  # ИНФ35 — коворкинги/фаблабы
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.35, "distance_to_city_mask": 0.25,
                  "road_network_mask": 0.20, "territory_type_mask": 0.20},
    },
    "C4771001015": {  # ЧК15 — доля занятых в ВТ/наукоёмких ВЭД
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"science_mask": 0.30, "worldpop_mask": 0.30,
                  "distance_to_city_mask": 0.20, "territory_type_mask": 0.20},
    },
    # базовая техника ИКТ-среза: веса как у цифровых собратьев выше
    "Y477110168": {  # персональные компьютеры
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.35, "distance_to_city_mask": 0.20,
                  "road_network_mask": 0.25, "territory_type_mask": 0.20},
    },
    "Y477110169": {  # серверы
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.35, "distance_to_city_mask": 0.25,
                  "road_network_mask": 0.20, "territory_type_mask": 0.20},
    },
    "Y477110166": {  # локальные вычислительные сети
        "direction": "direct", "indicator_weight": 0.05,
        "masks": {"worldpop_mask": 0.35, "distance_to_city_mask": 0.25,
                  "road_network_mask": 0.20, "territory_type_mask": 0.20},
    },
}

COMPOSITE_NAME = "Комплексная оценка (результирующий слой)"
COMPOSITE_DOMAIN = [0, 100]
