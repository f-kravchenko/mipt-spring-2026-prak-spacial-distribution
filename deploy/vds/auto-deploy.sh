#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BRANCH="${DEPLOY_BRANCH:-main}"
LOCK_FILE="/var/lock/mipt-deaggr-auto-deploy.lock"
LOG_TAG="mipt-deaggr-auto-deploy"

cd "$ROOT"

# A build can take longer than the polling interval. Never overlap deploys.
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

git fetch --quiet origin "$BRANCH"
local_rev="$(git rev-parse HEAD)"
remote_rev="$(git rev-parse "origin/$BRANCH")"

if [[ "$local_rev" == "$remote_rev" ]]; then
  exit 0
fi

if ! git merge-base --is-ancestor "$local_rev" "$remote_rev"; then
  logger -t "$LOG_TAG" "Refusing non-fast-forward update: $local_rev -> $remote_rev"
  exit 1
fi

logger -t "$LOG_TAG" "Deploying $BRANCH: $local_rev -> $remote_rev"
./deploy/vds/deploy.sh
logger -t "$LOG_TAG" "Deploy completed at $remote_rev"
