import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { fetchRegions, fetchIndicators, fetchMasks, fetchDefaultWeights, fetchMaskPeaks, fetchConcentrationStructure, fetchGlobalScale, recompute } from "./api";

// Специальный id пресета "Автоподбор": веса запрашиваются у бэкенда через
// GET /api/default-weights?indicator=... (src/masks/weighting.resolve_weights,
// см. §9 "обоснование весов" — зависят от r2/category показателя, поэтому
// больше не могут быть одним статичным числом здесь, как раньше all_5_masks).
const AUTO_PRESET_ID = "auto";

// Остальные пресеты — НЕ дефолты, а зафиксированные условия ablation-
// эксперимента (§9.5 ТЗ): сравнение с одним и тем же набором весов при
// исключении маски, поэтому они намеренно не пересчитываются под показатель.
// Источник чисел — etl/config.yaml -> ablation. Если меняешь веса там,
// синхронизируй и здесь (пока нет отдельного API для ablation-пресетов —
// см. заметку в PR/задаче про дублирование весов в 3 местах).
const PRESETS = [
  { id: "no_regression", name: "Без регрессии",
    weights: { worldpop_mask: 0.5, distance_to_city_mask: 0.25, distance_to_center_mask: 0.25 } },
  { id: "no_worldpop", name: "Без населения",
    weights: { regression_mask: 0.7, distance_to_city_mask: 0.15, distance_to_center_mask: 0.15 } },
  { id: "no_distance_to_city", name: "Без близости к городам",
    weights: { regression_mask: 0.6, worldpop_mask: 0.3, distance_to_center_mask: 0.1 } },
  { id: "no_distance_to_center", name: "Без близости к центру",
    weights: { regression_mask: 0.6, worldpop_mask: 0.3, distance_to_city_mask: 0.1 } },
  { id: "only_regression", name: "Только регрессия", weights: { regression_mask: 1 } },
  { id: "only_worldpop", name: "Только население", weights: { worldpop_mask: 1 } },
  { id: "only_baseline", name: "Только базовая (площадь)", weights: { baseline_mask: 1 } },
];

// Фолбэк, если бэкенд ещё без /api/default-weights (старый плоский набор) —
// чтобы приложение не падало молча, а честно продолжало работать по старой
// схеме до появления эндпоинта. См. fetchDefaultWeightsSafe ниже.
const FALLBACK_AUTO_WEIGHTS = {
  regression_mask: 0.5, worldpop_mask: 0.3, distance_to_city_mask: 0.1, distance_to_center_mask: 0.1,
};

// Цветовые шкалы
const MASK_C0 = "#f7fbff", MASK_C1 = "#08306b"; // синяя: вес маски 0..1
// Тепловая шкала распределения: синий (мало) -> красный (много). Прямая
// интерполяция синий->красный проходит через грязно-фиолетовый, поэтому
// многоступенчатая рампа (ColorBrewer RdYlBu, перевёрнутая).
const DIST_STOPS = ["#4575b4", "#91bfdb", "#ffffbf", "#fc8d59", "#d73027"];

// Подложка мини-карты (локатора): пустой фон, без внешних тайлов — это
// маленький обзорный виджет, детальная подложка ему не нужна и не нужен
// лишний сетевой запрос на каждый рендер.
const LOCATOR_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#dbe6f0" } }],
};

// Подложка главной карты: светлый растр CARTO (без API-ключа, публичный CDN).
// light_nolabels — без подписей: они бы спорили с подписями городов
// (city-circle попап) и общей палитрой данных.
const MAIN_STYLE = {
  version: 8,
  sources: {
    "carto-basemap": {
      type: "raster",
      tiles: ["https://basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}@2x.png"],
      tileSize: 256,
      maxzoom: 20,
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/attributions">CARTO</a>',
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#dbe6f0" } },
    { id: "basemap", type: "raster", source: "carto-basemap" },
  ],
};
const RF_BOUNDS = [[18, 40], [180, 82]]; // вид всей РФ (для мини-карты), без хвоста за 180°

// Индекс качества городской среды (Минстрой, 2024) — показатель-индекс: не
// сумма-сохраняемый, поэтому у него не пересчёт композиций (recompute), а
// свой слой векторных тайлов tile_index (миграция 0010): регион приходит из
// /api/regions с index_tile_url, ячейка несёт value (балл НП + затухание) и weight
// (плотность). grid-машинерия (маски/пики/шкала) для него выключается.
const INDEX_INDICATOR_NAME = "Индекс качества городской среды";
// Балл индекса: та же «холодное→горячее» шкала (RdYlBu, синий→красный), что
// у распределений в других регионах — низкий балл холодный, высокий горячий.
// Домен по разбросу городов области (см. etl/ingest_index_novosibirsk.py).
const INDEX_STOPS = DIST_STOPS;
// value = балл внутри контура НП (171..223), затухание вдвое/5 км вне него → до
// ~0 в глубине области. Поэтому шкала от 0, а не от 171 (мин. балл города):
// иначе весь ореол затухания и село схлопывались бы в один холодный цвет.
const INDEX_DOMAIN = [0, 223];
// Нелинейная (степенная) шкала: значения затухания скошены к 0 (95% ячеек —
// низкие), при линейной шкале карта была бы «почти вся синяя». gamma>1 сгущает
// цветовые стопы к низу диапазона → затухание получает больше оттенков.
// Позиции стопов (value, цвет) считаем один раз и делим между заливкой и легендой.
const INDEX_GAMMA = 2;
const INDEX_COLOR_STOPS = INDEX_STOPS.map((color, i) => ({
  color,
  v: INDEX_DOMAIN[0] + (INDEX_DOMAIN[1] - INDEX_DOMAIN[0])
     * Math.pow(i / (INDEX_STOPS.length - 1), INDEX_GAMMA),
}));
// Баллы реестра по НП (Минстрой-2024). Внутри контура value == балл города
// (затухание 0.5^0 = 1); чуть наружу — строго меньше. Так по value отличаем
// «курсор прямо над городом» от «ближайший город» без флага в тайле.
const CITY_SCORE = {
  Новосибирск: 223, Бердск: 210, Обь: 216, Искитим: 179, Куйбышев: 200,
  Барабинск: 178, Карасук: 205, Татарск: 195, Купино: 180, Черепаново: 180,
  Болотное: 178, Каргат: 178, Чулым: 173, Тогучин: 171,
};

// Маски присутствия индекс-региона (та же панель чекбоксов/ползунков, что у
// других регионов). Компоненты 0..1 приходят в тайле tile_index по ячейке;
// вес (яркость) собирается ЗДЕСЬ выражением fill-opacity — смена веса
// перекрашивает мгновенно, без пересчёта тайлов. slug = ключ в свойствах тайла.
const INDEX_MASKS = [
  { slug: "pop",   title: "Население (WorldPop)", w: 0.30,
    tip: "WorldPop, растр 1 км. Зональная сумма населения по ячейке.\nНормировка: sqrt(v / p95)." },
  { slug: "poi",   title: "POI (OSM)",            w: 0.22,
    tip: "OSM amenity/shop. Плотность точек по ячейке (инфраструктура).\nНормировка: sqrt(v / p95)." },
  { slug: "green", title: "Зелень (OSM)",         w: 0.12,
    tip: "OSM parks/gardens/grass (городская зелень, без леса).\nПлотность по ячейке, sqrt(v / p95)." },
  { slug: "road",  title: "Дороги (OSM)",         w: 0.125,
    tip: "Близость к крупным дорогам OSM (motorway…secondary).\nЗатухание exp(-d/σ), σ=5 км." },
  { slug: "rail",  title: "Ж/д (OSM)",            w: 0.075,
    tip: "Близость к железным дорогам OSM (railway=rail).\nЗатухание exp(-d/σ), σ=6 км." },
  { slug: "power", title: "ЛЭП (OSM)",            w: 0.05,
    tip: "Близость к ЛЭП OSM (power=line).\nЗатухание exp(-d/σ), σ=8 км." },
  { slug: "city",  title: "Близость к городу",    w: 0.11,
    tip: "Затухание к ближайшему из 14 городов (гаусс, σ=15 км).\nБазовое присутствие, чтобы не было дыр." },
];
const INDEX_DEFAULT_W = Object.fromEntries(INDEX_MASKS.map((m) => [m.slug, m.w]));

// fill-opacity: пол 0.12 (сплошное покрытие, как baseline) + вклад масок,
// нормированный на сумму весов (дефолт воспроизводит исходную смесь). Все
// маски выкл (Σ=0) → ровный фон 0.12.
// Пол непрозрачности: даже при нулевом «присутствии» цвет-балл должен читаться
// (в частности value=0 — сплошной синий, а не выцветшая подложка). Присутствие
// модулирует яркость выше пола (FLOOR..0.9), но не гасит цвет в прозрачность.
const INDEX_OPACITY_FLOOR = 0.5;
function buildIndexOpacity(w) {
  const slugs = INDEX_MASKS.map((m) => m.slug);
  const sum = slugs.reduce((s, k) => s + (w[k] || 0), 0);
  if (sum <= 0) return INDEX_OPACITY_FLOOR;
  const terms = slugs.filter((k) => w[k] > 0).map((k) => ["*", w[k] / sum, ["get", k]]);
  return ["interpolate", ["linear"], ["+", 0, ...terms], 0, INDEX_OPACITY_FLOOR, 1, 0.9];
}

// Задержка автопересчёта при движении слайдера веса (мс). Достаточно, чтобы
// не слать запрос на каждый пиксель драга, но ощущаться "живым", как в
// Google Maps при перетаскивании — без обязательного нажатия кнопки.
const WEIGHT_DEBOUNCE_MS = 400;

// ---- Персистентность состояния (localStorage + URL) ----
// Последнее состояние переживает перезагрузку страницы: URL-параметры
// (?region=&indicator=&scale=) имеют приоритет над localStorage — ссылкой
// можно делиться, она открывает ровно то же представление. Остальные
// настройки (веса по показателям, пики, затухание, слои) — в localStorage
// под версионным ключом: смена формата в будущем не ломает старые записи
// (чужая версия просто игнорируется). Живые данные (recompute/structure)
// не сохраняются — пересчитываются по восстановленным настройкам.
const STORAGE_KEY = "deaggr:state:v1";

function loadInitialState() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch { /* приватный режим/запрет storage — работаем без памяти */ }
  const url = new URLSearchParams(window.location.search);
  const urlScale = url.get("scale");
  return {
    ...saved,
    regionId: Number(url.get("region")) || saved.regionId || null,
    indicator: url.get("indicator") || saved.indicator || null,
    scaleMode: urlScale === "russia" || urlScale === "territory"
      ? urlScale
      : (saved.scaleMode === "russia" ? "russia" : "territory"),
  };
}

