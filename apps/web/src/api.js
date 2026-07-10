// Клиент API. База берётся из runtime-конфига (window.__APP_CONFIG__, см. public/config.js),
// затем из VITE_API_BASE (сборка), затем дефолт.
const cfg = (typeof window !== "undefined" && window.__APP_CONFIG__) || {};
// `??` (а не `||`), чтобы apiBase: "" означал «тот же origin» (nginx проксирует /api),
// а не откатывался на дефолт.
const API_BASE = cfg.apiBase ?? import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

async function get(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

export const fetchRegions = () => get("/api/regions");
export const fetchIndicators = () => get("/api/indicators");
export const fetchMasks = () => get("/api/masks");
export const fetchCompositions = (regionId, indicator) =>
  get(`/api/compositions?region_id=${regionId}&indicator=${indicator}`);

// Автоподбор весов композиции под показатель (§9, "обоснование весов").
// -> dict {mask.slug: вес}, сумма = 1.0. Бэкенд должен вызвать
// src/masks/weighting.resolve_weights(indicator_code) и перевести её
// внутренние ключи (regression, worldpop, ..., territory, power, ...) в
// mask.slug из /api/masks (MASK_DESCRIPTION["name"]) — ВАЖНО: territory ->
// "territory_type_mask" и power -> "power_lines_mask" не следуют общему
// паттерну "{ключ}_mask", остальные семь ключей следуют. Если этот перевод
// не сделать на бэкенде, вес для этих двух масок потеряется на фронте
// (ключи не совпадут с m.slug).
export const fetchDefaultWeights = (indicator) => get(`/api/default-weights?indicator=${indicator}`);

// Порог пика для отдельного слоя маски (ТЗ п.2 — топ 5-10% для каждого слоя,
// не только для суммарного). frac=0.05 по умолчанию (топ 5%), см. main.py.
export const fetchMaskPeaks = (regionId, slug, indicator, frac = 0.05) => {
  const params = new URLSearchParams({ region_id: regionId, mask: slug, frac: String(frac) });
  if (indicator) params.set("indicator", indicator);
  return get(`/api/mask-peaks?${params.toString()}`);
};

// Линии концентрации между пиками (ТЗ п.5) + параметр модели затухания
// (ТЗ п.6, decay_sigma_km возвращается для справки/подписи, per-cell не
// применяется на фронте). GeoJSON FeatureCollection: Point-пики + LineString
// рёбра MST между ними. peakMassShare — правило Парето: оставить сильнейшие
// кластеры, вместе накрывающие эту долю массы пиковых ячеек (см. main.py).
export const fetchConcentrationStructure = (
  regionId, indicator, weights, peakFrac = 0.10, decaySigmaKm = 10, peakMassShare = 0.90
) => {
  const params = new URLSearchParams({
    region_id: regionId, indicator, weights: JSON.stringify(weights),
    peak_frac: String(peakFrac), decay_sigma_km: String(decaySigmaKm),
    peak_mass_share: String(peakMassShare),
  });
  return get(`/api/concentration-structure?${params.toString()}`);
};

// Живой пересчёт распределения по произвольным весам масок (slug -> вес).
export async function recompute(regionId, indicator, weights) {
  const res = await fetch(`${API_BASE}/api/recompute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ region_id: regionId, indicator, weights }),
  });
  if (!res.ok) throw new Error(`recompute: ${res.status}`);
  return res.json();
}
