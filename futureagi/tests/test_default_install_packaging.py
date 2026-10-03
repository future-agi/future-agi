"""Packaging contracts of the standalone install (docker-compose.yml plus the
futureagi/standalone image in deploy/standalone) and of the renamed distributed
topology.

Files are parsed, never run: no Docker, no services. The code-executor's
fallback runner is the exception: it runs real (tiny) subprocesses."""

from __future__ import annotations

import configparser
import ctypes
import importlib.util
import json
import os
import re
import socket
import sys
import time
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
STANDALONE = ROOT / "deploy" / "standalone"
STANDALONE_COMPOSE = ROOT / "docker-compose.yml"
DISTRIBUTED_COMPOSE = ROOT / "docker-compose.distributed.yml"
CODE_EXECUTOR = ROOT / "futureagi" / "code-executor" / "server.py"
SECRETS_DIR = "/etc/futureagi/secrets"


def _compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _supervisor() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    parser.read(STANDALONE / "supervisord.conf", encoding="utf-8")
    return parser


def _programs() -> dict[str, dict[str, str]]:
    parser = _supervisor()
    return {
        section.split(":", 1)[1]: dict(parser.items(section))
        for section in parser.sections()
        if section.startswith("program:")
    }


def test_standalone_compose_is_three_containers_plus_two_profiles() -> None:
    services = _compose(STANDALONE_COMPOSE)["services"]
    assert set(services) == {
        "app",
        "postgres",
        "clickhouse",
        "serving",
        "code-executor",
    }
    assert services["serving"]["profiles"] == ["ml"]
    assert services["code-executor"]["profiles"] == ["sandbox"]
    assert services["code-executor"]["privileged"] is True
    for name in ("app", "postgres", "clickhouse"):
        assert "profiles" not in services[name]
    # Datastores stay on the compose network.
    assert "ports" not in services["postgres"]
    assert "ports" not in services["clickhouse"]
    assert "rabbitmq" not in STANDALONE_COMPOSE.read_text(encoding="utf-8").lower()


def test_no_privileged_service_is_published_beyond_the_host() -> None:
    """code-executor runs whatever code it is sent, without authentication."""
    for compose in (STANDALONE_COMPOSE, DISTRIBUTED_COMPOSE):
        for name, service in _compose(compose)["services"].items():
            if not service.get("privileged"):
                continue
            for mapping in service.get("ports", []):
                assert mapping.startswith("127.0.0.1:"), (
                    f"{compose.name}: {name} publishes {mapping}"
                )


CH_PASSWORD = "${CH_PASSWORD:-}"
CLICKHOUSE_USERS = (
    ROOT / "deploy" / "clickhouse" / "users.d" / "zz-default-password.xml"
)


def test_clickhouse_and_every_client_of_its_default_user_share_ch_password() -> None:
    """ClickHouse reads CH_PASSWORD at every start; a client without it gets
    AUTHENTICATION_FAILED on an install whose .env sets one."""
    assert '<password from_env="CLICKHOUSE_PASSWORD"/>' in CLICKHOUSE_USERS.read_text(
        encoding="utf-8"
    )
    mount = (
        "./deploy/clickhouse/users.d/zz-default-password.xml:"
        "/etc/clickhouse-server/users.d/zz-default-password.xml:ro"
    )
    password_keys = ("CH_PASSWORD", "FI_CH_PASSWORD", "DST_CH_PASSWORD")
    for compose in (STANDALONE_COMPOSE, DISTRIBUTED_COMPOSE):
        services = _compose(compose)["services"]
        clickhouse = services.pop("clickhouse")
        # Also the entrypoint's login when it creates CLICKHOUSE_DB.
        assert clickhouse["environment"]["CLICKHOUSE_PASSWORD"] == CH_PASSWORD
        assert mount in clickhouse["volumes"]
        for name, service in services.items():
            env = service.get("environment") or {}
            # The observed-attribute index's own users have their own passwords.
            clients = [
                key
                for key, value in env.items()
                if re.search(r"(^|//)clickhouse(:|$)", str(value))
                and not key.startswith(("FI_OBSERVED_CATALOG_", "PROPERTY_CATALOG_"))
            ]
            if not clients:
                continue
            given = {
                key: env[key]
                for key in (*password_keys, "CLICKHOUSE_PASSWORD")
                if key in env
            }
            assert given, (
                f"{compose.name}: {name} reaches ClickHouse without a password"
            )
            assert set(given.values()) == {CH_PASSWORD}, f"{compose.name}: {name}"
            # The collector sends credentials only along with a user name.
            if name in ("app", "fi-collector"):
                assert env["FI_CH_USERNAME"] == "${CH_USERNAME:-default}", name
                assert env["FI_CH_PASSWORD"] == CH_PASSWORD, name


