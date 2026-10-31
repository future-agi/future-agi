"""Distributed and production fixtures for the bin/update Docker e2e tests.

Builds on harness.py (one registry, cosign keys, the real manifest generator,
fake checkouts) and adds what a Distributed (`./bin/install --distributed`)
or production (`deploy/setup.sh`) install needs:

* fake images for the seven Distributed repositories (futureagi/future-agi,
  frontend, fi-collector, agentcc-gateway, serving, code-executor,
  future-agi-simulation-runner), assembled straight into the run's registry
  like harness.ImageAssembler does. One script (FAKE_SCRIPT) plays every role:
  `serve PORT` (an HTTP server with /health/), `worker`, and `bootstrap ...`
  (a one-shot job that logs its arguments to a volume; it applies its
  release's migrations, or with --check fails until they are applied). Every image
  carries the OCI version label and a healthcheck. Releases: v1.51.0 and
  v1.52.0 (healthy), v1.53.0 (agentcc-gateway never turns healthy), and
  `latest` = v1.51.0;
* a fi-collector image built locally with `docker build` from the checkout's
  fi-collector/ (no version label), tagged `...fi-collector:local`, exactly
  what the default Distributed install runs;
* deployment files shaped like the real ones: docker-compose.distributed.yml
  (backend, worker, frontend, a buildable fi-collector defaulting to :local,
  agentcc-gateway, serving, code-executor, the postgres-schema-bootstrap job
  with service_completed_successfully, worker-simulation-runner in a profile,
  datastore stand-ins with healthchecks), deploy/docker-compose.production.yml
  (every image pinned with ${VAR:?}, the collector build reset, a check-only
  bootstrap), the Standalone docker-compose.yml from harness.py, the env
  examples;
* checkouts for both modes (DistributedCheckout): .env with
  COMPOSE_FILE=docker-compose.distributed.yml, or deploy/.env.production (or
  another env file, given with --env-file) with the seven pins and secrets. A
  production checkout is initialized the way deploy/README.md documents
  (DistributedCheckout.initialize: the bootstrap job in apply mode,
  `compose run --rm` against the production env file) before its first `up`,
  and again by a test after `apply --stage`;
* an older docker-compose.distributed.yml that still defines a service later
  releases retired (RETIRED_SERVICE, on the backend image, like
  property-catalog-supervisor): DistributedCheckout.install_older_release
  starts the stack from it, then puts this release's file back and runs `up`,
  which leaves that service's container running as an orphan.

Every Docker resource and directory carries the prefix (default
fagi-upd-e2e-dist-, FUTURE_AGI_UPDATE_E2E_DIST_PREFIX overrides it). The
checkouts get bin/update and deploy/update from FUTURE_AGI_UPDATE_E2E_SOURCE
like the Standalone ones (harness.py), so a test can be shown to fail on a
copy with one behaviour reverted.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sys
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import harness as e2e

DIST_PREFIX_VAR = "FUTURE_AGI_UPDATE_E2E_DIST_PREFIX"
DEFAULT_DIST_PREFIX = "fagi-upd-e2e-dist-"

V51, V52, V53 = "v1.51.0", "v1.52.0", "v1.53.0"
LATEST = V51
PRODUCTION_ENV = "deploy/.env.production"
DISTRIBUTED_FILE = "docker-compose.distributed.yml"
PRODUCTION_OVERLAY = "deploy/docker-compose.production.yml"
COLLECTOR_LOCAL_TAG = "local"

# Manifest component -> (repository, default command of its image, env key).
COMPONENTS: Dict[str, Tuple[str, List[str], str]] = {
    "backend": ("futureagi/future-agi", ["serve", "80"], "FUTURE_AGI_VERSION"),
    "frontend": ("futureagi/frontend", ["serve", "80"], "FRONTEND_VERSION"),
    "fi_collector": ("futureagi/fi-collector", ["serve", "4318"], "FI_COLLECTOR_VERSION"),
    "agentcc_gateway": ("futureagi/agentcc-gateway", ["serve", "8080"], "AGENTCC_GATEWAY_VERSION"),
    "serving": ("futureagi/serving", ["serve", "8080"], "SERVING_VERSION"),
    "code_executor": ("futureagi/code-executor", ["serve", "8060"], "CODE_EXECUTOR_VERSION"),
    "simulation_runner": ("futureagi/future-agi-simulation-runner", ["worker"], "SIMULATION_RUNNER_VERSION"),
}
VERSION_KEYS = tuple(variable for _, _, variable in COMPONENTS.values())
# Compose service -> manifest component (every Future AGI service of the fixture).
SERVICES: Dict[str, str] = {
    "backend": "backend",
    "worker": "backend",
    "postgres-schema-bootstrap": "backend",
    "frontend": "frontend",
    "fi-collector": "fi_collector",
    "agentcc-gateway": "agentcc_gateway",
    "serving": "serving",
    "code-executor": "code_executor",
    "worker-simulation-runner": "simulation_runner",
}
PROFILE_SERVICES = ("worker-simulation-runner",)
# A service an older release defined on the backend image and this one no longer does (deploy/README.md ›
# Retiring RabbitMQ and the catalog sequencer keeps such workloads running until their retirement is approved).
RETIRED_SERVICE = "property-catalog-supervisor"
BOOTSTRAP = "postgres-schema-bootstrap"
DATASTORES = ("postgres", "redis")
# deploy/README.md's step 1 of the initialization ($COMPOSE run --rm ... postgres-schema-bootstrap): the
# overlay pins the job to `migrate --check`; the operator's overrides make it apply. -T: no terminal here.
INIT_JOB = ("run", "--rm", "-T", "-e", "SERVICE_TYPE=bootstrap", "-e", "STARTUP_DB_MUTATION_MODE=operator",
            "-e", "NO_STARTUP_DB_MUTATIONS=false", BOOTSTRAP, "bootstrap", "migrate", "--noinput")

# version -> {component: mode}; components not listed are "ok".
RELEASE_MODES: Dict[str, Dict[str, str]] = {
    V51: {},
    V52: {},
    V53: {"agentcc_gateway": "unhealthy"},
}

FAKE_SCRIPT = """\
#!/bin/sh
# A stand-in for every Future AGI image in the bin/update Distributed e2e tests
# (deploy/tests/update_e2e/distributed.py). sh stays PID 1 with a TERM trap, so
# a Compose recreate does not wait out the stop grace period.
#   serve PORT        HTTP on PORT: /health/ (unless MODE=unhealthy), /version.txt
#   worker            runs until stopped
#   bootstrap ARGS..  one-shot job: logs its arguments to /data. Without --check
#                     it "applies" this release's migrations (/data/migrated-$VERSION)
#                     and exits 0; with --check (the production overlay's job) it
#                     exits 1 while they are unapplied, like `manage.py migrate --check`
trap 'exit 0' TERM INT
role="${1:-serve}"
[ "$#" -gt 0 ] && shift
case "$role" in
  bootstrap)
    : > /tmp/healthy
    mkdir -p /data
    echo "bootstrap $VERSION $COMPONENT $*" >> /data/bootstrap-history
    echo "fake bootstrap $VERSION ($COMPONENT): $*"
    case " $* " in
      *" --check "*)
        if [ ! -f "/data/migrated-$VERSION" ]; then
          echo "fake bootstrap $VERSION: unapplied migrations; run the release's initialization first" >&2
          exit 1
        fi
        ;;
      *) : > "/data/migrated-$VERSION" ;;
    esac
    exit 0
    ;;
  worker)
    [ "$MODE" = unhealthy ] || : > /tmp/healthy
    echo "fake $COMPONENT worker $VERSION ($MODE) running"
    while :; do sleep 1 & wait $!; done
    ;;
  serve)
    port="${1:-80}"
    mkdir -p /www/health
    echo "$COMPONENT $VERSION $MODE" > /www/version.txt
    echo "<html><body>$COMPONENT $VERSION</body></html>" > /www/index.html
    if [ "$MODE" != unhealthy ]; then
      echo ok > /www/health/index.html
      : > /tmp/healthy
    fi
    httpd -f -p "$port" -h /www &
    pid=$!
    trap 'kill "$pid" 2>/dev/null; exit 0' TERM INT
    echo "fake $COMPONENT $VERSION ($MODE) serving on $port"
    wait "$pid"
    ;;
  *)
    echo "fake image: unknown role $role" >&2
    exit 64
    ;;
