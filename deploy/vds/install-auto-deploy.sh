#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SYSTEMD_DIR=/etc/systemd/system

install -m 0755 "$ROOT/deploy/vds/auto-deploy.sh" /opt/mipt-deaggr/app/deploy/vds/auto-deploy.sh
install -m 0644 "$ROOT/deploy/vds/mipt-deaggr-auto-deploy.service" "$SYSTEMD_DIR/mipt-deaggr-auto-deploy.service"
install -m 0644 "$ROOT/deploy/vds/mipt-deaggr-auto-deploy.timer" "$SYSTEMD_DIR/mipt-deaggr-auto-deploy.timer"

systemctl daemon-reload
systemctl enable --now mipt-deaggr-auto-deploy.timer
systemctl start mipt-deaggr-auto-deploy.service

systemctl --no-pager --full status mipt-deaggr-auto-deploy.timer