def test_standalone_app_service_contract() -> None:
    app = _compose(STANDALONE_COMPOSE)["services"]["app"]
    env = app["environment"]
    # A published image only: a failed pull must never turn into a source build.
    assert app["image"] == "futureagi/standalone:${FUTURE_AGI_VERSION:-latest}"
    assert "build" not in app
    assert env["NO_STARTUP_DB_MUTATIONS"] == "true"
    assert env["FI_SKIP_CH25_MIGRATION"] == "1"
    assert env["FI_CDC_MODE"] == "${FI_CDC_MODE:-outbox}"
    assert env["CHANNEL_LAYER_BACKEND"] == "redis"
    assert env["CHANNEL_REDIS_URL"] == (
        "redis://:${REDIS_PASSWORD:-local-dev-only-redis-password}@127.0.0.1:6379/3"
    )
    assert env["EXACT_AGGREGATION_TASK_QUEUE"] == (
        "${EXACT_AGGREGATION_TASK_QUEUE:-exact_aggregation}"
    )
    # No Kafka: the collector writes the observed-attribute index the
    # bootstrap provisions, as its writer, from a spool on the data volume.
    assert env["FI_OBSERVED_CATALOG_MODE"] == "direct"
    assert env["FI_OBSERVED_CATALOG_CH_URL"] == "http://clickhouse:8123"
    assert env["FI_OBSERVED_CATALOG_CH_DATABASE"] == env["PROPERTY_CATALOG_DATABASE"]
    assert env["FI_OBSERVED_CATALOG_CH_USERNAME"] == "observed_catalog_writer"
    assert (
        env["FI_OBSERVED_CATALOG_CH_PASSWORD"]
        == env["PROPERTY_CATALOG_CONSUMER_PASSWORD"]
    )
    assert env["FI_OBSERVED_CATALOG_SPOOL_DIR"].startswith("/data/collector/")
    assert env["FI_PROPERTY_CATALOG_MODE"] == "disabled"
    assert env["ENABLE_GRPC"] == "false"
    assert env["DEBUG"] == "${DEBUG:-false}"
    assert env["TEMPORAL_HOST"] == "127.0.0.1:7233"
    # The worker flag belongs to the API process only, not `compose exec`.
    assert "FI_EMBEDDED_TEMPORAL_WORKER" not in env
    assert "code-executor:127.0.0.1" in app["extra_hosts"]
    assert app["stop_grace_period"] == "60s"
    # The image's HEALTHCHECK command, with its proxy-free opener.
    assert "test" not in app["healthcheck"]
    assert "app-data:/data" in app["volumes"]
    assert set(app["depends_on"]) == {"postgres", "clickhouse"}
    # The setup screen probes the in-container collector, on its container
    # port (.env's FI_COLLECTOR_OTLP_PORT is the host port).
    assert env["FI_COLLECTOR_HOST"] == "127.0.0.1"
    assert env["FI_COLLECTOR_OTLP_PORT"] == "4317"
    # The code reads MODEL_SERVING_URL; SERVING_URL was never read.
    assert env["MODEL_SERVING_URL"] == "${MODEL_SERVING_URL:-http://serving:8080}"
    assert "SERVING_URL" not in env
    # A .env written for the distributed stack sizes its big workers with these.
    for knob in ("ACTIVITIES", "WORKFLOW_TASKS"):
        value = env[f"TEMPORAL_MAX_CONCURRENT_{knob}"]
        assert value == f"${{FI_APP_TEMPORAL_MAX_CONCURRENT_{knob}:-8}}"
    assert env["FUTURE_AGI_TELEMETRY_BUFFER_DIR"].endswith(":-/data/telemetry}")
    assert app["pids_limit"] > 0
    # The boot summary's URLs and the SPA's API URL (bin/start) follow the
    # host ports also when they are set in the shell rather than in .env.
    assert env["FRONTEND_PORT"] == "${FRONTEND_PORT:-3000}"
    assert env["BACKEND_PORT"] == "${BACKEND_PORT:-8000}"
    # ClickHouse runs two threads a query here, so list pages get 15 s
    # instead of 5 s before they come back partial.
    for page in ("SPAN", "TRACE", "SESSION"):
        wall = f"{page}_LIST_PAGE_WALL_MS"
        assert env[wall] == f"${{{wall}:-15000}}"


def test_every_redis_client_of_the_app_uses_the_password() -> None:
    env = _compose(STANDALONE_COMPOSE)["services"]["app"]["environment"]
    password = "${REDIS_PASSWORD:-local-dev-only-redis-password}"
    assert env["REDIS_PASSWORD"] == password
    urls = {key: value for key, value in env.items() if key.endswith("REDIS_URL")}
    urls.update(
        {
            key: env[key]
            for key in ("REDIS_CACHE_URL", "REDIS_LOCK_URL", "REDIS_STATE_URL")
        }
    )
    assert set(urls) >= {"REDIS_URL", "CHANNEL_REDIS_URL", "REDIS_CACHE_URL"}
    for key, url in urls.items():
        assert url.startswith(f"redis://:{password}@127.0.0.1:6379/"), key
    assert env["FI_AUTH_REDIS_ADDR"] == "127.0.0.1:6379"
    assert env["FI_AUTH_REDIS_PASSWORD"] == password
    # The generated default must be usable inside a URL as it is.
    assert re.fullmatch(r"[A-Za-z0-9._~-]+", "local-dev-only-redis-password")


def test_secrets_are_mounted_where_eval_code_cannot_read_them() -> None:
    app = _compose(STANDALONE_COMPOSE)["services"]["app"]
    targets = {
        re.sub(r":(ro|rw)$", "", volume).rsplit(":", 1)[1] for volume in app["volumes"]
    }
    assert f"{SECRETS_DIR}/agentcc.yaml" in targets
    assert f"{SECRETS_DIR}/vertex.json" in targets
    for target in targets:
        assert target == "/data" or target.startswith(f"{SECRETS_DIR}/"), target
    gateway = _programs()["gateway"]
    assert f"--config {SECRETS_DIR}/agentcc.yaml" in gateway["command"]
    assert (
        f'GOOGLE_APPLICATION_CREDENTIALS="{SECRETS_DIR}/vertex.json"'
        in (gateway["environment"])
    )
    dockerfile = (STANDALONE / "Dockerfile").read_text(encoding="utf-8")
    assert f"install -d -m 0700 -o root -g root {SECRETS_DIR}" in dockerfile
    start = (STANDALONE / "bin" / "start").read_text(encoding="utf-8")
    assert "chmod 700 /data " in start


def _image_healthcheck() -> str:
    dockerfile = (STANDALONE / "Dockerfile").read_text(encoding="utf-8")
    return dockerfile.split("\nHEALTHCHECK ", 1)[1].split("\nENTRYPOINT ", 1)[0]


def test_standalone_app_healthcheck_covers_the_in_container_services() -> None:
    test = _image_healthcheck()
    # HTTP_PROXY from .env must never see the loopback probes.
    assert "u.ProxyHandler({})" in test
    healthcheck = _compose(STANDALONE_COMPOSE)["services"]["app"]["healthcheck"]
    assert "test" not in healthcheck
    for option, value in (
        ("interval", "15s"),
        ("timeout", "10s"),
        ("retries", 5),
        ("start_period", "3600s"),
    ):
        assert healthcheck[option] == value
        assert f"--{option.replace('_', '-')}={value}" in test
    for url in (
        "http://127.0.0.1:8000/health/",
        "http://127.0.0.1:9464/healthz",
        "http://127.0.0.1:8080/healthz",
        "http://127.0.0.1:3000/",
        "http://127.0.0.1:9005/minio/health/live",
    ):
        assert url in test
    env = _compose(STANDALONE_COMPOSE)["services"]["app"]["environment"]
    assert env["FI_ADMIN_ADDR"] == "127.0.0.1:9464"


