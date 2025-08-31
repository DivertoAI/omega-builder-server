#!/usr/bin/env bash
set -euo pipefail

command -v jq >/dev/null 2>&1 || { echo "jq is required"; exit 1; }

BASE="${BASE:-http://127.0.0.1:8000}"

# unique suffix to avoid conflicts on repeated runs
SUF="$(date +%s)-$RANDOM"
NAME="smoke-$SUF"
PATH_STUB="/smoke-$SUF"

SID=""

cleanup() {
  if [[ -n "${SID:-}" ]]; then
    echo "== cleanup: delete created stub =="
    # show status line only
    curl -fsS -X DELETE "$BASE/api/stubs/$SID" -i | head -n1 || true
  fi
}
trap cleanup EXIT

echo "== health =="
curl -fsS "$BASE/api/health" | jq . > /dev/null && echo "OK"

echo "== create stub =="
STUB=$(
  curl -fsS -X POST "$BASE/api/stubs" \
    -H 'content-type: application/json' \
    -d "{\"name\":\"$NAME\",\"path\":\"$PATH_STUB\",\"env\":\"default\"}"
)
echo "$STUB" | jq . > /dev/null
SID=$(echo "$STUB" | jq -r .id)

echo "== list stubs =="
curl -fsS "$BASE/api/stubs" | jq 'length'

echo "== export =="
curl -fsS "$BASE/api/stubs/export" | jq '.stubs | length'

echo "== import (merge) =="
curl -fsS -X POST "$BASE/api/stubs/import" \
  -H 'content-type: application/json' \
  -d '{"stubs":[{"name":"bulk","path":"/bulk","env":"default"}],"mode":"merge"}' | jq .

echo "== tags =="
curl -fsS "$BASE/api/tags" | jq .
curl -fsS -X POST "$BASE/api/tags" -H 'content-type: application/json' -d '{"tag":"smoke-tag"}' | jq .
curl -fsS -X DELETE "$BASE/api/tags/smoke-tag" | jq .

echo "== delete created stub =="
# the trap will delete it; show status here too
curl -fsS -X DELETE "$BASE/api/stubs/$SID" -i | head -n1 || true
SID="" # so trap doesn't try twice

echo "done."