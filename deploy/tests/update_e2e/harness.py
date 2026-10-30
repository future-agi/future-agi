"""Docker end-to-end harness for bin/update, the host-side self-updater.

It runs bin/update exactly the way an operator does, against real Docker:

* one local `registry:2` for the whole run, on a free port;
* fake `futureagi/standalone` images defined by ./fakeapp (busybox: /health/
  on 8000, a UI on 3000, the OCI version label, a /data history file),
  published as v1.51.0 (ok), v1.52.0 (ok), v1.53.0 (unhealthy), v1.54.0
  (crash), v1.55.0 (slow first start), and `latest` = v1.51.0. They are
  assembled in the registry (no `docker build`), so Docker only ever gets
  them by pulling, as on a real host;
* cosign key pairs (trusted and attacker) and release manifests written by the
  real scripts/generate_update_manifest.py and signed in key mode;
* fake checkouts: bin/update and deploy/update copied from this worktree, a
  docker-compose.yml shaped like the real Standalone file, placeholders for
  the other compose files, .env/.env.example, and an update.conf that trusts
  the test key offline.

Nothing here runs unless FUTURE_AGI_UPDATE_E2E=1 (see availability()).

Environment:
  FUTURE_AGI_UPDATE_E2E=1         enable the suites
  FUTURE_AGI_UPDATE_E2E_DIR       base directory (default ~/.fagi-update-e2e); it
                                  must be visible to Docker bind mounts
  FUTURE_AGI_UPDATE_E2E_PREFIX    name prefix of every Docker resource and
                                  directory the harness creates (default fagi-upd-e2e-sa-;
                                  the Distributed suite uses its own, fagi-upd-e2e-dist-, and
                                  the fault-injection suite fagi-upd-e2e-flt-)
  FUTURE_AGI_UPDATE_E2E_KEEP=1    keep containers, registry and files at the end
  FUTURE_AGI_UPDATE_E2E_PYTHON    interpreter bin/update runs with (its PYTHON)
  FUTURE_AGI_UPDATE_E2E_BUILD     `docker` to build images from fakeapp/Dockerfile and
                                  push them, instead of assembling them in the registry
  FUTURE_AGI_UPDATE_E2E_JOBS      tests run at once (default 4; 1 runs them one by one).
                                  Each test owns its project, ports and checkout, and
                                  most of its time is spent waiting on Docker.
  FUTURE_AGI_UPDATE_E2E_SOURCE    directory whose bin/update and deploy/update the fake
                                  Standalone checkouts get (default: this worktree), e.g.
                                  a copy with one behaviour reverted, to show a test
                                  fails without it (the release side always comes from
                                  this worktree)
  FUTURE_AGI_UPDATE_E2E_COMPOSE   path to a docker-compose binary (say Compose v2.24.0,
                                  the documented minimum) that every Compose call of
                                  the run uses: a private bin directory goes first on
                                  the PATH of each bin/update and Compose command the
                                  harness runs (ComposeSwitch), so bin/update gets that
                                  version whether it probes `docker compose` or
                                  `docker-compose`. The harness logs the version it ran.

The harness is idempotent: start() first removes whatever an earlier run with
the same prefix left behind, and stop() removes everything it created. Both
leave alone what carries another suite's prefix, even one that extends this
run's (a Standalone run given the prefix fagi-upd-e2e- must not remove a
Distributed run's fagi-upd-e2e-dist- resources), so the suites can run at once.
Images carry no prefix in their names (localhost:<port>/futureagi/...), so the
fake images are labelled with the prefix that published them (HARNESS_LABEL),
and images pulled from an earlier run's registry port are removed only while
no other suite's container publishes that port.
"""

from __future__ import annotations

import atexit
import gzip
import hashlib
import io
import json
import os
import random
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

WORKTREE = Path(__file__).resolve().parents[3]
FAKEAPP_CONTEXT = Path(__file__).resolve().parent / "fakeapp"

ENABLE_VAR = "FUTURE_AGI_UPDATE_E2E"
DIR_VAR = "FUTURE_AGI_UPDATE_E2E_DIR"
PREFIX_VAR = "FUTURE_AGI_UPDATE_E2E_PREFIX"
KEEP_VAR = "FUTURE_AGI_UPDATE_E2E_KEEP"
PYTHON_VAR = "FUTURE_AGI_UPDATE_E2E_PYTHON"
BUILD_VAR = "FUTURE_AGI_UPDATE_E2E_BUILD"
JOBS_VAR = "FUTURE_AGI_UPDATE_E2E_JOBS"
SOURCE_VAR = "FUTURE_AGI_UPDATE_E2E_SOURCE"
COMPOSE_VAR = "FUTURE_AGI_UPDATE_E2E_COMPOSE"
DEFAULT_JOBS = 4
# Image label naming the harness prefix that published a fake image (cleanup_leftovers()).
HARNESS_LABEL = "com.futureagi.update-e2e.prefix"
DEFAULT_PREFIX = "fagi-upd-e2e-sa-"
# The Distributed suite's prefix (distributed.py reads the same variable).
DIST_PREFIX_VAR = "FUTURE_AGI_UPDATE_E2E_DIST_PREFIX"
DEFAULT_DIST_PREFIX = "fagi-upd-e2e-dist-"
# The fault-injection suite's prefix (faults.py reads the same variable).
FAULTS_PREFIX_VAR = "FUTURE_AGI_UPDATE_E2E_FAULTS_PREFIX"
DEFAULT_FAULTS_PREFIX = "fagi-upd-e2e-flt-"
DEFAULT_BASE = Path.home() / ".fagi-update-e2e"

try:  # the updater's own pinned cosign image, so tests sign with what it verifies with
    from deploy.update.settings import DEFAULT_COSIGN_IMAGE as COSIGN_IMAGE
except Exception:  # pragma: no cover - only when imported outside the checkout
    COSIGN_IMAGE = ("ghcr.io/sigstore/cosign/cosign:v3.1.3"
                    "@sha256:9e5c2f2edc34351160407ca3416c61855bdf9403c3c5936e0f0be7fc261611b8")
REGISTRY_IMAGE = "registry:2"
# Every manifest media type the fake images may be stored as (the HEAD/GET Accept header).
MANIFEST_ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json",
                             "application/vnd.docker.distribution.manifest.list.v2+json",
                             "application/vnd.oci.image.manifest.v1+json",
                             "application/vnd.docker.distribution.manifest.v2+json"))
BASE_IMAGE = "busybox:1.37"
REPOSITORY = "futureagi/standalone"
MANIFEST_NAME = "future-agi-update-manifest.json"
BUNDLE_NAME = "future-agi-update-manifest.sigstore.json"
COSIGN_PASSWORD = "fagi-upd-e2e-password"
PUBLISHED_AT = "2026-10-19T00:00:00Z"
HELM_CHART = "oci://ghcr.io/future-agi/charts/futureagi"
STATE_DIR = ".futureagi-updates"

# version -> fake app mode. `latest` is pushed as LATEST_VERSION.
IMAGES = {
    "v1.51.0": "ok",
    "v1.52.0": "ok",
    "v1.53.0": "unhealthy",
    "v1.54.0": "crash",
    "v1.55.0": "slow",
}
LATEST_VERSION = "v1.51.0"
# How long MODE=slow logs migrations before it serves (fakeapp/Dockerfile).
SLOW_SECONDS = 20

# Manifest components other than standalone are never pulled by the fixture;
# they still need well-formed references and digests.
OTHER_COMPONENTS = {
    "backend": ("futureagi/future-agi", ""),
    "frontend": ("futureagi/frontend", ""),
    "fi_collector": ("futureagi/fi-collector", ""),
    "agentcc_gateway": ("futureagi/agentcc-gateway", ""),
    "serving": ("futureagi/serving", ""),
    "serving_gpu": ("futureagi/serving", "-gpu"),
    "code_executor": ("futureagi/code-executor", ""),
    "simulation_runner": ("futureagi/future-agi-simulation-runner", ""),
}
VERSION_VARIABLES = ("FUTURE_AGI_VERSION", "FRONTEND_VERSION", "FI_COLLECTOR_VERSION", "AGENTCC_GATEWAY_VERSION",
                     "SERVING_VERSION", "CODE_EXECUTOR_VERSION", "SIMULATION_RUNNER_VERSION")

# Files of the fake checkout that releases hash (its deploy/update/deployment-files.txt).
DEPLOYMENT_FILES = (
    "docker-compose.yml",
    "docker-compose.distributed.yml",
    "deploy/docker-compose.production.yml",
    ".env.example",
    "deploy/.env.production.example",
    "deploy/standalone/datastore",
)

