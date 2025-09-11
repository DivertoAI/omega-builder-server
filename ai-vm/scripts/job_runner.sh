#!/usr/bin/env bash
set -euo pipefail

REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"  # compose service by default
QUEUE_KEY="queue:build"
WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"

log() { echo "[$(date +'%F %T')] $*"; }

dequeue() {
  # BLPOP blocks until a job arrives
  redis-cli -u "$REDIS_URL" BLPOP "$QUEUE_KEY" 0 | tail -n1
}

publish_status() {
  local id="$1"; local status="$2"; shift 2
  redis-cli -u "$REDIS_URL" HSET "job:${id}" status "$status" "$@" >/dev/null
}

run_flutter() {
  local dir="$1"; local target="$2"; local platform="$3"; local outdir="$4"
  mkdir -p "$outdir"
  pushd "$dir" >/dev/null

  case "$target" in
    analyze) flutter analyze 2>&1 | tee "$outdir/analyze.log";;
    test)    flutter test -r expanded 2>&1 | tee "$outdir/test.log";;
    web)     flutter build web 2>&1 | tee "$outdir/build-web.log";;
    # NOTE: 'apk' would require Android SDK; not included in this image
    *)       echo "unknown target $target" >&2; popd >/dev/null; return 2;;
  esac

  popd >/dev/null
}

while true; do
  PAYLOAD="$(dequeue)"
  [[ -z "$PAYLOAD" ]] && continue
  ID="$(echo "$PAYLOAD" | jq -r '.id')"
  DIR="$(echo "$PAYLOAD" | jq -r '.project_dir')"
  TGT="$(echo "$PAYLOAD" | jq -r '.target')"
  PLT="$(echo "$PAYLOAD" | jq -r '.platform')"
  OUT="${WORKSPACE_DIR}/.omega/jobs/$ID"

  log "job $ID -> $DIR ($TGT/$PLT)"
  publish_status "$ID" "running" started_at "$(date -Iseconds)"

  if run_flutter "$DIR" "$TGT" "$PLT" "$OUT"; then
    publish_status "$ID" "success" finished_at "$(date -Iseconds)"
  else
    publish_status "$ID" "failed" finished_at "$(date -Iseconds)"
  fi
done