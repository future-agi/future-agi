# Production-grade sandboxed code executor using nsjail
# Provides an HTTP API for executing untrusted Python/JS code safely
#
# Security layers (nsjail's defaults and the flags server.py passes per run):
#   1. PID, mount, user, IPC, UTS and cgroup namespaces
#   2. Read-only root (-R /) with a 16 MB tmpfs at /tmp
#   3. rlimits: address space, CPU time, open files, and file size for Python
#   4. A wall-clock time limit
# There is no seccomp filter, and a run shares the container's network (-N).
#
# System packages, the nsjail build and the sandbox Python libs live in
# Dockerfile.base, published as futureagi/code-executor-base by
# .github/workflows/base-image-publish.yml.
#
# v1.1.0 is the multi-stage base (~185 MB gzip instead of v1.0.0's 331 MB) with
# the sandbox libs pinned to futureagi/uv.lock. It must be published before a
# release builds this file; pin it by digest once it is:
#   CODE_EXECUTOR_BASE=futureagi/code-executor-base:v1.1.0@sha256:<index digest>
# To build without publishing, build the base locally under that tag first:
#   docker build -f Dockerfile.base -t futureagi/code-executor-base:v1.1.0 .
ARG CODE_EXECUTOR_BASE=futureagi/code-executor-base:v1.1.0
FROM ${CODE_EXECUTOR_BASE}

# config/python.cfg is not read: server.py passes nsjail flags (see above).
COPY config/ /sandbox/config/
COPY server.py /sandbox/server.py

WORKDIR /sandbox

EXPOSE 8060

# Runs as root (https://docs.futureagi.com/docs/self-hosting/images#users):
# nsjail needs it, with `privileged: true`, to
# create the namespaces and cgroups of each jail. server.py runs nsjail with
# command-line flags, not config/python.cfg, so evaluated code runs inside
# the jail as uid 0 with every capability dropped, without a seccomp filter,
# and on the container's network (-N).
# hadolint ignore=DL3002
USER root
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-I", "-S", "-c", "import urllib.request as u; u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8060/health', timeout=5).close()"]
# Run the HTTP API server
CMD ["gunicorn", "--bind", "0.0.0.0:8060", "--workers", "4", "--timeout", "60", "server:app"]

# Image metadata (TESTING.md › Image conventions). Declared last: a new version, commit or
# build time changes the image config only, never a cached layer.
ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=""
LABEL org.opencontainers.image.title="Future AGI code executor" \
      org.opencontainers.image.description="nsjail sandbox that runs untrusted Python and JavaScript code evals behind an HTTP API on :8060." \
      org.opencontainers.image.source="https://github.com/future-agi/future-agi" \
      org.opencontainers.image.url="https://futureagi.com" \
      org.opencontainers.image.documentation="https://docs.futureagi.com/docs/self-hosting/images" \
      org.opencontainers.image.vendor="Future AGI" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"
