#!/usr/bin/env bash
# One-time host setup for the talkbot CD timer. Run as root on the prod host:
#
#   sudo bash deploy/host/setup-host.sh
#
# The deploy itself runs as the runner user (gh-runner) from a persistent clone
# in its own home, so no permission or remote changes are needed here anymore.
# Only the weekly prune timer is installed (systemd needs root).
set -euo pipefail

UNIT_DIR="/etc/systemd/system"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi

install -m 0644 "$HERE/docker-image-prune.service" "$UNIT_DIR/docker-image-prune.service"
install -m 0644 "$HERE/docker-image-prune.timer" "$UNIT_DIR/docker-image-prune.timer"
systemctl daemon-reload
systemctl enable --now docker-image-prune.timer

echo "prune timer installed. next run:"
systemctl list-timers docker-image-prune.timer --no-pager