esac
"""

# The image healthcheck: quick to turn healthy, tolerant of a slow host.
HEALTHCHECK = {"Test": ["CMD", "test", "-f", "/tmp/healthy"], "Interval": 2_000_000_000,
               "Timeout": 10_000_000_000, "StartPeriod": 5_000_000_000, "StartInterval": 1_000_000_000,
               "Retries": 3}

COLLECTOR_DOCKERFILE = """\
# Stand-in for fi-collector/Dockerfile (bin/update Distributed e2e fixture).
# The default Distributed install builds the collector from the checkout and
# tags it futureagi/fi-collector:local. Like the real one, no version label.
FROM @BASE_IMAGE@
COPY fake.sh /fake.sh
ENV VERSION=local MODE=ok COMPONENT=fi_collector
HEALTHCHECK --interval=2s --timeout=10s --start-period=5s --start-interval=1s --retries=3 \\
  CMD test -f /tmp/healthy
ENTRYPOINT ["/bin/sh", "/fake.sh"]
CMD ["serve", "4318"]
"""

DISTRIBUTED_COMPOSE = """\
# ============================================================================
# bin/update e2e fixture: the Distributed stack, shaped like the real
# docker-compose.distributed.yml (deploy/tests/update_e2e/distributed.py
# writes it). Images come from the run's registry; the datastores are
# busybox stand-ins with healthchecks. The project name comes from
# COMPOSE_PROJECT_NAME (.env, or deploy/.env.production); `name:` is only
# the fallback.
# ============================================================================
name: @PREFIX@distributed

x-backend-env: &backend-env
  SECRET_KEY: ${SECRET_KEY:-local-dev-only-not-for-production-replace-me}
  PG_PASSWORD: ${PG_PASSWORD:-futureagi}
  FUTURE_AGI_VERSION: ${FUTURE_AGI_VERSION:-latest}
  ENV_TYPE: local

x-fi-collector-image: &fi-collector-image
  # OSS installs build this image from the checked-out source.
  image: @REGISTRY@/futureagi/fi-collector:${FI_COLLECTOR_VERSION:-local}
  build:
    context: ./fi-collector
    dockerfile: Dockerfile

