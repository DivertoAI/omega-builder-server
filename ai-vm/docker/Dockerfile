FROM ubuntu:22.04

# Basic deps for Flutter + worker
RUN apt-get update && apt-get install -y --no-install-recommends \
  git curl unzip xz-utils zip ca-certificates libglu1-mesa \
  python3 python3-pip jq redis-tools dumb-init \
  && rm -rf /var/lib/apt/lists/*

ARG FLUTTER_VERSION=3.27.1
ARG FLUTTER_HOME=/home/flutter/flutter
ARG PUB_CACHE=/home/flutter/.pub-cache

# Non-root user
RUN useradd -ms /bin/bash flutter
USER flutter
WORKDIR /home/flutter

# Install Flutter
RUN git clone --depth 1 --branch ${FLUTTER_VERSION} https://github.com/flutter/flutter.git ${FLUTTER_HOME}
ENV PATH="${FLUTTER_HOME}/bin:${PUB_CACHE}/bin:${PATH}" \
  PUB_CACHE="${PUB_CACHE}" \
  CI=true \
  WORKSPACE_DIR="/workspace" \
  REDIS_URL="redis://redis:6379/0"

# Smoke (keep layers small)
RUN flutter --version && flutter config --enable-web || true

# Install Python deps for the API
USER root
RUN python3 -m pip install --no-cache-dir \
  fastapi==0.116.1 \
  "uvicorn[standard]==0.30.5" \
  "pydantic==2.*" \
  && chmod 755 /usr/local/bin
USER flutter

# Mount point for repo
WORKDIR /workspace

# Prepare /scripts and copy worker with correct perms/ownership
USER root
RUN install -d -m 0755 -o flutter -g flutter /scripts
COPY --chown=flutter:flutter --chmod=0755 scripts/job_runner.sh /scripts/job_runner.sh
USER flutter

# Run the Redis worker + FastAPI
# NOTE: app dir is /workspace/ai-vm/app (folder is 'ai-vm', not 'ai_vm')
ENTRYPOINT ["dumb-init", "--"]
CMD ["/bin/bash","-lc","bash /scripts/job_runner.sh & python3 -m uvicorn main:app --host 0.0.0.0 --port 8080 --app-dir /workspace/ai-vm/app"]