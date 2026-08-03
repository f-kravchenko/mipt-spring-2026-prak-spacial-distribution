#!/usr/bin/env bash
# Полное обновление данных на ВДС в ПРАВИЛЬНОМ ПОРЯДКЕ.
#
# Порядок здесь не косметика: etl.ingest пересоздаёт регион
# (DELETE FROM region WHERE slug=…) и каскадом сносит его ИКГС-ячейки. Значит
# ingest_index обязан идти ПОСЛЕ, иначе индекс исчезнет с карты. Ровно поэтому
# шаги собраны в один скрипт, а не оставлены двумя командами в инструкции.
#
#   1. сетки, которых нет в репозитории (сетка НСО — 148 МБ, выше лимита GitHub);
#   2. etl.ingest — показатели Росстата, маски, композиции (regional_value);
#   3. etl.ingest_index — ИКГС по всем индекс-регионам + restart tiles.
#
# distribution_cell больше не пишется (см. миграцию 0012): живому сервису
# per-cell значения не нужны, это экономит ~7 ГБ и заметно ускоряет шаг 2.
# Нужен артефакт §9.5 ТЗ — добавьте --store-cells в шаг 2.
#
# Usage:  ./deploy/vds/run-etl-all.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ ! -f .env.prod ]; then
  echo "Missing .env.prod. Copy .env.prod.example and edit it first." >&2
  exit 1
fi

COMPOSE=(docker compose --env-file .env.prod -f docker-compose.prod.yml)

# data смонтирована в etl только для чтения — для шага 1 подкладываем
# writable-копию репозитория и работаем из неё
RW=(--volume "$ROOT:/work" --workdir /work)

if [ ! -f data/processed/grid_novosibirsk_1km_features.gpkg ]; then
  echo ">>> сетка НСО (её нет в репозитории — собираем)"
  "${COMPOSE[@]}" run --rm --build "${RW[@]}" etl \
    python -m etl.build_grid --region novosibirsk_ikgs \
      --name novosibirsk --center Новосибирск
fi

echo ">>> Росстат: показатели, маски, композиции"
"${COMPOSE[@]}" run --rm --build etl python -u -m etl.ingest --config etl/config.yaml

echo ">>> ИКГС (после Росстата: ingest пересоздаёт регионы и сносит его ячейки)"
./deploy/vds/run-etl-index.sh

echo "Готово. Проверьте /api/regions: у каждого региона должны быть и grid_cells, и index_cells."
