# docker/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps (curl for healthcheck; build tools only if needed)
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy and install Python deps
COPY pyproject.toml /app/
# If you have a lock file, copy that too and use it instead.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Copy the rest of the source
COPY . /app

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]