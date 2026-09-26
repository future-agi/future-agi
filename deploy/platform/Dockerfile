# ============================================================================
# Future AGI — standalone install application image (futureagi/platform).
#
# One container runs every stateless process of the standalone install under
# supervisord (see supervisord.conf): the API with the Temporal worker in the
# same process, a Temporal dev server (SQLite on /data), fi-collector,
# agentcc-gateway, the code-evals sandbox, the UI (nginx), Redis (no
# persistence) and S3-compatible object storage. Postgres and ClickHouse keep
# their official images (docker-compose.yml). docker-compose.distributed.yml runs the
# same components as separate services.
#
# The image is assembled from the published component images, so the build
# context is just this directory and nothing is compiled:
#   docker build -t futureagi/platform:latest deploy/platform
# A from-source install (./bin/install --from-source) and the release build
# pass their own component tags instead. The backend they pass is the slim
# variant (futureagi/Dockerfile.oss --build-arg IMAGE_VARIANT=slim):
#   docker build --build-arg BACKEND_IMAGE=futureagi/future-agi:local \
#     --build-arg FRONTEND_IMAGE=futureagi/frontend:local \
#     --build-arg FI_COLLECTOR_IMAGE=futureagi/fi-collector:local \
#     --build-arg AGENTCC_GATEWAY_IMAGE=futureagi/agentcc-gateway:local \
#     -t futureagi/platform:local deploy/platform
#
# Runs as root (docs/images.md): supervisord starts Redis, MinIO, nginx and
# the API as root, runs code evals as the unprivileged `sandbox` user, and
# keeps secrets in a root-only directory.
# The component image defaults float on :latest for a quick local build; a
# release pins all four to the digests it just published (release-images.yml).
# ============================================================================
ARG BACKEND_IMAGE=futureagi/future-agi:latest
ARG FRONTEND_IMAGE=futureagi/frontend:latest
ARG FI_COLLECTOR_IMAGE=futureagi/fi-collector:latest
ARG AGENTCC_GATEWAY_IMAGE=futureagi/agentcc-gateway:latest
# Temporal CLI; `temporal server start-dev` is a single-binary server. Pinned
# by digest (multi-arch index), like MinIO: this binary runs as root.
ARG TEMPORAL_IMAGE=temporalio/temporal:1.9.1@sha256:ad4c82c97bd12b417d1ea942610dbcd511afb250c4d5ed26c694009533df447e
# Same frozen MinIO build (pinned by digest) as docker-compose.distributed.yml.
ARG MINIO_IMAGE=ghcr.io/coollabsio/minio:RELEASE.2025-10-15T17-29-55Z@sha256:69b55a1c1c5dc285ce04db96689f5b2102317fc77a50680a1874ca6efd1c87f9

FROM ${FRONTEND_IMAGE} AS frontend
FROM ${FI_COLLECTOR_IMAGE} AS collector
FROM ${AGENTCC_GATEWAY_IMAGE} AS gateway
FROM ${TEMPORAL_IMAGE} AS temporal
FROM ${MINIO_IMAGE} AS minio

FROM ${BACKEND_IMAGE}
ARG BACKEND_IMAGE

# Identifies this backend build. bin/bootstrap.py re-runs migrations and seeds
# whenever it changes; this layer is rebuilt only when the base image changes.
RUN mkdir -p /etc/futureagi \
    && python -c "import uuid; print(uuid.uuid4())" > /etc/futureagi/build-id \
    && echo "${BACKEND_IMAGE}" >> /etc/futureagi/build-id

