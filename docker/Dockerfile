# omega-builder/docker/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Minimal system deps (curl for healthcheck/log checks)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Environment for noiseless logs & imports
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# ---- Copy project metadata first (cache-friendly) ----
# If your package is defined in pyproject.toml (it is), we still need the source
# for an editable install, but copying these first keeps later layers cached.
COPY pyproject.toml README.md ./

# ---- Copy source that the package exposes ----
# (Editable install needs the actual package code present.)
COPY backend backend
COPY cli cli

# If you have any top-level modules or entrypoints, copy them too
# COPY scripts scripts

# ---- Install Python deps ----
# - Upgrade pip
# - Editable install of this repo (pulls deps from pyproject.toml)
# - Ensure redis client is present (belt & suspenders; pyproject already includes it)
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e . \
    && python -m pip install --no-cache-dir "redis>=5"

EXPOSE 8000

# Uvicorn entrypoint
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]