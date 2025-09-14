# Faster, reproducible Flutter + Android SDK image for ARM64
FROM ubuntu:22.04

# ----------------------------
# Global env & faster mirrors
# ----------------------------
ENV DEBIAN_FRONTEND=noninteractive \
  LANG=C.UTF-8 \
  LC_ALL=C.UTF-8

# Use faster mirrors (tweak if a different mirror is faster for you)
RUN sed -i 's|http://archive.ubuntu.com|http://ubuntu.cloudflare.com|g; s|http://ports.ubuntu.com|http://ubuntu.ports.ubuntu.com|g' /etc/apt/sources.list

# --------------------------------------------------------
# Base OS deps (with BuildKit cache & minimal image size)
# --------------------------------------------------------
# HINT: build with BuildKit for best speed:
#   DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose build --progress=plain --no-cache ai-vm
RUN --mount=type=cache,target=/var/cache/apt \
  --mount=type=cache,target=/var/lib/apt/lists \
  set -eux; \
  apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update; \
  apt-get install -y --no-install-recommends \
  ca-certificates curl git jq unzip xz-utils zip \
  python3 python3-pip \
  redis-tools dumb-init \
  # Flutter Linux desktop toolchain bits (and GL)
  clang cmake ninja-build pkg-config libglu1-mesa libgl1 \
  # JDK for Android tools later
  openjdk-17-jdk-headless; \
  ln -sf /usr/bin/python3 /usr/local/bin/python; \
  rm -rf /var/lib/apt/lists/*

# ----------------------------
# Flutter version & locations
# ----------------------------
ARG FLUTTER_VERSION=3.27.1
ARG FLUTTER_HOME=/home/flutter/flutter
ARG PUB_CACHE=/home/flutter/.pub-cache

# ----------------
# Non-root user
# ----------------
RUN useradd -ms /bin/bash flutter
USER flutter
WORKDIR /home/flutter

# ----------------
# Install Flutter
# ----------------
RUN git clone --depth 1 --branch "${FLUTTER_VERSION}" https://github.com/flutter/flutter.git "${FLUTTER_HOME}"

ENV PATH="${FLUTTER_HOME}/bin:${PUB_CACHE}/bin:${PATH}" \
  PUB_CACHE="${PUB_CACHE}" \
  CI=true \
  WORKSPACE_DIR="/workspace" \
  REDIS_URL="redis://redis:6379/0"

# Smoke (keep layers small)
RUN flutter --version && flutter config --enable-web || true

# Pre-cache Android & Linux artifacts; cache Pub to speed rebuilds
RUN --mount=type=cache,target=/home/flutter/.pub-cache \
  flutter precache --android --linux --force || true

# ----------------------------
# API & worker Python dependencies
# ----------------------------
USER root
RUN python -m pip install --no-cache-dir \
  fastapi==0.116.1 \
  "uvicorn[standard]==0.30.5" \
  "pydantic==2.*" \
  aiofiles==23.* \
  rich==13.* \
  redis==5.* \
  openai==1.* \
  python-dotenv==1.* \
  && chmod 755 /usr/local/bin

# ----------------------------
# Android SDK (CLI tools)
# ----------------------------
ENV ANDROID_SDK_ROOT=/opt/android-sdk \
  ANDROID_HOME=/opt/android-sdk
ENV PATH="${PATH}:${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${ANDROID_SDK_ROOT}/platform-tools:${ANDROID_SDK_ROOT}/build-tools/34.0.0"

# Google cmdline-tools + required SDKs
RUN --mount=type=cache,target=/var/cache/apt \
  --mount=type=cache,target=/var/lib/apt/lists \
  set -eux; \
  apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update; \
  rm -rf /var/lib/apt/lists/*; \
  mkdir -p "${ANDROID_SDK_ROOT}/cmdline-tools"; \
  curl -fsSL -o /tmp/cmdtools.zip "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"; \
  unzip -q /tmp/cmdtools.zip -d "${ANDROID_SDK_ROOT}/cmdline-tools"; \
  mv "${ANDROID_SDK_ROOT}/cmdline-tools/cmdline-tools" "${ANDROID_SDK_ROOT}/cmdline-tools/latest"; \
  rm -f /tmp/cmdtools.zip; \
  yes | sdkmanager --sdk_root="${ANDROID_SDK_ROOT}" --licenses; \
  sdkmanager --sdk_root="${ANDROID_SDK_ROOT}" \
  "platform-tools" \
  "build-tools;34.0.0" \
  "platforms;android-34"; \
  chown -R flutter:flutter "${ANDROID_SDK_ROOT}"

# ----------------------------
# Workspace mount point
# ----------------------------
WORKDIR /workspace

# Prepare /scripts and copy worker with correct perms/ownership
RUN install -d -m 0755 -o flutter -g flutter /scripts
COPY --chown=flutter:flutter --chmod=0755 scripts/job_runner.sh /scripts/job_runner.sh

# Back to non-root for runtime
USER flutter

# ----------------------------
# Entrypoint / CMD
# ----------------------------
# NOTE: app dir is /workspace/ai-vm/app (folder is 'ai-vm', not 'ai_vm')
ENTRYPOINT ["dumb-init", "--"]
CMD ["/bin/bash","-lc","bash /scripts/job_runner.sh & python -m uvicorn main:app --host 0.0.0.0 --port 8080 --app-dir /workspace/ai-vm/app"]