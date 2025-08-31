# Omega Builder (fresh skeleton)

AI-first, template-free **app builder**: Deep Research → Plan (OmegaSpec) → Generate → Verify → Repair.

## Dev quickstart
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn backend.main:app --reload