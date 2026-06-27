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
