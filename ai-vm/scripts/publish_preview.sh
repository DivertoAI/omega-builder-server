#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   publish_preview.sh <APP_PATH> <PROJECT> <APP_NAME>
#   - APP_PATH: either the Flutter web build dir (…/build/web) OR the project root
#   - PROJECT:  preview project name (e.g., insta_pharma)
#   - APP_NAME: app name (e.g., customer)

APP_PATH="${1:?APP_PATH required}"
PROJECT="${2:?PROJECT required}"
APP_NAME="${3:?APP_NAME required}"

DEST_ROOT="${OMEGA_PREVIEW_ROOT:-/preview}"
DEST_DIR="$DEST_ROOT/${PROJECT}/${APP_NAME}"

# --- resolve to a real web build folder ---
resolve_build_dir_py='
import os, sys
app_path = os.path.abspath(sys.argv[1])

def is_web_build(p):
    return os.path.isdir(p) and os.path.isfile(os.path.join(p, "index.html"))

candidates = [
    app_path,
    os.path.join(app_path, "build", "web"),
    os.path.join(app_path, "apps", "customer", "build", "web"),
    os.path.join(app_path, "build"),
]

best = None
for p in candidates:
    if is_web_build(p):
        best = p
        break

if best is None and os.path.isdir(app_path):
    # Walk a bit to find a typical Flutter web build
    for root, dirs, files in os.walk(app_path):
        if "index.html" in files and root.endswith(os.path.join("build", "web")):
            best = root
            break

if best is None:
    sys.stderr.write(f"[publish_preview] Could not find a Flutter web build under: {app_path}\\n")
    sys.exit(2)

print(best)
'

BUILD_DIR="$(python -c "$resolve_build_dir_py" "$APP_PATH")"

echo "[publish_preview] PROJECT=$PROJECT APP=$APP_NAME"
echo "[publish_preview] DEST_ROOT=$DEST_ROOT"
echo "[publish_preview] BUILD_DIR=$BUILD_DIR"
echo "[publish_preview] DEST_DIR=$DEST_DIR"

# --- publish ---
mkdir -p "$DEST_DIR"

if command -v rsync >/dev/null 2>&1; then
  rsync -a "$BUILD_DIR"/ "$DEST_DIR"/
else
  # Fallback: Python shutil copy preserving times
  python - "$BUILD_DIR" "$DEST_DIR" <<'PY'
import os, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
for root, _, files in os.walk(src):
    rel = os.path.relpath(root, src)
    out = dst if rel == "." else os.path.join(dst, rel)
    os.makedirs(out, exist_ok=True)
    for f in files:
        shutil.copy2(os.path.join(root, f), os.path.join(out, f))
print(f"[publish_preview] Copied files to {dst}")
PY
fi

echo "[publish_preview] Done → $DEST_DIR"
echo "[publish_preview] Tip: ensure FastAPI serves it: app.mount('/preview', StaticFiles(directory='${DEST_ROOT}'), name='preview')"