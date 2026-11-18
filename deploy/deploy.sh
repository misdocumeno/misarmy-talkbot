#!/usr/bin/env bash
# Deploy one pinned talkbot image to the prod host. Runs on the self-hosted
# runner (user gh-runner, member of the docker group) from the workflow.
#
#   deploy.sh <full-commit-sha>
#
# Flow:
#   1. sync the prod checkout to <sha> (compose file, lavalink + observability config)
#   2. pull ghcr.io/<repo>:sha-<short>   (fails here = nothing touched)
#   3. record the running healthy tag as the rollback target
#   4. `up -d` with TALKBOT_IMAGE pinned, wait for the container healthcheck
#      (GET /healthz on the metrics port)
#   5. healthy  -> write .last-good, prune images of this repo except current + rollback
#      unhealthy -> roll back to the previous tag, wait for it, exit 1
set -euo pipefail

SHA="${1:?usage: deploy.sh <full-commit-sha>}"
PROD_DIR="${PROD_DIR:?PROD_DIR must point at the prod checkout}"
REPO_SLUG="misdocumeno/misarmy-talkbot"
IMAGE_REPO="ghcr.io/${REPO_SLUG}"
REPO_HTTPS="https://github.com/${REPO_SLUG}.git"
PROJECT="misarmy-talkbot"
SERVICE="misarmy_talkbot"
COMPOSE=(docker compose -p "$PROJECT" -f docker-compose.prod.yml)
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
# Upper bound on a drain. The bot itself has no deadline for a healthy drain
# (long messages finish); this only catches a dead process.
DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-1800}"
LAST_GOOD_FILE="$PROD_DIR/.last-good"

TAG="sha-${SHA:0:7}"
NEW_IMAGE="${IMAGE_REPO}:${TAG}"

log() { printf '[deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

cd "$PROD_DIR"

# ---- 1. sync checkout -------------------------------------------------------
git remote set-url origin "$REPO_HTTPS"
git fetch --quiet --prune origin
git checkout --quiet --force --detach "$SHA"
log "checkout at $(git rev-parse --short HEAD)"

# ---- helpers ----------------------------------------------------------------
running_image() {
  local cid
  cid="$("${COMPOSE[@]}" ps -q "$SERVICE" 2>/dev/null || true)"
  [[ -n "$cid" ]] && docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || true
}

wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) cid status=none
  while ((SECONDS < deadline)); do
    cid="$("${COMPOSE[@]}" ps -q "$SERVICE" 2>/dev/null || true)"
    if [[ -n "$cid" ]]; then
      status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo none)"
    fi
    if [[ "$status" == healthy ]]; then
      return 0
    fi
    sleep 3
  done
  log "healthcheck did not turn healthy within ${HEALTH_TIMEOUT}s (last status: $status)"
  return 1
}

deploy_image() {
  local image="$1" cid running
  # --force-recreate: never *reuse* the container. After a drain it is Exited
  # (restart=no); plain `up -d` may print "Running" and leave it stopped when the
  # image is unchanged. Recreating always creates and starts a fresh container,
  # with the compose restart policy (unless-stopped) restored. --no-deps keeps
  # lavalink/observability untouched.
  TALKBOT_IMAGE="$image" "${COMPOSE[@]}" up -d --force-recreate --no-deps "$SERVICE" || return 1
  # Don't trust compose's exit status alone: verify the container really runs,
  # so a stopped container fails fast instead of burning HEALTH_TIMEOUT.
  cid="$("${COMPOSE[@]}" ps -q "$SERVICE" 2>/dev/null || true)"
  running="$([[ -n "$cid" ]] && docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)"
  if [[ "$running" != true ]]; then
    log "container is not running after up (cid=${cid:-none}); failing this attempt"
    return 1
  fi
  wait_healthy
}

# Zero-disruption deploy: SIGUSR1 makes the bot finish the audio it is playing,
# snapshot its queue to the DB, and exit 0 without a graceful voice teardown.
# The new container then restores the snapshot on boot. Only the first deploy
# does this; rollbacks skip it.
drain_running() {
  local cid running code
  cid="$("${COMPOSE[@]}" ps -q "$SERVICE" 2>/dev/null || true)"
  [[ -z "$cid" ]] && return 0
  running="$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)"
  [[ "$running" == true ]] || return 0
  # Turn off the restart policy first, or Docker would restart the old image
  # the moment it exits. deploy_image restores the policy after `up -d`.
  docker update --restart=no "$cid" >/dev/null
  log "draining running bot (SIGUSR1); waiting for the queue snapshot"
  docker kill -s SIGUSR1 "$cid" >/dev/null
  if code="$(timeout "$DRAIN_TIMEOUT" docker wait "$cid")"; then
    log "drained (exit $code)"
  else
    # Event loop is dead, not just busy. Fall back to the normal SIGTERM path.
    log "drain did not finish within ${DRAIN_TIMEOUT}s; falling back to graceful stop"
    docker update --restart=unless-stopped "$cid" >/dev/null
  fi
}

prune_images() {
  local keep_current="$1" keep_previous="$2" ref
  # Dangling layers from older builds of *this* repo (label set by metadata-action).
  docker image prune -f \
    --filter "label=org.opencontainers.image.source=https://github.com/${REPO_SLUG}" \
    >/dev/null || true
  # Tagged sha images of this repo that are neither running nor the rollback target.
  while read -r ref; do
    case "$ref" in
      "$keep_current" | "$keep_previous") ;;
      *) docker rmi "$ref" >/dev/null 2>&1 && log "pruned $ref" || true ;;
    esac
  done < <(docker images --format '{{.Repository}}:{{.Tag}}' "$IMAGE_REPO")
}

# ---- 2. pull the new image (fails before anything is touched) ---------------
log "pulling $NEW_IMAGE"
TALKBOT_IMAGE="$NEW_IMAGE" "${COMPOSE[@]}" pull "$SERVICE"

# ---- 3. remember the rollback target ----------------------------------------
PREVIOUS=""
if [[ -s "$LAST_GOOD_FILE" ]]; then
  PREVIOUS="$(cat "$LAST_GOOD_FILE")"
else
  # First deploy under this script: fall back to whatever is running now.
  PREVIOUS="$(running_image)"
fi
log "previous image: ${PREVIOUS:-<none>}"

# ---- 4. deploy and verify ---------------------------------------------------
log "deploying $NEW_IMAGE"
drain_running
if deploy_image "$NEW_IMAGE"; then
  printf '%s\n' "$NEW_IMAGE" > "$LAST_GOOD_FILE"
  log "healthy on $NEW_IMAGE"
  prune_images "$NEW_IMAGE" "$PREVIOUS"
  exit 0
fi

# ---- 5. rollback ------------------------------------------------------------
if [[ -z "$PREVIOUS" ]]; then
  log "no previous image to roll back to; service is UNHEALTHY"
  exit 1
fi
log "rolling back to $PREVIOUS"
if deploy_image "$PREVIOUS"; then
  log "rollback healthy on $PREVIOUS"
else
  log "rollback also UNHEALTHY; manual intervention needed"
fi
exit 1