STANDALONE_COMPOSE = """\
# ============================================================================
# bin/update e2e fixture: a Standalone install shaped like the real
# docker-compose.yml (deploy/tests/update_e2e/harness.py writes it).
#
#   app         the fake futureagi/standalone image from the run's registry
#   postgres    a healthy datastore stand-in with a bind-mounted config dir
#
# The project name comes from COMPOSE_PROJECT_NAME in .env, as ./bin/install
# records it; `name:` below is only the fallback.
# ============================================================================
name: @PREFIX@standalone

services:
  app:
    image: @REGISTRY@/futureagi/standalone:${FUTURE_AGI_VERSION:-latest}
    env_file:
      - path: .env
        required: false
    environment:
      SECRET_KEY: ${SECRET_KEY:-local-dev-only-not-for-production-replace-me}
      FUTURE_AGI_VERSION: ${FUTURE_AGI_VERSION:-unknown}
      FUTURE_AGI_DEPLOYMENT_TYPE: ${FUTURE_AGI_DEPLOYMENT_TYPE:-docker}
    ports:
      - "${FRONTEND_PORT:-3000}:3000"
      - "${BACKEND_PORT:-8000}:8000"
    volumes:
      - app-data:/data
      # Shaped like the real file's agentcc config mount: a short-syntax bind whose host path is a variable
      # with a default. Compose before v2.40.3 rejects it in `config --no-interpolate` ("too many colons").
      - ./${FAGI_E2E_LIMITS_PATH:-deploy/standalone/datastore/limits.conf}:/etc/futureagi/e2e-limits.conf:ro
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      interval: 2s
      timeout: 2s
      retries: 3
      start_period: 600s
    stop_grace_period: 10s
    restart: unless-stopped

  postgres:
    image: @BASE_IMAGE@
    command: ["sh", "-c", "trap 'exit 0' TERM INT; cat /etc/datastore/datastore.conf; touch /tmp/ready; while :; do sleep 1 & wait $$!; done"]
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./deploy/standalone/datastore:/etc/datastore:ro
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 1s
      timeout: 2s
      retries: 30
    restart: unless-stopped

volumes:
  app-data:
  postgres-data:
"""

DISTRIBUTED_PLACEHOLDER = """\
# bin/update e2e fixture: placeholder for docker-compose.distributed.yml.
# The Standalone suite never starts it; it exists (and is hashed) because a
# real checkout has it.
name: @PREFIX@distributed
services:
  backend:
    image: @REGISTRY@/futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}
"""

PRODUCTION_PLACEHOLDER = """\
# bin/update e2e fixture: placeholder for deploy/docker-compose.production.yml.
services:
  backend:
    environment:
      ENV_TYPE: production
"""

ENV_EXAMPLE = """\
# Future AGI configuration template (bin/update e2e fixture, shaped like .env.example)

# 1. Secrets. ./bin/install generates them.
SECRET_KEY=
PG_PASSWORD=
@EXTRA@
# 4. Advanced tuning.
# Image tag of the Future AGI images: empty = latest, a release pins it.
@VERSION_LINE@

# Host ports. ./bin/install offers a free port and writes it here on a conflict.
# FRONTEND_PORT=3000
# BACKEND_PORT=8000
# INSTALL_READY_TIMEOUT_SECONDS=600
# INSTALL_STABILITY_SECONDS=15
"""

# Checkout variants differ only in .env.example (a deployment file).
VARIANTS = {
    "default": {"version_line": "FUTURE_AGI_VERSION=", "extra": ""},
    # A release whose .env.example adds a setting the install's .env lacks.
    "newkey": {"version_line": "FUTURE_AGI_VERSION=",
               "extra": "\n# 2. Added by this release.\nNEW_RELEASE_SETTING=enabled\n"},
    # Same runtime behavior as default, but with a deployment-file change so
    # Docker E2E can exercise rollback across two git-tagged releases.
    "compose-change": {"version_line": "FUTURE_AGI_VERSION=", "extra": "",
                       "compose_note": "# E2E deployment revision for v1.52.0\n"},
    # An older template that leaves FUTURE_AGI_VERSION commented: installs made
    # from it carry no FUTURE_AGI_VERSION at all and follow :latest.
    "nopin": {"version_line": "# FUTURE_AGI_VERSION=", "extra": ""},
}


def log(message: str) -> None:
    sys.stderr.write(f"[update-e2e] {message}\n")
    sys.stderr.flush()


# ------------------------------------------------------------------ running --

@dataclass
class Result:
    args: List[str]
    code: int
    stdout: str
    stderr: str
    seconds: float = 0.0

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def describe(self) -> str:
        return (f"$ {' '.join(self.args)}\n(exit {self.code} after {self.seconds:.1f}s)\n"
                f"--- stdout ---\n{self.stdout}\n--- stderr ---\n{self.stderr}")


class CommandFailed(RuntimeError):
    def __init__(self, result: Result) -> None:
        super().__init__(result.describe())
        self.result = result


def run(args: Sequence[str], *, cwd: Optional[Path] = None, env: Optional[Mapping[str, str]] = None,
        check: bool = True, timeout: float = 600, stdin: Optional[str] = None) -> Result:
    started = time.monotonic()
    try:
        completed = subprocess.run(list(args), cwd=str(cwd) if cwd else None, env=dict(env) if env else None,
                                   capture_output=True, text=True, timeout=timeout, input=stdin, check=False)
        result = Result(list(map(str, args)), completed.returncode, completed.stdout or "", completed.stderr or "",
                        time.monotonic() - started)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        result = Result(list(map(str, args)), -999, out, err + f"\n[harness] timed out after {timeout}s",
                        time.monotonic() - started)
    if check and result.code != 0:
        raise CommandFailed(result)
    return result


def clean_environ(extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """The caller's environment minus everything that would steer bin/update
    or Compose away from the checkout's own files (updater settings, COMPOSE_*,
    exported image versions). With FUTURE_AGI_UPDATE_E2E_COMPOSE, its bin
    directory leads PATH unless `extra`'s PATH already holds it (a pull
    wrapper's, which runs it after the wrapper: see search_path)."""

    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("FUTURE_AGI_UPDATE_", "COMPOSE_")) and key not in VERSION_VARIABLES}
    env.update(extra or {})
    switch = compose_switch()
    if switch is not None and str(switch.bin) not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = search_path(env.get("PATH"))
    return env


class ComposeSwitch:
    """FUTURE_AGI_UPDATE_E2E_COMPOSE: one docker-compose binary for every Compose call of the run.

    A private bin directory holds `docker-compose` (a symlink to the binary)
    and a `docker` shim that runs the binary for `docker compose ...` and the
    real docker CLI for every other command. search_path() and clean_environ()
    put it first on the PATH of each bin/update and Compose command the
    harness runs, so bin/update uses that Compose whichever way it probes
    (`docker compose version`, then `docker-compose version`), and so do the
    Compose commands it prints when they are pasted. The shim only redirects
    `compose` as the first argument: bin/update runs docker with no global
    options. The directory is temporary (removed at exit); it is never
    bind-mounted, so it need not be visible to Docker.
    """

    def __init__(self, binary: Path) -> None:
        self.binary = binary
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError(f"{COMPOSE_VAR}={binary} is not an executable file")
        real_docker = shutil.which("docker")
        if not real_docker:
            raise ValueError("docker is not on PATH")
        self.real_docker = Path(real_docker).resolve()
        self.directory = Path(tempfile.mkdtemp(prefix="fagi-upd-e2e-compose-"))
        atexit.register(shutil.rmtree, self.directory, True)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.compose = self.bin / "docker-compose"
        self.compose.symlink_to(binary)
        docker = self.bin / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            f"# bin/update e2e ({COMPOSE_VAR}): `docker compose ...` runs {binary}, any other command the real docker.\n"
            "if [ \"$1\" = compose ]; then\n"
            "  shift\n"
            f"  exec {shlex.quote(str(binary))} \"$@\"\n"
            "fi\n"
            f"exec {shlex.quote(str(self.real_docker))} \"$@\"\n", encoding="utf-8")
        docker.chmod(0o755)
        probe = run([str(self.compose), "version", "--short"], check=False, timeout=60)
        self.version = probe.stdout.strip() if probe.code == 0 else ""

    def describe(self) -> str:
        return (f"Docker Compose {self.version or '(unknown version)'} from {COMPOSE_VAR}={self.binary} "
                f"(`docker compose` and `docker-compose` both run it, via {self.bin})")


_SWITCH: List[Optional[ComposeSwitch]] = []
_SWITCH_LOCK = threading.Lock()


def compose_switch() -> Optional[ComposeSwitch]:
    """The run's ComposeSwitch, made on first use; None without FUTURE_AGI_UPDATE_E2E_COMPOSE.

    ValueError when the variable names no executable file.
    """

    with _SWITCH_LOCK:
        if not _SWITCH:
            raw = os.environ.get(COMPOSE_VAR) or ""
            _SWITCH.append(ComposeSwitch(Path(raw).expanduser().resolve()) if raw else None)
        return _SWITCH[0]


def search_path(path: Optional[str] = None) -> str:
    """`path` (default: this process's PATH) with the ComposeSwitch's bin directory first, if there is one."""

    path = os.environ.get("PATH", os.defpath) if path is None else path
    switch = compose_switch()
    if switch is None:
        return path
    entries = [entry for entry in path.split(os.pathsep) if entry and entry != str(switch.bin)]
    return os.pathsep.join([str(switch.bin), *entries])


def compose_version(command: Sequence[str]) -> str:
    """What `<command> version --short` prints (say 2.24.0), or "" when it fails."""

    probe = run([*command, "version", "--short"], check=False, timeout=60)
    return probe.stdout.strip() if probe.code == 0 else ""


def compose_command() -> Optional[List[str]]:
    """The ComposeSwitch's docker-compose (FUTURE_AGI_UPDATE_E2E_COMPOSE), else
    `docker compose` (v2 plugin), else a v2+ standalone docker-compose."""

    switch = compose_switch()
    if switch is not None:
        return [str(switch.compose)] if re.match(r"^v?([2-9]|\d{2,})\.", switch.version) else None
    if shutil.which("docker"):
        probe = run(["docker", "compose", "version", "--short"], check=False, timeout=60)
        if probe.code == 0:
            return ["docker", "compose"]
    standalone = shutil.which("docker-compose")
    if standalone:
        probe = run([standalone, "version", "--short"], check=False, timeout=60)
        if probe.code == 0 and re.match(r"^v?([2-9]|\d{2,})\.", probe.stdout.strip()):
            return [standalone]
    return None


