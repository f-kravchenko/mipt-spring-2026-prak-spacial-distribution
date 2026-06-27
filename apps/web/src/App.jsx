import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { fetchRegions, fetchIndicators, fetchMasks, recompute } from "./api";

// Пресеты весов (по slug маски) — стартовые наборы для живого пересчёта.
// Имена по-русски; веса соответствуют ablation-конфигурациям ТЗ (§9.5).
const PRESETS = [
  { id: "all_5_masks", name: "Все маски (база)",
    weights: { regression_mask: 0.5, worldpop_mask: 0.3, distance_to_city_mask: 0.1, distance_to_center_mask: 0.1 } },
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
const BASE_WEIGHTS = PRESETS[0].weights;

// Цветовые шкалы
const MASK_C0 = "#f7fbff", MASK_C1 = "#08306b"; // синяя: вес маски 0..1
const DIST_C0 = "#fff5eb", DIST_C1 = "#7f2704"; // оранжевая: значение распределения

// Self-contained подложка: пустой фон. Без внешних тайлов (demotiles и т.п.).
const BASE_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#dbe6f0" } }],
};
const RF_BOUNDS = [[18, 40], [180, 82]]; // вид всей РФ (для мини-карты), без хвоста за 180°

function fmt(x) {
  if (x == null) return "—";
  if (Math.abs(x) >= 1000) return x.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  return x.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
}

// Расширенные границы bbox (для maxBounds — немного контекста вокруг региона).
function padBounds([minx, miny, maxx, maxy], f = 0.4) {
  const dx = (maxx - minx) * f, dy = (maxy - miny) * f;
  return [[minx - dx, miny - dy], [maxx + dx, maxy + dy]];
}

