FROM ubuntu:22.04

# Basic deps (keep small but sufficient)
RUN apt-get update && apt-get install -y --no-install-recommends \
  git curl unzip xz-utils zip ca-certificates libglu1-mesa \
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
  CI=true

# Lightweight verify (no precache here to avoid overlayfs write issues)
RUN flutter --version && flutter config --enable-web || true

# Switch to workspace mount at runtime
WORKDIR /workspace

# Copy helper (make sure this path exists in build context)
USER root
COPY ./scripts/job_runner.sh /scripts/job_runner.sh
RUN chmod +x /scripts/job_runner.sh
USER flutter

CMD ["/bin/bash"]