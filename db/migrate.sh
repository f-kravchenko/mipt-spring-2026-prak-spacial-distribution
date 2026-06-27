#!/usr/bin/env bash
# Накат миграций на целевую БД (для внешнего Postgres — разово при провижене).
# Миграции применяются по порядку имён. Пример:
#   DATABASE_URL=postgres://masks:pwd@host:5432/masks ./db/migrate.sh
set -euo pipefail

: "${DATABASE_URL:?Задайте DATABASE_URL}"
# Миграции живут в чарте (единый источник для Helm-ConfigMap и локали).
DIR="$(cd "$(dirname "$0")/../deploy/helm/spatial-masks/files/migrations" && pwd)"

for f in "$DIR"/*.sql; do
  echo ">> $f"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
echo "Миграции применены."
