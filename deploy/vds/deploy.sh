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
export GIT_SHA="$(git rev-parse HEAD)"

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
# Check inside the private network: the public endpoint may require HTTP auth.
# Do not mark a pulled commit as deployed until the new services respond.
docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T api python - <<'PY'
import time
import urllib.request

for url in ("http://127.0.0.1:8000/health", "http://web/"):
    for attempt in range(60):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                assert response.status == 200
            break
        except Exception:
            if attempt == 59:
                raise
            time.sleep(1)
    print(f"Healthy: {url}")
PY
printf '%s\n' "$GIT_SHA" > "$(git rev-parse --git-path last-successful-deploy)"