function fmt(x) {
  if (x == null) return "—";
  const a = Math.abs(x);
  if (a >= 1000) return x.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  if (a >= 1 || a === 0) return x.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
  // < 1: три ЗНАЧАЩИХ цифры, не три знака после запятой — иначе малые
  // значения ячеек (0.0004 млн руб.) округляются в "0"
  if (a < 1e-6) return x.toExponential(2).replace(".", ",");
  return x.toLocaleString("ru-RU", { maximumSignificantDigits: 3 });
}

// Расширенные границы bbox (для maxBounds — немного контекста вокруг региона).
function padBounds([minx, miny, maxx, maxy], f = 0.4) {
  const dx = (maxx - minx) * f, dy = (maxy - miny) * f;
  return [[minx - dx, miny - dy], [maxx + dx, maxy + dy]];
}

// Порядок слоёв снизу вверх. Каждый слой управляется своим эффектом независимо,
// поэтому при добавлении вставляем его перед ближайшим слоем с большим рангом —
// так переключение городов/дорог не трогает распределение и маски.
const RANK = { dist: 0, mask: 1, road: 2, city: 3, structure: 4 };

function addOrdered(map, ranks, layerDef, rank) {
  let beforeId, best = Infinity;
  for (const [id, r] of Object.entries(ranks.current)) {
    if (r > rank && r < best && map.getLayer(id)) { best = r; beforeId = id; }
  }
  map.addLayer(layerDef, beforeId);
  ranks.current[layerDef.id] = rank;
}

function dropLayer(map, ranks, layerId, sourceId) {
  if (map.getLayer(layerId)) map.removeLayer(layerId);
  if (sourceId && map.getSource(sourceId)) map.removeSource(sourceId);
  delete ranks.current[layerId];
}