services:
  # One-shot schema job: every app service waits for it to exit 0.
  postgres-schema-bootstrap:
    <<: &backend-bootstrap
      image: @REGISTRY@/futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}
      restart: "no"
    command: ["bootstrap", "migrate", "--noinput"]
    environment:
      <<: *backend-env
      SERVICE_TYPE: bootstrap
    volumes:
      - bootstrap-data:/data
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  frontend:
    image: @REGISTRY@/futureagi/frontend:${FRONTEND_VERSION:-latest}
    ports:
      - "${FRONTEND_PORT:-3000}:80"
    environment:
      VITE_HOST_API: ${VITE_HOST_API:-http://localhost:${BACKEND_PORT:-8000}}
    depends_on:
      - backend
    stop_grace_period: 5s
    restart: unless-stopped

  backend:
    image: @REGISTRY@/futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}
    ports:
      - "${BACKEND_PORT:-8000}:80"
    env_file:
      - path: .env
        required: false
    environment:
      <<: *backend-env
      SERVICE_TYPE: backend
    depends_on:
      postgres-schema-bootstrap:
        condition: service_completed_successfully
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      agentcc-gateway:
        condition: service_started
      fi-collector:
        condition: service_started
    stop_grace_period: 5s
    restart: unless-stopped

  worker:
    image: @REGISTRY@/futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}
    command: ["worker"]
    env_file:
      - path: .env
        required: false
    environment:
      <<: *backend-env
      SERVICE_TYPE: worker
    depends_on:
      postgres-schema-bootstrap:
        condition: service_completed_successfully
      redis:
        condition: service_healthy
    stop_grace_period: 5s
    restart: unless-stopped

  worker-simulation-runner:
    image: @REGISTRY@/futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:-latest}
    profiles: ["workers", "all", "full"]
    environment:
      <<: *backend-env
      SERVICE_TYPE: temporal-worker
      TEMPORAL_TASK_QUEUE: simulation_runner
    depends_on:
      postgres-schema-bootstrap:
        condition: service_completed_successfully
    stop_grace_period: ${SIMULATION_RUNNER_STOP_GRACE_PERIOD:-5s}
    volumes:
      # As in the real file: a short-syntax bind whose host path is a variable with a default, in a profile.
      - ${GOOGLE_APPLICATION_CREDENTIALS:-/dev/null}:/app/backend/Vertex_AI_Creds.json:ro
    restart: unless-stopped

  agentcc-gateway:
    image: @REGISTRY@/futureagi/agentcc-gateway:${AGENTCC_GATEWAY_VERSION:-latest}
    environment:
      AGENTCC_ADMIN_TOKEN: ${AGENTCC_ADMIN_TOKEN:-local-dev-only-admin-token-replace-me}
      AGENTCC_CONFIG: /app/config.yaml
    volumes:
      # Shaped like the real gateway's config mount: a ./ bind whose host path is a variable with a default.
      # Compose before v2.40.3 rejects it in `config --no-interpolate` ("too many colons"), so bin/update must
      # never need that (deploy/update/install.py, Installation.service_model).
      - ./${AGENTCC_CONFIG_PATH:-agentcc-gateway/config.example.yaml}:/app/config.yaml:ro
      - ${GOOGLE_APPLICATION_CREDENTIALS:-/dev/null}:/app/Vertex_AI_Creds.json:ro
    stop_grace_period: 5s
    restart: unless-stopped

  serving:
    image: @REGISTRY@/futureagi/serving:${SERVING_VERSION:-latest}
    environment:
      <<: *backend-env
    depends_on:
      - backend
    stop_grace_period: 5s
    restart: unless-stopped

  code-executor:
    image: @REGISTRY@/futureagi/code-executor:${CODE_EXECUTOR_VERSION:-latest}
    stop_grace_period: 5s
    restart: unless-stopped

  fi-collector:
    <<: *fi-collector-image
    environment:
      FI_PG_WRITE_PASSWORD: ${PG_PASSWORD:-futureagi}
    volumes:
      - fi-collector-data:/var/lib/fi-collector
    depends_on:
      postgres-schema-bootstrap:
        condition: service_completed_successfully
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    stop_grace_period: 5s
    restart: unless-stopped

  postgres:
    image: @BASE_IMAGE@
    command: ["sh", "-c", "trap 'exit 0' TERM INT; touch /tmp/ready; while :; do sleep 1 & wait $$!; done"]
    environment:
      POSTGRES_PASSWORD: ${PG_PASSWORD:-futureagi}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 2s
      timeout: 10s
      retries: 30
      start_period: 5s
      start_interval: 1s
    restart: unless-stopped

  redis:
    image: @BASE_IMAGE@
    command: ["sh", "-c", "trap 'exit 0' TERM INT; touch /tmp/ready; while :; do sleep 1 & wait $$!; done"]
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 2s
      timeout: 10s
      retries: 30
      start_period: 5s
      start_interval: 1s
    restart: unless-stopped

volumes:
  bootstrap-data:
  postgres-data:
  fi-collector-data:
"""

# What an older release's docker-compose.distributed.yml added: RETIRED_SERVICE, on the backend image.
RETIRED_SERVICE_COMPOSE = """\
  # Retired by a later release; its container keeps running until it is removed.
  @RETIRED@:
    image: @REGISTRY@/futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}
    command: ["worker"]
    environment:
      <<: *backend-env
      SERVICE_TYPE: @RETIRED@
    depends_on:
      redis:
        condition: service_healthy
    stop_grace_period: 5s
    restart: unless-stopped

"""

PRODUCTION_COMPOSE = """\
# bin/update e2e fixture: the production overlay, shaped like the real
# deploy/docker-compose.production.yml. Layered on docker-compose.distributed.yml:
#   docker compose --env-file deploy/.env.production \\
#     -f docker-compose.distributed.yml -f deploy/docker-compose.production.yml up -d
# Every image is pinned with ${VAR:?}, and the bootstrap job only checks.

x-production-backend-env: &production-backend-env
  ENV_TYPE: production
  NO_STARTUP_DB_MUTATIONS: "true"
  SECRET_KEY: ${SECRET_KEY:?must be set for production}
  PG_PASSWORD: ${PG_PASSWORD:?must be set for production}
  FUTURE_AGI_VERSION: ${FUTURE_AGI_VERSION:?must be set for production}