// Порядок слоёв снизу вверх. Каждый слой управляется своим эффектом независимо,
// поэтому при добавлении вставляем его перед ближайшим слоем с большим рангом —
// так переключение городов/дорог не трогает распределение и маски.
const RANK = { dist: 0, mask: 1, road: 2, city: 3 };

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
  const [showCities, setShowCities] = useState(true);
  const [showRoads, setShowRoads] = useState(true);

  // Распределение всегда считается живьём по весам; пресет — стартовый набор весов.
  const [presetId, setPresetId] = useState(PRESETS[0].id);
  const [weights, setWeights] = useState({ ...BASE_WEIGHTS }); // slug -> вес
  const [liveComp, setLiveComp] = useState(null);              // {tile_url, value_max, metrics}

  // Инициализация карты + загрузка справочников
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASE_STYLE,
      center: [95, 64],
      zoom: 2.2,
    });
    map.addControl(new maplibregl.NavigationControl());
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
      style: BASE_STYLE,
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
        setWeights(Object.fromEntries(mk.map((m) => [m.slug, BASE_WEIGHTS[m.slug] ?? 0])));
        if (rg.length) setRegionId(rg[0].id);
        if (ind.length) setIndicator(ind[0].code);
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

  // Пересчёт распределения по явному набору весов (кнопка/пресет/смена региона).
  const runRecompute = (w) => {
    if (regionId == null || !indicator) return;
    recompute(regionId, indicator, w)
      .then((r) => setLiveComp({ ...r, sum_preserved: true }))  // инвариант по построению
      .catch(() => setLiveComp(null));
  };

  // Автопересчёт только при смене региона/показателя (веса — текущие).
  // Изменения слайдеров применяются кнопкой «Пересчитать» (см. runRecompute).
  const wRef = useRef(weights);
  wRef.current = weights;
  useEffect(() => {
    if (regionId == null || !indicator) return;
    runRecompute(wRef.current);
  }, [regionId, indicator]);

  // Слой распределения (низ). Источник — живой пересчёт (liveComp).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    dropLayer(map, ranks, "dist-fill", "dist");
    const dist = liveComp;
    if (!dist || !dist.tile_url) return;
    const vmax = dist.value_max && dist.value_max > 0 ? dist.value_max : 1;
    // maxzoom 13: дальше maplibre переиспользует (overzoom) тайлы — сетка 1 км,
    // мельче детали нет, лишних запросов на z14+ не делаем.
    map.addSource("dist", { type: "vector", tiles: [dist.tile_url], minzoom: 0, maxzoom: 13 });
    addOrdered(map, ranks, {
      id: "dist-fill", type: "fill", source: "dist", "source-layer": "distribution",
      paint: {
        "fill-color": ["interpolate", ["linear"], ["get", "value"], 0, DIST_C0, vmax, DIST_C1],
        "fill-opacity": 0.85,
      },
    }, RANK.dist);
  }, [mapReady, liveComp]);

  // Маски (над распределением). Зависят только от своего состояния.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    for (const m of masks) dropLayer(map, ranks, `mask-${m.slug}-fill`, `mask-${m.slug}`);
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
    }
  }, [mapReady, masks, maskState, indicator]);

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

  const toggleMask = (slug) =>
    setMaskState((s) => ({ ...s, [slug]: { ...s[slug], visible: !s[slug].visible } }));
  const setOpacity = (slug, v) =>
    setMaskState((s) => ({ ...s, [slug]: { ...s[slug], opacity: v } }));

  const setWeight = (slug, v) => setWeights((w) => ({ ...w, [slug]: v }));
  // веса по всем маскам (отсутствующие в пресете -> 0)
  const fullWeights = (base) => Object.fromEntries(masks.map((m) => [m.slug, base[m.slug] ?? 0]));
  const selectPreset = (id) => {
    const p = PRESETS.find((x) => x.id === id) || PRESETS[0];
    const w = fullWeights(p.weights);
    setPresetId(id);
    setWeights(w);
    runRecompute(w);  // пресет — явное действие, считаем сразу
  };

  const contract = masks.find((m) => m.slug === contractSlug);
  const selRegion = regions.find((r) => r.id === regionId);
  const active = liveComp;  // распределение всегда живое

  return (
    <div className="app">
      <div className="panel">
        <h1>Пространственная дезагрегация</h1>
        <div className="sub">Система аналитических масок · сетка 1×1 км</div>

        <div className="section">
          <label>Регион</label>
          <select value={regionId ?? ""} onChange={(e) => setRegionId(Number(e.target.value))}>
            {regions.map((r) => (
              <option key={r.id} value={r.id}>{r.name} ({r.cell_count} ячеек)</option>
            ))}
          </select>
        </div>

        <div className="section">
          <label>Показатель</label>
          <select value={indicator ?? ""} onChange={(e) => setIndicator(e.target.value)}>
            {indicators.map((i) => (
              <option key={i.code} value={i.code}>{i.name}</option>
            ))}
          </select>
        </div>

        <div className="section">
          <label>Пресет весов</label>
          <select value={presetId} onChange={(e) => selectPreset(e.target.value)}>
            {PRESETS.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>

          <div className="weights">
            <div className="weights-head">
              <span>вес маски в составе · сумма сохраняется</span>
              <span className="reset" onClick={() => selectPreset(presetId)}>сброс</span>
            </div>
            {masks.map((m) => (
              <div className="wrow" key={m.slug}>
                <span className="wname">{m.title}</span>
                <input type="range" min="0" max="1" step="0.05"
                  value={weights[m.slug] ?? 0}
                  onChange={(e) => setWeight(m.slug, Number(e.target.value))} />
                <span className="wval">{(weights[m.slug] ?? 0).toFixed(2)}</span>
              </div>
            ))}
            <button className="recompute-btn" onClick={() => runRecompute(weights)}>
              Пересчитать
            </button>
            {!liveComp && <div className="sub">пересчёт…</div>}
          </div>

          {active && active.metrics && <Metrics comp={active} />}
        </div>

        <div className="section">
          <label>Слои карты</label>
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
        </div>

        <div className="section">
          <label>Маски (наложение слоёв)</label>
          {masks.map((m) => {
            const st = maskState[m.slug] || { visible: false, opacity: 0.7 };
            return (
              <div className="mask-row" key={m.slug}>
                <div className="head">
                  <input type="checkbox" checked={st.visible} onChange={() => toggleMask(m.slug)} />
                  <span className="title" onClick={() => toggleMask(m.slug)}>{m.title}</span>
                  {m.is_baseline && <span className="badge">baseline</span>}
                  <span className="info" title="Контракт маски" onClick={() => setContractSlug(m.slug)}>i</span>
                </div>
                {st.visible && (
                  <input type="range" min="0" max="1" step="0.05" value={st.opacity}
                    onChange={(e) => setOpacity(m.slug, Number(e.target.value))} />
                )}
              </div>
            );
          })}
        </div>

        {contract && (
          <div className="section">
            <MaskContract m={contract} onClose={() => setContractSlug(null)} />
          </div>
        )}
      </div>

      <div id="map" ref={containerRef} />

      <div className="locator">
        <div className="locator-title">Расположение региона</div>
        <div className="locator-map" ref={locatorContainerRef} />
      </div>

      {active && (
        <div className="legend">
          <div>Распределение, {active.value_max != null ? `до ${fmt(active.value_max)}/ячейку` : ""}</div>
          <div className="bar" style={{ background: `linear-gradient(90deg, ${DIST_C0}, ${DIST_C1})` }} />
          <div className="ends"><span>0</span><span>{fmt(active.value_max)}</span></div>
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