export default function App() {
  // Восстановленное состояние (URL > localStorage > дефолты) — читается один
  // раз при монтировании, до первого автопересчёта.
  const initState = useRef(loadInitialState()).current;

  const mapRef = useRef(null);
  const containerRef = useRef(null);
  const ranks = useRef({}); // layerId -> ранг (для упорядоченной вставки)
  const [mapReady, setMapReady] = useState(false);

  const locatorRef = useRef(null);
  const locatorContainerRef = useRef(null);
  const [locatorReady, setLocatorReady] = useState(false);

  const [regions, setRegions] = useState([]);
  const [indicators, setIndicators] = useState([]);
  const [masks, setMasks] = useState([]);

  const [regionId, setRegionId] = useState(null);
  const [indicator, setIndicator] = useState(null);
  const [maskState, setMaskState] = useState({}); // slug -> {visible, opacity}
  const [contractSlug, setContractSlug] = useState(null);
  // Города по умолчанию скрыты — это справочный оверлей для визуальной сверки
  // распределения, а не часть результата; включаются в панели слоёв.
  const [showCities, setShowCities] = useState(initState.showCities ?? false);
  const [showRoads, setShowRoads] = useState(initState.showRoads ?? true);
  const [showBorders, setShowBorders] = useState(initState.showBorders ?? true);

  // Распределение всегда считается живьём по весам; пресет — стартовый набор весов.
  const [presetId, setPresetId] = useState(AUTO_PRESET_ID);
  const [weights, setWeights] = useState({ ...FALLBACK_AUTO_WEIGHTS }); // slug -> вес
  const [liveComp, setLiveComp] = useState(null);              // {tile_url, value_max, metrics}
  const [structure, setStructure] = useState(null);             // GeoJSON: пики + линии концентрации (ТЗ п.5)

  // Веса масок присутствия индекс-региона (см. INDEX_MASKS). Отдельно от
  // weights (те — для recompute обычных регионов). indexWeights — черновик
  // (ползунки/чекбоксы), на карту попадает только по кнопке «Пересчитать»
  // (appliedIndexW). Пока карта перерисовывается — кнопка неактивна.
  const [indexWeights, setIndexWeights] = useState(initState.indexWeights || INDEX_DEFAULT_W);
  const [appliedIndexW, setAppliedIndexW] = useState(initState.indexWeights || INDEX_DEFAULT_W);
  const [indexComputing, setIndexComputing] = useState(false);
  const idxPrevRef = useRef({}); // прежний вес маски для возврата по чекбоксу
  const toggleIndexMask = (slug) => setIndexWeights((w) => {
    const cur = w[slug] || 0;
    if (cur > 0) { idxPrevRef.current[slug] = cur; return { ...w, [slug]: 0 }; }
    return { ...w, [slug]: idxPrevRef.current[slug] || 0.2 };
  });
  const setIndexWeight = (slug, v) => setIndexWeights((w) => ({ ...w, [slug]: v }));
  const resetIndexWeights = () =>
    setIndexWeights(Object.fromEntries(INDEX_MASKS.map((m) => [m.slug, 0])));
  const indexDirty = JSON.stringify(indexWeights) !== JSON.stringify(appliedIndexW);
  const applyIndexWeights = () => {
    if (!indexDirty) return;
    setIndexComputing(true);           // снимется по событию карты 'idle' (эффект ниже)
    setAppliedIndexW(indexWeights);
  };

  // Левая панель — плавающая карточка (не edge-to-edge сайдбар), можно свернуть.
  // panelWidth измеряется реально (ResizeObserver), а не константой, чтобы
  // локатор мог отталкиваться от фактической ширины, а не от захардкоженного
  // числа (см. .locator ниже) — при сворачивании/адаптиве ширина меняется.
  const [collapsed, setCollapsed] = useState(false);
  const panelRef = useRef(null);
  const [panelWidth, setPanelWidth] = useState(340);

  // Debounce автопересчёта при движении слайдера веса.
  const weightDebounceRef = useRef(null);
  useEffect(() => () => clearTimeout(weightDebounceRef.current), []);

  // Модель затухания от пиков/линий концентрации (ТЗ п.6). enabled=false по
  // умолчанию — это интерпретационный оверлей поверх основного слоя, должен
  // включаться осознанно, не навязываться. sigmaKm/beta — открытые параметры
  // для эмпирического подбора "на глаз", как прямо просит ТЗ; дефолт sigmaKm=10
  // обоснован в README_part3 §8 (аналогия с distance_to_city).
  const [decay, setDecay] = useState({ enabled: false, sigmaKm: 10, beta: 0.3, ...initState.decay });
  const decayDebounceRef = useRef(null);
  useEffect(() => () => clearTimeout(decayDebounceRef.current), []);

  // Контур ячеек-пиков (топ-5%) на суммарном слое и слоях масок. Выключение —
  // через setLayoutProperty(visibility), НЕ через пересоздание слоёв (см.
  // урок с переключателем шкалы: пересоздание источника перезагружает тайлы).
  const [showPeaks, setShowPeaks] = useState(initState.showPeaks ?? true);
  // Фиолетовые точки-пики и рёбра MST (структура концентрации) — тем же приёмом.
  const [showStructure, setShowStructure] = useState(initState.showStructure ?? true);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const vis = showPeaks ? "visible" : "none";
    if (map.getLayer("dist-peak-outline")) map.setLayoutProperty("dist-peak-outline", "visibility", vis);
    for (const m of masks) {
      const id = `mask-${m.slug}-peak-outline`;
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
    }
    const svis = showStructure ? "visible" : "none";
    for (const id of ["concentration-lines", "concentration-peaks"])
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", svis);
  }, [mapReady, showPeaks, showStructure, masks]);

  // Правило Парето для пиков концентрации: доля массы пиковых ячеек, которую
  // накрывают сильнейшие кластеры (дефолт 0.90 — Парето-90). Живой слайдер с
  // debounce: перезапрашивается только structure, не весь recompute.
  const [peakShare, setPeakShare] = useState(initState.peakShare ?? 0.90);
  const shareDebounceRef = useRef(null);
  useEffect(() => () => clearTimeout(shareDebounceRef.current), []);

  // Масштаб сравнения: "territory" — цвета растянуты до максимума текущей
  // территории (видна её внутренняя структура); "russia" — шкала фиксирована
  // по всем регионам (p99 из /api/global-scale): одинаковый цвет означает
  // одинаковое абсолютное значение показателя, при переключении территории
  // шкала не меняется. Значение ячейки одно и то же (cell_abs через
  // региональный показатель) — селектор меняет только окраску и подписи.
  const [scaleMode, setScaleMode] = useState(initState.scaleMode);
  const [globalScale, setGlobalScale] = useState(null); // {p99, national_total}

  // p99 другого показателя — не наша шкала: сбрасываем, чтобы карта не красилась
  // по чужому домену, пока едет свежий ответ (легенда покажет "считаем…").
  useEffect(() => { setGlobalScale(null); }, [indicator]);

  // Шкала РФ зависит от показателя и весов -> перезапрашивается после каждого
  // пересчёта (liveComp) в режиме "russia". liveComp меняется после setWeights,
  // так что weights здесь всегда актуальны. Полученное значение живёт в стейте
  // и при повторном переключении Территория->Россия применяется мгновенно.
  useEffect(() => {
    if (scaleMode !== "russia" || !indicator || !liveComp) return;
    let cancelled = false;
    fetchGlobalScale(indicator, weights)
      .then((s) => { if (!cancelled) setGlobalScale(s); })
      .catch(() => { if (!cancelled) setGlobalScale(null); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scaleMode, indicator, liveComp]);

  // Шкала цвета — дивергентная вокруг "базовой концентрации": base — значение
  // ячейки при РАВНОМЕРНОМ размазывании показателя (rv / число ячеек).
  // Линейная шкала 0..max на скошенном распределении даёт "всё синее +
  // крошечные красные пики" (95% ячеек в нижней десятой диапазона); шкала
  // в кратных базы (коэффициент локализации, LQ = value/base) показывает
  // и разрежение (LQ<1, холодное), и градации концентрации (лог-ступени
  // 4x/16x, тёплое). Жёлтая середина RdYlBu = ровно базовый уровень.
  // Территория: база своего региона; Россия: общая база по всем регионам
  // (base_cell из /api/global-scale) — цвета сопоставимы между регионами.
  const LQ_MULTS = [0, 0.5, 1, 4, 16]; // множители базы для DIST_STOPS
  const selRegionCells = regions.find((r) => r.id === regionId)?.cell_count ?? 0;
  const territoryBase = liveComp?.regional_value > 0 && selRegionCells > 0
    ? liveComp.regional_value / selRegionCells : null;
  const distBase = scaleMode === "russia" && globalScale?.base_cell > 0
    ? globalScale.base_cell : territoryBase;
  const distVmax = liveComp?.value_max > 0 ? liveComp.value_max : 1;
  // для тултипа: читается из хендлера hover через ref, чтобы смена режима
  // шкалы не попадала в зависимости эффекта создания слоя (не пересоздавать
  // источник ради подписи)
  const distBaseRef = useRef(null);
  distBaseRef.current = distBase;
  const distFillColor = (base) => ["interpolate", ["linear"], ["get", "value"],
    ...(base > 0
      ? LQ_MULTS.flatMap((m, i) => [base * m, DIST_STOPS[i]])
      // фолбэк, пока база не готова: старая линейная шкала 0..vmax
      : DIST_STOPS.flatMap((c, i) => [distVmax * i / (DIST_STOPS.length - 1), c]))];

  // Переключение Территория/Россия меняет ТОЛЬКО paint-свойство готового слоя
  // (setPaintProperty — мгновенная перекраска уже загруженных тайлов).
  // Пересоздавать источник нельзя: MapLibre перезагрузил бы все тайлы, и вместе
  // с ~4 с на /api/global-scale переключатель выглядел бы "не работающим".
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !map.getLayer("dist-fill")) return;
    map.setPaintProperty("dist-fill", "fill-color", distFillColor(distBase));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, distBase]);

  useEffect(() => {
    const el = panelRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => setPanelWidth(entries[0].contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Инициализация карты + загрузка справочников
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAIN_STYLE,
      center: [95, 64],
      zoom: 2.2,
      // Подложка CARTO/OSM -> атрибуция обязательна (лицензия ODbL);
      // compact — свёрнута в кнопку (i), не занимает угол строкой.
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl());
    window.__map = map; // отладка из консоли: getPaintProperty, queryRenderedFeatures
    map.on("load", () => setMapReady(true));

    // Попап по клику на город (подписей нет — в style нет glyphs, поэтому имя в попапе)
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: true });
    map.on("click", "city-circle", (e) => {
      const p = e.features[0].properties;
      const pop = p.population != null && p.population !== "" ? fmt(Number(p.population)) : "—";
      popup.setLngLat(e.lngLat)
        .setHTML(`<b>${p.name || "—"}</b><br>${p.place || ""} · нас. ${pop}`)
        .addTo(map);
    });
    map.on("mouseenter", "city-circle", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "city-circle", () => { map.getCanvas().style.cursor = ""; });
    mapRef.current = map;

    // Мини-карта (локатор): вся РФ, выбранный регион залит цветом. Зумится.
    const locator = new maplibregl.Map({
      container: locatorContainerRef.current,
      style: LOCATOR_STYLE,
      bounds: RF_BOUNDS,
      fitBoundsOptions: { padding: 6 },
      attributionControl: false,
    });
    locator.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    locator.on("load", () => {
      locator.addSource("rf", { type: "geojson", data: "/russia.geojson" });
      locator.addLayer({ id: "rf-fill", type: "fill", source: "rf",
        paint: { "fill-color": "#eef3f8", "fill-opacity": 0.95 } });
      locator.addLayer({ id: "rf-line", type: "line", source: "rf",
        paint: { "line-color": "#b7c6d6", "line-width": 0.4 } });
      // Заливка выбранного региона (фильтр по name выставляется в эффекте ниже)
      locator.addLayer({ id: "rf-sel", type: "fill", source: "rf",
        filter: ["==", ["get", "name"], "__none__"],
        paint: { "fill-color": "#d94801", "fill-opacity": 0.6 } });
      locator.addLayer({ id: "rf-sel-line", type: "line", source: "rf",
        filter: ["==", ["get", "name"], "__none__"],
        paint: { "line-color": "#8c2d04", "line-width": 1.2 } });
      setLocatorReady(true);
    });
    locatorRef.current = locator;

    Promise.all([fetchRegions(), fetchIndicators(), fetchMasks()]).then(
      ([rg, ind, mk]) => {
        setRegions(rg);
        setIndicators(ind);
        setMasks(mk);
        setMaskState(Object.fromEntries(mk.map((m) => [m.slug, { visible: false, opacity: 0.7 }])));
        setWeights(Object.fromEntries(mk.map((m) => [m.slug, FALLBACK_AUTO_WEIGHTS[m.slug] ?? 0])));
        // Восстановленные регион/показатель применяем только если они ещё
        // существуют в справочниках (данные могли смениться) — иначе дефолт.
        if (rg.length)
          setRegionId(rg.some((r) => r.id === initState.regionId) ? initState.regionId : rg[0].id);
        if (ind.length)
          setIndicator(ind.some((i) => i.code === initState.indicator) ? initState.indicator : ind[0].code);
      }
    );
    return () => { map.remove(); locator.remove(); };
  }, []);

  // Центрирование на регионе: показываем только выбранный регион
  useEffect(() => {
    const map = mapRef.current;
    const reg = regions.find((r) => r.id === regionId);
    if (!map || !mapReady || !reg) return;
    const b = reg.bbox;
    map.setMaxBounds(padBounds(b));
    map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 800 });
  }, [regionId, regions, mapReady]);

  // Подсветка региона на мини-карте: заливаем его полигон в russia.geojson.
  // Имя там без суффикса «, центр» (Якутия), поэтому берём часть до запятой.
  useEffect(() => {
    const locator = locatorRef.current;
    const reg = regions.find((r) => r.id === regionId);
    if (!locator || !locatorReady || !reg) return;
    const name = reg.name.split(",")[0].trim();
    const flt = ["==", ["get", "name"], name];
    locator.setFilter("rf-sel", flt);
    locator.setFilter("rf-sel-line", flt);
  }, [regionId, regions, locatorReady]);

  // Пересчёт распределения по явному набору весов (кнопка/пресет/смена региона/debounce).
  // Линии концентрации (ТЗ п.5) запрашиваются тем же набором весов — пики
  // на суммарном слое зависят от весов так же, как сам слой распределения.
  // Порядковая защита от гонки ответов: recompute тяжёлый (~1-2 с SQL), запросы
  // летят параллельно, и без счётчика ПОЗДНИЙ ответ от УСТАРЕВШИХ весов может
  // перетереть свежий (типичный случай: подвигал слайдеры -> "сброс" -> слайдеры
  // вернулись, а карта осталась от нащёлканных весов). Принимаем только ответ
  // последнего отправленного запроса; structure — отдельный счётчик, т.к. её
  // запрашивают и runRecompute, и слайдер Парето.
  const distSeqRef = useRef(0);
  const structSeqRef = useRef(0);

  const fetchStructureSafe = (w, share) => {
    const seq = ++structSeqRef.current;
    fetchConcentrationStructure(regionId, indicator, w, 0.10, 10, share)
      .then((s) => { if (seq === structSeqRef.current) setStructure(s); })
      .catch(() => { if (seq === structSeqRef.current) setStructure(null); });
  };

  const [computing, setComputing] = useState(false);
  const runRecompute = (w) => {
    if (regionId == null || !indicator) return;
    const seq = ++distSeqRef.current;
    setComputing(true);
    recompute(regionId, indicator, w)
      .then((r) => { if (seq === distSeqRef.current) { setLiveComp({ ...r, sum_preserved: true }); setComputing(false); } })
      .catch(() => { if (seq === distSeqRef.current) { setLiveComp(null); setComputing(false); } });
    fetchStructureSafe(w, peakShare);
  };

  // Смена доли Парето перезапрашивает только пики/линии (не пересчёт слоя):
  // порог отбора кластеров — свойство отображения структуры, значения ячеек
  // от него не зависят.
  useEffect(() => {
    if (regionId == null || !indicator) return;
    clearTimeout(shareDebounceRef.current);
    shareDebounceRef.current = setTimeout(() => {
      fetchStructureSafe(weights, peakShare);
    }, WEIGHT_DEBOUNCE_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [peakShare]);

  // веса по всем маскам (отсутствующие в наборе -> 0)
  const fullWeights = (base) => Object.fromEntries(masks.map((m) => [m.slug, base[m.slug] ?? 0]));

  // Веса для пресета "Автоподбор": запрашиваем у бэкенда (см. AUTO_PRESET_ID
  // выше). Если эндпоинта ещё нет (404/сеть) — честно откатываемся на
  // FALLBACK_AUTO_WEIGHTS, а не роняем приложение молча.
  const fetchDefaultWeightsSafe = async () => {
    if (!indicator) return fullWeights({});
    try {
      const w = await fetchDefaultWeights(indicator);
      return fullWeights(w);
    } catch {
      return fullWeights(FALLBACK_AUTO_WEIGHTS);
    }
  };

  // Настройка весов живёт ОТДЕЛЬНО у каждого показателя: слайдеры/чекбоксы/
  // пресет, выставленные для показателя A, не теряются при переключении на B
  // и восстанавливаются при возврате. Ключ — код показателя.
  const savedByIndicatorRef = useRef(initState.savedByIndicator || {}); // indicator -> {presetId, weights}
  const indicatorRef = useRef(indicator);
  indicatorRef.current = indicator;
  const saveTuning = (w, pid) => {
    if (indicatorRef.current)
      savedByIndicatorRef.current[indicatorRef.current] = { presetId: pid, weights: w };
  };

  const selectPreset = async (id) => {
    setPresetId(id);
    clearTimeout(weightDebounceRef.current);
    if (id === AUTO_PRESET_ID) {
      const w = await fetchDefaultWeightsSafe();
      setWeights(w);
      saveTuning(w, id);
      runRecompute(w);
      return;
    }
    const p = PRESETS.find((x) => x.id === id) || PRESETS[0];
    const w = fullWeights(p.weights);
    setWeights(w);
    saveTuning(w, id);
    runRecompute(w);  // пресет — явное действие, считаем сразу
  };

  // Сброс = обнуление всех весов (не возврат к пресету): чистый лист, маски
  // включаются заново чекбоксами/пресетом. recompute с нулями вернёт 400 ->
  // на карте пусто + сообщение "все маски выключены" (см. под кнопкой).
  const resetWeights = () => {
    clearTimeout(weightDebounceRef.current);
    const zeros = fullWeights({});
    setWeights(zeros);
    saveTuning(zeros, presetId);
    runRecompute(zeros);
  };

  // Автопересчёт при смене региона/показателя. Если у показателя есть
  // сохранённая настройка — восстанавливаем её; иначе для "Автоподбора"
  // переспрашиваем веса у бэкенда (они зависят от r2/category показателя).
  const wRef = useRef(weights);
  wRef.current = weights;
  const presetRef = useRef(presetId);
  presetRef.current = presetId;
  useEffect(() => {
    // Индекс-регион (index_tile_url): grid-слой не считается (нет масок/суммы) —
    // гасим живые данные, рисует отдельный эффект nsk-index ниже.
    if (regions.find((r) => r.id === regionId)?.index_tile_url) {
      setLiveComp(null); setStructure(null); return;
    }
    if (regionId == null || !indicator) return;
    const saved = savedByIndicatorRef.current[indicator];
    if (saved) {
      setPresetId(saved.presetId);
      setWeights(saved.weights);
      runRecompute(saved.weights);
    } else if (presetRef.current === AUTO_PRESET_ID) {
      selectPreset(AUTO_PRESET_ID);
    } else {
      runRecompute(wRef.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regionId, indicator, regions]);

  // Запись состояния: localStorage (debounce — не на каждый пиксель драга) +
  // URL через replaceState (без засорения истории браузера; ссылкой можно
  // делиться). weights в зависимостях — чтобы настройки весов по показателям
  // (savedByIndicatorRef) тоже досохранялись после каждого изменения.
  const persistDebounceRef = useRef(null);
  useEffect(() => {
    clearTimeout(persistDebounceRef.current);
    persistDebounceRef.current = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
          regionId, indicator, scaleMode,
          savedByIndicator: savedByIndicatorRef.current,
          peakShare, showPeaks, showStructure, decay, showCities, showRoads, showBorders,
          indexWeights: appliedIndexW,
        }));
      } catch { /* приватный режим — просто без памяти */ }
    }, 500);
    return () => clearTimeout(persistDebounceRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regionId, indicator, scaleMode, peakShare, showPeaks, showStructure,
      decay, showCities, showRoads, showBorders, weights, appliedIndexW]);

  useEffect(() => {
    if (regionId == null || !indicator) return;
    const p = new URLSearchParams(window.location.search);
    p.set("region", String(regionId));
    p.set("indicator", indicator);
    p.set("scale", scaleMode);
    window.history.replaceState(null, "", `${window.location.pathname}?${p}`);
  }, [regionId, indicator, scaleMode]);

  // Слой распределения (низ) + hover-тултип по ячейке. Источник — живой
  // пересчёт (liveComp). Хендлеры hover снимаются в cleanup эффекта, иначе
  // при каждом пересчёте (смена liveComp) они бы накапливались дублями.
  // decay в зависимостях — переключение "показать зону затухания" или сдвиг
  // sigma/beta должны пересоздать источник с новыми query-параметрами тайла
  // (см. tile_composition в 0007_tile_composition_decay.sql).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    dropLayer(map, ranks, "dist-peak-outline");
    dropLayer(map, ranks, "dist-fill", "dist");
    const dist = liveComp;
    if (!dist || !dist.tile_url) return;

    let url = dist.tile_url;
    if (decay.enabled && structure) {
      const peaks = structure.features
        .filter((f) => f.properties.kind === "peak")
        .map((f) => ({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] }));
      const lines = structure.features
        .filter((f) => f.properties.kind === "concentration_line")
        .map((f) => f.geometry.coordinates);
      url += `&peaks=${encodeURIComponent(JSON.stringify(peaks))}`;
      url += `&lines=${encodeURIComponent(JSON.stringify(lines))}`;
      url += `&sigma_km=${decay.sigmaKm}&beta=${decay.beta}&vmax=${dist.value_max ?? 0}`;
    }

    // maxzoom 13: дальше maplibre переиспользует (overzoom) тайлы — сетка 1 км,
    // мельче детали нет, лишних запросов на z14+ не делаем.
    map.addSource("dist", { type: "vector", tiles: [url], minzoom: 0, maxzoom: 13 });
    // База шкалы (distBase) намеренно берётся замыканием, а не через
    // зависимости эффекта: её живая смена обрабатывается setPaintProperty
    // выше, пересоздание источника не нужно.
    addOrdered(map, ranks, {
      id: "dist-fill", type: "fill", source: "dist", "source-layer": "distribution",
      paint: {
        "fill-color": distFillColor(distBase),
        "fill-opacity": 0.85,
      },
    }, RANK.dist);

    // Пики (§9 "пики на суммарном слое"): контур поверх заливки для ячеек
    // >= peak_threshold (top 5%, считается в SQL — см. _AGG_SQL/peak_threshold
    // в /api/recompute). Отдельный тонкий слой, а не перекраска fill-color, —
    // иначе пики теряются в той же непрерывной палитре, что и остальной слой.
    if (dist.peak_threshold != null) {
      addOrdered(map, ranks, {
        id: "dist-peak-outline", type: "line", source: "dist", "source-layer": "distribution",
        filter: [">=", ["get", "value"], dist.peak_threshold],
        layout: { visibility: showPeaks ? "visible" : "none" },
        // Толщина/прозрачность по зуму: на обзорном масштабе ячейка — 1-3 px,
        // и фиксированный контур визуально забивает заливку (сплошная чернота
        // вместо тепловой карты). На обзоре контур почти исчезает.
        paint: {
          "line-color": "#111111",
          "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.1, 10, 0.7, 13, 1.6],
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 7, 0.15, 10, 0.5, 13, 0.8],
        },
      }, RANK.dist);
    }

    // Тултип: абсолют + доля территории + доля России (все сразу, независимо
    // от режима шкалы). Доля России = cell_abs / national_total — cell_abs уже
    // получен через региональный показатель (tile_composition), умножать вес
    // ячейки сразу на национальный итог нельзя.
    const indMeta = indicators.find((i) => i.code === indicator);
    const hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
    const onMove = (e) => {
      const v = Number(e.features[0].properties.value);
      map.getCanvas().style.cursor = "pointer";
      const unit = indMeta?.unit ? ` ${indMeta.unit}` : "";
      const rows = [`Абсолютно: <b>${fmt(v)}</b>${unit}`];
      if (distBaseRef.current > 0)
        rows.push(`Концентрация: ×${fmt(v / distBaseRef.current)} базовой`);
      if (dist.regional_value > 0)
        rows.push(`Доля территории: ${fmt((v / dist.regional_value) * 100)}%`);
      if (indMeta?.national_total > 0)
        rows.push(`Доля России: ${fmt((v / indMeta.national_total) * 100)}%`);
      hoverPopup.setLngLat(e.lngLat).setHTML(rows.join("<br>")).addTo(map);
    };
    const onLeave = () => {
      map.getCanvas().style.cursor = "";
      hoverPopup.remove();
    };
    map.on("mousemove", "dist-fill", onMove);
    map.on("mouseleave", "dist-fill", onLeave);

    return () => {
      map.off("mousemove", "dist-fill", onMove);
      map.off("mouseleave", "dist-fill", onLeave);
      hoverPopup.remove();
    };
    // scaleMode/globalScale намеренно НЕ в зависимостях — см. setPaintProperty выше.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, liveComp, decay, structure, indicators, indicator]);

  // Маски (над распределением). Зависят только от своего состояния.
  // Контур пиков (ТЗ п.2) на каждом видимом слое — не только на суммарном
  // (ТЗ п.4, dist-peak-outline выше): порог считается в SQL (percentile_cont,
  // /api/mask-peaks), фронт только сравнивает значение с порогом. cancelled —
  // защита от гонки, если maskState/regionId поменялись до ответа fetch.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    let cancelled = false;
    for (const m of masks) {
      dropLayer(map, ranks, `mask-${m.slug}-peak-outline`);
      dropLayer(map, ranks, `mask-${m.slug}-fill`, `mask-${m.slug}`);
    }
    if (regions.find((r) => r.id === regionId)?.index_tile_url) return; // у индекс-региона масок нет
    for (const m of masks) {
      const st = maskState[m.slug];
      if (!st || !st.visible) continue;
      let url = m.tile_url;
      if (m.indicator_dependent && indicator) url += `&indicator=${indicator}`;
      const sid = `mask-${m.slug}`;
      map.addSource(sid, { type: "vector", tiles: [url], minzoom: 0, maxzoom: 13 });
      addOrdered(map, ranks, {
        id: `${sid}-fill`, type: "fill", source: sid, "source-layer": "mask",
        paint: {
          "fill-color": ["interpolate", ["linear"], ["get", "weight"], 0, MASK_C0, 1, MASK_C1],
          "fill-opacity": st.opacity,
        },
      }, RANK.mask);

      if (regionId != null) {
        fetchMaskPeaks(regionId, m.slug, m.indicator_dependent ? indicator : null)
          .then((r) => {
            if (cancelled || !map.getSource(sid)) return;
            addOrdered(map, ranks, {
              id: `${sid}-peak-outline`, type: "line", source: sid, "source-layer": "mask",
              filter: [">=", ["get", "weight"], r.peak_threshold],
              layout: { visibility: showPeaks ? "visible" : "none" },
              // см. dist-peak-outline: контур растворяется на обзорном зуме
              paint: {
                "line-color": "#111111",
                "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.1, 10, 0.6, 13, 1.2],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 7, 0.15, 10, 0.5, 13, 0.8],
              },
            }, RANK.mask);
          })
          .catch(() => {}); // нет данных пиков — не рисуем контур, не критично
      }
    }
    return () => { cancelled = true; };
  }, [mapReady, masks, maskState, indicator, regionId, regions]);

  // Дороги (над масками). Переключение не трогает другие слои.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    dropLayer(map, ranks, "roads-line", "roads");
    const reg = regions.find((r) => r.id === regionId);
    if (!reg || !showRoads || !reg.roads_tile_url) return;
    map.addSource("roads", { type: "vector", tiles: [reg.roads_tile_url], minzoom: 0, maxzoom: 14 });
    addOrdered(map, ranks, {
      id: "roads-line", type: "line", source: "roads", "source-layer": "road",
      paint: {
        "line-color": "#5a6470",
        "line-opacity": 0.7,
        "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.3, 10, 0.8, 13, 1.6],
      },
    }, RANK.road);
  }, [mapReady, regionId, regions, showRoads]);

  // Границы НП (контур площади под баллом реестра) — статический geojson,
  // совпадает с окрашенным ядром. Только для регион-индекса (isIndex).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    dropLayer(map, ranks, "nsk-cities-line", "nsk-cities");
    const reg = regions.find((r) => r.id === regionId);
    if (!reg || !reg.index_tile_url || !showBorders) return;
    map.addSource("nsk-cities", { type: "geojson", data: "/novosibirsk_cities.geojson" });
    addOrdered(map, ranks, {
      id: "nsk-cities-line", type: "line", source: "nsk-cities",
      paint: { "line-color": "#1f2d3d", "line-width": 1.2, "line-opacity": 0.85 },
    }, RANK.road);
  }, [mapReady, regionId, regions, showBorders]);

  // Города (поверх всего). Переключение не трогает другие слои.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    dropLayer(map, ranks, "city-circle", "cities");
    const reg = regions.find((r) => r.id === regionId);
    if (!reg || !showCities || !reg.cities_tile_url) return;
    map.addSource("cities", { type: "vector", tiles: [reg.cities_tile_url], minzoom: 0, maxzoom: 14 });
    addOrdered(map, ranks, {
      id: "city-circle", type: "circle", source: "cities", "source-layer": "city",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["coalesce", ["get", "population"], 0],
          0, 2.5, 50000, 4, 500000, 7, 2000000, 11],
        "circle-color": "#b30000",
        "circle-stroke-color": "#fff",
        "circle-stroke-width": 1,
        "circle-opacity": 0.9,
      },
    }, RANK.city);
  }, [mapReady, regionId, regions, showCities]);

  // Индекс качества городской среды: «виртуальные области» городов (Вороного)
  // залиты по баллу, при наведении — плашка с именем города и баллом.
  // Отдельный статический geojson-слой, не связан с grid/масками.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    dropLayer(map, ranks, "nsk-index-line");
    dropLayer(map, ranks, "nsk-index-fill", "nsk-index");
    dropLayer(map, ranks, "nsk-region-line");
    dropLayer(map, ranks, "nsk-region-bg", "nsk-region");
    const reg = regions.find((r) => r.id === regionId);
    if (!reg || !reg.index_tile_url) return;

    // Светлая подложка области (под сеткой) + контур сверху.
    map.addSource("nsk-region", { type: "geojson", data: "/novosibirsk_border.geojson" });
    addOrdered(map, ranks, {
      id: "nsk-region-bg", type: "fill", source: "nsk-region",
      paint: { "fill-color": "#eef3f8", "fill-opacity": 0.92 },
    }, RANK.dist);
    addOrdered(map, ranks, {
      id: "nsk-region-line", type: "line", source: "nsk-region",
      paint: { "line-color": "#b7c6d6", "line-width": 1 },
    }, RANK.road);

    // Сетка 1 км (векторные тайлы tile_index): цвет = value (балл НП по его
    // площади + затухание), прозрачность = weight (плотность населения WorldPop + затухание к
    // городу; пол 0.12 — сплошное покрытие без дыр). fill-antialias:false —
    // без сетки швов между ячейками.
    map.addSource("nsk-index", { type: "vector", tiles: [reg.index_tile_url], minzoom: 0, maxzoom: 12 });
    const fillColor = ["interpolate", ["linear"], ["get", "value"],
      ...INDEX_COLOR_STOPS.flatMap((s) => [s.v, s.color])];
    addOrdered(map, ranks, {
      id: "nsk-index-fill", type: "fill", source: "nsk-index", "source-layer": "index",
      paint: {
        "fill-color": fillColor,
        "fill-antialias": false,
        // яркость = вклад ПРИМЕНЁННЫХ масок; смена — эффектом ниже
        // (setPaintProperty), поэтому appliedIndexW берётся замыканием, не в deps
        "fill-opacity": buildIndexOpacity(appliedIndexW),
      },
    }, RANK.dist);

    const hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
    const onMove = (e) => {
      const p = e.features[0].properties;
      map.getCanvas().style.cursor = "pointer";
      // курсор прямо над городом (внутри контура) → value == балл реестра;
      // «(ближайший)» показываем только вне контура
      const inCity = Number(p.value) >= (CITY_SCORE[p.name] ?? Infinity) - 0.5;
      hoverPopup.setLngLat(e.lngLat)
        .setHTML(`<b>${p.name}</b>${inCity ? "" : " (ближайший)"}<br>Индекс здесь: <b>${fmt(Number(p.value))}</b> балла`)
        .addTo(map);
    };
    const onLeave = () => { map.getCanvas().style.cursor = ""; hoverPopup.remove(); };
    map.on("mousemove", "nsk-index-fill", onMove);
    map.on("mouseleave", "nsk-index-fill", onLeave);
    return () => {
      map.off("mousemove", "nsk-index-fill", onMove);
      map.off("mouseleave", "nsk-index-fill", onLeave);
      hoverPopup.remove();
    };
  }, [mapReady, regionId, regions]);

  // Применение ПРИМЕНЁННЫХ весов (по кнопке «Пересчитать»): перекраска
  // fill-opacity готового слоя (без пересоздания источника — как переключатель
  // шкалы). Флаг indexComputing снимаем по 'idle' — когда карта дорисовалась,
  // тогда кнопка снова активна. appliedIndexW, не indexWeights (черновик).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    // слой ещё не создан (или карта пересоздаётся) — не зависаем в «Перестройка…»
    if (!map.getLayer("nsk-index-fill")) { setIndexComputing(false); return; }
    map.setPaintProperty("nsk-index-fill", "fill-opacity", buildIndexOpacity(appliedIndexW));
    // Снимаем «Перестройка…» по 'idle' (карта дорисовалась) ИЛИ по таймауту —
    // подложка в песочнице иногда не доходит до 'idle', и без страховки кнопка
    // залипала бы. Перекраска paint-выражением всё равно применяется мгновенно.
    let cleared = false;
    const done = () => { if (!cleared) { cleared = true; setIndexComputing(false); } };
    map.once("idle", done);
    const t = setTimeout(done, 700);
    return () => { map.off("idle", done); clearTimeout(t); };
  }, [mapReady, appliedIndexW]);

  // Линии концентрации между пиками (ТЗ п.5) + точки-пики, поверх всего
  // (RANK.structure — выше городов). Реальные GeoJSON-фичи, не векторные
  // тайлы — фич мало (единицы-десятки), тайлы здесь не оправданы.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    dropLayer(map, ranks, "concentration-lines");
    dropLayer(map, ranks, "concentration-peaks");
    if (map.getSource("concentration")) map.removeSource("concentration");
    if (!structure) return;

    map.addSource("concentration", { type: "geojson", data: structure });
    addOrdered(map, ranks, {
      id: "concentration-lines", type: "line", source: "concentration",
      filter: ["==", ["get", "kind"], "concentration_line"],
      layout: { visibility: showStructure ? "visible" : "none" },
      paint: {
        "line-color": "#7b2cbf", "line-width": 2,
        "line-dasharray": [2, 1.5], "line-opacity": 0.85,
      },
    }, RANK.structure);
    addOrdered(map, ranks, {
      id: "concentration-peaks", type: "circle", source: "concentration",
      filter: ["==", ["get", "kind"], "peak"],
      layout: { visibility: showStructure ? "visible" : "none" },
      paint: {
        "circle-radius": 7, "circle-color": "#7b2cbf",
        "circle-stroke-color": "#fff", "circle-stroke-width": 2, "circle-opacity": 0.95,
      },
    }, RANK.structure);

    // Клик по пику — значение и sigma затухания (ТЗ п.6, для визуальной
    // сверки "на глаз": видно, на каком расстоянии до соседних объектов
    // на карте — городов, дорог — стоит пик, и какой sigma сейчас задан).
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: true });
    const onClick = (e) => {
      const p = e.features[0].properties;
      popup.setLngLat(e.lngLat)
        .setHTML(`<b>Пик концентрации</b><br>значение: ${fmt(Number(p.value))}<br>σ затухания: ${structure.decay_sigma_km} км`)
        .addTo(map);
    };
    map.on("click", "concentration-peaks", onClick);
    return () => map.off("click", "concentration-peaks", onClick);
  }, [mapReady, structure]);

  const toggleMask = (slug) =>
    setMaskState((s) => ({ ...s, [slug]: { ...s[slug], visible: !s[slug].visible } }));
  const setOpacity = (slug, v) =>
    setMaskState((s) => ({ ...s, [slug]: { ...s[slug], opacity: v } }));

  // Живое изменение веса: обновляем состояние сразу (плавная отрисовка слайдера),
  // а recompute шлём с задержкой WEIGHT_DEBOUNCE_MS после последнего движения —
  // не на каждый пиксель драга. Кнопка "Пересчитать" остаётся для явного действия.
  const setWeightLive = (slug, v) => {
    setWeights((w) => {
      const next = { ...w, [slug]: v };
      saveTuning(next, presetRef.current);
      clearTimeout(weightDebounceRef.current);
      weightDebounceRef.current = setTimeout(() => runRecompute(next), WEIGHT_DEBOUNCE_MS);
      return next;
    });
  };

  // Деактивация маски чекбоксом = коэффициент 0 (маска выпадает из композиции;
  // нормировка на total в tile_composition переиспользует освободившийся вес).
  // Прежний вес запоминается, чтобы повторное включение возвращало его, а не 0.
  const prevWeightsRef = useRef({});
  const toggleWeight = (slug) => {
    const w = weights[slug] ?? 0;
    if (w > 0) {
      prevWeightsRef.current[slug] = w;
      setWeightLive(slug, 0);
    } else {
      setWeightLive(slug, prevWeightsRef.current[slug] || 0.1);
    }
  };

  const contract = masks.find((m) => m.slug === contractSlug);
  const selRegion = regions.find((r) => r.id === regionId);
  const isIndex = selRegion?.index_tile_url != null;
  const active = liveComp;  // распределение всегда живое

  // Легенда: шкала в кратных базовой концентрации (base = показатель /
  // число ячеек — значение ячейки при равномерном размазывании). Жёлтая
  // середина = ×1 базы; тёплые ступени лог-кратные (×4, ≥×16).
  const rfReady = scaleMode === "russia" && globalScale?.base_cell > 0;
  const legendTitle = rfReady
    ? `Концентрация к базе РФ (${fmt(globalScale.base_cell)}/ячейку)`
    : scaleMode === "russia" ? "Распределение — считаем базу РФ…"
    : `Концентрация к базе территории${distBase > 0 ? ` (${fmt(distBase)}/ячейку)` : ""}`;
  const legendMults = ["0", "×0.5", "×1", "×4", "≥×16"];

  return (
    <div className="app">
      <div id="map" ref={containerRef} />

      <div className={`panel${collapsed ? " collapsed" : ""}`} ref={panelRef}>
        <button
          className="panel-toggle"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "Показать панель" : "Свернуть панель"}
        >
          {collapsed ? "›" : "‹"}
        </button>

        {!collapsed && (
          <div className="panel-body">
            <h1>Пространственная дезагрегация</h1>
            <div className="sub">
              {isIndex
                ? "Индекс качества городской среды · города области"
                : "Система аналитических масок · сетка 1×1 км"}
            </div>

            <div className="section">
              <label>Регион</label>
              <select value={regionId ?? ""} onChange={(e) => setRegionId(Number(e.target.value))}>
                {regions.map((r) => (
                  <option key={r.id} value={r.id}>
                    {`${r.name} (${r.cell_count} ячеек)`}
                  </option>
                ))}
              </select>
            </div>

            {!isIndex && (
              <div className="section">
                <label>Масштаб сравнения</label>
                <div className="scale-toggle">
                  <button
                    className={scaleMode === "territory" ? "on" : ""}
                    title="Шкала растянута до максимума выбранной территории — видна её внутренняя структура"
                    onClick={() => setScaleMode("territory")}
                  >Территория</button>
                  <button
                    className={scaleMode === "russia" ? "on" : ""}
                    title="Шкала фиксирована по всем регионам (p99): одинаковый цвет — одинаковое абсолютное значение"
                    onClick={() => setScaleMode("russia")}
                  >Россия</button>
                </div>
              </div>
            )}

            <div className="section">
              <label>Показатель</label>
              {isIndex ? (
                <select value="idx" disabled>
                  <option value="idx">{INDEX_INDICATOR_NAME}</option>
                </select>
              ) : (
                <select value={indicator ?? ""} onChange={(e) => setIndicator(e.target.value)}>
                  {indicators.map((i) => (
                    <option key={i.code} value={i.code}>{i.name}</option>
                  ))}
                </select>
              )}
            </div>

            {isIndex && (
              <div className="section">
                <div className="sub" style={{ margin: 0 }}>
                  Сетка 1 км (векторные тайлы, как у других регионов). Цвет —
                  балл реестра по всей фактической площади НП (контуры OSM), вне
                  него — затухание вдвое каждые 5 км, при перекрытии берётся
                  больший (доминирующий НП). Яркость — «присутствие городской
                  среды»: население (WorldPop) + POI и зелень (OSM) + близость к
                  дорогам/ж-д/ЛЭП + затухание к городу. Наведите курсор —
                  ближайший город и балл в точке.
                </div>
              </div>
            )}

            {isIndex && (
              <div className="section">
                <div className="weights">
                  <div className="weights-head">
                    <span>вклад маски в яркость</span>
                    <span className="reset" title="Обнулить все веса"
                      onClick={resetIndexWeights}>сброс</span>
                  </div>
                  {INDEX_MASKS.map((m) => {
                    const wv = indexWeights[m.slug] ?? 0;
                    const on = wv > 0;
                    return (
                      <div className="mask-row" key={m.slug}>
                        <div className="head">
                          <input type="checkbox" checked={on}
                            onChange={() => toggleIndexMask(m.slug)} />
                          <span className="title" style={{ opacity: on ? 1 : 0.5 }}
                            onClick={() => toggleIndexMask(m.slug)}>{m.title}</span>
                          <span className="info" title={m.tip} style={{ cursor: "help" }}>i</span>
                        </div>
                        <div className="wrow" style={{ border: "none", padding: "2px 0 0" }}>
                          <input type="range" min="0" max="1" step="0.05" disabled={!on}
                            value={wv}
                            onChange={(e) => setIndexWeight(m.slug, Number(e.target.value))} />
                          <span className="wval">{wv.toFixed(2)}</span>
                        </div>
                      </div>
                    );
                  })}
                  <button className="recompute-btn" disabled={indexComputing || !indexDirty}
                    onClick={applyIndexWeights}>
                    {indexComputing ? "Перестройка…" : "Пересчитать"}
                  </button>
                </div>
              </div>
            )}

            {isIndex && (
              <details className="section fold">
                <summary>Слои карты</summary>
                <div className="mask-row">
                  <div className="head">
                    <input type="checkbox" checked={showRoads}
                      disabled={!selRegion?.roads_tile_url}
                      onChange={() => setShowRoads((v) => !v)} />
                    <span className="title"
                      onClick={() => selRegion?.roads_tile_url && setShowRoads((v) => !v)}
                      style={{ opacity: selRegion?.roads_tile_url ? 1 : 0.45 }}>
                      Дороги <span className="dot road" />
                      {!selRegion?.roads_tile_url && <span className="badge">нет данных</span>}
                    </span>
                  </div>
                </div>
                <div className="mask-row">
                  <div className="head">
                    <input type="checkbox" checked={showBorders}
                      onChange={() => setShowBorders((v) => !v)} />
                    <span className="title" onClick={() => setShowBorders((v) => !v)}>
                      Границы НП
                    </span>
                  </div>
                </div>
              </details>
            )}

            {!isIndex && <>
            <div className="section">
              <div className="weights">
                <div className="weights-head">
                  <span>вес маски в составе · сумма сохраняется</span>
                  <span className="reset" title="Обнулить все веса (чистый лист)"
                    onClick={resetWeights}>сброс</span>
                </div>
                {masks.map((m) => {
                  const w = weights[m.slug] ?? 0;
                  const enabled = w > 0;
                  return (
                    <div className="mask-row" key={m.slug}>
                      <div className="head">
                        <input type="checkbox" checked={enabled}
                          title="Участие в композиции (выкл = вес 0)"
                          onChange={() => toggleWeight(m.slug)} />
                        <span className="title" style={{ opacity: enabled ? 1 : 0.5 }}
                          onClick={() => toggleWeight(m.slug)}>{m.title}</span>
                        {m.is_baseline && <span className="badge">baseline</span>}
                        <span className="info" title={maskTip(m)}
                          onClick={() => setContractSlug(m.slug)}>i</span>
                      </div>
                      <div className="wrow" style={{ border: "none", padding: "2px 0 0" }}>
                        <input type="range" min="0" max="1" step="0.05"
                          disabled={!enabled}
                          value={w}
                          onChange={(e) => setWeightLive(m.slug, Number(e.target.value))} />
                        <span className="wval">{w.toFixed(2)}</span>
                      </div>
                    </div>
                  );
                })}
                <button className="recompute-btn" disabled={computing}
                  onClick={() => runRecompute(weights)}>
                  {computing ? "Пересчёт…" : "Пересчитать"}
                </button>
                {!computing && !liveComp && (
                  <div className="sub" style={{ marginTop: 8, marginBottom: 0, color: "var(--signal-bad)" }}>
                    {Object.values(weights).some((v) => v > 0)
                      ? "Пересчёт не удался — проверьте доступность API."
                      : "Все маски выключены — распределение не определено. Включите маски чекбоксами: сумма по ячейкам всегда равна показателю региона, поэтому карта не «остывает» от нулевых весов, а исчезает."}
                  </div>
                )}
              </div>

              {contract && <MaskContract m={contract} onClose={() => setContractSlug(null)} />}
            </div>

            <details className="section fold">
              <summary>
                Пики концентрации
                {structure && (
                  <span className="fold-note">
                    {structure.features.filter((f) => f.properties.kind === "peak").length} пик(ов)
                  </span>
                )}
              </summary>
              <div className="weights">
                <div className="mask-row" style={{ border: "none", padding: "0 0 6px" }}>
                  <div className="head">
                    <input
                      type="checkbox"
                      checked={showPeaks}
                      onChange={() => setShowPeaks((v) => !v)}
                    />
                    <span className="title" onClick={() => setShowPeaks((v) => !v)}>
                      Контур ячеек-пиков (топ-5%)
                    </span>
                  </div>
                </div>
                <div className="mask-row" style={{ border: "none", padding: "0 0 6px" }}>
                  <div className="head">
                    <input
                      type="checkbox"
                      checked={showStructure}
                      onChange={() => setShowStructure((v) => !v)}
                    />
                    <span className="title" onClick={() => setShowStructure((v) => !v)}>
                      Точки-пики и линии концентрации
                    </span>
                  </div>
                </div>
                <div className="wrow"
                  title="Пики — сильнейшие кластеры, вместе накрывающие эту долю массы пиковых ячеек. Число пиков подстраивается под структуру региона, критерий одинаков для всех регионов.">
                  <span className="wname">Парето: доля массы</span>
                  <input type="range" min="0.5" max="0.99" step="0.01"
                    value={peakShare}
                    onChange={(e) => setPeakShare(Number(e.target.value))} />
                  <span className="wval">{Math.round(peakShare * 100)}%</span>
                </div>
              </div>
            </details>

            <details className="section fold">
              <summary>Слои карты</summary>
              <div className="mask-row">
                <div className="head">
                  <input type="checkbox" checked={showCities} onChange={() => setShowCities((v) => !v)} />
                  <span className="title" onClick={() => setShowCities((v) => !v)}>
                    Города <span className="dot city" />
                  </span>
                </div>
              </div>
              <div className="mask-row">
                <div className="head">
                  <input type="checkbox" checked={showRoads}
                    disabled={!selRegion?.roads_tile_url}
                    onChange={() => setShowRoads((v) => !v)} />
                  <span className="title" onClick={() => selRegion?.roads_tile_url && setShowRoads((v) => !v)}
                    style={{ opacity: selRegion?.roads_tile_url ? 1 : 0.45 }}>
                    Дороги <span className="dot road" />
                    {!selRegion?.roads_tile_url && <span className="badge">нет данных</span>}
                  </span>
                </div>
              </div>
            </details>
            </>}
          </div>
        )}
      </div>

      <div className="locator" style={{ left: collapsed ? 20 : panelWidth + 32 }}>
        <div className="locator-title">Расположение региона</div>
        <div className="locator-map" ref={locatorContainerRef} />
      </div>

      {isIndex && (
        <div className="legend">
          <div>Индекс качества городской среды, 2024 (балл)</div>
          <div className="bar" style={{ background: `linear-gradient(90deg, ${INDEX_COLOR_STOPS.map((s) => `${s.color} ${((s.v - INDEX_DOMAIN[0]) / (INDEX_DOMAIN[1] - INDEX_DOMAIN[0]) * 100).toFixed(0)}%`).join(", ")})` }} />
          <div className="ends"><span>0 · вне НП</span><span>223 · макс.</span></div>
          <div style={{ marginTop: 6, fontSize: 11 }}>
Город — 171…223 балла (верх шкалы); вне контура затухание вдвое/5 км до 0. Яркость = присутствие среды
          </div>
        </div>
      )}

      {active && !isIndex && (
        <div className="legend">
          <div>{legendTitle}</div>
          <div className="bar" style={{ background: `linear-gradient(90deg, ${DIST_STOPS.join(", ")})` }} />
          <div className="ends">{legendMults.map((m) => <span key={m}>{m}</span>)}</div>
          {structure && (
            <div style={{ marginTop: 6, fontSize: 11 }}>
              Пиков концентрации: <b>{structure.features.filter((f) => f.properties.kind === "peak").length}</b>
            </div>
          )}
          {active.metrics && <Metrics comp={active} />}
        </div>
      )}
    </div>
  );
}

