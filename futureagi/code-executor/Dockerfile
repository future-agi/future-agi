# Production-grade sandboxed code executor using nsjail
# Provides an HTTP API for executing untrusted Python/JS code safely
#
# Security layers:
#   1. nsjail: Linux namespaces (PID, mount, net, user) + seccomp-bpf + cgroups
#   2. Read-only root filesystem with per-execution tmpfs
#   3. No network access inside sandbox
#   4. Resource limits (CPU, memory, file size, processes)
#   5. Timeout enforcement

FROM python:3.11-slim-bookworm

# Install system dependencies + Node.js + nsjail build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    # nsjail build dependencies
    protobuf-compiler libprotobuf-dev libnl-3-dev libnl-route-3-dev \
    pkg-config bison flex git make g++ \
    # Node.js
    nodejs \
    # Runtime
    libprotobuf32 libnl-3-200 libnl-route-3-200 \
    && rm -rf /var/lib/apt/lists/*

# Build nsjail from source
RUN cd /tmp \
    && git clone --depth 1 --branch 3.4 https://github.com/google/nsjail.git \
    && cd nsjail \
    && make -j$(nproc) \
    && cp nsjail /usr/local/bin/ \
    && cd / && rm -rf /tmp/nsjail

# Remove build dependencies to keep image small
RUN apt-get purge -y protobuf-compiler libprotobuf-dev libnl-3-dev libnl-route-3-dev \
    pkg-config bison flex git make g++ \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages available inside the sandbox
# - numpy/pandas/scipy/sklearn: numerical + ML metrics
# - pillow: image processing
# - requests/httpx: HTTP clients
# - nltk + rouge-score: BLEU/ROUGE for text-similarity evals
# - Levenshtein: edit-distance based evals
RUN pip install --no-cache-dir \
    numpy pandas pillow scipy scikit-learn requests httpx \
    nltk rouge-score Levenshtein \
    && pip install --no-cache-dir falcon gunicorn

# Create sandbox directories
RUN mkdir -p /sandbox/tmp /sandbox/python-libs /sandbox/config \
    && chmod 755 /sandbox/tmp

# Copy nsjail configs and server
COPY config/ /sandbox/config/
COPY server.py /sandbox/server.py

WORKDIR /sandbox

EXPOSE 8060

# Run the HTTP API server
CMD ["gunicorn", "--bind", "0.0.0.0:8060", "--workers", "4", "--timeout", "60", "server:app"]
