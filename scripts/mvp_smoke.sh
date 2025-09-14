#!/usr/bin/env bash
set -euo pipefail

say(){ echo -e "\n[SMOKE] $*"; }

# -------- sanity: files we need --------
test -f .env || { echo "Missing .env at repo root"; exit 1; }
test -f ai-vm/workers/assets_worker.py || { echo "Missing ai-vm/workers/assets_worker.py"; exit 1; }

# -------- clean & rebuild --------
say "docker compose down -v"
docker compose down -v || true

say "docker compose build --no-cache"
docker compose build --no-cache

say "docker compose up -d"
docker compose up -d

# -------- wait for services --------
say "waiting for omega API..."
for i in {1..60}; do
  if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
  [[ $i -eq 60 ]] && { echo "omega did not become healthy"; docker compose logs omega --tail=200; exit 1; }
done
say "omega is up"

say "waiting for ai-vm..."
for i in {1..60}; do
  if curl -fsS http://localhost:8080/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
  [[ $i -eq 60 ]] && { echo "ai-vm did not become healthy"; docker compose logs ai-vm --tail=200; exit 1; }
done
say "ai-vm is up"

# -------- launch assets worker (background in ai-vm) --------
say "ensuring assets worker is running inside ai-vm"
docker compose exec -T ai-vm bash -lc '
  set -e
  if ! pgrep -f "workers/assets_worker.py" >/dev/null 2>&1; then
    nohup python -u /workspace/ai-vm/workers/assets_worker.py > /tmp/assets_worker.log 2>&1 &
    echo $! > /tmp/assets_worker.pid
  fi
  pgrep -a -f "workers/assets_worker.py" || true
'

# -------- quick API sanity --------
say "GET / (root)"
curl -fsS http://localhost:8000/ | jq -r .service,.version

say "GET /api/health"
curl -fsS http://localhost:8000/api/health | jq .

say "GET /meta"
curl -fsS http://localhost:8000/meta | jq .

# -------- assets pipeline: enqueue and wait --------
JOB_ID="smoke-assets-$(date +%s)"
OUT_DIR="/workspace/staging/$JOB_ID"
say "POST /api/assets/generate  -> $OUT_DIR"
curl -fsS -X POST http://localhost:8000/api/assets/generate \
  -H 'content-type: application/json' \
  -d @- <<JSON
{
  "job_id": "$JOB_ID",
  "output_dir": "$OUT_DIR",
  "brand_name": "Omega MVP",
  "color_hex": "#4F46E5",
  "style": "clean, modern, friendly",
  "spec": { "app": { "name": "Omega MVP" } }
}
JSON

say "waiting for assets to appear (up to 120s)"
for i in {1..120}; do
  docker compose exec -T ai-vm bash -lc '
    test -s "'"$OUT_DIR"'/app_icon.png" \
    && test -s "'"$OUT_DIR"'/hero_home.png" \
    && test -s "'"$OUT_DIR"'/empty_state.png"
  ' && break || true
  sleep 1
  [[ $i -eq 120 ]] && { echo "assets not found in time"; docker compose exec ai-vm bash -lc "ls -la $OUT_DIR || true; tail -n 200 /tmp/assets_worker.log || true"; exit 1; }
done
say "assets ready:"
docker compose exec -T ai-vm bash -lc "ls -lh $OUT_DIR && file $OUT_DIR/*.png || true"

# -------- flutter build smoke in ai-vm --------
say "Flutter build smoke (creates a throwaway app and builds APK for arm/arm64)"
docker compose exec -T ai-vm bash -lc '
  set -e
  test -d /workspace/_smoke || flutter create /workspace/_smoke >/dev/null
  cd /workspace/_smoke
  flutter build apk --release --target-platform android-arm,android-arm64 >/dev/null
  ls -lh build/app/outputs/flutter-apk/ | sed "s/^/[ai-vm] /"
'

say "ALL GREEN ✅"