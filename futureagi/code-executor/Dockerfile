# Production-grade sandboxed code executor using nsjail
# Provides an HTTP API for executing untrusted Python/JS code safely
#
# Security layers:
#   1. nsjail: Linux namespaces (PID, mount, net, user) + seccomp-bpf + cgroups
#   2. Read-only root filesystem with per-execution tmpfs
#   3. No network access inside sandbox
#   4. Resource limits (CPU, memory, file size, processes)
#   5. Timeout enforcement
#
# System packages, the nsjail build and the sandbox Python libs live in
# Dockerfile.base, published as futureagi/code-executor-base.

FROM futureagi/code-executor-base:v1.0.0

# Copy nsjail configs and server
COPY config/ /sandbox/config/
COPY server.py /sandbox/server.py

WORKDIR /sandbox

EXPOSE 8060

# Run the HTTP API server
CMD ["gunicorn", "--bind", "0.0.0.0:8060", "--workers", "4", "--timeout", "60", "server:app"]
