# scripts/smoke.sh
#!/usr/bin/env bash
set -euo pipefail

command -v jq   >/dev/null 2>&1 || { echo "jq is required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }

BASE="${BASE:-http://127.0.0.1:8000}"

# -------- Agent run options (optional) --------
# Set these env vars if you want to also run the agent and see a progress bar.
# Example:
#   BRIEF="simple todo app with dark theme" ./scripts/smoke.sh
#   BRIEF="..." VALIDATE_ONLY=true ./scripts/smoke.sh
BRIEF="${BRIEF:-}"                 # if empty, agent step is skipped
DEV_INSTRUCTIONS="${DEV_INSTRUCTIONS:-}"  # optional
VALIDATE_ONLY="${VALIDATE_ONLY:-false}"   # "true" or "false"

# -------- Progress bar width --------
W=${W:-40}

# -------- Unique suffix for stub CRUD test --------
SUF="$(date +%s)-$RANDOM"
NAME="smoke-$SUF"
PATH_STUB="/smoke-$SUF"

SID=""

cleanup() {
  if [[ -n "${SID:-}" ]]; then
    echo "== cleanup: delete created stub =="
    curl -fsS -X DELETE "$BASE/api/stubs/$SID" -i | head -n1 || true
  fi
}
trap cleanup EXIT

progress_stream() {
  local job_id="$1"
  echo "== streaming progress for job: $job_id =="

  # Listen to SSE and parse JSON lines with 'progress' (0..1) and 'message'
  # Renders a nice inline progress bar.
  curl -Ns "$BASE/api/stream?job_id=$job_id" | \
  awk -v W="$W" '
    function draw(pct, msg) {
      filled = int(pct * W + 0.5)
      empty  = W - filled
      bar = ""
      for (i=0;i<filled;i++) bar = bar "█"
      for (i=0;i<empty;i++)  bar = bar "░"
      printf "\r[%s] %3d%% %s", bar, int(pct*100+0.5), msg
      fflush()
    }
    /"progress":[0-9.]+/ {
      match($0, /"progress":([0-9.]+)/, a); pct=a[1]
      msg=""
      if (match($0, /"message":"([^"]*)"/, m)) { msg=m[1] }
      draw(pct, msg)
      if (pct >= 0.999) { print ""; exit 0 }
    }'
}

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
curl -fsS -X DELETE "$BASE/api/stubs/$SID" -i | head -n1 || true
SID="" # so trap doesn't try twice

# ---------------- Optional Agent Step ----------------
if [[ -n "$BRIEF" ]]; then
  echo "== start agent generate =="
  # Build payload with jq to safely include optional fields.
  PAYLOAD=$(jq -n \
    --arg brief "$BRIEF" \
    --arg dev "$DEV_INSTRUCTIONS" \
    --argjson validate "$VALIDATE_ONLY" \
    '{
       brief: $brief
     }
     + ( ($dev|length>0)    as $hasDev    | if $hasDev then {dev_instructions:$dev} else {} end)
     + ( ($validate==true)  as $isDry     | if $isDry  then {validate_only:true} else {} end)
    ')

  RESP=$(curl -fsS -X POST "$BASE/api/generate" \
    -H 'content-type: application/json' \
    -d "$PAYLOAD")

  echo "$RESP" | jq .

  JOB_ID=$(echo "$RESP" | jq -r '.job_id // empty')

  if [[ -z "$JOB_ID" || "$JOB_ID" == "null" ]]; then
    echo
    echo "No job_id found in response."
    echo "Note: The streaming progress bar only applies to the *agent* flow."
    echo "If you call /api/generate in codegen mode, it returns files/dir but no job_id."
  else
    echo
    progress_stream "$JOB_ID"
  fi
fi

echo
echo "done."