def test_installers_wait_as_long_as_docker_calls_the_app_starting() -> None:
    """A slow host's first boot outlasted a 900s start_period (migrations
    alone took 2260s), and `docker compose up --wait` then failed on an app
    that finished fine. The installers give up no earlier than Docker does."""
    start_period = _compose(STANDALONE_COMPOSE)["services"]["app"]["healthcheck"][
        "start_period"
    ]
    seconds = int(start_period.removesuffix("s"))
    assert seconds >= 3600
    shell = (ROOT / "bin" / "install").read_text(encoding="utf-8")
    powershell = (ROOT / "bin" / "install.ps1").read_text(encoding="utf-8")
    assert f"bounded_env_int INSTALL_READY_MAX_SECONDS {seconds} " in shell
    assert f"'INSTALL_READY_MAX_SECONDS' {seconds} " in powershell


def test_postgres_has_room_for_the_api_and_the_worker() -> None:
    command = _compose(STANDALONE_COMPOSE)["services"]["postgres"]["command"]
    assert "max_connections=200" in command
    # granian's --backpressure counts connections (keep-alive, websockets),
    # not requests: a low value would stall browsers, so it is not used.
    assert "--backpressure" not in _programs()["api"]["command"]


def test_standalone_compose_bind_mounts_exist() -> None:
    services = _compose(STANDALONE_COMPOSE)["services"]
    for name, service in services.items():
        for volume in service.get("volumes", []):
            source = volume.split(":", 1)[0]
            if source.startswith("./") and "$" not in source:
                assert (ROOT / source).exists(), f"{name}: {source}"
    assert (ROOT / "agentcc-gateway" / "config.example.yaml").exists()


def test_standalone_and_distributed_postgres_share_one_image() -> None:
    # Volumes move between the two files; glibc and musl builds sort text
    # differently and would corrupt each other's indexes.
    standalone = _compose(STANDALONE_COMPOSE)["services"]["postgres"]
    distributed = _compose(DISTRIBUTED_COMPOSE)["services"]["postgres"]
    assert standalone["image"] == distributed["image"]
    # A distributed install's PeerDB slots must not stop Postgres from starting.
    assert "wal_level=logical" in standalone["command"]
    assert "max_replication_slots" in standalone["command"]


def test_distributed_compose_has_no_rabbitmq_and_uses_redis_channels() -> None:
    config = _compose(DISTRIBUTED_COMPOSE)
    assert "rabbitmq" not in DISTRIBUTED_COMPOSE.read_text(encoding="utf-8").lower()
    assert "rabbitmq-data" not in config["volumes"]
    for name, service in config["services"].items():
        assert "rabbitmq" not in (service.get("depends_on") or {}), name
    env = config["x-backend-env"]
    assert env["CHANNEL_LAYER_BACKEND"] == "redis"
    assert env["CHANNEL_REDIS_URL"] == "redis://redis:6379/3"
    assert env["FI_CDC_MODE"] == "peerdb"
    assert "CELERY_BROKER_URL" not in env
    # Workers relay live updates to the backend service (granian on :80), not
    # to BASE_URL's localhost:8000, which no full-stack container serves.
    assert env["WEBSOCKET_ENDPOINT"] == (
        "${WEBSOCKET_ENDPOINT:-http://backend/call-websocket/}"
    )
    assert env["MODEL_SERVING_URL"] == "${MODEL_SERVING_URL:-http://serving:8080}"
    assert "SERVING_URL" not in env
    text = DISTRIBUTED_COMPOSE.read_text(encoding="utf-8")
    assert text.count("<<: *backend-env") + text.count("<<: *catalog-bootstrap-env") > 5


def test_supervisor_runs_the_contracted_processes() -> None:
    programs = _programs()
    assert set(programs) == {
        "redis",
        "temporal",
        "objects",
        "bootstrap",
        "api",
        "collector",
        "gateway",
        "sandbox",
        "web",
    }
    api = programs["api"]
    assert api["command"].startswith("/opt/futureagi/bin/after-bootstrap granian ")
    assert "--workers 1" in api["command"] and "--reload" not in api["command"]
    assert 'FI_EMBEDDED_TEMPORAL_WORKER="true"' in api["environment"]
    assert int(api["stopwaitsecs"]) < 60
    assert programs["collector"]["command"].startswith(
        "/opt/futureagi/bin/after-bootstrap "
    )
    assert "--db-filename /data/temporal/" in programs["temporal"]["command"]
    for attribute in ("OrgId", "ProjectId", "RunType", "Status"):
        assert f"EvalTask{attribute}=Keyword" in programs["temporal"]["command"]
    sandbox = programs["sandbox"]
    assert sandbox["user"] == "sandbox"
    assert sandbox["command"].startswith("/usr/bin/env -i ")
    # No user site-packages an eval could plant code in for the server.
    assert "PYTHONNOUSERSITE=1" in sandbox["command"]
    # The only setting of the container's that the server's scrubbed
    # environment keeps: .env's egress ports.
    assert (
        'CODE_EXECUTOR_EGRESS_PORTS="%(ENV_CODE_EXECUTOR_EGRESS_PORTS)s"'
        in sandbox["command"]
    )
    assert "HOME=/tmp" not in sandbox["command"]
    assert programs["bootstrap"]["autorestart"] == "unexpected"
    # The API stops first: its worker drains while everything it calls
    # (datastores, gateway, sandbox, collector) is still up.
    for name, program in programs.items():
        if name != "api":
            assert int(program.get("priority", 999)) < int(api["priority"]), name


