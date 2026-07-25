#!/usr/bin/env bash
# Загрузка ИКГС как индекс-слоя для одного или нескольких регионов
# (etl.ingest_index --region <slug>). Данные (баллы/контуры НП и опц.
# население/OSM) уже в репозитории — data/processed/<slug>_*.{json,tif}; сбор из
# OSM/Overpass делается заранее локально (etl.fetch_index_region) и коммитится.
# Регион НЕ пересоздаётся: стираются только ИКГС-ячейки, Росстат-данные остаются.
#
# tile_index Martin находит при старте → после загрузки перезапускаем tiles.
#
# Usage:  ./deploy/vds/run-etl-index.sh [slug ...]
#         (без аргументов — все известные индекс-регионы)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ ! -f .env.prod ]; then
  echo "Missing .env.prod. Copy .env.prod.example and edit it first." >&2
  exit 1
fi

SLUGS=("$@")
[ ${#SLUGS[@]} -eq 0 ] && SLUGS=(novosibirsk_ikgs krasnodar yakutia_center moscow)

COMPOSE=(docker compose --env-file .env.prod -f docker-compose.prod.yml)
for slug in "${SLUGS[@]}"; do
  echo ">>> ingest ИКГС: $slug"
  "${COMPOSE[@]}" run --rm --build etl python -m etl.ingest_index --region "$slug"
done
"${COMPOSE[@]}" restart tiles