services:
  # Production uses the reviewed published image, never a source build or :local.
  fi-collector:
    image: @REGISTRY@/futureagi/fi-collector:${FI_COLLECTOR_VERSION:?set the reviewed collector release for production}
    build: !reset null

  postgres-schema-bootstrap:
    image: @REGISTRY@/futureagi/future-agi:${FUTURE_AGI_VERSION:?must be set for production}
    environment:
      <<: *production-backend-env
    entrypoint: ["/bin/sh", "/fake.sh"]
    command: ["bootstrap", "migrate", "--check", "--noinput"]

  backend:
    image: @REGISTRY@/futureagi/future-agi:${FUTURE_AGI_VERSION:?must be set for production}
    environment:
      <<: *production-backend-env
      FRONTEND_URL: ${FRONTEND_URL:?must be set for production}
    restart: always

  worker:
    <<: &production-worker
      image: @REGISTRY@/futureagi/future-agi:${FUTURE_AGI_VERSION:?must be set for production}
      environment:
        <<: *production-backend-env
      restart: always

  worker-simulation-runner:
    <<: *production-worker
    # This worker requires the separate SDK image, not the backend image.
    image: @REGISTRY@/futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:?must be set for production}

  agentcc-gateway:
    image: @REGISTRY@/futureagi/agentcc-gateway:${AGENTCC_GATEWAY_VERSION:?must be set for production}
    environment:
      AGENTCC_ADMIN_TOKEN: ${AGENTCC_ADMIN_TOKEN:?must be set for production}
    restart: always

  frontend:
    image: @REGISTRY@/futureagi/frontend:${FRONTEND_VERSION:?must be set for production}
    environment:
      VITE_HOST_API: ${VITE_HOST_API:?set to your backend URL}
    restart: always

  serving:
    image: @REGISTRY@/futureagi/serving:${SERVING_VERSION:?must be set for production}
    environment:
      ENV_TYPE: production
      SECRET_KEY: ${SECRET_KEY:?must be set for production}
    restart: always

  code-executor:
    image: @REGISTRY@/futureagi/code-executor:${CODE_EXECUTOR_VERSION:?must be set for production}
    restart: always

  postgres:
    environment:
      POSTGRES_PASSWORD: ${PG_PASSWORD:?must be set for production}
    restart: always
"""

ENV_EXAMPLE = """\
# Future AGI configuration template (bin/update e2e fixture, shaped like .env.example)
# Distributed installs record this (./bin/install --distributed):
# COMPOSE_FILE=docker-compose.distributed.yml
# COMPOSE_PROFILES=workers

# 1. Secrets. ./bin/install generates them.
SECRET_KEY=
PG_PASSWORD=

# 4. Advanced tuning.
# Image tag of the Future AGI images: empty = latest, a release pins it.
FUTURE_AGI_VERSION=
# FRONTEND_VERSION=
# AGENTCC_GATEWAY_VERSION=
# FI_COLLECTOR_VERSION=local
# SERVING_VERSION=
# CODE_EXECUTOR_VERSION=

# FRONTEND_PORT=3000
# BACKEND_PORT=8000
"""

PRODUCTION_ENV_EXAMPLE = """\
# Production environment (bin/update e2e fixture, shaped like deploy/.env.production.example).
# ---- REQUIRED ----
SECRET_KEY=
AGENTCC_ADMIN_TOKEN=
PG_PASSWORD=
# ---- REQUIRED — pin a specific release per image ----
FUTURE_AGI_VERSION=
FRONTEND_VERSION=
FI_COLLECTOR_VERSION=
AGENTCC_GATEWAY_VERSION=
SERVING_VERSION=
CODE_EXECUTOR_VERSION=
# Dedicated SDK image, not the backend image. Required even before enabling its profile.
SIMULATION_RUNNER_VERSION=
# ---- REQUIRED — public URLs ----
FRONTEND_URL=https://app.your-domain.com
VITE_HOST_API=https://api.your-domain.com
"""

DEPLOYMENT_FILES = (
    "docker-compose.yml",
    DISTRIBUTED_FILE,
    PRODUCTION_OVERLAY,
    ".env.example",
    "deploy/.env.production.example",
    "agentcc-gateway/config.example.yaml",
    "deploy/standalone/datastore",
)
# What the gateway's config mount (./${AGENTCC_CONFIG_PATH:-agentcc-gateway/config.example.yaml}) shows it.
GATEWAY_CONFIG = "# agentcc-gateway config stand-in (bin/update e2e fixture; bind-mounted, hashed)\nproviders: []\n"


def dist_prefix() -> str:
    prefix = os.environ.get(DIST_PREFIX_VAR) or DEFAULT_DIST_PREFIX
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*-", prefix):
        raise ValueError(f"{DIST_PREFIX_VAR} must be lower-case letters, digits and dashes ending in '-', not {prefix!r}")
    return prefix


def availability() -> Optional[str]:
    reason = e2e.availability()
    if reason:
        return reason
    try:
        dist_prefix()
    except ValueError as exc:
        return str(exc)
    return None


def write_distributed_files(root: Path, *, registry: str, prefix: str) -> None:
    """The deployment files every Distributed/production checkout of a run
    shares byte for byte (so one signed manifest fits them all)."""

    files = {
        "docker-compose.yml": e2e.render(e2e.STANDALONE_COMPOSE, REGISTRY=registry, PREFIX=prefix,
                                         BASE_IMAGE=e2e.BASE_IMAGE),
        DISTRIBUTED_FILE: e2e.render(DISTRIBUTED_COMPOSE, REGISTRY=registry, PREFIX=prefix, BASE_IMAGE=e2e.BASE_IMAGE),
        PRODUCTION_OVERLAY: e2e.render(PRODUCTION_COMPOSE, REGISTRY=registry),
        ".env.example": ENV_EXAMPLE,
        "deploy/.env.production.example": PRODUCTION_ENV_EXAMPLE,
        "deploy/standalone/datastore/datastore.conf": "# datastore stand-in config (bind-mounted, hashed)\nmode=small-host\n",
        # The Standalone file's ./${FAGI_E2E_LIMITS_PATH:-...} mount (harness.STANDALONE_COMPOSE).
        "deploy/standalone/datastore/limits.conf": "max_connections=200\n",
        "agentcc-gateway/config.example.yaml": GATEWAY_CONFIG,
        "fi-collector/Dockerfile": e2e.render(COLLECTOR_DOCKERFILE, BASE_IMAGE=e2e.BASE_IMAGE),
        "fi-collector/fake.sh": FAKE_SCRIPT,
        "deploy/update/deployment-files.txt": (
            "# Deployment files of the bin/update Distributed e2e fixture (deploy/tests/update_e2e/distributed.py).\n"
            + "".join(f"{path}\n" for path in DEPLOYMENT_FILES)),
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def older_distributed_compose(*, registry: str, prefix: str) -> str:
    """docker-compose.distributed.yml as an older release shipped it: this
    release's file plus RETIRED_SERVICE (just before the datastores)."""

    current = e2e.render(DISTRIBUTED_COMPOSE, REGISTRY=registry, PREFIX=prefix, BASE_IMAGE=e2e.BASE_IMAGE)
    anchor = "\n  postgres:\n"
    if current.count(anchor) != 1:
        raise RuntimeError("the Distributed fixture lost its postgres service")
    retired = e2e.render(RETIRED_SERVICE_COMPOSE, REGISTRY=registry, RETIRED=RETIRED_SERVICE)
    return current.replace(anchor, "\n" + retired + anchor.lstrip("\n"), 1)


