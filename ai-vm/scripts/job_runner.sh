#!/usr/bin/env bash
set -euo pipefail

# ----------------------------
# Config
# ----------------------------
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"   # default to docker-compose service "redis"
QUEUE_KEY="${QUEUE_KEY:-queue:build}"            # must match API enqueue key
WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"

# Ensure Flutter is on PATH when running as non-login shell
export PATH="$HOME/flutter/bin:/home/flutter/flutter/bin:$PATH"

log() { echo "[$(date +'%F %T')] $*"; }
die() { echo "[$(date +'%F %T')] ERROR: $*" >&2; exit 1; }

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

while true; do
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
done