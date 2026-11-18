#!/usr/bin/env bash
# One-time host setup for the talkbot CD pipeline. Run as root on the prod host:
#
#   sudo PROD_DIR=/home/emmanuel/discord/misarmy-talkbot bash deploy/host/setup-host.sh
#
# Idempotent. Does three things:
#   1. Lets the runner user (gh-runner, docker group) update the prod checkout:
#      group docker owns it, setgid on dirs, git trusts it system-wide.
#   2. Points origin at HTTPS (repo is public, so no SSH deploy key is needed
#      for the runner).
#   3. Installs the weekly prune timer for dangling images older than 7 days.
set -euo pipefail

PROD_DIR="${PROD_DIR:?set PROD_DIR to the prod checkout}"
REPO_HTTPS="https://github.com/misdocumeno/misarmy-talkbot.git"
UNIT_DIR="/etc/systemd/system"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi

# 1. permissions -------------------------------------------------------------
chgrp -R docker "$PROD_DIR"
chmod -R g+rwX "$PROD_DIR"
find "$PROD_DIR" -type d -not -path "*/.git/*" -exec chmod g+s {} +
[[ -f "$PROD_DIR/.env" ]] && chmod 640 "$PROD_DIR/.env"
git config --system --add safe.directory "$PROD_DIR" || true
git -C "$PROD_DIR" config core.sharedRepository group

# 2. remote -------------------------------------------------------------------
git -C "$PROD_DIR" remote set-url origin "$REPO_HTTPS"

# 3. prune timer -------------------------------------------------------------
install -m 0644 "$HERE/docker-image-prune.service" "$UNIT_DIR/docker-image-prune.service"
install -m 0644 "$HERE/docker-image-prune.timer" "$UNIT_DIR/docker-image-prune.timer"
systemctl daemon-reload
systemctl enable --now docker-image-prune.timer

echo "host setup done. next timer run:"
systemctl list-timers docker-image-prune.timer --no-pager
