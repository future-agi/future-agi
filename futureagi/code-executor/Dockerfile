# Production-grade sandboxed code executor using nsjail
# Provides an HTTP API for executing untrusted Python/JS code safely
#
# Security layers (nsjail's defaults and the flags server.py passes per run):
#   1. PID, mount, user, IPC, UTS and cgroup namespaces
#   2. Read-only root (-R /) with a 16 MB tmpfs at /tmp
#   3. rlimits: address space, CPU time, open files, and file size for Python
#   4. A wall-clock time limit
#   5. Shared uid-scoped nftables egress policy and a child seccomp filter (IP
#      sockets only, no io_uring, no new user namespaces)
#
# System packages, the nsjail build and the sandbox Python libs live in
# Dockerfile.base, published as futureagi/code-executor-base by
# .github/workflows/base-image-publish.yml.
#
# v1.1.1 rebuilds the multi-stage base (~185 MB gzip instead of v1.0.0's 331 MB)
# with the T6 dependency pins (certifi, AnyIO, NLTK) from requirements.sandbox.txt / uv.lock.
# It must be published before a release builds this file; pin it by digest once it is:
#   CODE_EXECUTOR_BASE=futureagi/code-executor-base:v1.1.1@sha256:<index digest>
# To build without publishing, build the base locally under that tag first:
#   docker build -f Dockerfile.base -t futureagi/code-executor-base:v1.1.1 .
ARG CODE_EXECUTOR_BASE=futureagi/code-executor-base:v1.1.1
FROM ${CODE_EXECUTOR_BASE} AS init-build
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY executor_init.c /tmp/executor_init.c
RUN gcc -O2 -Wall -Wextra -Werror /tmp/executor_init.c -o /executor-init

FROM ${CODE_EXECUTOR_BASE}
# Only this fixed launcher receives a file capability, never Python or nft.
# The container bounding set must contain NET_ADMIN and nothing else. Init runs
# as uid 65534 already, so SETUID/SETGID are neither needed nor granted.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends nftables libcap2-bin \
    && rm -rf /var/lib/apt/lists/*
COPY --from=init-build /executor-init /usr/local/bin/executor-init
RUN chown root:root /usr/local/bin/executor-init \
    && chmod 0755 /usr/local/bin/executor-init \
    && setcap cap_net_admin=ep /usr/local/bin/executor-init \
    && mkdir -p /sandbox/scripts && chown 65534:65534 /sandbox/scripts

# config/python.cfg is not read: server.py passes nsjail flags (see above).
COPY config/ /sandbox/config/
COPY server.py egress_guard.py egress_runtime.py executor_init.py /sandbox/

WORKDIR /sandbox

EXPOSE 8060

# No root process at startup or runtime. The fixed entrypoint enables only
# NET_ADMIN, installs and verifies the policy, then drops every capability and
# sets no_new_privs before exec. User namespaces give nsjail its mount isolation;
# their capabilities cannot administer the outer, shared network namespace.
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/executor-init"]
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