def test_the_gateway_loads_keys_from_the_app_and_sends_it_request_logs() -> None:
    """Without the control plane the gateway forgets keys made in the UI when
    it restarts, and its request logs and analytics stay empty."""
    app = _compose(STANDALONE_COMPOSE)["services"]["app"]["environment"]
    assert app["AGENTCC_CONTROL_PLANE_URL"] == "http://127.0.0.1:8000"
    assert app["AGENTCC_CONTROL_PLANE_TOKEN"] == app["AGENTCC_ADMIN_TOKEN"]
    assert app["AGENTCC_SYNC_ON_STARTUP"] == "true"
    # And every minute, so a gateway that started before the app catches up.
    assert app["AGENTCC_SYNC_INTERVAL"] == "${AGENTCC_SYNC_INTERVAL:-60s}"
    assert app["AGENTCC_WEBHOOK_SECRET"] == "${AGENTCC_WEBHOOK_SECRET:-}"
    # It loads the keys once the API is up, not during a long bootstrap.
    assert _programs()["gateway"]["command"].startswith(
        "/opt/futureagi/bin/after-bootstrap agentcc-gateway "
    )

    distributed = _compose(DISTRIBUTED_COMPOSE)
    gateway = distributed["services"]["agentcc-gateway"]["environment"]
    backend = distributed["x-backend-env"]
    # The backend's granian listens on :80.
    assert "${BACKEND_PORT:-8000}:80" in distributed["services"]["backend"]["ports"]
    assert gateway["AGENTCC_CONTROL_PLANE_URL"] == "http://backend"
    assert gateway["AGENTCC_CONTROL_PLANE_TOKEN"] == backend["AGENTCC_ADMIN_TOKEN"]
    assert gateway["AGENTCC_SYNC_ON_STARTUP"] == "true"
    assert gateway["AGENTCC_SYNC_INTERVAL"] == app["AGENTCC_SYNC_INTERVAL"]
    assert gateway["AGENTCC_WEBHOOK_SECRET"] == backend["AGENTCC_WEBHOOK_SECRET"]

    # Org providers on private networks stay refused unless the operator
    # opts in, in the gateway and in the API alike.
    private = "${AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS:-false}"
    assert app["AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS"] == private
    assert gateway["AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS"] == private
    assert backend["AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS"] == private


@pytest.mark.parametrize("configured", ["", "from-dot-env"])
def test_start_makes_a_webhook_secret_only_when_none_is_set(
    tmp_path, configured
) -> None:
    import subprocess

    section = _start_section('if [ -z "${AGENTCC_WEBHOOK_SECRET:-}" ]; then', "fi\n")
    script = tmp_path / "webhook.sh"
    script.write_text(
        f'set -euo pipefail\n{section}\nprintf %s "$AGENTCC_WEBHOOK_SECRET"\n',
        encoding="utf-8",
    )
    env = {"PATH": os.environ["PATH"], "AGENTCC_WEBHOOK_SECRET": configured}
    result = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, check=True
    )
    if configured:
        assert result.stdout == configured
    else:
        assert re.fullmatch(r"[0-9a-f]{64}", result.stdout)


def test_supervisor_keeps_secrets_off_command_lines_and_out_of_tmp() -> None:
    conf = (STANDALONE / "supervisord.conf").read_text(encoding="utf-8")
    programs = _programs()
    assert programs["redis"]["command"] == (f"redis-server {SECRETS_DIR}/redis.conf")
    for name, program in programs.items():
        assert "requirepass" not in program["command"], name
        assert "PASSWORD" not in program["command"], name
    assert "/tmp/" not in conf
    nginx = (STANDALONE / "nginx.conf").read_text(encoding="utf-8")
    directives = [
        line for line in nginx.splitlines() if not line.lstrip().startswith("#")
    ]
    assert not [line for line in directives if "/tmp" in line]
    start = (STANDALONE / "bin" / "start").read_text(encoding="utf-8")
    assert f"> {SECRETS_DIR}/redis.conf" in start
    assert "requirepass" in start and "umask 077" in start


def test_a_fatal_program_stops_the_container() -> None:
    parser = _supervisor()
    listener = dict(parser.items("eventlistener:fatal"))
    assert listener["events"] == "PROCESS_STATE_FATAL"
    assert 'kill -TERM "$PPID"' in listener["command"]
    # stdout is the listener protocol channel.
    assert listener["stdout_logfile"] == "NONE"
    assert "redirect_stderr" not in listener


def _dockerfile_args(dockerfile: Path = STANDALONE / "Dockerfile") -> set[str]:
    text = dockerfile.read_text(encoding="utf-8")
    return set(re.findall(r"^ARG ([A-Za-z_][A-Za-z0-9_]*)", text, re.M))