def distributed_env(project: str, *, versions: Mapping[str, Optional[str]], profiles: str, backend_port: int,
                    frontend_port: int, secret: str, pg_password: str, compose_file: bool = True) -> str:
    """A .env like ./bin/install --distributed writes. A version key whose
    value is None is left out (it follows :latest, or :local for the collector)."""

    lines = [
        "# Future AGI configuration (bin/update e2e fixture, shaped like ./bin/install --distributed's .env).",
        "# bin/update may only ever change the image version keys below.",
        f"COMPOSE_PROJECT_NAME={project}",
    ]
    if compose_file:
        lines.append(f"COMPOSE_FILE={DISTRIBUTED_FILE}")
    if profiles:
        lines.append(f"COMPOSE_PROFILES={profiles}")
    lines += [
        "",
        "# --- secrets -----------------------------------------------------------",
        f"SECRET_KEY={secret}",
        f'PG_PASSWORD="{pg_password}"',
        "EXTRA_SETTING=kept byte for byte # with a trailing comment",
        "",
        "# --- release ------------------------------------------------------------",
    ]
    for key in VERSION_KEYS:
        if versions.get(key) is not None:
            lines.append(f"{key}={versions[key]}")
    lines += [
        "# FI_COLLECTOR_VERSION=local   (unset: the collector is built from this checkout)",
        f"FRONTEND_PORT={frontend_port}",
        f"BACKEND_PORT={backend_port}",
    ]
    return "\n".join(lines) + "\n"


def production_env(project: str, *, versions: Mapping[str, Optional[str]], backend_port: int, frontend_port: int,
                   secret: str, pg_password: str, admin_token: str) -> str:
    """deploy/.env.production as deploy/setup.sh leaves it: secrets, the seven pins, URLs."""

    lines = [
        "# Production environment for self-hosted Future AGI (bin/update e2e fixture,",
        "# shaped like the deploy/.env.production deploy/setup.sh writes).",
        f"COMPOSE_PROJECT_NAME={project}",
        "",
        "# ---- REQUIRED ----",
        f"SECRET_KEY={secret}",
        f"AGENTCC_ADMIN_TOKEN={admin_token}",
        f"PG_PASSWORD='{pg_password}'",
        "",
        "# ---- REQUIRED — pin a specific release per image ----",
    ]
    for key in VERSION_KEYS:
        if versions.get(key) is not None:
            lines.append(f"{key}={versions[key]}")
    lines += [
        "",
        "# ---- REQUIRED — public URLs ----",
        "FRONTEND_URL=https://app.example.test",
        "VITE_HOST_API=https://api.example.test   # trailing comment kept",
        f"FRONTEND_PORT={frontend_port}",
        f"BACKEND_PORT={backend_port}",
    ]
    return "\n".join(lines) + "\n"


def pinned(version: str, *, collector: Optional[str] = "same", runner: Optional[str] = "same") -> Dict[str, Optional[str]]:
    """Version keys all set to `version`; collector/runner None leaves them out."""

    values: Dict[str, Optional[str]] = {key: version for key in VERSION_KEYS}
    values["FI_COLLECTOR_VERSION"] = version if collector == "same" else collector
    values["SIMULATION_RUNNER_VERSION"] = version if runner == "same" else runner
    return values


def split_env(text: str, keys: Iterable[str] = VERSION_KEYS) -> Tuple[List[str], Dict[str, List[str]]]:
    """(every line that is not an uncommented `key=` line for `keys`, in order;
    key -> values of its lines)."""

    keys = tuple(keys)
    rest: List[str] = []
    values: Dict[str, List[str]] = {}
    for line in text.splitlines(keepends=True):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*?)\r?\n?$", line)
        if match and match.group(1) in keys:
            values.setdefault(match.group(1), []).append(match.group(2))
        else:
            rest.append(line)
    return rest, values


# ---------------------------------------------------------------- images --

