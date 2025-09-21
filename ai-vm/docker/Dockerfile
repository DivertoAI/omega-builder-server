# ai-vm/docker/Dockerfile
# Faster, reproducible Flutter + Android SDK image for ARM64 (mirror-robust)
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
  LANG=C.UTF-8 \
  LC_ALL=C.UTF-8

# -------- Base OS & tooling (ARM mirrors + retries) --------
RUN --mount=type=cache,target=/var/cache/apt \
  --mount=type=cache,target=/var/lib/apt/lists \
  set -eux; \
  # Force ports.ubuntu.com on arm to avoid archive.ubuntu.com amd64 index flakiness
  sed -i 's|http://archive.ubuntu.com/ubuntu|http://ports.ubuntu.com/ubuntu-ports|g' /etc/apt/sources.list || true; \
  find /etc/apt -type f -name '*.list' -print0 | xargs -0 sed -i 's|http://archive.ubuntu.com/ubuntu|http://ports.ubuntu.com/ubuntu-ports|g' || true; \
  # Retry-friendly apt-get update (handles mirror sync races)
  tries=0; \
  until apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update; do \
  tries=$((tries+1)); \
  if [ "$tries" -ge 5 ]; then echo "apt-get update: giving up"; exit 1; fi; \
  echo "apt-get update failed; sleeping before retry..."; sleep 5; \
  done; \
  apt-get install -y --no-install-recommends \
  ca-certificates curl git jq unzip xz-utils zip \
  python3 python3-pip \
  redis-tools dumb-init \
  clang cmake ninja-build pkg-config libglu1-mesa libgl1 \
  openjdk-17-jdk-headless; \
  ln -sf /usr/bin/python3 /usr/local/bin/python; \
  rm -rf /var/lib/apt/lists/*

# -------- Flutter toolchain --------
ARG FLUTTER_REF=stable
ARG FLUTTER_HOME=/home/flutter/flutter
ARG PUB_CACHE=/home/flutter/.pub-cache

RUN useradd -ms /bin/bash flutter
USER flutter
WORKDIR /home/flutter

RUN set -eux; \
  mkdir -p "${PUB_CACHE}"; \
  chmod 775 "${PUB_CACHE}"; \
  chown -R flutter:flutter "${PUB_CACHE}"

RUN git clone --depth 1 --branch "${FLUTTER_REF}" https://github.com/flutter/flutter.git "${FLUTTER_HOME}"

ENV PATH="${FLUTTER_HOME}/bin:${PUB_CACHE}/bin:${PATH}" \
  PUB_CACHE="${PUB_CACHE}" \
  CI=true \
  WORKSPACE_DIR="/workspace" \
  REDIS_URL="redis://redis:6379/0" \
  AI_VM_QUEUE_KEY="queue:build" \
  FLUTTER_GIT_URL="https://github.com/flutter/flutter.git"

RUN set -eux; \
  git -C "${FLUTTER_HOME}" remote set-url origin "${FLUTTER_GIT_URL}"; \
  flutter config --no-analytics || true; \
  flutter config --enable-web || true; \
  flutter --version

# -------- Android SDK (cmdline-tools + platform tools) --------
USER root
ENV ANDROID_SDK_ROOT=/opt/android-sdk \
  ANDROID_HOME=/opt/android-sdk
ENV PATH="${PATH}:${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${ANDROID_SDK_ROOT}/platform-tools:${ANDROID_SDK_ROOT}/build-tools/34.0.0"

RUN --mount=type=cache,target=/var/cache/apt \
  --mount=type=cache,target=/var/lib/apt/lists \
  set -eux; \
  # Keep CA bundle fresh for Google downloads
  update-ca-certificates || true; \
  # Best-effort update with retry
  tries=0; \
  until apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update; do \
  tries=$((tries+1)); \
  if [ "$tries" -ge 5 ]; then echo "apt-get update: giving up"; exit 1; fi; \
  echo "apt-get update failed; sleeping before retry..."; sleep 5; \
  done; \
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

# -------- Precache Flutter artifacts (web/android) --------
USER flutter
RUN --mount=type=cache,target=/home/flutter/.pub-cache,uid=1000,gid=1000 \
  set -eux; \
  flutter precache --android --linux --web -v || flutter precache --android --linux --web -v; \
  for p in \
  "/home/flutter/flutter/bin/cache/artifacts/engine/android-arm-release/linux-arm64/gen_snapshot" \
  "/home/flutter/flutter/bin/cache/artifacts/engine/android-arm64-release/linux-arm64/gen_snapshot" \
  "/home/flutter/flutter/bin/cache/artifacts/engine/android-x64-release/linux-arm64/gen_snapshot" \
  "/home/flutter/flutter/bin/cache/artifacts/engine/android-arm-release/linux-x64/gen_snapshot" \
  "/home/flutter/flutter/bin/cache/artifacts/engine/android-arm64-release/linux-x64/gen_snapshot" \
  "/home/flutter/flutter/bin/cache/artifacts/engine/android-x64-release/linux-x64/gen_snapshot" \
  ; do echo "VERIFY $p"; test -e "$p" && chmod +x "$p" || true; done

# -------- Python deps for the ai-vm API --------
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

# -------- Workspace & scripts --------
WORKDIR /workspace
RUN install -d -m 0755 -o flutter -g flutter /scripts
COPY --chown=flutter:flutter --chmod=0755 scripts/job_runner.sh /scripts/job_runner.sh

# -------- Runtime --------
USER flutter
EXPOSE 8080
ENTRYPOINT ["dumb-init", "--"]
CMD ["/bin/bash","-lc","bash /scripts/job_runner.sh & python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --app-dir /workspace/ai-vm"]