def test_standalone_image_builds_from_its_own_directory() -> None:
    dockerfile = (STANDALONE / "Dockerfile").read_text(encoding="utf-8")
    for arg in (
        "BACKEND_IMAGE",
        "FRONTEND_IMAGE",
        "FI_COLLECTOR_IMAGE",
        "AGENTCC_GATEWAY_IMAGE",
        "TEMPORAL_IMAGE",
        "MINIO_IMAGE",
    ):
        assert f"ARG {arg}=" in dockerfile
    assert "ARG BACKEND_IMAGE=futureagi/future-agi:latest" in dockerfile
    assert "\nFROM ${BACKEND_IMAGE}\n" in dockerfile
    # Third-party binaries that run as root are pinned by digest.
    for arg in ("TEMPORAL_IMAGE", "MINIO_IMAGE"):
        assert re.search(rf"^ARG {arg}=\S+@sha256:[0-9a-f]{{64}}$", dockerfile, re.M)
    # RLIMIT_NPROC counts a uid across the host: not postgres's 999.
    assert "useradd --system --uid 18060 " in dockerfile
    ignored = [
        line.strip().rstrip("/")
        for line in (STANDALONE / ".dockerignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.startswith("#")
    ]
    for line in dockerfile.splitlines():
        if line.startswith("COPY ") and "--from=" not in line:
            source = line.split()[1]
            assert (STANDALONE / source).exists(), source
            assert source.rstrip("/") not in ignored, source
    for script in ("start", "after-bootstrap", "bootstrap.py"):
        assert (STANDALONE / "bin" / script).stat().st_mode & 0o111, script


def test_the_standalone_image_reports_its_version_to_telemetry() -> None:
    """tfc.deployment_telemetry falls back to SERVICE_VERSION when
    FUTURE_AGI_VERSION names no release. Set with the metadata, after every
    layer, so a new version never invalidates the build cache."""
    lines = (STANDALONE / "Dockerfile").read_text(encoding="utf-8").splitlines()
    env = lines.index("ENV SERVICE_VERSION=${VERSION}")
    assert lines.index("ARG VERSION=dev") < env
    layers = [
        number
        for number, line in enumerate(lines)
        if line.startswith(("RUN ", "COPY ", "ADD "))
    ]
    assert max(layers) < env
    ignored = (STANDALONE / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "**/__pycache__" in ignored


@pytest.mark.parametrize("recipe", ["build-local.sh", "build-local.ps1"])
def test_installer_build_args_are_declared_in_the_standalone_dockerfile(
    recipe,
) -> None:
    # docker only warns about an unknown --build-arg; the image would then be
    # assembled from the published :latest components, or the backend built
    # as the wrong variant. The installers and bin/dev build with bin/lib.
    text = (ROOT / "bin" / "lib" / recipe).read_text(encoding="utf-8")
    build_arg = r"--build-arg['\", ]+([A-Za-z_][A-Za-z0-9_]*)="
    standalone = re.search(
        r"futureagi/standalone:(?:local|\$tag).*?deploy/standalone'?$",
        text,
        re.S | re.M,
    ).group(0)
    passed = set(re.findall(build_arg, standalone))
    assert {"BACKEND_IMAGE", "FI_COLLECTOR_IMAGE", "AGENTCC_GATEWAY_IMAGE"} <= passed
    assert passed <= _dockerfile_args()
    # The rest go to the backend build (futureagi/Dockerfile.oss).
    backend = set(re.findall(build_arg, text)) - passed
    assert backend == {"IMAGE_VARIANT"}
    assert backend <= _dockerfile_args(ROOT / "futureagi" / "Dockerfile.oss")


def test_start_exports_every_value_supervisor_interpolates() -> None:
    """supervisord does not start when an %(ENV_X)s it reads is unset."""
    settings = [
        line
        for line in (STANDALONE / "supervisord.conf").read_text().splitlines()
        if not line.startswith(";")
    ]
    start = (STANDALONE / "bin" / "start").read_text(encoding="utf-8")
    before_supervisor = start[: start.index("exec supervisord")]
    names = set(re.findall(r"%\(ENV_([A-Z0-9_]+)\)s", "\n".join(settings)))
    assert "CODE_EXECUTOR_EGRESS_PORTS" in names
    for name in names:
        assert re.search(rf"^\s*export {name}\b", before_supervisor, re.M), name


def test_start_runs_one_off_commands_without_supervisor() -> None:
    start = (STANDALONE / "bin" / "start").read_text(encoding="utf-8")
    one_off = start.index('exec "$@"')
    assert one_off < start.index("rm -f /data/.bootstrap-ok")
    assert one_off < start.index("exec supervisord")


def _start_section(first_line: str, last_line: str) -> str:
    start = (STANDALONE / "bin" / "start").read_text(encoding="utf-8")
    begin = start.index(first_line)
    return start[begin : start.index(last_line, begin) + len(last_line)]


@pytest.mark.parametrize(
    "profiles, configured, expected",
    [
        ("", "http://code-executor:8060", "http://code-executor:8060"),
        ("ml", "http://code-executor:8060", "http://code-executor:8060"),
        ("ml,sandbox", "http://code-executor:8060", "http://code-executor-nsjail:8060"),
        ("sandbox", "http://other:8060", "http://other:8060"),
    ],
)
def test_sandbox_profile_routes_code_evals_to_nsjail(
    tmp_path, profiles, configured, expected
) -> None:
    import subprocess

    section = _start_section('case ",${COMPOSE_PROFILES', "esac\n")
    script = tmp_path / "route.sh"
    script.write_text(
        f'set -euo pipefail\n{section}\nprintf %s "$CODE_EXECUTOR_URL"\n',
        encoding="utf-8",
    )
    env = {
        "PATH": os.environ["PATH"],
        "COMPOSE_PROFILES": profiles,
        "CODE_EXECUTOR_URL": configured,
    }
    result = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, check=True
    )
    assert result.stdout.splitlines()[-1] == expected


@pytest.mark.parametrize(
    "password, expected",
    [
        ("plain-Hex_0.9~", None),
        (
            "p@ss:w/rd#?%",
            "postgres://fi%20user:p%40ss%3Aw%2Frd%23%3F%25@postgres:5432/"
            "futureagi?sslmode=disable",
        ),
    ],
)
def test_start_percent_encodes_the_collector_dsn_only_when_needed(
    tmp_path, password, expected
) -> None:
    import subprocess

    section = _start_section('case "${PG_USER', "esac\n")
    script = tmp_path / "dsn.sh"
    script.write_text(
        f'set -euo pipefail\n{section}\nprintf %s "$FI_PG_WRITE"\n',
        encoding="utf-8",
    )
    user = "futureagi" if expected is None else "fi user"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PG_USER": user,
        "PG_PASSWORD": password,
        "PG_DB": "futureagi",
        "PG_HOST": "postgres",
        "PG_PORT": "5432",
        "FI_PG_WRITE": "from-compose",
    }
    result = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, check=True
    )
    assert result.stdout == (expected or "from-compose")


def _run_start_section(section: str, env: dict[str, str], epilogue: str) -> str:
    import subprocess

    result = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{section}\n{epilogue}"],
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.mark.parametrize(
    "binary, expected",
    [
        (b"\x7fELF FI_OBSERVED_CATALOG_MODE=direct requires ...", "direct"),
        (b"\x7fELF FI_OBSERVED_CATALOG_MODE must be disabled or kafka", "disabled"),
    ],
)
def test_start_keeps_a_collector_without_direct_mode_running(
    tmp_path, binary, expected
) -> None:
    """docker-compose.yml asks for FI_OBSERVED_CATALOG_MODE=direct; a standalone
    image built with an older fi-collector would refuse it and never start."""
    collector = tmp_path / "fi-collector"
    collector.write_bytes(binary)
    section = _start_section(
        'if [ "${FI_OBSERVED_CATALOG_MODE:-}" = "direct" ]', "fi\n"
    ).replace("/usr/local/bin/fi-collector", str(collector))
    out = _run_start_section(
        section,
        {"FI_OBSERVED_CATALOG_MODE": "direct"},
        'printf %s "$FI_OBSERVED_CATALOG_MODE"',
    )
    assert out == expected


