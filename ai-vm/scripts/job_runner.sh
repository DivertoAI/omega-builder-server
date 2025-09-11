#!/usr/bin/env bash
set -euo pipefail

# ---- Config ----
# Prefer env override; default to the docker-compose service name "redis".
# (If you use a different host like "omega-redis", set REDIS_URL in the container env.)
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
QUEUE_KEY="${QUEUE_KEY:-queue:build}"
WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"

# Ensure Flutter is on PATH when running as non-login shell.
export PATH="$HOME/flutter/bin:/home/flutter/flutter/bin:$PATH"

log() { echo "[$(date +'%F %T')] $*"; }
die() { echo "[$(date +'%F %T')] ERROR: $*" >&2; exit 1; }

# ---- Redis helpers ----
dequeue() {
  # BLPOP blocks until a job arrives; returns two lines: key and value
  # We only want the payload/value line.
  redis-cli -u "$REDIS_URL" BLPOP "$QUEUE_KEY" 0 | tail -n1
}

publish_status() {
  local id="$1"; local status="$2"; shift 2
  # Remaining args are field/value pairs, e.g. msg "text" finished_at "..."
  redis-cli -u "$REDIS_URL" HSET "job:${id}" status "$status" "$@" >/dev/null
}

# ---- Flutter runner ----
run_flutter() {
  local dir="$1"; local target="$2"; local platform="$3"; local outdir="$4"
  mkdir -p "$outdir"

  [[ -d "$dir" ]] || { echo "project dir not found: $dir" >&2; return 2; }

  pushd "$dir" >/dev/null

  # Always get deps first; many commands fail without this on a fresh workspace.
  # Keep it non-fatal if it’s a pure static web stub (no pubspec).
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

# ---- Main worker loop ----
log "worker starting; redis=$REDIS_URL, queue=$QUEUE_KEY, workspace=$WORKSPACE_DIR"

while true; do
  PAYLOAD="$(dequeue || true)"
  [[ -z "${PAYLOAD}" ]] && continue

  # Be strict: jq -e to fail if keys missing; fall back defaults for optional fields.
  ID="$(jq -er '.id' <<<"$PAYLOAD" 2>/dev/null || echo "")"
  DIR="$(jq -er '.project_dir' <<<"$PAYLOAD" 2>/dev/null || echo "")"
  TGT="$(jq -er '.target' <<<"$PAYLOAD" 2>/dev/null || echo "analyze")"
  PLT="$(jq -er '.platform' <<<"$PAYLOAD" 2>/dev/null || echo "web")"

  if [[ -z "$ID" || -z "$DIR" ]]; then
    log "bad job payload (missing id or project_dir): $PAYLOAD"
    # No ID to report to; skip.
    continue
  fi

  OUT="${WORKSPACE_DIR}/.omega/jobs/$ID"
  mkdir -p "$OUT"

  log "job $ID -> dir=$DIR target=$TGT platform=$PLT"
  publish_status "$ID" "running" started_at "$(date -Iseconds)" msg "started"

  # Run and capture rc
  if run_flutter "$DIR" "$TGT" "$PLT" "$OUT"; then
    publish_status "$ID" "success" finished_at "$(date -Iseconds)" msg "completed"
    log "job $ID completed"
  else
    rc=$?
    publish_status "$ID" "failed" finished_at "$(date -Iseconds)" msg "runner failed" exit_code "$rc"
    log "job $ID failed (rc=$rc)"
  fi
done