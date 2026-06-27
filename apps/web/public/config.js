// Runtime-конфиг фронта. В проде заменяется смонтированным ConfigMap (Helm),
// поэтому образ собирается один раз и работает в любом окружении.
// apiBase: "" — тот же origin: nginx проксирует /api на сервис api.
window.__APP_CONFIG__ = { apiBase: "" };