@pytest.mark.parametrize("key_mounted", [True, False])
def test_start_points_google_credentials_at_the_mounted_key(
    tmp_path, key_mounted
) -> None:
    """.env's GOOGLE_APPLICATION_CREDENTIALS is the key's path on the host;
    docker-compose.yml mounts the file at SECRETS_DIR/vertex.json."""
    key = tmp_path / "vertex.json"
    # Without a key, compose mounts /dev/null there.
    key.write_text('{"type": "service_account"}' if key_mounted else "")
    section = _start_section(
        f"if [ -s {SECRETS_DIR}/vertex.json ]; then", "fi\n"
    ).replace(f"{SECRETS_DIR}/vertex.json", str(key))
    out = _run_start_section(
        section,
        {"GOOGLE_APPLICATION_CREDENTIALS": "/Users/me/sa.json"},
        'printf %s "${GOOGLE_APPLICATION_CREDENTIALS-<unset>}"',
    )
    assert out == (str(key) if key_mounted else "<unset>")


@pytest.mark.parametrize(
    "allowed, expected",
    [
        (None, "<unset>"),
        ("*", "*"),
        ("api.example.com, *", "api.example.com, *"),
        ("api.example.com", "api.example.com,127.0.0.1,localhost"),
        (
            "a.example.com,.b.example.com",
            "a.example.com,.b.example.com,127.0.0.1,localhost",
        ),
    ],
)
def test_start_keeps_the_loopback_probes_allowed(allowed, expected) -> None:
    """The bootstrap's wait for the API and the healthcheck ask 127.0.0.1:8000;
    an ALLOWED_HOSTS naming only public hosts would answer them 400."""
    section = _start_section('hosts=",${ALLOWED_HOSTS-*},"', "esac\n")
    env = {} if allowed is None else {"ALLOWED_HOSTS": allowed}
    out = _run_start_section(section, env, 'printf %s "${ALLOWED_HOSTS-<unset>}"')
    assert out == expected


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, "http://localhost:8000"),
        ({"BACKEND_PORT": "8001"}, "http://localhost:8001"),
        (
            {"BACKEND_PORT": "8001", "VITE_HOST_API": "https://api.example.com"},
            "https://api.example.com",
        ),
    ],
)
def test_start_gives_the_ui_the_api_on_backend_port(tmp_path, env, expected) -> None:
    generator = tmp_path / "frontend-config"
    generator.write_text(
        '#!/bin/sh\nprintf "%s|%s|" "$VITE_HOST_API" "$CONFIG_PATH"\n',
        encoding="utf-8",
    )
    generator.chmod(0o755)
    section = _start_section(
        'VITE_HOST_API="${VITE_HOST_API:-', "frontend-config true\n"
    ).replace("/opt/futureagi/bin/frontend-config", str(generator))
    out = _run_start_section(section, env, 'printf %s "${VITE_HOST_API-<unset>}"')
    # Only the generator gets the default: setup checks see .env's value.
    assert out == (
        f"{expected}|/srv/frontend/config.js|{env.get('VITE_HOST_API', '<unset>')}"
    )


def test_app_urls_have_working_defaults_in_both_setups() -> None:
    """APP_URL builds invite and reset links (unset: 'None/...');
    FI_COLLECTOR_PUBLIC_URL is the FI_BASE_URL the UI tells SDKs to use."""
    public_url = (
        "${FI_COLLECTOR_PUBLIC_URL:-http://localhost:"
        "${FI_COLLECTOR_OTLP_HTTP_PORT:-4318}}"
    )
    envs = [
        _compose(STANDALONE_COMPOSE)["services"]["app"]["environment"],
        _compose(DISTRIBUTED_COMPOSE)["x-backend-env"],
    ]
    for env in envs:
        assert env["APP_URL"] == "${APP_URL:-localhost:${FRONTEND_PORT:-3000}}"
        assert env["FI_COLLECTOR_PUBLIC_URL"] == public_url
        # Nothing reads these.
        assert "FUTURE_AGI_CLOUD_API_KEY" not in env
        assert "FUTURE_AGI_CLOUD_API_URL" not in env


# Keys the Standalone app sets otherwise than Distributed's x-backend-env,
# each with why. Every other key the two share must be equal, so a change to
# one setup's backend environment reaches the other.
STANDALONE_OWN_BACKEND_ENV = {
    # Services inside the app container, on loopback.
    "AGENTCC_GATEWAY_INTERNAL_URL": "the gateway runs in the app container",
    "AGENTCC_INTERNAL_URL": "the gateway runs in the app container",
    "ALK_RUNNER_API_URL": "the API runs in the app container",
    "FI_COLLECTOR_HOST": "the collector runs in the app container",
    "REDIS_HOST": "Redis runs in the app container",
    "REDIS_URL": "Redis runs in the app container, with a password",
    "REDIS_CACHE_URL": "Redis runs in the app container, with a password",
    "REDIS_LOCK_URL": "Redis runs in the app container, with a password",
    "REDIS_STATE_URL": "Redis runs in the app container, with a password",
    "CHANNEL_REDIS_URL": "Redis runs in the app container, with a password",
    "S3_ENDPOINT_URL": "object storage runs in the app container",
    "TEMPORAL_HOST": "the Temporal dev server runs in the app container",
    "TEMPORAL_NAMESPACE": "the in-container Temporal serves the default one",
    "WEBSOCKET_ENDPOINT": "the API runs in the app container",
    "CODE_EXECUTOR_URL": "bin/start points it at the nsjail executor when asked",
    # Choices of the setup.
    "FI_CDC_MODE": "Standalone syncs through the outbox, Distributed through PeerDB",
    "FUTURE_AGI_TELEMETRY_BUFFER_DIR": "/tmp is writable by the in-app sandbox",
    "CODE_EXECUTOR_LOCAL_FALLBACK": "the API process holds every secret and /data",
    "CH_ENABLED": "the app always has its ClickHouse",
    "CH_USE_REPLICATED_ENGINES": "one ClickHouse node",
}


# Keys of Distributed's backends that the Standalone app does without, and why.
DISTRIBUTED_OWN_BACKEND_ENV = {
    "FAST_STARTUP": "only futureagi/entrypoint.sh reads it; the app starts through bin/start",
}


