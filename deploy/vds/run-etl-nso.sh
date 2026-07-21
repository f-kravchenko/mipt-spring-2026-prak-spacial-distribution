#!/usr/bin/env bash
# Загрузка региона «Индекс качества городской среды» (Новосибирская область):
# сетка 1 км + маски присутствия в grid_cell, дороги в road. Отдельно от
# основного run-etl.sh (тот грузит Росстат-регионы модулем etl.ingest).
#
# Данные берутся из ./data (смонтирован в контейнер): pop_nso_z.tif,
# osm_nso.json, osm_nso_infra.json (все в репозитории). Граница —
# apps/web/public/russia.geojson (вшита в etl-образ).
#
# tile_index — новая тайл-функция; Martin находит функции при старте,
# поэтому после первой загрузки перезапускаем tiles.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ ! -f .env.prod ]; then
  echo "Missing .env.prod. Copy .env.prod.example and edit it first." >&2
  exit 1
fi

COMPOSE=(docker compose --env-file .env.prod -f docker-compose.prod.yml)

"${COMPOSE[@]}" run --rm --build etl python -m etl.ingest_index_novosibirsk
"${COMPOSE[@]}" restart tiles
