#!/usr/bin/env bash
set -euo pipefail

JOB_ID="${1:-}"
BASE="${BASE:-http://127.0.0.1:8000}"
[[ -z "$JOB_ID" ]] && { echo "usage: $0 <job_id>"; exit 1; }

# width of the bar
W=${W:-40}

# bar glyphs (override with FILL/EMPTY if your terminal doesn't like unicode)
FILL_CHAR="${FILL:-█}"
EMPTY_CHAR="${EMPTY:-░}"

# Stream SSE, pull out JSON payload after "data: ", and render a progress bar.
curl -Ns "$BASE/api/stream?job_id=$JOB_ID" \
| sed -n 's/^data: //p' \
| awk -v W="$W" -v FILL="$FILL_CHAR" -v EMPTY="$EMPTY_CHAR" '
  function draw(pct, msg,  i, filled, empty, bar) {
    if (pct < 0) pct = 0
    if (pct > 1) pct = 1
    filled = int(pct * W + 0.5)
    empty  = W - filled
    bar = ""
    for (i=0;i<filled;i++) bar = bar FILL
    for (i=0;i<empty;i++)  bar = bar EMPTY
    printf "\r[%s] %3d%% %s", bar, int(pct*100+0.5), msg
    fflush()
  }
  {
    line = $0
    # extract progress
    pct = line
    if (pct !~ /"progress":[0-9.]+/) next
    gsub(/.*"progress":/, "", pct)
    gsub(/[^0-9.].*/, "", pct)

    # extract message (optional)
    msg = ""
    if (line ~ /"message":/) {
      msg = line
      gsub(/.*"message":"?/, "", msg)
      gsub(/"?[,}].*$/, "", msg)
      # unescape basic \" -> "
      gsub(/\\"/, "\"", msg)
    }

    draw(pct+0, msg)
    if ((pct+0) >= 0.999) { print ""; exit 0 }
  }
'