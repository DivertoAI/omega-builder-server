#!/usr/bin/env bash
set -euo pipefail

REDIS_URL="${REDIS_URL:-redis://host.docker.internal:6379/0}"  # from omega .env
QUEUE_KEY="queue:build"

log() { echo "[$(date +'%F %T')] $*"; }

dequeue() {
  # BLPOP blocks; change timeout if you prefer
  redis-cli -u "$REDIS_URL" BLPOP "$QUEUE_KEY" 0 | tail -n1
}

publish_status() {
  local id="$1"; local status="$2"; local field="$3"; local val="$4"
  redis-cli -u "$REDIS_URL" HSET "job:${id}" status "$status" "$field" "$val" >/dev/null
}

run_flutter() {
  local dir="$1"; local target="$2"; local platform="$3"; local outdir="$4"
  mkdir -p "$outdir"
  pushd "$dir" >/dev/null

  case "$target" in
    analyze) flutter analyze 2>&1 | tee "$outdir/analyze.log";;
    test)    flutter test -r expanded 2>&1 | tee "$outdir/test.log";;
    apk)     flutter build apk --release 2>&1 | tee "$outdir/build-apk.log";;
    web)     flutter build web 2>&1 | tee "$outdir/build-web.log";;
    *)       echo "unknown target $target" >&2; return 2;;
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
  OUT="/app/workspace/.omega/jobs/$ID"

  log "job $ID -> $DIR ($TGT/$PLT)"
  publish_status "$ID" "running" "started_at" "$(date -Iseconds)"
  if run_flutter "$DIR" "$TGT" "$PLT" "$OUT"; then
    publish_status "$ID" "success" "finished_at" "$(date -Iseconds)"
  else
    publish_status "$ID" "failed" "finished_at" "$(date -Iseconds)"
  fi
done