function Metrics({ comp }) {
  const m = comp.metrics || {};
  return (
    <div className="metrics" style={{ marginTop: 10 }}>
      <table>
        <tbody>
          <tr><td>Сумма сохранена</td><td className={comp.sum_preserved ? "ok" : "bad"}>{comp.sum_preserved ? "да" : "нет"}</td></tr>
          <tr><td>Ошибка суммы</td><td>{fmt(m.sum_error)}</td></tr>
          <tr><td>Джини</td><td>{fmt(m.gini)}</td></tr>
          <tr><td>Top-10% доля</td><td>{m.top10_share != null ? (m.top10_share * 100).toFixed(1) + "%" : "—"}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

// Текст нативной подсказки над «i»: суть маски сразу при наведении (нативный
// title не обрезается overflow панели, в отличие от CSS-тултипа). Клик — полный
// контракт в MaskContract.
function maskTip(m) {
  return [
    m.signal || m.title,
    m.source && `Источник: ${m.source}`,
    m.influence && `Влияние: ${m.influence}`,
    m.formula && `Формула: ${m.formula}`,
    "— клик: полный контракт",
  ].filter(Boolean).join("\n");
}

function MaskContract({ m, onClose }) {
  return (
    <div className="contract">
      <h3>{m.title} <span style={{ float: "right", cursor: "pointer" }} onClick={onClose}>✕</span></h3>
      <dl>
        <dt>Источник</dt><dd>{m.source || "—"}</dd>
        <dt>Сигнал</dt><dd>{m.signal || "—"}</dd>
        <dt>Тип влияния</dt><dd>{m.influence}</dd>
        <dt>Формула</dt><dd>{m.formula || "—"}</dd>
        <dt>Нормировка</dt><dd>{m.normalization || "—"}</dd>
        <dt>Применимость</dt><dd>{(m.applicability || []).join(", ") || "—"}</dd>
        <dt>Ограничения</dt><dd>{m.limitations || "—"}</dd>
      </dl>
    </div>
  );
}