class FakeImages:
    """Assembles the fake Distributed images in the run's registry: the
    busybox base layers, a layer holding FAKE_SCRIPT as /fake.sh, and one
    config per (repository, version)."""

    def __init__(self, harness: "DistributedHarness") -> None:
        self.harness = harness
        self.base_layers, self.base_diff_ids, self.platform, self.base_env = e2e.ImageAssembler._base_image()
        raw = self._script_tar()
        self.script_layer = gzip.compress(raw, mtime=0)
        self.script_diff_id = "sha256:" + hashlib.sha256(raw).hexdigest()
        self._uploaded: set = set()
        self._lock = threading.Lock()

    @staticmethod
    def _script_tar() -> bytes:
        data = FAKE_SCRIPT.encode("utf-8")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
            info = tarfile.TarInfo("fake.sh")
            info.size, info.mode, info.mtime = len(data), 0o755, 1760832000
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    def _blob(self, data: bytes, repository: str) -> None:
        key = (repository, hashlib.sha256(data).hexdigest())
        with self._lock:
            if key in self._uploaded:
                return
        self.harness.upload_blob(data, repository)
        with self._lock:
            self._uploaded.add(key)

    def push(self, component: str, tag: str, version: str, mode: str) -> str:
        repository, command, _ = COMPONENTS[component]
        created = "2026-10-19T00:00:00Z"
        for blob in (*self.base_layers, self.script_layer):
            self._blob(blob, repository)
        port = command[1] if len(command) > 1 else None
        config = {
            **self.platform,
            "created": created,
            "config": {
                "Env": [*self.base_env, f"VERSION={version}", f"MODE={mode}", f"COMPONENT={component}"],
                "Entrypoint": ["/bin/sh", "/fake.sh"],
                "Cmd": list(command),
                "Labels": {"org.opencontainers.image.version": version,
                           "org.opencontainers.image.revision": f"{component}-{mode}",
                           "org.opencontainers.image.title": f"bin/update e2e fake {repository} image"},
                "ExposedPorts": {f"{port}/tcp": {}} if port else {},
                "Healthcheck": HEALTHCHECK,
            },
            "rootfs": {"type": "layers", "diff_ids": [*self.base_diff_ids, self.script_diff_id]},
            "history": [{"created": created, "created_by": f"{e2e.BASE_IMAGE} (docker save)"},
                        {"created": created, "created_by": "COPY fake.sh /fake.sh # fagi-upd-e2e-dist harness"}],
        }
        config_blob = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._blob(config_blob, repository)
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                       "size": len(config_blob), "digest": "sha256:" + hashlib.sha256(config_blob).hexdigest()},
            "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                        "size": len(blob), "digest": "sha256:" + hashlib.sha256(blob).hexdigest()}
                       for blob in (*self.base_layers, self.script_layer)],
        }
        return self.harness.put_manifest(tag, json.dumps(manifest, indent=2).encode("utf-8"),
                                         "application/vnd.docker.distribution.manifest.v2+json", repository)


# ---------------------------------------------------------------- harness --