def base_dir() -> Path:
    return Path(os.environ.get(DIR_VAR) or DEFAULT_BASE).expanduser().resolve()


def name_prefix() -> str:
    prefix = os.environ.get(PREFIX_VAR) or DEFAULT_PREFIX
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*-", prefix):
        raise ValueError(f"{PREFIX_VAR} must be lower-case letters, digits and dashes ending in '-', not {prefix!r}")
    return prefix


def suite_prefixes() -> List[str]:
    """The prefix of every e2e suite (Standalone, Distributed, fault injection), as configured."""

    return [os.environ.get(PREFIX_VAR) or DEFAULT_PREFIX, os.environ.get(DIST_PREFIX_VAR) or DEFAULT_DIST_PREFIX,
            os.environ.get(FAULTS_PREFIX_VAR) or DEFAULT_FAULTS_PREFIX]


def availability() -> Optional[str]:
    """None when the Docker end-to-end suites can run, else why they skip."""

    if os.environ.get(ENABLE_VAR) != "1":
        return f"set {ENABLE_VAR}=1 to run the Docker end-to-end tests of bin/update"
    if not shutil.which("docker"):
        return "docker is not on PATH"
    info = run(["docker", "info", "--format", "{{.ServerVersion}}"], check=False, timeout=60)
    if info.code != 0:
        return f"the Docker daemon is not reachable: {info.stderr.strip()[-300:]}"
    try:
        switch = compose_switch()
    except ValueError as exc:
        return str(exc)
    if compose_command() is None:
        if switch is not None:
            return f"{COMPOSE_VAR}={switch.binary} is Docker Compose {switch.version or '(unknown version)'}, not v2+"
        return "Docker Compose v2 is not available (neither `docker compose` nor `docker-compose` v2+)"
    if sys.platform.startswith("win"):
        return "bin/update needs a POSIX host"
    try:
        name_prefix()
    except ValueError as exc:
        return str(exc)
    source = update_source()
    if not (source / "bin" / "update").is_file() or not (source / "deploy" / "update" / "__main__.py").is_file():
        return f"{SOURCE_VAR}={source} holds no bin/update and deploy/update/__main__.py"
    return None


def update_source() -> Path:
    """Where the fake checkouts' bin/update and deploy/update come from."""

    return Path(os.environ.get(SOURCE_VAR) or WORKTREE).expanduser().resolve()


_PORT_LOCK = threading.Lock()


def free_port(taken: set) -> int:
    with _PORT_LOCK:
        return _free_port(taken)


def _free_port(taken: set) -> int:
    for _ in range(500):
        port = random.randint(20000, 39999)
        if port in taken:
            continue
        busy = False
        for host in ("0.0.0.0", "127.0.0.1"):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind((host, port))
                except OSError:
                    busy = True
                    break
        if busy:
            continue
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            continue  # something (e.g. a VM port forward) answers there
        except OSError:
            pass
        taken.add(port)
        return port
    raise RuntimeError("no free TCP port found")


def http_ok(url: str, timeout: float = 3.0) -> bool:
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except HTTPError as exc:  # an answer, just not a good one: close it like a response
        exc.close()
        return False
    except (URLError, OSError, ValueError):
        return False


def http_text(url: str, timeout: float = 3.0) -> Optional[str]:
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        exc.close()
        return None
    except (URLError, OSError, ValueError):
        return None


def wait_for(predicate: Callable[[], Any], *, timeout: float, interval: float = 0.25, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")
        time.sleep(interval)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------- fixtures --

def render(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace(f"@{key}@", value)
    return template


def write_deployment_files(root: Path, *, registry: str, prefix: str, variant: str) -> None:
    """The fake checkout's deployment files: byte-identical for every checkout
    of one run and variant, so one signed manifest fits them all."""

    spec = VARIANTS[variant]
    files = {
        "docker-compose.yml": (render(STANDALONE_COMPOSE, REGISTRY=registry, PREFIX=prefix,
                                       BASE_IMAGE=BASE_IMAGE) + spec.get("compose_note", "")),
        "docker-compose.distributed.yml": render(DISTRIBUTED_PLACEHOLDER, REGISTRY=registry, PREFIX=prefix),
        "deploy/docker-compose.production.yml": PRODUCTION_PLACEHOLDER,
        ".env.example": render(ENV_EXAMPLE, EXTRA=spec["extra"], VERSION_LINE=spec["version_line"]),
        "deploy/.env.production.example": "# placeholder: production is not part of the Standalone e2e suite\n",
        "deploy/standalone/datastore/datastore.conf": "# datastore stand-in config (bind-mounted, hashed)\nmode=small-host\n",
        "deploy/standalone/datastore/limits.conf": "max_connections=200\n",
        "deploy/update/deployment-files.txt": (
            "# Deployment files of the bin/update e2e fixture (deploy/tests/update_e2e).\n"
            + "".join(f"{path}\n" for path in DEPLOYMENT_FILES)),
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def env_lines(project: str, *, version: Optional[str], backend_port: int, frontend_port: int,
              secret: str, extra: Iterable[str] = ()) -> str:
    """A .env like the one ./bin/install writes. version=None leaves
    FUTURE_AGI_VERSION out entirely; "" keeps an empty (follow :latest) key."""

    lines = [
        "# Future AGI configuration (bin/update e2e fixture, shaped like ./bin/install's .env).",
        "# bin/update may only ever change the image version keys below.",
        f"COMPOSE_PROJECT_NAME={project}",
        "",
        "# --- secrets -----------------------------------------------------------",
        f"SECRET_KEY={secret}",
        f'PG_PASSWORD="e2e pg password {secret[-8:]}"',
        "EXTRA_SETTING=kept byte for byte # with a trailing comment",
        "",
        "# --- release ------------------------------------------------------------",
    ]
    if version is not None:
        lines.append(f"FUTURE_AGI_VERSION={version}")
    lines += [
        f"FRONTEND_PORT={frontend_port}",
        f"BACKEND_PORT={backend_port}",
        "INSTALL_READY_TIMEOUT_SECONDS=60",
        "INSTALL_STABILITY_SECONDS=5",
        *extra,
    ]
    return "\n".join(lines) + "\n"


@dataclass
class Release:
    version: str
    variant: str
    signer: str
    directory: Path
    digest: str

    @property
    def manifest(self) -> Path:
        return self.directory / MANIFEST_NAME

    @property
    def bundle(self) -> Path:
        return self.directory / BUNDLE_NAME

    def data(self) -> Dict[str, Any]:
        return json.loads(self.manifest.read_text(encoding="utf-8"))

    def copy_to(self, directory: Path, *, bundle: bool = True) -> "Release":
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.manifest, directory / MANIFEST_NAME)
        if bundle:
            shutil.copyfile(self.bundle, directory / BUNDLE_NAME)
        return Release(self.version, self.variant, self.signer, directory, self.digest)


@dataclass
class Container:
    id: str
    name: str
    service: str
    image: str
    image_id: str
    status: str
    restart_count: int
    started_at: str
    env: Dict[str, str]
    ports: Dict[str, Any]
    mounts: List[Dict[str, Any]]
    health: str
    oneoff: bool = False

    @property
    def running(self) -> bool:
        return self.status == "running"

    def host_ports(self, container_port: str) -> List[str]:
        return sorted(binding.get("HostPort", "") for binding in (self.ports.get(container_port) or []))


class Spawned:
    """A bin/update run in the background (own session, output in files)."""

    def __init__(self, args: List[str], proc: subprocess.Popen, out: Path, err: Path) -> None:
        self.args = args
        self.proc = proc
        self.out = out
        self.err = err
        self.started = time.monotonic()

    @property
    def pid(self) -> int:
        return self.proc.pid

    def alive(self) -> bool:
        return self.proc.poll() is None

    def send(self, sig: int) -> None:
        os.kill(self.proc.pid, sig)

    def kill_group(self, sig: int = signal.SIGKILL) -> None:
        try:
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError:
            pass

    def stderr_so_far(self) -> str:
        return self.err.read_text(encoding="utf-8", errors="replace") if self.err.exists() else ""

    def wait(self, timeout: float = 600) -> Result:
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill_group()
            code = self.proc.wait(timeout=30)
            with self.err.open("a") as handle:
                handle.write(f"\n[harness] killed after {timeout}s\n")
        return Result(self.args, code, self.out.read_text(encoding="utf-8", errors="replace"),
                      self.err.read_text(encoding="utf-8", errors="replace"), time.monotonic() - self.started)


class _PullWrapper:
    """docker / docker-compose wrappers on a PATH of their own that run the
    shell function `on_pull` before `compose pull` (Standalone runs Compose
    with no global options, so `pull` is its first word) and pass every
    command on to the binary the run's PATH has (search_path: with
    FUTURE_AGI_UPDATE_E2E_COMPOSE, its shim and binary)."""

    def __init__(self, directory: Path, on_pull: str) -> None:
        self.directory = directory
        self.bin = directory / "bin"
        self.started = directory / "pull-started"
        self.bin.mkdir(parents=True, exist_ok=True)
        real_docker = shutil.which("docker", path=search_path())
        real_compose = shutil.which("docker-compose", path=search_path())
        if real_docker:
            self._write("docker", f"#!/bin/sh\n{on_pull}\n"
                                  f"if [ \"$1\" = compose ] && [ \"$2\" = pull ]; then on_pull; fi\n"
                                  f"exec '{real_docker}' \"$@\"\n")
        if real_compose:
            self._write("docker-compose", f"#!/bin/sh\n{on_pull}\nif [ \"$1\" = pull ]; then on_pull; fi\n"
                                          f"exec '{real_compose}' \"$@\"\n")

    def _write(self, name: str, text: str) -> None:
        path = self.bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def env(self) -> Dict[str, str]:
        return {"PATH": f"{self.bin}{os.pathsep}{search_path()}"}


class SlowPull(_PullWrapper):
    """docker / docker-compose wrappers that hold `compose pull` until
    released (or until the bin/update process that started them is gone),
    so a test can interrupt bin/update deterministically while it pulls."""

    def __init__(self, directory: Path) -> None:
        self.release_file = directory / "release-pull"
        super().__init__(directory, f"""
on_pull() {{
  : > '{directory / "pull-started"}'
  n=0
  while [ ! -e '{self.release_file}' ] && [ "$n" -lt 1200 ]; do
    kill -0 "$PPID" 2>/dev/null || exit 143
    sleep 0.1
    n=$((n + 1))
  done
}}
""")

    def release(self) -> None:
        self.release_file.touch()


class FailPull(_PullWrapper):
    """docker / docker-compose wrappers whose `compose pull` fails at once
    (exit 1, `message` on stderr), the way an unreachable registry fails it,
    and count the failed pulls in `pull-started`."""

    MESSAGE = "Error response from daemon: Get \"https://registry.invalid/v2/\": dial tcp: no such host (e2e)"

    def __init__(self, directory: Path, message: str = MESSAGE) -> None:
        self.message = message
        super().__init__(directory, f"""
on_pull() {{
  echo pull >> '{directory / "pull-started"}'
  echo {shlex.quote(message)} >&2
  exit 1
}}
""")

    def pulls(self) -> int:
        return len(self.started.read_text().split()) if self.started.exists() else 0


# ---------------------------------------------------------------- images --

class ImageAssembler:
    """Pushes the fakeapp image (fakeapp/Dockerfile) to the registry without
    `docker build`: busybox:1.37's own layer (from `docker save`), a layer
    holding start.sh, and a config equal to what the Dockerfile produces.
    Each version is then one small config plus one manifest."""

    def __init__(self, harness: "Harness") -> None:
        self.harness = harness
        self.base_layers, self.base_diff_ids, self.platform, self.base_env = self._base_image()
        self.app_layer, self.app_diff_id = self._app_layer()
        for blob in [*self.base_layers, self.app_layer]:
            harness.upload_blob(blob)

    @staticmethod
    def _base_image():
        # `save` never pulls. A fresh CI daemon need not have the Dockerfile's
        # base cached; use the same inherited Docker context for inspect, pull
        # and save, and leave an existing cached image untouched.
        if run(["docker", "image", "inspect", BASE_IMAGE], check=False).code != 0:
            run(["docker", "pull", BASE_IMAGE], timeout=300)
        with tempfile.TemporaryDirectory(prefix="fagi-upd-e2e-") as tmp:
            archive = Path(tmp) / "base.tar"
            run(["docker", "save", BASE_IMAGE, "-o", str(archive)], timeout=300)
            with tarfile.open(archive) as tar:
                manifest = json.load(tar.extractfile("manifest.json"))[0]
                config = json.load(tar.extractfile(manifest["Config"]))
                raw_layers = [tar.extractfile(name).read() for name in manifest["Layers"]]
        layers = [raw if raw[:2] == b"\x1f\x8b" else gzip.compress(raw, mtime=0) for raw in raw_layers]
        platform = {key: config[key] for key in ("architecture", "os", "variant") if config.get(key)}
        env = list((config.get("config") or {}).get("Env") or [])
        return layers, list(config["rootfs"]["diff_ids"]), platform, env

    @staticmethod
    def _app_layer():
        script = (FAKEAPP_CONTEXT / "start.sh").read_bytes()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
            info = tarfile.TarInfo("start.sh")
            info.size, info.mode, info.mtime = len(script), 0o755, 1760832000
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(script))
        raw = buffer.getvalue()
        return gzip.compress(raw, mtime=0), "sha256:" + hashlib.sha256(raw).hexdigest()

    def push(self, tag: str, version: str, mode: str, flavor: str = "release") -> str:
        created = "2026-10-19T00:00:00Z"
        config = {
            **self.platform,
            "created": created,
            "config": {
                "Env": [*self.base_env, f"VERSION={version}", f"MODE={mode}", f"SLOW_SECONDS={SLOW_SECONDS}", f"FLAVOR={flavor}"],
                "Entrypoint": ["/bin/sh", "/start.sh"],
                "Cmd": None,
                "Labels": {"org.opencontainers.image.version": version,
                           "org.opencontainers.image.revision": flavor,
                           "org.opencontainers.image.title": "bin/update e2e fake standalone image",
                           HARNESS_LABEL: self.harness.prefix},
                "ExposedPorts": {"3000/tcp": {}, "8000/tcp": {}},
                "Healthcheck": {"Test": ["CMD-SHELL", "wget -q -O /dev/null http://127.0.0.1:8000/health/ || exit 1"],
                                "Interval": 2_000_000_000, "Timeout": 2_000_000_000,
                                "StartPeriod": 5_000_000_000, "Retries": 3},
            },
            "rootfs": {"type": "layers", "diff_ids": [*self.base_diff_ids, self.app_diff_id]},
            "history": [{"created": created, "created_by": f"{BASE_IMAGE} (docker save)"},
                        {"created": created, "created_by": "COPY start.sh /start.sh # fagi-upd-e2e harness"}],
        }
        config_blob = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.harness.upload_blob(config_blob)
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                       "size": len(config_blob), "digest": "sha256:" + hashlib.sha256(config_blob).hexdigest()},
            "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                        "size": len(blob), "digest": "sha256:" + hashlib.sha256(blob).hexdigest()}
                       for blob in [*self.base_layers, self.app_layer]],
        }
        return self.harness.put_manifest(tag, json.dumps(manifest, indent=2).encode("utf-8"),
                                         "application/vnd.docker.distribution.manifest.v2+json")