def test_standalone_backend_env_matches_distributed_outside_its_own_keys() -> None:
    app = _compose(STANDALONE_COMPOSE)["services"]["app"]["environment"]
    backend = _compose(DISTRIBUTED_COMPOSE)["x-backend-env"]
    # A key added to one setup only would escape the comparison below.
    assert set(backend) - set(app) == set(DISTRIBUTED_OWN_BACKEND_ENV)
    differing = {
        key for key in set(app) & set(backend) if str(app[key]) != str(backend[key])
    }
    assert differing == set(STANDALONE_OWN_BACKEND_ENV), (
        "differ but not listed: "
        f"{sorted(differing - set(STANDALONE_OWN_BACKEND_ENV))}; "
        f"listed but equal: {sorted(set(STANDALONE_OWN_BACKEND_ENV) - differing)}"
    )


def test_every_distributed_backend_probes_the_collector_on_the_network() -> None:
    """env_file hands .env's FI_COLLECTOR_OTLP_PORT, the host port the
    installer moves when 4317 is taken, to every service that loads it."""
    services = _compose(DISTRIBUTED_COMPOSE)["services"]
    backends = [
        name
        for name, service in services.items()
        if "env_file" in service and "PG_HOST" in (service.get("environment") or {})
    ]
    assert {"backend", "worker", "worker-default", "serving"} <= set(backends)
    for name in backends:
        env = services[name]["environment"]
        assert env["FI_COLLECTOR_HOST"] == "fi-collector", name
        assert env["FI_COLLECTOR_OTLP_PORT"] == "4317", name


def test_the_ui_calls_the_api_on_backend_port_by_default() -> None:
    default = "${VITE_HOST_API:-http://localhost:${BACKEND_PORT:-8000}}"
    assert (
        _compose(DISTRIBUTED_COMPOSE)["services"]["frontend"]["environment"][
            "VITE_HOST_API"
        ]
        == default
    )
    for overlay in ("docker-compose.dev.yml", "docker-compose.distributed.dev.yml"):
        # Compose's merge tags (!override, !reset) are not YAML's.
        text = re.sub(r"!(override|reset)\b", "", (ROOT / overlay).read_text("utf-8"))
        frontend = yaml.safe_load(text)["services"]["frontend"]
        assert frontend["environment"]["VITE_HOST_API"] == default, overlay