class DistributedHarness(e2e.Harness):
    """harness.Harness plus the Distributed images, fixture and releases."""

    def __init__(self, base: Optional[Path] = None, prefix: Optional[str] = None) -> None:
        super().__init__(base=base, prefix=prefix or dist_prefix())
        self.dist_digests: Dict[Tuple[str, str], str] = {}   # (component, tag) -> registry digest
        self.collector_local_id = ""
        self.collector_local_repo_digests: List[str] = []
        self.images: Optional[FakeImages] = None
        self._dist_releases: Dict[Tuple[str, str], e2e.Release] = {}

    def start(self, releases: Iterable[Sequence[str]] = (), extra_images: Optional[Mapping[str, str]] = None,
              dist_releases: Iterable[Tuple[str, str]] = ()) -> "DistributedHarness":
        """The base harness (registry, standalone images, keys), then the
        Distributed images, the locally built collector and `dist_releases`
        ((version, signer) pairs, signed in parallel)."""

        if self._started:
            return self
        super().start(releases=releases, extra_images=extra_images)
        self.images = FakeImages(self)
        for version, modes in RELEASE_MODES.items():
            for component in COMPONENTS:
                self.push_component(component, version, modes.get(component, "ok"))
        for component in COMPONENTS:
            self.push_component(component, LATEST, RELEASE_MODES[LATEST].get(component, "ok"), tag="latest")
        self.build_local_collector()
        specs = list(dist_releases)
        if specs:
            with ThreadPoolExecutor(max_workers=2) as pool:
                for future in [pool.submit(self.dist_release, version, signer=signer) for version, signer in specs]:
                    future.result()
        return self

    def dist_image(self, component: str, tag: str) -> str:
        return self.image_ref(tag, COMPONENTS[component][0])

    def push_component(self, component: str, version: str, mode: str, *, tag: Optional[str] = None) -> str:
        assert self.images is not None
        tag = tag or version
        repository = COMPONENTS[component][0]
        digest = self.images.push(component, tag, version, mode)
        if digest != self.registry_digest(tag, repository):
            raise RuntimeError(f"registry does not serve {repository}:{tag} as {digest}")
        self.dist_digests[(component, tag)] = digest
        e2e.log(f"published {self.dist_image(component, tag)} ({version} {mode}) {digest[:19]}…")
        return digest

    def dist_template(self) -> Path:
        with self._lock:
            root = self.templates_dir / "distributed"
            if not (root / "deploy/update/upgrade-floor").exists():
                write_distributed_files(root, registry=self.registry, prefix=self.prefix)
                shutil.copyfile(e2e.WORKTREE / "deploy/update/upgrade-floor", root / "deploy/update/upgrade-floor")
            return root

    @property
    def collector_local_ref(self) -> str:
        return self.dist_image("fi_collector", COLLECTOR_LOCAL_TAG)

    def build_local_collector(self) -> None:
        """What the default Distributed install does: build fi-collector from
        the checkout as futureagi/fi-collector:local (no version label). Built
        once per run, so every checkout runs the same local image."""

        tag = self.collector_local_ref
        self.local_tags.add(tag)
        self._save_state()
        e2e.run(["docker", "build", "-q", "-t", tag, str(self.dist_template() / "fi-collector")],
                env={**os.environ, "DOCKER_BUILDKIT": "0"}, timeout=900)
        record = json.loads(e2e.run(["docker", "image", "inspect", tag]).stdout)[0]
        labels = (record.get("Config") or {}).get("Labels") or {}
        if labels.get("org.opencontainers.image.version"):
            raise RuntimeError(f"the locally built {tag} must carry no version label")
        self.collector_local_id = record["Id"]
        # Empty with the classic image store; the containerd image store
        # records the local build's own digest (never a registry's).
        self.collector_local_repo_digests = list(record.get("RepoDigests") or [])
        e2e.log(f"built {tag} locally ({self.collector_local_id[:19]}…, RepoDigests "
                f"{self.collector_local_repo_digests or 'none'})")

    def dist_release(self, version: str, *, signer: str = "trusted") -> e2e.Release:
        """A manifest for `version` naming the registry digests of the
        Distributed images, written by the real generator for the
        Distributed fixture's deployment files and signed by `signer`."""

        key = (version, signer)
        with self._lock:
            if key in self._dist_releases:
                return self._dist_releases[key]
        out = self.releases_dir / f"distributed-{signer}" / version
        out.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, str(e2e.WORKTREE / "scripts/generate_update_manifest.py"),
                "--version", version, "--source-sha", e2e.sha256_hex("distributed" + version)[:40],
                "--published-at", e2e.PUBLISHED_AT, "--helm-chart", e2e.HELM_CHART,
                "--helm-digest", "sha256:" + e2e.sha256_hex("helm" + version),
                "--repo-root", str(self.dist_template()), "--output", str(out / e2e.MANIFEST_NAME),
                "--component", f"standalone={self.image_ref(version)}@{self.registry_digest(version)}",
                "--component", f"serving_gpu={self.image_ref(version + '-gpu', 'futureagi/serving')}"
                               f"@sha256:{e2e.sha256_hex('serving_gpu' + version)}"]
        for component in COMPONENTS:
            args += ["--component", f"{component}={self.dist_image(component, version)}@{self.dist_digests[(component, version)]}"]
        e2e.run(args)
        self.sign(out, signer)
        release = e2e.Release(version, "distributed", signer, out, self.dist_digests[("backend", version)])
        with self._lock:
            self._dist_releases[key] = release
        e2e.log(f"signed distributed/{version} manifest with the {signer} key")
        return release

    def new_distributed_checkout(self, name: str, *, production: bool = False,
                                 versions: Optional[Mapping[str, Optional[str]]] = None, profiles: str = "",
                                 settings: Optional[Mapping[str, str]] = None,
                                 env_file: Optional[str] = None) -> "DistributedCheckout":
        """A checkout named `name`; a production one keeps its settings in
        `env_file` (relative to the checkout, default deploy/.env.production)."""

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise ValueError(f"checkout names are lower-case letters, digits and dashes: {name!r}")
        if env_file is not None and not production:
            raise ValueError("only a production checkout takes another env file")
        project = f"{self.prefix}{name}"
        root = self.checkouts_dir / project
        if root.exists():
            DistributedCheckout(self, project, root, 0, 0, "", production=production, env_file=env_file).down()
            shutil.rmtree(root)
        (root / "bin").mkdir(parents=True)
        # FUTURE_AGI_UPDATE_E2E_SOURCE (default: this worktree), as for the Standalone checkouts; the
        # release side (manifests, upgrade-floor) always comes from this worktree.
        source = e2e.update_source()
        shutil.copy2(source / "bin/update", root / "bin/update")
        shutil.copytree(source / "deploy/update", root / "deploy/update",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        write_distributed_files(root, registry=self.registry, prefix=self.prefix)
        shutil.copyfile(e2e.WORKTREE / "deploy/update/upgrade-floor", root / "deploy/update/upgrade-floor")
        backend, frontend = e2e.free_port(self.ports), e2e.free_port(self.ports)
        secret = f"e2e-secret-{secrets.token_hex(12)}"
        checkout = DistributedCheckout(self, project, root, backend, frontend, secret, production=production,
                                       env_file=env_file)
        checkout.env_path.parent.mkdir(parents=True, exist_ok=True)
        if production:
            checkout.pg_password = f"e2e prod pg $ecret {secrets.token_hex(4)}"
            checkout.admin_token = f"e2e-admin-token-{secrets.token_hex(8)}"
            text = production_env(project, versions=versions or pinned(V51), backend_port=backend,
                                  frontend_port=frontend, secret=secret, pg_password=checkout.pg_password,
                                  admin_token=checkout.admin_token)
        else:
            checkout.pg_password = f"e2e dist pg password {secrets.token_hex(4)}"
            text = distributed_env(project, versions=versions or pinned(V51), profiles=profiles,
                                   backend_port=backend, frontend_port=frontend, secret=secret,
                                   pg_password=checkout.pg_password)
        checkout.env_path.write_text(text, encoding="utf-8")
        checkout.env_path.chmod(0o600)
        checkout.write_update_conf({"FUTURE_AGI_UPDATE_WAIT_TIMEOUT_SECONDS": "300", **(settings or {})})
        return checkout


class DistributedCheckout(e2e.Checkout):
    """A fake checkout running the Distributed stack, plain (.env) or as a
    production install (deploy/.env.production plus the overlay)."""

    def __init__(self, harness: DistributedHarness, project: str, root: Path, backend_port: int,
                 frontend_port: int, secret: str, *, production: bool, env_file: Optional[str] = None) -> None:
        super().__init__(harness, project, root, backend_port, frontend_port, secret)
        self.production = production
        # The env file, relative to the checkout: .env, deploy/.env.production, or the one given with --env-file.
        self.env_name = env_file or (PRODUCTION_ENV if production else ".env")
        self.pg_password = ""
        self.admin_token = ""

    # ------------------------------------------------------------- files --
    @property
    def env_path(self) -> Path:
        return self.root / self.env_name

    @property
    def dotenv_path(self) -> Path:
        return self.root / ".env"

    @property
    def production_env_path(self) -> Path:
        return self.root / PRODUCTION_ENV

    def temp_env_files(self) -> List[str]:
        found = []
        for directory in (self.root, self.root / "deploy"):
            if directory.is_dir():
                found += [str(p.relative_to(self.root)) for p in directory.iterdir()
                          if p.name.startswith(".env.futureagi-update-")]
        return sorted(found)

    # ---------------------------------------------------------- commands --
    def compose_prefix(self) -> List[str]:
        if self.production:
            return ["--env-file", str(self.env_path), "-f", str(self.root / DISTRIBUTED_FILE),
                    "-f", str(self.root / PRODUCTION_OVERLAY)]
        return []

    def compose(self, *args: str, check: bool = True, timeout: float = 600) -> e2e.Result:
        return e2e.run([*self.harness.compose, *self.compose_prefix(), *args], cwd=self.root,
                       env=e2e.clean_environ(), check=check, timeout=timeout)

    def paste(self, command: str, timeout: float = 1200) -> e2e.Result:
        """Run a command bin/update printed, as an operator pastes it into a
        shell in the checkout (the environment bin_update gives bin/update)."""

        result = e2e.run(["sh", "-c", command], cwd=self.root, env=self.update_env(), check=False, timeout=timeout)
        self._log(f"[pasted in {self.root}]\n{result.describe()}")
        return result

    def install(self, timeout: float = 420) -> None:
        """./bin/install --distributed's (or deploy/setup.sh's) last step:
        start the stack and wait until it serves. A production install is
        initialized first (its overlay's bootstrap job only checks). The
        collector image was built once for the run
        (DistributedHarness.build_local_collector)."""

        name = json.loads(self.compose("config", "--format", "json").stdout).get("name")
        if name != self.project:
            raise RuntimeError(f"compose resolves project {name!r}, not {self.project!r}: refusing to start it")
        if self.production:
            self.initialize()
        self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", str(int(timeout)), timeout=timeout + 120)
        self.wait_healthy(timeout=120)

    def install_older_release(self, timeout: float = 420) -> e2e.Result:
        """Install from an older docker-compose.distributed.yml that still
        defines RETIRED_SERVICE, then do what an operator does after checking
        out this release: its file comes back (byte for byte, so the manifest
        still matches it) and `up -d` runs. Compose warns about the retired
        service's container and leaves it running: an orphan. Returns that
        `up`."""

        path = self.root / DISTRIBUTED_FILE
        current = path.read_bytes()
        path.write_text(older_distributed_compose(registry=self.harness.registry, prefix=self.harness.prefix),
                        encoding="utf-8")
        try:
            self.install(timeout=timeout)
        finally:
            path.write_bytes(current)
        return self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", str(int(timeout)),
                            timeout=timeout + 120)

    def initialize(self, timeout: float = 600) -> e2e.Result:
        """The release's initialization (deploy/README.md › Performing that
        initialization, step 1): the bootstrap job in apply mode, one-off
        (`run --rm` starts it and nothing else), against the production env
        file and compose files, so it runs the image the pins select."""

        return self.compose(*INIT_JOB, timeout=timeout)

    def wait_healthy(self, timeout: float = 90) -> None:
        e2e.wait_for(lambda: e2e.http_ok(self.url("/health/")) and e2e.http_ok(self.url("/", ui=True)),
                     timeout=timeout, interval=0.5,
                     what=f"{self.project} to answer on ports {self.backend_port}/{self.frontend_port}")

    def down(self) -> None:
        if self.harness.keep:
            return
        if (self.root / DISTRIBUTED_FILE).exists() and self.env_path.exists():
            self.compose("down", "-v", "--remove-orphans", "--timeout", "5", check=False, timeout=300)
        label = f"label=com.docker.compose.project={self.project}"
        ids = e2e.run(["docker", "ps", "-a", "-q", "--filter", label], check=False).stdout.split()
        if ids:
            e2e.run(["docker", "rm", "-f", "-v", *ids], check=False)
        volumes = e2e.run(["docker", "volume", "ls", "-q", "--filter", label], check=False).stdout.split()
        if volumes:
            e2e.run(["docker", "volume", "rm", "-f", *volumes], check=False)
        networks = e2e.run(["docker", "network", "ls", "-q", "--filter", label], check=False).stdout.split()
        if networks:
            e2e.run(["docker", "network", "rm", *networks], check=False)

    # -------------------------------------------------------- inspection --
    # Docker CLI calls are slow on a busy host: these batch them.
    def stack(self) -> Dict[str, e2e.Container]:
        """service -> its container, from one `docker ps` + `docker inspect`."""

        stack: Dict[str, e2e.Container] = {}
        for container in self.containers():
            if container.service in stack:
                raise AssertionError(f"{self.project} has more than one {container.service} container")
            stack[container.service] = container
        return stack

    def images_info(self, references: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """reference (or image id) -> image_info(), from one `docker image inspect`."""

        references = sorted(set(references))
        if not references:
            return {}
        records = json.loads(e2e.run(["docker", "image", "inspect", *references]).stdout)
        if len(records) != len(references):
            raise AssertionError(f"docker image inspect returned {len(records)} records for {references}")
        infos = {}
        for reference, record in zip(references, records):  # noqa: B905 - lengths checked above (3.9 has no strict=)
            labels = (record.get("Config") or {}).get("Labels") or {}
            infos[reference] = {"id": record.get("Id", ""), "repo_digests": record.get("RepoDigests") or [],
                                "version": labels.get("org.opencontainers.image.version", ""),
                                "flavor": labels.get("org.opencontainers.image.revision", "")}
        return infos

    def inspect(self, container_id: str) -> Dict[str, Any]:
        return json.loads(e2e.run(["docker", "inspect", container_id]).stdout)[0]

    def bootstrap_history(self) -> List[str]:
        return [line.strip() for line in self.volume_text("bootstrap-history", "bootstrap-data").splitlines()
                if line.strip()]

    def snapshot(self) -> Dict[str, Any]:
        """Everything a refused or failed-before-restart run must leave alone."""

        def read(path: Path) -> Optional[bytes]:
            return path.read_bytes() if path.exists() else None

        return {
            "dotenv": read(self.dotenv_path),
            "production_env": read(self.production_env_path),
            "env_file": read(self.env_path),
            "env_mode": self.env_path.stat().st_mode & 0o7777 if self.env_path.exists() else None,
            "containers": sorted((c.service, c.id, c.image_id, c.status, c.started_at) for c in self.containers()),
            "receipts": [p.name for p in self.receipt_paths()],
            "journal": self.journal(),
        }