# ------------------------------------------------------- private registry --

class PrivateRegistry:
    """A registry:2 of one test's own, on a free port, holding copies of some
    of the run's images (copied over the registry API: same manifests, same
    digests). Unlike the run's registry, which every test pulls from, it can
    be stopped: a real outage, seen by that test alone. Its container carries
    the run's prefix and the images pulled from it the harness label, so
    cleanup_leftovers() removes both if remove() never ran."""

    def __init__(self, harness: "Harness", name: str, tags: Iterable[str], repository: str = REPOSITORY) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise ValueError(f"private registry names are lower-case letters, digits and dashes: {name!r}")
        self.harness = harness
        self.container = f"{harness.prefix}registry-{name}"
        self.port = free_port(harness.ports)
        run(["docker", "rm", "-f", "-v", self.container], check=False)
        run(["docker", "run", "-d", "--name", self.container, "--restart", "no",
             "--label", f"{harness.prefix}harness=private-registry",
             "-p", f"{self.port}:5000", REGISTRY_IMAGE])
        self._wait(up=True)
        self.digests = {tag: self._copy(tag, repository) for tag in tags}
        log(f"private registry {self.container} on {self.host} holds {', '.join(sorted(self.digests))}")

    @property
    def host(self) -> str:
        return f"localhost:{self.port}"

    def _wait(self, *, up: bool) -> None:
        wait_for(lambda: http_ok(f"http://{self.host}/v2/") == up, timeout=120, interval=0.5,
                 what=f"the private registry on port {self.port} to {'answer' if up else 'stop answering'}")

    def _copy(self, tag: str, repository: str) -> str:
        """Copy `repository:tag` (its manifest, config and layers) from the run's registry; returns its digest."""

        harness = self.harness
        status, headers, body = harness._registry("GET", f"/v2/{repository}/manifests/{tag}",
                                                  headers={"Accept": MANIFEST_ACCEPT})
        if status != 200:
            raise RuntimeError(f"the run's registry has no {repository}:{tag} (HTTP {status})")
        media_type = headers.get("Content-Type", "")
        manifest = json.loads(body)
        for blob in [manifest["config"], *manifest["layers"]]:
            status, _, data = harness._registry("GET", f"/v2/{repository}/blobs/{blob['digest']}")
            if status != 200:
                raise RuntimeError(f"the run's registry has no blob {blob['digest']} (HTTP {status})")
            harness.upload_blob(data, repository, registry=self.host)
        digest = harness.put_manifest(tag, body, media_type, repository, registry=self.host)
        if digest != harness.registry_digest(tag, repository):
            raise RuntimeError(f"the copy of {repository}:{tag} has digest {digest}, not the run's")
        return digest

    def stop(self) -> None:
        """The outage: the registry container stops, and its port stops answering."""

        run(["docker", "stop", "-t", "2", self.container])
        self._wait(up=False)
        log(f"private registry {self.container} stopped")

    def start(self) -> None:
        run(["docker", "start", self.container])
        self._wait(up=True)

    def remove(self) -> None:
        """The container (and its storage) and every image pulled from it."""

        if self.harness.keep:
            return
        run(["docker", "rm", "-f", "-v", self.container], check=False)
        listing = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], check=False).stdout.split()
        mine = sorted(t for t in listing if t.startswith(f"{self.host}/"))
        if mine:
            run(["docker", "rmi", "-f", *mine], check=False)


# ---------------------------------------------------------------- harness --

