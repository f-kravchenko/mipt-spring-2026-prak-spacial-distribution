#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ ! -f .env.prod ]; then
  echo "Missing .env.prod. Copy .env.prod.example and edit it first." >&2
  exit 1
fi

echo "Updating repository..."
git pull --ff-only

echo "Building and starting database..."
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build db

echo "Running migrations..."
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm migrate

echo "Building and starting services..."
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build tiles api web caddy

echo "Current services:"
docker compose --env-file .env.prod -f docker-compose.prod.yml ps

echo
echo "Health check:"
PUBLIC_BASE_URL="$(grep -E '^PUBLIC_BASE_URL=' .env.prod | cut -d= -f2-)"
curl -fsS "${PUBLIC_BASE_URL%/}/health" || true
