# Omega Builder (fresh skeleton)

AI-first, template-free **app builder**: Deep Research → Plan (OmegaSpec) → Generate → Verify → Repair.

## Dev quickstart

````bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn backend.main:app --reload


## Quickstart

```bash
uvicorn backend.main:app --reload
# health
curl -s http://127.0.0.1:8000/api/health | jq .
# smoke
chmod +x scripts/smoke.sh && BASE=http://127.0.0.1:8000 scripts/smoke.sh
````