# nginx-light serves the UI; redis-server backs cache, locks and the channel
# layer. supervisor comes from PyPI so Debian's python3 is not pulled in.
# falcon + gunicorn run the code-executor HTTP server (the backend image has
# its source but not its dependencies). channels-redis is installed only when
# the base image predates it; --no-deps keeps the base image's pins.
#
# Code evals run as `sandbox`, a uid of their own: RLIMIT_NPROC counts every
# process of a uid on the Docker host, so it must not be shared with another
# container's user (postgres is 999). /etc/futureagi/secrets holds what only
# root may read: the Redis config (bin/start) and the bind-mounted gateway
# config and GCP key (docker-compose.yml). Bind mounts keep the host's file
# mode, so the directory is what keeps eval code out.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx-light redis-server \
    && rm -rf /var/lib/apt/lists/* /var/log/nginx/* /etc/nginx/sites-enabled/default \
    && pip install --no-cache-dir supervisor==4.3.0 falcon==4.3.1 gunicorn==23.0.0 \
    && (python -c "import channels_redis" 2>/dev/null \
        || pip install --no-cache-dir --no-deps channels-redis==4.3.0) \
    && useradd --system --uid 18060 --user-group --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin sandbox \
    && install -d -m 0700 -o root -g root /etc/futureagi/secrets

COPY --from=collector /usr/local/bin/fi-collector /usr/local/bin/fi-collector
COPY --from=collector /etc/fi-collector/config.yaml /etc/fi-collector/config.yaml
COPY --from=gateway /app/agentcc-gateway /usr/local/bin/agentcc-gateway
COPY --from=temporal /usr/local/bin/temporal /usr/local/bin/temporal
COPY --from=minio /usr/bin/minio /usr/local/bin/minio
COPY --from=frontend /usr/share/nginx/html /srv/frontend
COPY --from=frontend /docker-entrypoint.sh /opt/futureagi/bin/frontend-config

# Static files for /admin and /docs with DEBUG off. Build-time settings need no
# services; if this fails, bin/bootstrap.py collects them on first boot (the
# marker file tells it this step succeeded).
RUN if ENV_TYPE=local timeout 600 python manage.py collectstatic --noinput --clear -v 0; then \
        touch /etc/futureagi/static-collected; \
    else \
        echo "collectstatic deferred to first boot"; \
    fi

COPY nginx.conf /etc/nginx/nginx.conf
COPY security-headers.conf /etc/nginx/security-headers.conf
COPY starting.html /etc/nginx/starting.html
COPY supervisord.conf /etc/futureagi/supervisord.conf
COPY bin/ /opt/futureagi/bin/
# A Windows checkout (core.autocrlf) turns `#!/usr/bin/env bash` into
# `bash\r`, and the entrypoint then fails to start.
RUN sed -i 's/\r$//' /opt/futureagi/bin/start /opt/futureagi/bin/after-bootstrap \
        /opt/futureagi/bin/bootstrap.py /etc/futureagi/supervisord.conf \
        /etc/nginx/nginx.conf /etc/nginx/security-headers.conf \
        /etc/nginx/starting.html \
    && chmod 0755 /opt/futureagi/bin/start /opt/futureagi/bin/after-bootstrap \
        /opt/futureagi/bin/bootstrap.py

# glibc: fewer malloc arenas keeps the threaded Python processes smaller.
ENV MALLOC_ARENA_MAX=2

VOLUME ["/data"]
EXPOSE 3000 8000 4317 4318 8080 9005
# See the header: supervisord needs root.
# hadolint ignore=DL3002
USER root
# supervisord stops every program (the API drains its Temporal worker for up
# to 50 s, stopwaitsecs) on SIGTERM; give `docker stop` -t 60 or more.
STOPSIGNAL SIGTERM
# The API plus the collector, gateway, UI and object storage it runs next to,
# with proxies off (HTTP_PROXY from .env must not see loopback probes). The
# app service in docker-compose.yml uses this command and repeats only the
# timings. Replaces the backend image's own HEALTHCHECK, which probes :80.
HEALTHCHECK --interval=15s --timeout=10s --start-period=900s --retries=5 \
    CMD ["python", "-I", "-S", "-c", "import urllib.request as u; o = u.build_opener(u.ProxyHandler({})); [o.open(url, timeout=5).close() for url in ('http://127.0.0.1:8000/health/', 'http://127.0.0.1:9464/healthz', 'http://127.0.0.1:8080/healthz', 'http://127.0.0.1:3000/', 'http://127.0.0.1:9005/minio/health/live')]"]
ENTRYPOINT ["/opt/futureagi/bin/start"]

# Image metadata (docs/images.md). Declared last: a new version, commit or
# build time changes the image config only, never a cached layer.
ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=""
ARG FRONTEND_IMAGE
ARG FI_COLLECTOR_IMAGE
ARG AGENTCC_GATEWAY_IMAGE
ARG TEMPORAL_IMAGE
ARG MINIO_IMAGE
# Deployment telemetry reports it when FUTURE_AGI_VERSION names no release
# (tfc/deployment_telemetry/config.py).
ENV SERVICE_VERSION=${VERSION}
LABEL org.opencontainers.image.title="Future AGI platform (Standalone)" \
      org.opencontainers.image.description="The Standalone install's application container: API with an embedded Temporal worker, Temporal dev server, fi-collector, agentcc-gateway, code-evals sandbox, UI, Redis and object storage under supervisord." \
      org.opencontainers.image.source="https://github.com/future-agi/future-agi" \
      org.opencontainers.image.url="https://futureagi.com" \
      org.opencontainers.image.documentation="https://github.com/future-agi/future-agi/blob/main/docs/images.md" \
      org.opencontainers.image.vendor="Future AGI" \
      org.opencontainers.image.licenses="Apache-2.0 AND LicenseRef-FutureAGI-Enterprise-1.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}" \
      ai.futureagi.backend.image="${BACKEND_IMAGE}" \
      ai.futureagi.frontend.image="${FRONTEND_IMAGE}" \
      ai.futureagi.fi-collector.image="${FI_COLLECTOR_IMAGE}" \
      ai.futureagi.agentcc-gateway.image="${AGENTCC_GATEWAY_IMAGE}" \
      ai.futureagi.temporal.image="${TEMPORAL_IMAGE}" \
      ai.futureagi.minio.image="${MINIO_IMAGE}"