@pytest.mark.parametrize(
    "files",
    [
        ("docker-compose.yml",),
        ("docker-compose.yml", "docker-compose.dev.yml"),
        ("docker-compose.distributed.yml",),
        ("docker-compose.distributed.yml", "docker-compose.distributed.dev.yml"),
    ],
)
def test_every_setup_resolves_with_an_empty_env(files) -> None:
    """Compose interpolates every service, profile or not: a `${VAR:?}` in an
    opt-in service would stop every command until VAR is set."""
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        pytest.skip("docker CLI is unavailable")
    command = ["docker", "compose", "--env-file", os.devnull]
    for name in files:
        command += ["-f", str(ROOT / name)]
    result = subprocess.run(
        [*command, "config", "--format", "json"],
        cwd=ROOT,
        env={key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    backend = services["app" if "app" in services else "backend"]["environment"]
    assert backend["FI_COLLECTOR_OTLP_PORT"] == "4317"
    assert backend["APP_URL"] == "localhost:3000"
    assert backend["FI_COLLECTOR_PUBLIC_URL"] == "http://localhost:4318"


def test_cdc_defaults_cover_schedules_and_an_adopted_peerdb() -> None:
    """The bootstrap passes no options: the outbox module's defaults must sync
    the schedules and take over from a stopped PeerDB (an adopted Distributed
    install's database always has its slots, publications and raw tables)."""
    import inspect

    from tracer.services.clickhouse import oss_outbox_cdc

    params = inspect.signature(oss_outbox_cdc.ensure_installed).parameters
    assert params["schedules"].default is True
    assert params["takeover_peerdb"].default is True


# --- code-executor fallback (no nsjail), as run in the standalone install --------


def _load_executor(monkeypatch):
    # server.py imports falcon at module level; the backend venv lacks it.
    falcon = types.ModuleType("falcon")

    class App:
        def add_route(self, *args):
            pass

    falcon.App = App
    monkeypatch.setitem(sys.modules, "falcon", falcon)
    spec = importlib.util.spec_from_file_location("code_executor_server", CODE_EXECUTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def executor(monkeypatch):
    monkeypatch.delenv("CODE_EXECUTOR_EGRESS_PORTS", raising=False)
    return _load_executor(monkeypatch)


def _landlock_abi() -> int:
    """Asked of the kernel here, not of server.py."""
    if not sys.platform.startswith("linux"):
        return 0
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc.syscall(ctypes.c_long(444), None, ctypes.c_long(0), ctypes.c_long(1))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie is dead for our purposes.
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def test_fallback_runs_an_eval_with_an_empty_environment(executor, tmp_path) -> None:
    code = (
        "import os, resource\n"
        "def evaluate(**kwargs):\n"
        "    return {'result': 1.0, 'reason': repr((sorted(os.environ), os.getcwd(),"
        " resource.getrlimit(resource.RLIMIT_NPROC)[0],"
        " resource.getrlimit(resource.RLIMIT_FSIZE)[0]))}\n"
    )
    result = executor._execute_python_fallback(code, {"input": "x"}, 10)
    assert result["status"] == "success", result
    names, cwd, nproc, fsize = eval(result["data"]["reason"])
    assert "SECRET_KEY" not in names and "PG_PASSWORD" not in names
    # macOS adds __CF_USER_TEXT_ENCODING to every process.
    names = [name for name in names if not name.startswith("__CF")]
    assert set(names) <= set(executor.FALLBACK_ENV) | {"HOME", "TMPDIR", "LC_CTYPE"}
    assert os.path.basename(cwd).startswith("eval-")
    assert nproc == executor.FALLBACK_MAX_PROCESSES
    assert fsize == executor.FALLBACK_MAX_FILE_BYTES
    # The run's private directory is gone.
    assert not os.path.exists(cwd)


def test_fallback_timeout_kills_everything_the_run_started(
    executor, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(executor, "FALLBACK_MAX_PROCESSES", 100_000)
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        "def evaluate(**kwargs):\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"    open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "    time.sleep(60)\n"
    )
    started = time.monotonic()
    result = executor._execute_python_fallback(code, {}, 2)
    assert result == {"status": "error", "data": "Timed out (2s)"}
    assert time.monotonic() - started < 20
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(child)


def test_fallback_kills_background_processes_after_a_normal_run(
    executor, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(executor, "FALLBACK_MAX_PROCESSES", 100_000)
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess, sys\n"
        "def evaluate(**kwargs):\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"    open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "    return True\n"
    )
    result = executor._execute_python_fallback(code, {}, 10)
    assert result["status"] == "success", result
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(child)


def test_fallback_caps_output(executor) -> None:
    code = (
        "import sys\n"
        "def evaluate(**kwargs):\n"
        "    sys.stdout.write('x' * (4 * 1024 * 1024))\n"
        "    return True\n"
    )
    result = executor._execute_python_fallback(code, {}, 10)
    assert result["status"] == "error"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is Linux")
def test_fallback_limits_memory(executor) -> None:
    code = (
        "def evaluate(**kwargs):\n"
        "    blob = bytearray(3 * 1024 * 1024 * 1024)\n"
        "    return True\n"
    )
    result = executor._execute_python_fallback(code, {}, 10)
    # The eval script reports the MemoryError as a runtime error.
    assert result["status"] == "error"
    assert result["data"].startswith("Runtime error"), result


def _listening() -> socket.socket:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    return server


# Connects to each port, then tries to listen, and reports what happened.
EGRESS_PROBE = """
import errno, socket
def evaluate(ports, **kwargs):
    outcomes = []
    for port in ports:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=5).close()
            outcomes.append("connected")
        except OSError as e:
            outcomes.append(errno.errorcode.get(e.errno, str(e)))
    try:
        socket.socket().bind(("127.0.0.1", 0))
        outcomes.append("listened")
    except OSError as e:
        outcomes.append(errno.errorcode.get(e.errno, str(e)))
    return {"result": 1.0, "reason": " ".join(outcomes)}
"""


@pytest.mark.skipif(
    _landlock_abi() < 4, reason="needs Landlock's TCP rules (Linux 6.7+)"
)
def test_fallback_connects_only_to_the_egress_ports(monkeypatch) -> None:
    """Temporal (7233, no auth), ClickHouse, Postgres and Redis listen next to
    the Standalone sandbox: a run may reach only the listed ports."""
    with _listening() as allowed, _listening() as internal:
        ports = [allowed.getsockname()[1], internal.getsockname()[1]]
        monkeypatch.setenv("CODE_EXECUTOR_EGRESS_PORTS", str(ports[0]))
        executor = _load_executor(monkeypatch)
        result = executor._execute_python_fallback(EGRESS_PROBE, {"ports": ports}, 10)
    assert result["status"] == "success", result
    assert result["data"]["reason"] == "connected EACCES EACCES"


def test_fallback_reaches_every_port_where_the_kernel_cannot_restrict_it(
    executor, monkeypatch
) -> None:
    monkeypatch.setattr(executor, "LANDLOCK_TCP_RULES", False)
    with _listening() as internal:
        port = internal.getsockname()[1]
        result = executor._execute_python_fallback(EGRESS_PROBE, {"ports": [port]}, 10)
    assert result["status"] == "success", result
    assert result["data"]["reason"] == "connected listened"


@pytest.mark.parametrize(
    ("nsjail", "landlock", "ports"),
    [(False, True, [80, 443]), (False, False, None), (True, True, None)],
)
def test_health_says_which_ports_code_evals_can_reach(
    executor, monkeypatch, capsys, nsjail, landlock, ports
) -> None:
    monkeypatch.setattr(executor, "NSJAIL_AVAILABLE", nsjail)
    monkeypatch.setattr(executor, "LANDLOCK_TCP_RULES", landlock)

    class Response:
        media = None

    executor.HealthResource().on_get(None, Response)
    assert Response.media["egress_ports"] == ports
    executor._warn_if_egress_is_open()
    # The server's log says so once, at start, when runs reach every port.
    assert ("no Landlock network rules" in capsys.readouterr().err) == (
        not nsjail and not landlock
    )


@pytest.mark.parametrize(
    ("setting", "ports"), [("8443,443", (8443, 443)), ("", (80, 443))]
)
def test_the_egress_ports_come_from_the_environment(
    monkeypatch, setting, ports
) -> None:
    monkeypatch.setenv("CODE_EXECUTOR_EGRESS_PORTS", setting)
    assert _load_executor(monkeypatch).FALLBACK_EGRESS_PORTS == ports


@pytest.mark.parametrize("setting", ["80,", "https", "0", "70000", "80;443"])
def test_a_malformed_egress_port_list_stops_the_server(monkeypatch, setting) -> None:
    monkeypatch.setenv("CODE_EXECUTOR_EGRESS_PORTS", setting)
    with pytest.raises(ValueError, match="CODE_EXECUTOR_EGRESS_PORTS"):
        _load_executor(monkeypatch)


@pytest.mark.parametrize(
    "timeout, expected", [("7", 7), (None, 30), (999, 60), ("x", 30)]
)
def test_execute_clamps_the_requested_timeout(executor, monkeypatch, timeout, expected):
    seen = []
    monkeypatch.setattr(executor, "NSJAIL_AVAILABLE", False)
    monkeypatch.setattr(
        executor,
        "_execute_python_fallback",
        lambda code, data, t: seen.append(t) or {"status": "success", "data": {}},
    )

    class Request:
        class bounded_stream:
            @staticmethod
            def read():
                import json

                return json.dumps({"code": "x", "timeout": timeout}).encode()

    class Response:
        media = None

    executor.ExecuteResource().on_post(Request, Response)
    assert seen == [expected]
    assert Response.media["status"] == "success"


def test_a_javascript_eval_without_node_says_how_to_run_it(
    executor, monkeypatch
) -> None:
    """The standalone image ships no Node.js; the `sandbox` profile's nsjail
    executor has it (futureagi/code-executor/Dockerfile.base)."""
    monkeypatch.setattr(executor, "NODE_PATH", None)

    result = executor._execute_javascript("function evaluate() {}", {}, 5)

    assert result["status"] == "error"
    assert "COMPOSE_PROFILES=sandbox" in result["data"]
    assert result["setup_error"] is True
    # The eval playground shows it because the backend knows this exact text.
    from agentic_eval.core_evals.fi_utils import sandbox

    assert result["data"] == sandbox.EXECUTOR_NO_NODE_MESSAGE
    base = (ROOT / "futureagi" / "code-executor" / "Dockerfile.base").read_text(
        encoding="utf-8"
    )
    assert "nodejs" in base