class Harness:
    def __init__(self, base: Optional[Path] = None, prefix: Optional[str] = None) -> None:
        self.prefix = prefix or name_prefix()
        self.base = (base or base_dir()).resolve()
        self.run_dir = self.base / f"{self.prefix}run"
        self.keys_dir = self.run_dir / "keys"
        self.releases_dir = self.run_dir / "releases"
        self.templates_dir = self.run_dir / "templates"
        self.checkouts_dir = self.run_dir / "checkouts"
        self.hooks_dir = self.run_dir / "hooks"
        self.logs_dir = self.run_dir / "logs"
        self.registry_dir = self.run_dir / "registry"
        self.state_file = self.base / f"{self.prefix}state.json"
        self.registry_name = f"{self.prefix}registry"
        # Other suites' prefixes that extend this one: what carries them is theirs.
        self.foreign_prefixes = tuple(sorted({p for p in suite_prefixes()
                                              if p != self.prefix and p.startswith(self.prefix)}))
        self.keep = os.environ.get(KEEP_VAR) == "1"
        self.python = os.environ.get(PYTHON_VAR) or ""
        self.use_docker_build = os.environ.get(BUILD_VAR) == "docker"
        self.compose: List[str] = []
        # The Compose version every Compose call of the run uses (`version --short`), and how it was chosen.
        self.compose_version = ""
        self.compose_description = ""
        self.port = 0
        self.ports: set = set()
        self.digests: Dict[str, str] = {}
        self.local_tags: set = set()
        self.assembler: Optional[ImageAssembler] = None
        self._releases: Dict[tuple, Release] = {}
        self._lock = threading.Lock()
        self._started = False
        self._stopped = False

    def automatic_publisher(self, *, lane, versions, retained_layout, candidates,
                            deployment_dirs, platform="linux/amd64", run_id=42, deadline=None,
                            acceptance_reserve_seconds=0):
        """Real acceptance is independent of start(), registry and fake images.

        Immutable candidates and retained tag files are explicit inputs. The
        product factory is deliberately not new_checkout(), which creates the
        synthetic CLI fixtures used by the legacy/manual suites.
        """
        from .automatic_publisher import AutomaticPublisher
        from .real_automatic import ProductHarness, CONTEXT, home_path
        if lane != "product" or self._started:
            raise ValueError("automatic acceptance requires a fresh real-product harness")
        base = home_path(self.base)
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = base / ("fagi-upd-e2e-real-publisher-" + uuid.uuid4().hex)

        def product_factory(publisher, name):
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name):
                raise ValueError("unsafe product checkout name")
            evidence = base / ("fagi-upd-e2e-real-" + name + "-" + uuid.uuid4().hex[:12])
            evidence.mkdir(mode=0o700)
            checkout = ProductHarness(evidence, update_source(), evidence.name, context=CONTEXT,
                                      compose=compose_command())
            checkout.retain_deployment(publisher.deployment_dirs[retained_layout],
                                       publisher._prepared[retained_layout]['manifest'])
            return checkout

        return AutomaticPublisher(directory, lane=lane, versions=versions, retained_layout=retained_layout,
            candidates=candidates, deployment_dirs=deployment_dirs, product_factory=product_factory,
            docker_context=CONTEXT, platform=platform, run_id=run_id, deadline=deadline,
            acceptance_reserve_seconds=acceptance_reserve_seconds,
            cosign=os.environ.get('FUTURE_AGI_TEST_COSIGN') or shutil.which('cosign') or 'cosign')

    # ------------------------------------------------------------ lifecycle --
    @property
    def registry(self) -> str:
        return f"localhost:{self.port}"

    def image_ref(self, tag: str, repository: str = REPOSITORY, *, registry: Optional[str] = None) -> str:
        return f"{registry or self.registry}/{repository}:{tag}"

    @property
    def trusted_pub(self) -> Path:
        return self.keys_dir / "trusted" / "cosign.pub"

    def start(self, releases: Iterable[Sequence[str]] = (),
              extra_images: Optional[Mapping[str, str]] = None) -> "Harness":
        """Registry, images (IMAGES plus `extra_images`, version -> mode),
        keys, and the `releases` given as (version, variant, signer) tuples,
        signed in parallel."""

        if self._started:
            return self
        started = time.monotonic()
        compose = compose_command()
        if compose is None:
            raise RuntimeError("Docker Compose v2 is not available")
        self.compose = compose
        switch = compose_switch()
        if switch is not None:
            self.compose_version, self.compose_description = switch.version, switch.describe()
        else:
            self.compose_version = compose_version(compose)
            self.compose_description = (f"Docker Compose {self.compose_version or '(unknown version)'} "
                                        f"(`{' '.join(compose)}`, the host's default)")
        log(f"using {self.compose_description}")
        if str(self.base).startswith(("/tmp/", "/private/tmp/", "/Volumes/")) and sys.platform == "darwin":
            log(f"warning: {self.base} may not be visible to Docker bind mounts (set {DIR_VAR} under $HOME)")
        self.base.mkdir(parents=True, exist_ok=True)
        self.cleanup_leftovers()
        for directory in (self.keys_dir, self.releases_dir, self.templates_dir, self.checkouts_dir, self.hooks_dir,
                          self.logs_dir, self.registry_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "README").write_text(
            "Created by deploy/tests/update_e2e/harness.py; removed by its stop().\n", encoding="utf-8")
        atexit.register(self.stop)
        self._started = True
        with ThreadPoolExecutor(max_workers=1 if os.environ.get(JOBS_VAR) == "1" else 2) as pool:
            keys = [pool.submit(self._generate_key, who) for who in ("trusted", "attacker")]
            if os.environ.get(JOBS_VAR) == "1":
                # Serial validation includes Docker-based key generation, not
                # only the tests; don't overlap it with registry/image setup.
                for future in keys:
                    future.result()
            self._start_registry()
            self._save_state()
            for version, mode in {**IMAGES, **(extra_images or {})}.items():
                self.push_image(version, mode)
            self.push_image(LATEST_VERSION, IMAGES[LATEST_VERSION], tag="latest")
            for future in keys:
                future.result()
        log("generated trusted and attacker cosign key pairs")
        self.prepare_releases(releases)
        log(f"harness ready in {time.monotonic() - started:.0f}s: registry {self.registry}, run dir {self.run_dir}")
        return self

    def stop(self) -> None:
        if not self._started or self._stopped:
            return
        self._stopped = True
        log(f"this run used {self.compose_description}")
        if self.keep:
            log(f"{KEEP_VAR}=1: keeping registry {self.registry_name}, projects and {self.run_dir}")
            return
        self.cleanup_leftovers()
        log("removed every Docker resource and file the run created")

    def _save_state(self) -> None:
        self.state_file.write_text(json.dumps({"registry_port": self.port, "local_tags": sorted(self.local_tags)}),
                                   encoding="utf-8")

    def owns(self, name: str) -> bool:
        """Whether a Docker resource name carries this run's prefix (and not
        another suite's prefix that extends it)."""

        return name.startswith(self.prefix) and not any(name.startswith(p) for p in self.foreign_prefixes)

    def foreign_host_ports(self) -> Optional[set]:
        """Host ports published by containers this run does not own (another
        suite's registry, say), running or stopped; None when Docker cannot say."""

        ids = run(["docker", "ps", "-a", "-q", "--no-trunc"], check=False)
        if ids.code != 0:
            return None
        if not ids.stdout.split():
            return set()
        inspected = run(["docker", "inspect", *ids.stdout.split()], check=False)
        try:
            records = json.loads(inspected.stdout or "[]")
        except ValueError:
            return None
        if not isinstance(records, list) or (inspected.code != 0 and not records):
            return None
        ports: set = set()
        for record in records:
            labels = (record.get("Config") or {}).get("Labels") or {}
            if self.owns(record.get("Name", "").lstrip("/")) or self.owns(labels.get("com.docker.compose.project", "")):
                continue
            for table in ((record.get("HostConfig") or {}).get("PortBindings"),
                          (record.get("NetworkSettings") or {}).get("Ports")):
                for bindings in (table or {}).values():
                    for binding in bindings or []:
                        port = str((binding or {}).get("HostPort") or "")
                        if port.isdigit():
                            ports.add(int(port))
        return ports

    def _labelled_images(self, value: Optional[str] = None) -> set:
        """repository:tag of the images labelled HARNESS_LABEL (=value, if given)."""

        selector = f"label={HARNESS_LABEL}" + (f"={value}" if value is not None else "")
        listing = run(["docker", "images", "--filter", selector, "--format", "{{.Repository}}:{{.Tag}}"], check=False)
        return {t for t in listing.stdout.split() if "<none>" not in t}

    def cleanup_leftovers(self) -> None:
        """Remove every resource carrying this prefix: this run's or an
        earlier, interrupted run's (idempotent reruns). Images are removed
        when they carry the prefix, are labelled with it (HARNESS_LABEL), or
        come from this run's registry port. Another suite's resources are
        never touched (see owns()): an image labelled with another prefix
        stays, and so does every image of a registry port that a container
        this run does not own publishes now (the port was freed and reused)."""

        tags, ports = set(self.local_tags), {self.port} - {0}
        if self.state_file.exists():
            try:
                state = json.loads(self.state_file.read_text(encoding="utf-8"))
                tags.update(state.get("local_tags") or [])
                if state.get("registry_port"):
                    ports.add(int(state["registry_port"]))
            except (OSError, ValueError):
                pass
        listing = run(["docker", "ps", "-a", "--format", '{{.ID}}\t{{.Names}}\t{{.Label "com.docker.compose.project"}}'],
                      check=False)
        containers = []
        for line in listing.stdout.splitlines():
            parts = (line.split("\t") + ["", "", ""])[:3]
            if self.owns(parts[2]) or self.owns(parts[1]):
                containers.append(parts[0])
        if containers:
            run(["docker", "rm", "-f", "-v", *containers], check=False)
        volumes = [v for v in run(["docker", "volume", "ls", "-q"], check=False).stdout.split() if self.owns(v)]
        if volumes:
            run(["docker", "volume", "rm", "-f", *volumes], check=False)
        networks = [n for n in run(["docker", "network", "ls", "--format", "{{.Name}}"], check=False).stdout.split()
                    if self.owns(n)]
        if networks:
            run(["docker", "network", "rm", *networks], check=False)
        images = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], check=False).stdout.split()
        mine = self._labelled_images(self.prefix)
        theirs = self._labelled_images() - mine
        foreign = self.foreign_host_ports()
        shared = set(ports) if foreign is None else ports & foreign
        if shared:
            log(f"keeping the images of registry port(s) {', '.join(map(str, sorted(shared)))}: "
                + ("Docker could not list the published ports" if foreign is None else "another suite publishes them now"))
        ours = tuple(f"localhost:{port}/" for port in sorted(ports - shared))
        doomed = sorted({t for t in images if t in tags or (self.owns(t) and t.startswith(f"{self.prefix}fakeapp:"))
                         or t in mine or (ours and t.startswith(ours) and t not in theirs)})
        if doomed:
            run(["docker", "rmi", "-f", *doomed], check=False)
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir, ignore_errors=True)
            if self.run_dir.exists():  # files the registry container wrote as another user
                run(["docker", "run", "--rm", "--name", f"{self.prefix}cleanup-{uuid.uuid4().hex[:8]}",
                     "-v", f"{self.base}:/base", BASE_IMAGE, "rm", "-rf", f"/base/{self.run_dir.name}"], check=False)
        try:
            self.state_file.unlink()
        except FileNotFoundError:
            pass
        self.local_tags.clear()

    # ------------------------------------------------------------- registry --
    def _start_registry(self) -> None:
        self.port = free_port(self.ports)
        run(["docker", "run", "-d", "--name", self.registry_name, "--restart", "no",
             "--label", f"{self.prefix}harness=registry",
             "-p", f"{self.port}:5000", "-v", f"{self.registry_dir}:/var/lib/registry", REGISTRY_IMAGE])
        wait_for(lambda: http_ok(f"http://localhost:{self.port}/v2/"), timeout=120, interval=0.5,
                 what=f"the e2e registry on port {self.port}")
        log(f"registry {self.registry_name} on {self.registry}")

    def _registry(self, method: str, url: str, *, data: Optional[bytes] = None,
                  headers: Optional[Mapping[str, str]] = None):
        if url.startswith("/"):
            url = f"http://localhost:{self.port}{url}"
        request = Request(url, data=data, method=method, headers=dict(headers or {}))
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(request, timeout=60) as response:
                return response.status, response.headers, response.read()
        except HTTPError as exc:
            try:
                return exc.code, exc.headers, exc.read()
            finally:
                exc.close()

    def _registry_url(self, path: str, registry: Optional[str]) -> str:
        """`path` on `registry` (host:port), or on the run's registry ("/..." for _registry)."""

        return f"http://{registry}{path}" if registry else path

    def upload_blob(self, data: bytes, repository: str = REPOSITORY, *, registry: Optional[str] = None) -> str:
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        status, _, _ = self._registry("HEAD", self._registry_url(f"/v2/{repository}/blobs/{digest}", registry))
        if status == 200:
            return digest
        status, headers, body = self._registry("POST", self._registry_url(f"/v2/{repository}/blobs/uploads/", registry),
                                               data=b"")
        if status != 202:
            raise RuntimeError(f"registry refused a blob upload (HTTP {status}): {body[:200]!r}")
        location = headers.get("Location", "")
        if registry and location.startswith("/"):
            location = f"http://{registry}{location}"
        location += ("&" if "?" in location else "?") + f"digest={digest}"
        status, _, body = self._registry("PUT", location, data=data,
                                         headers={"Content-Type": "application/octet-stream"})
        if status != 201:
            raise RuntimeError(f"registry refused blob {digest} (HTTP {status}): {body[:200]!r}")
        return digest

    def put_manifest(self, tag: str, body: bytes, media_type: str, repository: str = REPOSITORY, *,
                     registry: Optional[str] = None) -> str:
        status, headers, reply = self._registry("PUT", self._registry_url(f"/v2/{repository}/manifests/{tag}", registry),
                                                data=body, headers={"Content-Type": media_type})
        if status != 201:
            raise RuntimeError(f"registry refused manifest {repository}:{tag} (HTTP {status}): {reply[:300]!r}")
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if headers.get("Docker-Content-Digest") not in (None, digest):
            raise RuntimeError(f"registry stored {repository}:{tag} as {headers.get('Docker-Content-Digest')}, "
                               f"not {digest}")
        return digest

    def registry_digest(self, tag: str, repository: str = REPOSITORY, *, registry: Optional[str] = None) -> str:
        status, headers, _ = self._registry("HEAD", self._registry_url(f"/v2/{repository}/manifests/{tag}", registry),
                                            headers={"Accept": MANIFEST_ACCEPT})
        digest = headers.get("Docker-Content-Digest", "") if status == 200 else ""
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest or ""):
            raise RuntimeError(f"registry has no {repository}:{tag} (HTTP {status})")
        return digest

    def push_image(self, version: str, mode: str, *, flavor: str = "release", tag: Optional[str] = None) -> str:
        """Publish the fake app for `version` as `tag` (default: version); returns its registry digest."""

        tag = tag or version
        if self.use_docker_build:
            digest = self._docker_build_and_push(version, mode, flavor, tag)
        else:
            if self.assembler is None:
                self.assembler = ImageAssembler(self)
            digest = self.assembler.push(tag, version, mode, flavor)
        if digest != self.registry_digest(tag):
            raise RuntimeError(f"registry does not serve {tag} as {digest}")
        self.digests[tag] = digest
        log(f"published {self.image_ref(tag)} ({version} {mode} {flavor}) {digest[:19]}…")
        return digest

    def _docker_build_and_push(self, version: str, mode: str, flavor: str, tag: str) -> str:
        local = f"{self.prefix}fakeapp:{version}-{mode}-{flavor}"
        target = self.image_ref(tag)
        self.local_tags.update((local, target))
        self._save_state()
        run(["docker", "build", "-q", "-t", local, "--build-arg", f"VERSION={version}", "--build-arg", f"MODE={mode}",
             "--build-arg", f"FLAVOR={flavor}", "--label", f"{HARNESS_LABEL}={self.prefix}", str(FAKEAPP_CONTEXT)],
            env={**os.environ, "DOCKER_BUILDKIT": "0"}, timeout=900)
        run(["docker", "tag", local, target])
        run(["docker", "push", "-q", target], timeout=900)
        return self.registry_digest(tag)

    # ---------------------------------------------------------------- trust --
    def _cosign(self, args: Sequence[str], mounts: Sequence[str], workdir: Optional[str] = None) -> Result:
        uid = f"{os.getuid()}:{os.getgid()}"
        command = ["docker", "run", "--rm", "--network", "none", "-e", f"COSIGN_PASSWORD={COSIGN_PASSWORD}",
                   "-e", "HOME=/tmp", "--tmpfs", "/tmp", "--user", uid]
        for mount in mounts:
            command += ["-v", mount]
        if workdir:
            command += ["-w", workdir]
        return run([*command, COSIGN_IMAGE, *args], timeout=600)

    def _generate_key(self, who: str) -> None:
        directory = self.keys_dir / who
        directory.mkdir(parents=True, exist_ok=True)
        self._cosign(["generate-key-pair"], [f"{directory}:/keys"], workdir="/keys")
        for name in ("cosign.key", "cosign.pub"):
            if not (directory / name).is_file():
                raise RuntimeError(f"cosign generate-key-pair wrote no {directory / name}")

    def sign(self, manifest_dir: Path, signer: str = "trusted") -> None:
        self._cosign(["sign-blob", "--yes", "--key", "/keys/cosign.key", "--use-signing-config=false",
                      "--tlog-upload=false", "--bundle", f"/w/{BUNDLE_NAME}", f"/w/{MANIFEST_NAME}"],
                     [f"{self.keys_dir / signer}:/keys:ro", f"{manifest_dir}:/w"])

    def template(self, variant: str, *, registry: Optional[str] = None) -> Path:
        """A checkout skeleton holding only what the manifest generator reads
        (its images on `registry`, default the run's)."""

        with self._lock:
            root = self.templates_dir / (variant if registry is None else f"{variant}-{registry.replace(':', '-')}")
            if not (root / "deploy/update/upgrade-floor").exists():
                write_deployment_files(root, registry=registry or self.registry, prefix=self.prefix, variant=variant)
                shutil.copyfile(WORKTREE / "deploy/update/upgrade-floor", root / "deploy/update/upgrade-floor")
            return root

    def prepare_releases(self, specs: Iterable[Sequence[str]]) -> None:
        """Generate and sign several releases at once (cosign runs in parallel)."""

        specs = [tuple(spec) for spec in specs]
        if not specs:
            return
        with ThreadPoolExecutor(max_workers=1 if os.environ.get(JOBS_VAR) == "1" else 2) as pool:
            futures = [pool.submit(self.release, version, variant=variant, signer=signer)
                       for version, variant, signer in specs]
            for future in futures:
                future.result()

    def release(self, version: str, *, variant: str = "default", signer: str = "trusted",
                name: Optional[str] = None, registry: Optional[str] = None) -> Release:
        """A manifest for `version` (digest: what the registry holds now) written
        by the real generator for `variant`'s deployment files, signed by `signer`.
        With `registry` (a PrivateRegistry's host), its images are on that registry."""

        key = (version, variant, signer, name, registry)
        with self._lock:
            if key in self._releases:
                return self._releases[key]
        digest = self.registry_digest(version, registry=registry)
        folder = name or f"{variant}-{signer}"
        out = self.releases_dir / (folder if registry is None else f"{folder}-{registry.replace(':', '-')}") / version
        out.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, str(WORKTREE / "scripts/generate_update_manifest.py"),
                "--version", version, "--source-sha", sha256_hex(version)[:40], "--published-at", PUBLISHED_AT,
                "--helm-chart", HELM_CHART, "--helm-digest", "sha256:" + sha256_hex("helm" + version),
                "--repo-root", str(self.template(variant, registry=registry)), "--output", str(out / MANIFEST_NAME),
                "--component", f"standalone={self.image_ref(version, registry=registry)}@{digest}"]
        for component, (repository, suffix) in OTHER_COMPONENTS.items():
            args += ["--component", f"{component}={self.image_ref(version + suffix, repository, registry=registry)}"
                                    f"@sha256:{sha256_hex(component + version)}"]
        run(args)
        self.sign(out, signer)
        release = Release(version, variant, signer, out, digest)
        with self._lock:
            self._releases[key] = release
        log(f"signed {name or variant}/{version} manifest with the {signer} key"
            + (f" (images on {registry})" if registry else ""))
        return release

    def private_registry(self, name: str, tags: Iterable[str]) -> "PrivateRegistry":
        """A registry of one test's own holding copies of the run's `tags`
        (see PrivateRegistry); the caller removes it (remove())."""

        return PrivateRegistry(self, name, tags)

    def track_tag(self, reference: str) -> None:
        """Have cleanup_leftovers() remove this local image tag (one a test made with `docker tag`)."""

        with self._lock:
            self.local_tags.add(reference)
            self._save_state()


    # ------------------------------------------------------------ checkouts --
    def hook_dir(self, name: str) -> Path:
        directory = self.hooks_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write_script(self, path: Path, body: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body.lstrip("\n"), encoding="utf-8")
        path.chmod(0o755)
        return path

    def slow_pull(self, name: str) -> SlowPull:
        return SlowPull(self.hook_dir(name) / "slow-pull")

    def failing_pull(self, name: str) -> FailPull:
        return FailPull(self.hook_dir(name) / "failing-pull")

    def new_checkout(self, name: str, *, version: Optional[str] = "v1.51.0", variant: str = "default",
                     settings: Optional[Mapping[str, str]] = None, env_extra: Iterable[str] = (),
                     registry: Optional[str] = None, root: Optional[Path] = None) -> "Checkout":
        """A checkout named `name` whose images come from `registry` (a
        PrivateRegistry's host; default the run's registry), in `root` (a
        directory inside this run's directory; default checkouts/<project>):
        a path with spaces or non-ASCII characters, say. The Compose project
        is `name` with the prefix either way."""

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise ValueError(f"checkout names are lower-case letters, digits and dashes: {name!r}")
        project = f"{self.prefix}{name}"
        if root is not None and self.run_dir.resolve() not in Path(root).resolve().parents:
            raise ValueError(f"a checkout root must be inside {self.run_dir} (cleanup removes it): {root}")
        root = Path(root) if root is not None else self.checkouts_dir / project
        if root.exists():
            Checkout(self, project, root, 0, 0, "").down()
            shutil.rmtree(root)
        (root / "bin").mkdir(parents=True)
        source = update_source()
        shutil.copy2(source / "bin/update", root / "bin/update")
        shutil.copytree(source / "deploy/update", root / "deploy/update",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        write_deployment_files(root, registry=registry or self.registry, prefix=self.prefix, variant=variant)
        backend, frontend = free_port(self.ports), free_port(self.ports)
        secret = f"e2e-secret-{secrets.token_hex(12)}"
        env_path = root / ".env"
        env_path.write_text(env_lines(project, version=version, backend_port=backend, frontend_port=frontend,
                                      secret=secret, extra=env_extra), encoding="utf-8")
        env_path.chmod(0o600)
        checkout = Checkout(self, project, root, backend, frontend, secret)
        checkout.write_update_conf(settings or {})
        return checkout


class Checkout:
    """One fake Future AGI checkout and its Compose project."""

    def __init__(self, harness: Harness, project: str, root: Path, backend_port: int, frontend_port: int,
                 secret: str) -> None:
        self.harness = harness
        self.project = project
        self.root = root
        self.backend_port = backend_port
        self.frontend_port = frontend_port
        self.secret = secret
        self.log_path = harness.logs_dir / f"{project}.log"

    # ------------------------------------------------------------- files --
    @property
    def env_path(self) -> Path:
        return self.root / ".env"

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    def env_text(self) -> str:
        return self.env_path.read_text(encoding="utf-8")

    def env_bytes(self) -> bytes:
        return self.env_path.read_bytes()

    def env_value(self, key: str) -> Optional[str]:
        value = None
        for line in self.env_text().splitlines():
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1]
        return value

    def write_update_conf(self, settings: Mapping[str, str]) -> None:
        lines = ["# update.conf (bin/update e2e fixture): host-only updater settings",
                 f"FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY={self.harness.trusted_pub}",
                 "FUTURE_AGI_UPDATE_COSIGN_OFFLINE=true"]
        lines += [f"{key}={value}" for key, value in settings.items()]
        (self.root / "update.conf").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def journal(self) -> Optional[Dict[str, Any]]:
        path = self.state_dir / "journal.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def receipt_paths(self) -> List[Path]:
        if not self.state_dir.is_dir():
            return []
        paths = [p for p in self.state_dir.iterdir() if re.fullmatch(r"receipt-\d{8}T\d{6}Z-\d+\.json", p.name)]
        return sorted(paths, key=lambda p: int(p.stem.rsplit("-", 1)[1]))

    def receipts(self) -> List[Dict[str, Any]]:
        records = []
        for path in self.receipt_paths():
            record = json.loads(path.read_text(encoding="utf-8"))
            record["_path"] = str(path)
            records.append(record)
        return records

    def temp_env_files(self) -> List[str]:
        return sorted(p.name for p in self.root.iterdir() if p.name.startswith(".env.futureagi-update-"))

    # ---------------------------------------------------------- commands --
    def _log(self, text: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip("\n") + "\n\n")

    def update_env(self, env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        environ = clean_environ(env)
        if self.harness.python:
            environ["PYTHON"] = self.harness.python
        return environ

    def bin_update(self, *args: str, cwd: Optional[Path] = None, env: Optional[Mapping[str, str]] = None,
                   timeout: float = 600) -> Result:
        """Run the checkout's bin/update the way an operator does."""

        result = run([str(self.root / "bin" / "update"), *args], cwd=cwd or self.root, env=self.update_env(env),
                     check=False, timeout=timeout)
        self._log(f"[cwd {cwd or self.root}]\n{result.describe()}")
        return result

    def spawn(self, *args: str, cwd: Optional[Path] = None, env: Optional[Mapping[str, str]] = None,
              nohup: bool = False) -> Spawned:
        tag = uuid.uuid4().hex[:8]
        out = self.harness.logs_dir / f"{self.project}-{tag}.out"
        err = self.harness.logs_dir / f"{self.project}-{tag}.err"
        command = [str(self.root / "bin" / "update"), *args]
        if nohup:
            command.insert(0, "nohup")
        with out.open("w") as stdout, err.open("w") as stderr:
            proc = subprocess.Popen(command, cwd=str(cwd or self.root), env=self.update_env(env),
                                    stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True)
        self._log(f"[spawned pid {proc.pid}] $ {' '.join(command)}")
        return Spawned(command, proc, out, err)

    def compose(self, *args: str, check: bool = True, timeout: float = 600) -> Result:
        return run([*self.harness.compose, *args], cwd=self.root, env=clean_environ(), check=check, timeout=timeout)

    def install(self, timeout: float = 240) -> None:
        """What ./bin/install's last step does: start the stack and wait until it serves."""

        name = json.loads(self.compose("config", "--format", "json").stdout).get("name")
        if name != self.project:
            raise RuntimeError(f"compose resolves project {name!r}, not {self.project!r}: refusing to start it")
        self.compose("up", "-d", "--wait", "--wait-timeout", str(int(timeout)), timeout=timeout + 60)
        self.wait_healthy(timeout=90)

    def wait_healthy(self, timeout: float = 90) -> None:
        wait_for(lambda: http_ok(self.url("/health/")) and http_ok(self.url("/", ui=True)), timeout=timeout,
                 interval=0.5, what=f"{self.project} to answer on ports {self.backend_port}/{self.frontend_port}")

    def url(self, path: str, *, ui: bool = False, port: Optional[int] = None) -> str:
        return f"http://localhost:{port or (self.frontend_port if ui else self.backend_port)}{path}"

    def down(self) -> None:
        if self.harness.keep:
            return
        if (self.root / "docker-compose.yml").exists():
            self.compose("down", "-v", "--remove-orphans", "--timeout", "5", check=False, timeout=300)
        ids = run(["docker", "ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={self.project}"],
                  check=False).stdout.split()
        if ids:
            run(["docker", "rm", "-f", "-v", *ids], check=False)
        volumes = run(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={self.project}"],
                      check=False).stdout.split()
        if volumes:
            run(["docker", "volume", "rm", "-f", *volumes], check=False)

    # -------------------------------------------------------- inspection --
    def containers(self) -> List[Container]:
        records: List[Dict[str, Any]] = []
        for _ in range(5):
            ids = run(["docker", "ps", "-a", "-q", "--no-trunc", "--filter",
                       f"label=com.docker.compose.project={self.project}"]).stdout.split()
            if not ids:
                return []
            # A container Compose removes between `ps` and `inspect` makes
            # inspect fail for that id only; the others are still printed.
            inspected = run(["docker", "inspect", *ids], check=False)
            try:
                records = json.loads(inspected.stdout or "[]")
            except ValueError:
                records = []
            if inspected.code == 0 or records:
                break
            time.sleep(0.5)
        containers = []
        for record in records:
            config = record.get("Config") or {}
            state = record.get("State") or {}
            labels = config.get("Labels") or {}
            env = {}
            for item in config.get("Env") or []:
                key, _, value = item.partition("=")
                env[key] = value
            containers.append(Container(
                id=record.get("Id", ""), name=record.get("Name", "").lstrip("/"),
                service=labels.get("com.docker.compose.service", ""),
                image=config.get("Image", ""), image_id=record.get("Image", ""), status=state.get("Status", ""),
                restart_count=int(record.get("RestartCount") or 0), started_at=state.get("StartedAt", ""),
                env=env, ports=(record.get("NetworkSettings") or {}).get("Ports") or {},
                mounts=record.get("Mounts") or [], health=((state.get("Health") or {}).get("Status")) or "none",
                oneoff=labels.get("com.docker.compose.oneoff") == "True"))
        return containers

    def service(self, name: str) -> Container:
        matches = [c for c in self.containers() if c.service == name and not c.oneoff]
        if len(matches) != 1:
            raise AssertionError(f"expected one {name} container in {self.project}, found {len(matches)}")
        return matches[0]

    def app(self) -> Container:
        return self.service("app")

    def image_info(self, reference: str) -> Dict[str, Any]:
        record = json.loads(run(["docker", "image", "inspect", reference]).stdout)[0]
        labels = (record.get("Config") or {}).get("Labels") or {}
        return {"id": record.get("Id", ""), "repo_digests": record.get("RepoDigests") or [],
                "version": labels.get("org.opencontainers.image.version", ""),
                "flavor": labels.get("org.opencontainers.image.revision", "")}

    def running_version(self) -> str:
        return self.image_info(self.app().image_id)["version"]

    def volume_text(self, path: str, volume: str = "app-data") -> str:
        result = run(["docker", "run", "--rm", "--name", f"{self.harness.prefix}reader-{uuid.uuid4().hex[:8]}",
                      "-v", f"{self.project}_{volume}:/volume:ro", BASE_IMAGE, "cat", f"/volume/{path}"], check=False)
        return result.stdout if result.code == 0 else ""

    def history(self) -> List[str]:
        """`start <version> <mode> <flavor>` per app start, from the data volume."""

        return [" ".join(line.split()[:4]) for line in self.volume_text("history").splitlines() if line.strip()]

    def exec_app(self, *command: str) -> Result:
        return self.compose("exec", "-T", "app", *command)

    def snapshot(self) -> Dict[str, Any]:
        """Everything a refused or failed-before-restart run must leave alone."""

        return {
            "env": self.env_bytes(),
            "env_mode": self.env_path.stat().st_mode & 0o7777,
            "containers": sorted((c.service, c.id, c.image_id, c.status, c.started_at) for c in self.containers()),
            "receipts": [p.name for p in self.receipt_paths()],
            "journal": self.journal(),
        }


def assert_stage_acknowledgment_required(case, co: Checkout, *args: str,
                                        env=None, backup_paths=(), invoke=None) -> None:
    """Prove refusal is read-only BEFORE an explicit operator-approved finish.

    This helper never supplies acknowledgment. The individual test must choose
    the intentional finish command, after checking its stage/initialization.
    """
    def fingerprints():
        paths = [co.env_path, *backup_paths]
        paths += [p for p in co.state_dir.rglob('*') if p.is_file() and p.name != 'update.lock'
                  and not any(part.startswith('verify-') for part in p.relative_to(co.state_dir).parts)]
        return {str(p): ((p.stat().st_ino, p.stat().st_mode, p.stat().st_mtime_ns,
                         hashlib.sha256(p.read_bytes()).hexdigest()) if p.exists() else None) for p in paths}

    case.assertNotIn('--acknowledge-stale-stage', args)
    before, files = co.snapshot(), fingerprints()
    # The shell environment is part of the stage binding. Do not insert a new
    # PATH/fault wrapper just for this refusal: that tests changed identity.
    result = invoke() if invoke is not None else co.bin_update(*args, env=env, timeout=600)
    case.assertEqual(result.code, 3, result.describe())
    case.assertEqual(result.stderr, 'bin/update: stale_stage\n', result.describe())
    case.assertEqual(co.snapshot(), before, result.describe())
    case.assertEqual(fingerprints(), files, 'refusal rewrote state/env or ran the backup')


# --------------------------------------------------------------- parallel --

class _Recorder(unittest.TestResult):
    """Collects one test's outcome in a worker thread, for replay."""

    def __init__(self) -> None:
        super().__init__()
        self.events: List[tuple] = []

    def addSuccess(self, test):  # noqa: N802 - unittest API
        self.events.append(("addSuccess", test))

    def addFailure(self, test, err):  # noqa: N802
        self.events.append(("addFailure", test, err))

    def addError(self, test, err):  # noqa: N802
        self.events.append(("addError", test, err))

    def addSkip(self, test, reason):  # noqa: N802
        self.events.append(("addSkip", test, reason))

    def addExpectedFailure(self, test, err):  # noqa: N802
        self.events.append(("addExpectedFailure", test, err))

    def addUnexpectedSuccess(self, test):  # noqa: N802
        self.events.append(("addUnexpectedSuccess", test))

    def addSubTest(self, test, subtest, err):  # noqa: N802
        self.events.append(("addSubTest", test, subtest, err))

    def addDuration(self, test, elapsed):  # noqa: N802 - Python 3.12+
        self.events.append(("addDuration", test, elapsed))


def slow(test_method: Callable) -> Callable:
    """Mark a long test: ParallelSuite starts it first (results stay in order)."""

    test_method._update_e2e_slow = True
    return test_method


def _is_slow(test: unittest.TestCase) -> bool:
    return bool(getattr(getattr(test, getattr(test, "_testMethodName", ""), None), "_update_e2e_slow", False))


def _flatten(suite: unittest.TestSuite) -> List[unittest.TestCase]:
    tests: List[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests.extend(_flatten(item))
        else:
            tests.append(item)
    return tests


class ParallelSuite(unittest.TestSuite):
    """Runs one module's tests `jobs` at a time between its setUpModule and
    tearDownModule, and reports them in order to the normal runner."""

    def __init__(self, tests: Iterable[unittest.TestCase], jobs: int) -> None:
        super().__init__(tests)
        self.jobs = jobs

    def run(self, result, debug=False):
        tests = _flatten(self)
        if debug or self.jobs <= 1 or len(tests) <= 1:
            return super().run(result, debug)
        # Finish whatever module ran before (as TestSuite.run would), then
        # own this module's fixtures.
        self._tearDownPreviousClass(None, result)
        self._handleModuleTearDown(result)
        result._previousTestClass = None
        result._moduleSetUpFailed = False
        module = sys.modules[tests[0].__class__.__module__]
        try:
            setup = getattr(module, "setUpModule", None)
            if setup is not None:
                setup()
        except unittest.SkipTest as exc:
            for test in tests:
                result.startTest(test)
                result.addSkip(test, str(exc))
                result.stopTest(test)
            return result
        except Exception:
            result.addError(unittest.suite._ErrorHolder(f"setUpModule ({module.__name__})"), sys.exc_info())
            self._tear_down(module, result)
            return result
        pool = ThreadPoolExecutor(max_workers=self.jobs, thread_name_prefix="update-e2e")
        try:
            # Long tests start first so they do not form the tail of the run.
            submitted = {id(test): pool.submit(self._run_one, test)
                         for test in sorted(tests, key=lambda case: not _is_slow(case))}
            futures = [(test, submitted[id(test)]) for test in tests]
            for test, future in futures:
                recorder = future.result()
                result.startTest(test)
                for name, *args in recorder.events:
                    method = getattr(result, name, None)
                    if method is not None:
                        method(*args)
                result.stopTest(test)
                if result.shouldStop:
                    break
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            self._tear_down(module, result)
        return result

    @staticmethod
    def _run_one(test: unittest.TestCase) -> _Recorder:
        recorder = _Recorder()
        test(recorder)
        return recorder

    @staticmethod
    def _tear_down(module, result) -> None:
        teardown = getattr(module, "tearDownModule", None)
        if teardown is None:
            return
        try:
            teardown()
        except Exception:
            result.addError(unittest.suite._ErrorHolder(f"tearDownModule ({module.__name__})"), sys.exc_info())


def parallel_suite(tests: unittest.TestSuite, jobs: Optional[int] = None) -> unittest.TestSuite:
    """For a module's load_tests(): run its tests FUTURE_AGI_UPDATE_E2E_JOBS
    at a time when the suite is enabled; otherwise the normal suite, whose
    setUpModule skips it."""

    if jobs is None:
        raw = os.environ.get(JOBS_VAR) or str(DEFAULT_JOBS)
        jobs = int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_JOBS
    if jobs <= 1 or os.environ.get(ENABLE_VAR) != "1":
        return tests
    return ParallelSuite(_flatten(tests), jobs)
