#!/usr/bin/env bash
set -euo pipefail

# ----------------------------
# Config
# ----------------------------
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"          # default to docker-compose service "redis"
QUEUE_KEY_DEFAULT="queue:build"
QUEUE_KEY="${AI_VM_QUEUE_KEY:-${QUEUE_KEY:-$QUEUE_KEY_DEFAULT}}"
WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"

# Ensure Flutter is on PATH when running as non-login shell
export PATH="$HOME/flutter/bin:/home/flutter/flutter/bin:$HOME/.pub-cache/bin:$PATH"

log() { echo "[$(date +'%F %T')] $*"; }
die() { echo "[$(date +'%F %T')] ERROR: $*" >&2; exit 1; }

# ----------------------------
# Graceful shutdown / crash handling
# ----------------------------
CURRENT_JOB_ID=""
_shutting_down=""

graceful_exit() {
  _shutting_down="1"
  log "received termination signal, shutting down worker gracefully…"
  exit 0
}

on_crash() {
  local rc=$?
  if [[ -n "${CURRENT_JOB_ID}" ]]; then
    # best-effort mark job as crashed
    if command -v redis-cli >/dev/null 2>&1; then
      redis-cli -u "$REDIS_URL" HSET "job:${CURRENT_JOB_ID}" status "crashed" finished_at "$(date -Iseconds)" msg "worker crashed" exit_code "$rc" >/dev/null || true
    fi
  fi
  log "worker exiting (rc=$rc)"
  exit "$rc"
}

trap graceful_exit SIGTERM SIGINT
trap on_crash EXIT

# ----------------------------
# Tool sanity checks
# ----------------------------
need() { command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1 (install with: apt-get update && apt-get install -y $1)"; }

need redis-cli
need jq
need flutter

# ----------------------------
# Redis helpers
# ----------------------------
wait_for_redis() {
  log "waiting for redis at $REDIS_URL"
  until redis-cli -u "$REDIS_URL" PING >/dev/null 2>&1; do
    sleep 1
    log "still waiting for redis..."
  done
  log "redis is up"
}

dequeue() {
  # BLPOP blocks until a job arrives; returns two lines: key and value
  # We only want the payload/value line.
  redis-cli -u "$REDIS_URL" BLPOP "$QUEUE_KEY" 0 | tail -n1
}

publish_status() {
  local id="$1"; local status="$2"; shift 2
  # Remaining args are field/value pairs, e.g. msg "text" finished_at "..."
  redis-cli -u "$REDIS_URL" HSET "job:${id}" status "$status" "$@" >/dev/null || true
}

# ----------------------------
# Flutter runner
# ----------------------------
run_flutter() {
  local dir="$1"; local target="$2"; local platform="$3"; local outdir="$4"
  mkdir -p "$outdir"

  # Resolve relative project_dir under WORKSPACE_DIR
  if [[ "$dir" != /* ]]; then
    dir="${WORKSPACE_DIR%/}/$dir"
  fi
  dir="$(realpath -m "$dir" 2>/dev/null || echo "$dir")"

  [[ -d "$dir" ]] || { echo "project dir not found: $dir" >&2; return 2; }

  pushd "$dir" >/dev/null

  if [[ -f "pubspec.yaml" ]]; then
    flutter pub get 2>&1 | tee "$outdir/pub-get.log"
  fi

  case "$target" in
    analyze)
      flutter analyze 2>&1 | tee "$outdir/analyze.log"
      ;;
    test)
      flutter test -r expanded 2>&1 | tee "$outdir/test.log"
      ;;
    web|build-web|build_web)
      # platform reserved for future targets (android/ios/etc.)
      flutter build web 2>&1 | tee "$outdir/build-web.log"
      ;;
    *)
      echo "unknown target: $target" >&2
      popd >/dev/null
      return 2
      ;;
  esac

  popd >/dev/null
}

# ----------------------------
# Main worker loop
# ----------------------------
log "worker starting; redis=$REDIS_URL, queue=$QUEUE_KEY, workspace=$WORKSPACE_DIR"
wait_for_redis

# Warm Flutter once for snappier first run (don’t fail the worker if this flakes)
flutter --version || true

while [[ -z "$_shutting_down" ]]; do
  PAYLOAD="$(dequeue || true)"
  [[ -z "${PAYLOAD}" ]] && continue

  ID="$(jq -er '.id' <<<"$PAYLOAD" 2>/dev/null || echo "")"
  DIR="$(jq -er '.project_dir' <<<"$PAYLOAD" 2>/dev/null || echo "")"
  TGT="$(jq -er '.target' <<<"$PAYLOAD" 2>/dev/null || echo "analyze")"
  PLT="$(jq -er '.platform' <<<"$PAYLOAD" 2>/dev/null || echo "web")"

  if [[ -z "$ID" || -z "$DIR" ]]; then
    log "bad job payload (missing id or project_dir): $PAYLOAD"
    continue
  fi

  CURRENT_JOB_ID="$ID"
  OUT="${WORKSPACE_DIR}/.omega/jobs/$ID"
  mkdir -p "$OUT"

  log "job $ID -> dir=$DIR target=$TGT platform=$PLT"
  publish_status "$ID" "running" started_at "$(date -Iseconds)" msg "started"

  if run_flutter "$DIR" "$TGT" "$PLT" "$OUT"; then
    publish_status "$ID" "success" finished_at "$(date -Iseconds)" msg "completed"
    log "job $ID completed"
  else
    rc=$?
    publish_status "$ID" "failed" finished_at "$(date -Iseconds)" msg "runner failed" exit_code "$rc"
    log "job $ID failed (rc=$rc)"
  fi

  CURRENT_JOB_ID=""
done