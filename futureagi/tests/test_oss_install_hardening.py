from __future__ import annotations

import base64
import json
import os
import pty
import re
import shutil
import subprocess
import sys
import termios
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
# The property catalog, Kafka and the split collector live in the distributed stack.
COMPOSE_FILE = ROOT / "docker-compose.distributed.yml"
STANDALONE_COMPOSE_FILE = ROOT / "docker-compose.yml"
INSTALL_SH = ROOT / "bin" / "install"
INSTALL_PS1 = ROOT / "bin" / "install.ps1"
# Sourced by bin/install, bin/uninstall and bin/dev.
INSTALL_LIB = ROOT / "bin" / "lib"
BACKFILL_SH = ROOT / "bin" / "property-catalog-backfill"
BACKFILL_PS1 = ROOT / "bin" / "property-catalog-backfill.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _installer_files(installer: Path) -> list[Path]:
    """An installer and the bin/lib files it sources (or dot-sources)."""
    suffix = ".ps1" if installer.suffix == ".ps1" else ".sh"
    return [installer, *sorted(INSTALL_LIB.glob(f"*{suffix}"))]


def _installer_text(installer: Path) -> str:
    return "\n".join(_read(path) for path in _installer_files(installer))


def _compose_config() -> dict[str, object]:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is unavailable")
    environment = {
        key: value for key, value in os.environ.items() if key in ("PATH", "HOME")
    }
    environment.update(
        {
            "FI_COLLECTOR_VERSION": "local",
            "PROPERTY_CATALOG_KAFKA_PORT": "29092",
            "PROPERTY_CATALOG_KAFKA_CPUS": "1.0",
            "PROPERTY_CATALOG_KAFKA_MEMORY": "1G",
            "PROPERTY_CATALOG_KAFKA_HEAP_OPTS": "-Xms256m -Xmx512m",
            "FI_COLLECTOR_CPUS": "1.0",
            "FI_COLLECTOR_MEMORY": "1G",
            "PROPERTY_CATALOG_CONSUMER_CPUS": "0.5",
            "PROPERTY_CATALOG_CONSUMER_MEMORY": "512M",
        }
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(COMPOSE_FILE),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"docker compose config failed: {result.stderr}")
    return json.loads(result.stdout)


def test_compose_builds_the_shared_collector_image_with_bounded_resources() -> None:
    config = _compose_config()
    services = config["services"]
    assert isinstance(services, dict)

    collector = services["fi-collector"]
    consumer = services["fi-property-catalog-consumer"]
    for service in (collector, consumer):
        assert service["image"] == "futureagi/fi-collector:local"
        assert Path(service["build"]["context"]).name == "fi-collector"
    assert consumer["entrypoint"] == ["/usr/local/bin/fi-property-catalog-consumer"]
    assert consumer["command"] == []

    collector_env = collector["environment"]
    consumer_env = consumer["environment"]
    topic = collector_env["FI_OBSERVED_CATALOG_KAFKA_TOPIC"]
    assert collector_env["FI_OBSERVED_CATALOG_MODE"] == "kafka"
    assert topic == "futureagi.observed-attributes.v1"
    assert consumer_env["FI_OBSERVED_CATALOG_KAFKA_TOPIC"] == topic
    assert consumer_env["FI_OBSERVED_CATALOG_KAFKA_GROUP"] == (
        "futureagi.observed-attributes.consumer.v1"
    )
    assert (
        consumer_env["FI_OBSERVED_CATALOG_CH_DATABASE"]
        == (services["backend"]["environment"]["PROPERTY_CATALOG_DATABASE"])
    )
    collector_volumes = {
        (volume["source"], volume["target"], volume.get("read_only", False))
        for volume in collector["volumes"]
    }
    assert ("fi-collector-data", "/var/lib/fi-collector", False) in collector_volumes
    assert collector_env["FI_OBSERVED_CATALOG_SPOOL_DIR"] == (
        "/var/lib/fi-collector/observed-catalog"
    )
    topic_init_env = services["property-catalog-topic-init"]["environment"]
    assert topic_init_env["OBSERVED_CATALOG_KAFKA_TOPIC"] == topic

    kafka = services["property-catalog-kafka"]
    assert kafka["environment"]["KAFKA_HEAP_OPTS"] == "-Xms256m -Xmx512m"
    assert kafka["environment"]["KAFKA_LISTENERS"] == (
        "INTERNAL://:9092,EXTERNAL://:29092,CONTROLLER://:9093"
    )
    assert kafka["environment"]["KAFKA_ADVERTISED_LISTENERS"] == (
        "INTERNAL://property-catalog-kafka:9092,EXTERNAL://127.0.0.1:29092"
    )
    assert kafka["environment"]["KAFKA_INTER_BROKER_LISTENER_NAME"] == "INTERNAL"
    assert kafka["ports"] == [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 29092,
            "published": "29092",
            "protocol": "tcp",
        }
    ]
    for service_name in (
        "property-catalog-kafka",
        "property-catalog-topic-init",
        "fi-collector",
        "fi-property-catalog-consumer",
    ):
        service = services[service_name]
        assert service.get("profiles") in (None, [])
        assert "default" in service["networks"]
    for service_name in (
        "property-catalog-kafka",
        "fi-collector",
        "fi-property-catalog-consumer",
    ):
        assert services[service_name]["cpus"] > 0
        assert int(services[service_name]["mem_limit"]) > 0
        limits = services[service_name]["deploy"]["resources"]["limits"]
        assert limits["cpus"] > 0
        assert int(limits["memory"]) > 0

    internal_broker = "property-catalog-kafka:9092"
    assert collector_env["FI_OBSERVED_CATALOG_KAFKA_BROKERS"] == internal_broker
    assert consumer_env["FI_OBSERVED_CATALOG_KAFKA_BROKERS"] == internal_broker


def test_installers_gate_success_on_the_full_catalog_path() -> None:
    shell = _installer_text(INSTALL_SH)
    powershell = _installer_text(INSTALL_PS1)
    required_services = (
        "property-catalog-kafka",
        "property-catalog-kafka-volume-init",
        "property-catalog-runtime-volume-init",
        "property-catalog-topic-init",
        "property-catalog-clickhouse-bootstrap",
        "fi-collector",
        "fi-property-catalog-consumer",
        "backend",
    )
    for service in required_services:
        assert service in shell
        assert service in powershell

    for retired in (
        "property-catalog-postgres-bootstrap",
        "fi-property-catalog-sequencer",
        "property-catalog-supervisor",
    ):
        assert retired not in shell
        assert retired not in powershell

    assert "INSTALL_READY_TIMEOUT_SECONDS" in shell
    assert "INSTALL_STABILITY_SECONDS" in shell
    assert "Stack did not become fully ready" in shell
    assert "Stack did not become fully ready" in powershell
    assert "Backend did not pass /health/" not in shell
    assert "Backend did not pass /health/" not in powershell


def test_installers_cover_kafka_port_and_all_catalog_persistent_state() -> None:
    shell = _installer_text(INSTALL_SH)
    powershell = _installer_text(INSTALL_PS1)
    for installer in (shell, powershell):
        assert "PROPERTY_CATALOG_KAFKA_PORT" in installer
        assert "property-catalog-kafka-data" in installer
        assert "property-catalog-sequencer-data" not in installer
        assert "fi-collector-data" in installer
        assert "app-data" in installer
        assert "--ignore-buildable" in installer
        assert "fi-property-catalog-consumer" in installer
        # The locally built collector image is never pulled.
        assert "futureagi/fi-collector:local" in installer

    assert "--wipe-volumes" in shell
    assert "WipeVolumes" in powershell
    assert "docker volume prune" not in shell
    assert "docker system prune" not in shell
    assert "docker volume prune" not in powershell
    assert "docker system prune" not in powershell


@pytest.mark.parametrize(
    "script", _installer_files(INSTALL_SH), ids=lambda path: path.name
)
def test_shell_installer_parses(script: Path) -> None:
    result = subprocess.run(
        ["bash", "-n", str(script)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_dev_api_proxy_re_resolves_recreated_compose_services() -> None:
    proxy = _read(ROOT / "deploy/dev-api-proxy/default.conf.template")
    assert "resolver 127.0.0.11" in proxy
    assert "server backend:80 resolve;" in proxy
    assert "server fi-collector:4318 resolve;" in proxy
    assert "proxy_pass http://dev_backend;" in proxy
    assert "proxy_pass http://dev_fi_collector;" in proxy


def test_retired_lifecycle_management_commands_are_not_discoverable() -> None:
    # Command discovery is filesystem-only; do not duplicate Django's expensive
    # cold-start import proof, which belongs to the reader compatibility suite.
    from django.core.management import find_commands

    commands = find_commands(str(ROOT / "futureagi/tracer/management"))
    assert not {
        "ch25_activate_attribute_catalog",
        "ch25_backfill_attribute_catalog",
        "ch25_property_catalog_activate_latest",
        "ch25_property_catalog_dev_rollout",
        "ch25_property_catalog_lifecycle_controller",
        "ch25_property_catalog_oss_supervisor",
    } & set(commands)


def test_shared_image_ships_the_backfill_binary_not_lifecycle_wrappers() -> None:
    dockerfile = _read(ROOT / "fi-collector" / "Dockerfile")
    for binary in (
        "fi-collector",
        "fi-property-catalog-consumer",
        "fi-observed-catalog-backfill",
    ):
        assert f"-o /out/{binary} ./cmd/{binary}" in dockerfile
        assert f"COPY --from=build /out/{binary} /usr/local/bin/{binary}" in dockerfile
    assert "fi-property-catalog-sequencer" not in dockerfile
    assert "fi-catalog-consumer" not in dockerfile
    assert not BACKFILL_SH.exists()
    assert not BACKFILL_PS1.exists()


@pytest.mark.parametrize(
    "script", _installer_files(INSTALL_PS1), ids=lambda path: path.name
)
def test_power_shell_installer_parses_when_pwsh_is_available(script: Path) -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")
    command = (
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$tokens, [ref]$errors) > $null; "
        "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


BACKEND_ROOT = ROOT / "futureagi"

SANDBOX_ENV_EXAMPLE = """BACKEND_PORT=8000
FRONTEND_PORT=3000
INSTALL_READY_TIMEOUT_SECONDS=60
INSTALL_STABILITY_SECONDS=5
INSTALL_READY_MAX_SECONDS=300
"""

# Stub CLIs the installer shells out to. State lives in $FAGI_STUB_STATE and the
# behaviour of each one is selected with FAGI_STUB_* environment variables.
STUB_DOCKER = r"""#!/bin/bash
state="$FAGI_STUB_STATE"
started="2026-01-01T00:00:00Z"

bump() {
  local file="$state/$1" value
  value=$(cat "$file" 2>/dev/null || echo 0)
  value=$((value + 1))
  printf '%s\n' "$value" > "$file"
  printf '%s' "$value"
}

env_value() {
  grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-
}

last_arg=""
for arg in "$@"; do last_arg="$arg"; done

# As in Compose: COMPOSE_FILE from the environment, even empty, wins over .env.
compose_file=${COMPOSE_FILE-$(env_value COMPOSE_FILE)}

if [ "$1" = "compose" ]; then
  shift
  case "$1" in
    version) printf '2.31.0\n'; exit 0 ;;
    config)
      # The resolved config for the stack COMPOSE_FILE selects, as `docker
      # compose config` prints it: services at two spaces, their keys at four.
      version=$(env_value FUTURE_AGI_VERSION)
      printf 'name: futureagi\nservices:\n'
      if printf '%s' "$compose_file" | grep -q 'docker-compose.distributed.yml'; then
        frontend=$(env_value FRONTEND_VERSION)
        gateway=$(env_value AGENTCC_GATEWAY_VERSION)
        set -- "agentcc-gateway futureagi/agentcc-gateway:${gateway:-latest}" \
          "backend futureagi/future-agi:${version:-latest}" \
          "fi-collector futureagi/fi-collector:local" \
          "frontend futureagi/frontend:${frontend:-latest}" \
          "postgres postgres:16"
      else
        set -- "app futureagi/standalone:${version:-latest}" \
          "clickhouse clickhouse/clickhouse-server:25.3-alpine" \
          "postgres postgres:16"
      fi
      for pair in "$@"; do
        printf '  %s:\n    environment:\n      image: nested\n    image: %s\n' \
          "${pair%% *}" "${pair#* }"
      done
      printf 'volumes:\n  postgres-data:\n    name: futureagi_postgres-data\n'
      exit 0 ;;
    pull)
      case " $* " in
        *" --help "*) printf '      --ignore-buildable   Ignore buildable images\n' ;;
        *)
          printf '%s\n' "$@" > "$state/pull.argv"
          [ "$FAGI_STUB_PULL" = "fail" ] && exit 1 ;;
      esac
      exit 0 ;;
    down)
      printf '%s\n' "$*" >> "$state/compose_down.log"
      exit 0 ;;
    up)
      # What the stack starts with, and from which compose files.
      cp .env "$state/env_at_up"
      printf '%s\n' "$compose_file" > "$state/compose_file_at_up"
      exit 0 ;;
    ps)
      [ "$3" = "-q" ] || exit 0
      printf 'cid-%s\n' "$last_arg"
      exit 0 ;;
    logs)
      [ "$3" = "2000" ] || exit 0
      polls=$(bump migration_polls)
      if [ "$FAGI_STUB_MIGRATIONS" = "climbing" ]; then
        line=1
        while [ "$line" -le "$polls" ]; do
          printf 'backend  | Applying accounts.%04d_auto... OK\n' "$line"
          line=$((line + 1))
        done
      else
        printf 'backend  | poll %s: waiting for the database to accept connections\n' "$polls"
      fi
      exit 0 ;;
    exec)
      printf '%s\n' "$@" > "$state/create_user.argv"
      [ -t 0 ] || cat > "$state/create_user.stdin"
      case "$FAGI_STUB_CREATE_USER" in
        exists) printf 'IntegrityError: a user with that email already exists\n'; exit 1 ;;
        fail) printf 'OperationalError: FATAL: password authentication failed\n'; exit 1 ;;
        *) printf 'Created user\n'; exit 0 ;;
      esac ;;
    *) exit 0 ;;
  esac
fi

case "$1" in
  inspect)
    # The environment of the project's clickhouse container, a line an entry.
    case " $* " in *".Config.Env"*)
      for entry in $FAGI_STUB_CLICKHOUSE_ENV; do printf '%s\n' "$entry"; done
      exit 0 ;;
    esac
    case "${last_arg#cid-}" in
      property-catalog-kafka)
        printf 'running|0|0|%s|%s\n' "$FAGI_STUB_KAFKA_HEALTH" "$started" ;;
      fi-collector|fi-property-catalog-consumer)
        printf 'running|0|0|none|%s\n' "$started" ;;
      app)
        restarts=0
        [ "$FAGI_STUB_APP_RESTARTS" = "climbing" ] && restarts=$(bump app_restarts)
        printf 'running|0|%s|none|%s\n' "$restarts" "$started" ;;
      *) printf 'exited|0|0|none|%s\n' "$started" ;;
    esac
    exit 0 ;;
  image)
    case " $* " in
      *" --format "*) printf '%s\n' "$FAGI_STUB_IMAGE_ARCH" ;;
      *" $last_arg "*)
        case " $FAGI_STUB_MISSING_IMAGES " in *" $last_arg "*) exit 1 ;; esac ;;
    esac
    exit 0 ;;
  build)
    printf '%s\n' "$*" >> "$state/build.log"
    exit 0 ;;
  run)
    # Preflight probes: the Docker VM's size, then the bind-mount check.
    case " $* " in
      *"/probe:ro"*) [ "$FAGI_STUB_MOUNT" = "hidden" ] && exit 1 ;;
      *" free -m"*)
        set -- $FAGI_STUB_VM
        printf '              total        used        free\n'
        printf 'Mem:           %s         500        1000\n' "$1"
        printf 'nproc %s\narch %s\n' "$2" "$3" ;;
    esac
    exit 0 ;;
  context) printf '%s\n' "$FAGI_STUB_CONTEXT"; exit 0 ;;
  ps)
    # Services of the project's containers.
    case " $* " in
      *'.Label "com.docker.compose.service"'*)
        for service in $FAGI_STUB_SERVICES; do printf '%s\n' "$service"; done
        [ -n "$FAGI_STUB_APP_CONTAINER" ] && printf 'app\n' ;;
      *"service=clickhouse"*)
        [ -n "$FAGI_STUB_CLICKHOUSE_ENV" ] && printf 'cid-clickhouse\n' ;;
    esac
    exit 0 ;;
  manifest)
    [ "$FAGI_STUB_MANIFEST" = "missing" ] && exit 1
    exit 0 ;;
  volume)
    if [ "$2" = "inspect" ]; then
      case " $FAGI_STUB_VOLUMES " in *" $3 "*) exit 0 ;; esac
      exit 1
    fi
    if [ "$2" = "rm" ]; then
      shift 2
      printf '%s\n' "$*" >> "$state/volume_rm.log"
    fi
    exit 0 ;;
  *) exit 0 ;;
esac
"""

STUB_CURL = r"""#!/bin/bash
url=""
for arg in "$@"; do
  case "$arg" in http*) url="$arg" ;; esac
done
printf '%s\n' "$url" >> "$FAGI_STUB_STATE/curl.log"
case "$url" in
  *'/health/'*) ;;
  http://localhost:*/) [ "$FAGI_STUB_UI" = "ok" ] && exit 0; exit 1 ;;
  *) exit 1 ;;
esac
case "$FAGI_STUB_HEALTH" in
  ok) exit 0 ;;
  after:*)
    file="$FAGI_STUB_STATE/health_polls"
    polls=$(cat "$file" 2>/dev/null || echo 0)
    polls=$((polls + 1))
    printf '%s\n' "$polls" > "$file"
    [ "$polls" -gt "${FAGI_STUB_HEALTH#after:}" ] && exit 0
    exit 1 ;;
esac
exit 1
"""

# Advances a deterministic clock five seconds per call, so the readiness loop
# runs its real arithmetic without the wall-clock wait.
STUB_DATE = r"""#!/bin/bash
if [ "$#" = "1" ] && [ "$1" = "+%s" ]; then
  file="$FAGI_STUB_STATE/clock"
  ticks=$(cat "$file" 2>/dev/null || echo 0)
  ticks=$((ticks + 1))
  printf '%s\n' "$ticks" > "$file"
  printf '%s\n' "$((1767225600 + ticks * 5))"
  exit 0
fi
exec /bin/date "$@"
"""

STUB_SLEEP = "#!/bin/bash\nexit 0\n"

# Ports in $FAGI_STUB_BUSY_PORTS are held by a process outside the project.
STUB_LSOF = r"""#!/bin/bash
port=""
for arg in "$@"; do
  case "$arg" in -iTCP:*) port="${arg#-iTCP:}" ;; esac
done
case " $FAGI_STUB_BUSY_PORTS " in
  *" $port "*) printf 'COMMAND PID USER\nnode 4242 dev\n'; exit 0 ;;
esac
exit 1
"""

STUB_SCRIPTS = {
    "docker": STUB_DOCKER,
    "curl": STUB_CURL,
    "date": STUB_DATE,
    "sleep": STUB_SLEEP,
    "lsof": STUB_LSOF,
}


def _installer_sandbox(
    tmp_path: Path, **stub_env: str
) -> tuple[Path, dict[str, str], Path]:
    repo = tmp_path / "repo"
    (repo / "bin").mkdir(parents=True)
    script = repo / "bin" / "install"
    script.write_bytes(INSTALL_SH.read_bytes())
    script.chmod(0o755)
    shutil.copytree(INSTALL_LIB, repo / "bin" / "lib")
    (repo / ".env.example").write_text(SANDBOX_ENV_EXAMPLE, encoding="utf-8")
    # Only their presence is checked; the stub docker answers `compose config`.
    for compose in ("docker-compose.yml", "docker-compose.distributed.yml"):
        (repo / compose).write_text("services: {}\n", encoding="utf-8")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in STUB_SCRIPTS.items():
        stub = stubs / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)

    state = tmp_path / "state"
    state.mkdir()
    environment = {
        "PATH": f"{stubs}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(state),
        "FAGI_STUB_STATE": str(state),
        "FAGI_STUB_HEALTH": "ok",
        "FAGI_STUB_MIGRATIONS": "none",
        "FAGI_STUB_CREATE_USER": "ok",
        "FAGI_STUB_KAFKA_HEALTH": "healthy",
        "FAGI_STUB_UI": "ok",
        # MB of memory, CPUs and `uname -m` of the Docker VM.
        "FAGI_STUB_VM": "16000 8 aarch64",
        "FAGI_STUB_IMAGE_ARCH": "arm64",
        "FAGI_STUB_CONTEXT": "default",
        "FAGI_STUB_MOUNT": "visible",
        "FAGI_STUB_VOLUMES": "",
        "FAGI_STUB_MISSING_IMAGES": "",
        "FAGI_STUB_APP_CONTAINER": "",
        "FAGI_STUB_SERVICES": "",
        "FAGI_STUB_APP_RESTARTS": "none",
        "FAGI_STUB_PULL": "ok",
        "FAGI_STUB_MANIFEST": "found",
        "FAGI_STUB_BUSY_PORTS": "",
        # Its clickhouse container's environment, space-separated. Empty: this
        # project has no clickhouse container.
        "FAGI_STUB_CLICKHOUSE_ENV": "",
    }
    environment.update(stub_env)
    return script, environment, state


def _run_installer(
    script: Path,
    environment: dict[str, str],
    *args: str,
    typed: bytes | None = None,
    timeout: float = 180.0,
) -> tuple[int, str, str]:
    command = ["bash", str(script), *args]
    repo = script.parents[1]
    if typed is None:
        finished = subprocess.run(
            command,
            cwd=repo,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        return finished.returncode, finished.stdout, finished.stderr

    # A terminal on stdin only: the installer takes its interactive path while
    # stdout stays a pipe, so prompts are captured uncoloured and un-echoed.
    controller, terminal = pty.openpty()
    attributes = termios.tcgetattr(terminal)
    attributes[3] &= ~termios.ECHO
    termios.tcsetattr(terminal, termios.TCSANOW, attributes)
    process = subprocess.Popen(
        command,
        cwd=repo,
        env=environment,
        stdin=terminal,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    os.close(terminal)
    try:
        os.write(controller, typed)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
    finally:
        os.close(controller)
    return process.returncode, stdout, stderr


def _readiness_ticks(state: Path) -> int:
    return int((state / "clock").read_text(encoding="utf-8").strip())


def _guarded_management_command():
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    from model_hub.apps import guarded_management_command

    return guarded_management_command


def test_installer_gates_the_sign_in_banner_and_exit_code_on_the_account_outcome(
    tmp_path: Path,
) -> None:
    admin = {
        "CI": "1",
        "FAGI_ADMIN_EMAIL": "abhijai@futureagi.com",
        "FAGI_ADMIN_NAME": "Abhijai",
        "FAGI_ADMIN_PASSWORD": "correct-horse-battery",
    }
    runs = {}
    for outcome in ("ok", "exists", "fail"):
        script, environment, _ = _installer_sandbox(
            tmp_path / outcome, FAGI_STUB_CREATE_USER=outcome, **admin
        )
        runs[outcome] = _run_installer(script, environment)

    sign_in = (
        "  1. Sign in\n"
        "     http://localhost:3000/auth/jwt/login   as abhijai@futureagi.com\n"
    )
    for outcome in ("ok", "exists"):
        code, stdout, stderr = runs[outcome]
        assert code == 0, stderr
        assert sign_in in stdout
        assert "ACTION REQUIRED" not in stdout

    code, stdout, stderr = runs["fail"]
    assert code == 1
    assert "ACTION REQUIRED" in stdout
    assert "no account was created" in stdout
    assert "1. Sign in" not in stdout
    assert "as abhijai@futureagi.com" not in stdout
    assert "docker compose exec app python manage.py create_user" in stdout


def test_installer_rejects_a_control_character_in_the_typed_email_before_create_user(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(tmp_path)
    typed = (
        b"\x1b[Zabhijai@futureagi.com\n"
        b"not-an-email\n"
        b"abhijai+test123@futureagi.com\n"
        b"Abhijai\n"
        b"correct-horse-battery\n"
        b"correct-horse-battery\n"
    )

    code, stdout, stderr = _run_installer(script, environment, typed=typed, timeout=120)

    assert "'[Zabhijai@futureagi.com' doesn't look like an email address" in stderr
    assert "'not-an-email' doesn't look like an email address" in stderr
    assert "\x1b" not in stdout + stderr
    assert code == 0, stderr
    assert (
        "  1. Sign in\n"
        "     http://localhost:3000/auth/jwt/login   as abhijai+test123@futureagi.com\n"
    ) in stdout
    argv = (state / "create_user.argv").read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--email") + 1] == "abhijai+test123@futureagi.com"


ADMIN_PASSWORD = "correct-horse-battery"


@pytest.mark.parametrize("typed", [False, True], ids=["FAGI_ADMIN_PASSWORD", "typed"])
def test_the_admin_password_reaches_create_user_on_stdin_only(
    tmp_path: Path, typed: bool
) -> None:
    """In Standalone create_user runs in the app container, where eval code
    can read every process's command line."""
    if typed:
        script, environment, state = _installer_sandbox(tmp_path)
        answers = ["abhijai@futureagi.com", "Abhijai", ADMIN_PASSWORD, ADMIN_PASSWORD]
        code, _, stderr = _run_installer(
            script, environment, typed="\n".join(answers + [""]).encode(), timeout=120
        )
    else:
        script, environment, state = _installer_sandbox(
            tmp_path,
            CI="1",
            FAGI_ADMIN_EMAIL="abhijai@futureagi.com",
            FAGI_ADMIN_NAME="Abhijai",
            FAGI_ADMIN_PASSWORD=ADMIN_PASSWORD,
        )
        code, _, stderr = _run_installer(script, environment)

    assert code == 0, stderr
    argv = _lines(state / "create_user.argv")
    assert "create_user" in argv
    assert "--password" not in argv
    assert not [arg for arg in argv if ADMIN_PASSWORD in arg]
    assert _read(state / "create_user.stdin") == ADMIN_PASSWORD + "\n"


def _power_shell_section(text: str, header: str) -> str:
    """install.ps1 from a `# ---- <header> ----` line to the next one."""
    start = text.index(f"# ---- {header} ----")
    return text[start : text.index("\n# ---- ", start + 1)]


def test_power_shell_installer_pipes_the_admin_password_to_create_user(
    tmp_path: Path,
) -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")
    stub = tmp_path / "docker"
    stub.write_text(
        '#!/bin/bash\nprintf \'%s\\n\' "$@" > "$FAGI_STUB_STATE/argv"\n'
        'cat > "$FAGI_STUB_STATE/stdin"\n'
        "echo 'Warning: Password input may be echoed.' >&2\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$DcCmd = 'docker'; $DcArgs = @('compose'); $AppService = 'app'",
            "function Invoke-Compose { & $DcCmd @DcArgs @args }",
            "$UserEmail = 'abhijai@futureagi.com'; $UserName = 'Abhijai'",
            f"$UserPass = '{ADMIN_PASSWORD}'",
            "$AccountCreated = 'created'; $AccountExists = 'exists'",
            "$AccountFailed = 'failed'; $AccountState = 'skipped'",
            "function Get-ComposeEnvValue { param($n, $default) $default }",
            "function Step { param($m) }",
            "function Ok { param($m) Write-Output $m }",
            "function Warn { param($m) Write-Output $m }",
            "function Say { param($m) }",
            _power_shell_section(_read(INSTALL_PS1), "create user"),
        ]
    )

    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "FAGI_STUB_STATE": str(tmp_path),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "Account created for abhijai@futureagi.com" in result.stdout
    argv = _lines(tmp_path / "argv")
    assert "--password" not in argv
    assert not [arg for arg in argv if ADMIN_PASSWORD in arg]
    assert _read(tmp_path / "stdin").rstrip("\r\n") == ADMIN_PASSWORD


def test_installer_extends_the_readiness_window_while_migrations_are_applying(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_HEALTH="after:20", FAGI_STUB_MIGRATIONS="climbing"
    )

    code, stdout, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 0, stderr
    applied = [
        int(match)
        for match in re.findall(r"migrations in progress \((\d+) applied\)", stdout)
    ]
    assert applied == sorted(applied)
    assert len(applied) >= 5
    assert applied[-1] > applied[0]
    assert "Backend healthy at http://localhost:8000" in stdout
    assert "did not become fully ready" not in stdout + stderr
    assert _readiness_ticks(state) >= 20


def test_installer_gives_up_at_the_base_timeout_when_the_backend_stalls(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_HEALTH="down", FAGI_STUB_MIGRATIONS="none"
    )

    code, stdout, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 1
    assert "still waiting on backend /health/ on port 8000" in stderr
    assert "extending the readiness window" not in stdout
    assert _readiness_ticks(state) <= 15


def test_installer_refuses_to_extend_the_window_for_an_unready_peer_service(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_STUB_HEALTH="ok",
        FAGI_STUB_MIGRATIONS="climbing",
        FAGI_STUB_KAFKA_HEALTH="starting",
    )

    code, stdout, stderr = _run_installer(
        script, environment, "--distributed", "--skip-user-creation"
    )

    assert code == 1
    assert "still waiting on property-catalog-kafka to report healthy" in stderr
    assert "backend /health/" not in stderr
    assert "extending the readiness window" not in stdout
    assert _readiness_ticks(state) <= 15


def test_installer_stops_extending_at_the_absolute_readiness_ceiling(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_HEALTH="down", FAGI_STUB_MIGRATIONS="climbing"
    )

    code, stdout, stderr = _run_installer(
        script, environment, "--skip-user-creation", timeout=300
    )

    assert code == 1
    assert "still waiting on backend /health/ on port 8000" in stderr
    assert "migrations in progress (50 applied)" in stdout
    assert 40 <= _readiness_ticks(state) <= 70


def _env_values(repo: Path) -> dict[str, str]:
    values = {}
    for line in (repo / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_a_standalone_install_runs_the_single_app_stack_without_recording_a_mode(
    tmp_path: Path,
) -> None:
    admin = {
        "CI": "1",
        "FAGI_ADMIN_EMAIL": "abhijai@futureagi.com",
        "FAGI_ADMIN_NAME": "Abhijai",
        "FAGI_ADMIN_PASSWORD": "correct-horse-battery",
    }
    script, environment, state = _installer_sandbox(tmp_path, **admin)

    code, stdout, stderr = _run_installer(script, environment)

    assert code == 0, stderr
    assert "Mode: Standalone (docker-compose.yml)" in stdout
    # Unset COMPOSE_FILE keeps docker-compose.override.yml auto-loading.
    assert "COMPOSE_FILE" not in _env_values(script.parents[1])
    # Every published image is pulled, so a failed pull never becomes a build.
    assert _lines(state / "pull.argv") == ["pull", "app", "clickhouse", "postgres"]
    assert not (state / "build.log").exists()
    # A short telemetry timeout: create_user waits for the registration.
    assert _lines(state / "create_user.argv")[:5] == [
        "exec",
        "-T",
        "-e",
        "FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS=2",
        "app",
    ]
    assert "App container stable" in stdout
    assert "Existing-data catalog backfill" not in stdout


def test_distributed_is_recorded_in_env_and_later_runs_stay_on_it(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(tmp_path, CI="1")
    repo = script.parents[1]
    (repo / "docker-compose.override.yml").write_text("services: {}\n")

    code, stdout, stderr = _run_installer(
        script, environment, "--distributed", "--skip-user-creation"
    )
    assert code == 0, stderr
    assert _env_values(repo)["COMPOSE_FILE"] == (
        "docker-compose.distributed.yml:docker-compose.override.yml"
    )
    # Only the standalone install's in-container Redis takes a password.
    assert "REDIS_PASSWORD" not in _env_values(repo)
    # fi-collector:local is built by `up --build`, never pulled.
    assert "fi-collector" not in _lines(state / "pull.argv")
    assert "backend" in _lines(state / "pull.argv")
    assert "Existing-data catalog backfill" in stdout

    code, stdout, stderr = _run_installer(script, environment, "--skip-user-creation")
    assert code == 0, stderr
    assert "Mode: Distributed" in stdout
    assert "Kafka healthy" in stdout


@pytest.mark.parametrize(
    "signal",
    [
        {"FAGI_STUB_VOLUMES": "futureagi_postgres-data futureagi_minio-data"},
        {"FAGI_STUB_VOLUMES": "futureagi_postgres-data futureagi_rabbitmq-data"},
        {"FAGI_STUB_SERVICES": "postgres worker"},
    ],
)
def test_an_existing_distributed_install_is_never_moved_to_standalone_silently(
    tmp_path: Path, signal: dict[str, str]
) -> None:
    script, environment, _ = _installer_sandbox(tmp_path, CI="1", **signal)

    code, stdout, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    # No ClickHouse container runs here: that is not a failure.
    assert "install failed" not in stdout + stderr
    assert "staying on the distributed stack" in stderr
    assert "Recorded COMPOSE_FILE=docker-compose.distributed.yml in .env" in stderr
    assert (
        _env_values(script.parents[1])["COMPOSE_FILE"]
        == "docker-compose.distributed.yml"
    )


def test_a_legacy_compose_file_setting_is_pointed_at_the_distributed_stack(
    tmp_path: Path,
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_STUB_VOLUMES="futureagi_postgres-data futureagi_peerdb-catalog-data",
    )
    repo = script.parents[1]
    (repo / ".env").write_text(
        SANDBOX_ENV_EXAMPLE
        + "COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml\n",
        encoding="utf-8",
    )

    code, _, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    assert "staying on the distributed stack" in stderr
    assert _env_values(repo)["COMPOSE_FILE"] == (
        "docker-compose.distributed.yml:docker-compose.override.yml"
    )


def test_wipe_volumes_resets_an_older_distributed_install_as_distributed(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_STUB_VOLUMES="futureagi_postgres-data futureagi_rabbitmq-data",
    )

    code, _, stderr = _run_installer(script, environment, "--wipe-volumes", "--no-up")

    assert code == 0, stderr
    values = _env_values(script.parents[1])
    assert values["COMPOSE_FILE"] == "docker-compose.distributed.yml"
    assert "--wipe-volumes resets it as a distributed stack" in stderr
    assert _lines(state / "compose_down.log") == ["down --remove-orphans"]
    assert _lines(state / "volume_rm.log") == [
        "futureagi_postgres-data futureagi_rabbitmq-data"
    ]
    # The wiped install starts over with secrets of its own.
    assert re.fullmatch(r"[0-9a-f]{64}", values["PG_PASSWORD"])


@pytest.mark.parametrize(
    "standalone_install",
    [
        {"FAGI_STUB_APP_CONTAINER": "1"},
        {"FAGI_STUB_VOLUMES": "futureagi_app-data futureagi_postgres-data"},
    ],
)
def test_distributed_refuses_to_move_an_existing_standalone_install(
    tmp_path: Path, standalone_install: dict[str, str]
) -> None:
    script, environment, _ = _installer_sandbox(tmp_path, CI="1", **standalone_install)
    repo = script.parents[1]

    # --force does not reach past it: the data would be stranded.
    code, _, stderr = _run_installer(
        script, environment, "--distributed", "--force", "--no-up"
    )

    assert code == 1
    assert "already holds a standalone install" in stderr
    assert "./bin/uninstall --wipe-data and ./bin/install --distributed" in stderr
    assert "keeps your data" not in stderr
    # A refused switch leaves .env as it was.
    assert "COMPOSE_FILE" not in _env_values(repo)

    code, _, stderr = _run_installer(script, environment, "--no-up")
    assert code == 0, stderr
    assert "COMPOSE_FILE" not in _env_values(repo)


def test_a_mode_recorded_by_mistake_names_the_line_to_delete(tmp_path: Path) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_VOLUMES="futureagi_app-data futureagi_postgres-data"
    )
    (script.parents[1] / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + "COMPOSE_FILE=docker-compose.distributed.yml\n",
        encoding="utf-8",
    )

    code, _, stderr = _run_installer(script, environment, "--no-up")

    assert code == 1
    assert "already holds a standalone install" in stderr
    assert "delete the COMPOSE_FILE line from .env" in stderr


def test_distributed_with_wipe_volumes_replaces_a_standalone_install(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_STUB_APP_CONTAINER="1",
        FAGI_STUB_VOLUMES="futureagi_app-data futureagi_postgres-data",
    )

    code, _, stderr = _run_installer(
        script, environment, "--distributed", "--wipe-volumes", "--no-up"
    )

    assert code == 0, stderr
    assert (
        _env_values(script.parents[1])["COMPOSE_FILE"]
        == "docker-compose.distributed.yml"
    )
    assert _lines(state / "volume_rm.log") == [
        "futureagi_app-data futureagi_postgres-data"
    ]


def test_a_stray_standalone_app_next_to_an_older_distributed_install_is_named(
    tmp_path: Path,
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_STUB_APP_CONTAINER="1",
        FAGI_STUB_VOLUMES="futureagi_postgres-data futureagi_minio-data",
    )

    code, _, stderr = _run_installer(script, environment, "--no-up")

    assert code == 1
    assert "docker compose -f docker-compose.yml -p futureagi rm -sf app" in stderr
    # The data belongs to the distributed stack, so .env points plain compose at it.
    assert (
        _env_values(script.parents[1])["COMPOSE_FILE"]
        == "docker-compose.distributed.yml"
    )


DISTRIBUTED_VOLUMES = {
    "FAGI_STUB_VOLUMES": "futureagi_postgres-data futureagi_minio-data"
}
STANDALONE_VOLUMES = {"FAGI_STUB_VOLUMES": "futureagi_app-data futureagi_postgres-data"}


@pytest.mark.parametrize(
    ("shell_compose_file", "args", "project", "env_line", "hint"),
    [
        pytest.param(
            "docker-compose.yml",
            (),
            DISTRIBUTED_VOLUMES,
            "",
            "set COMPOSE_FILE=docker-compose.distributed.yml instead",
            id="existing-distributed-install",
        ),
        pytest.param(
            "docker-compose.yml:docker-compose.override.yml",
            ("--distributed",),
            {},
            "",
            "set COMPOSE_FILE=docker-compose.distributed.yml:docker-compose.override.yml instead",
            id="distributed-requested",
        ),
        pytest.param(
            "docker-compose.yml",
            (),
            {},
            "COMPOSE_FILE=docker-compose.distributed.yml\n",
            "set COMPOSE_FILE=docker-compose.distributed.yml instead",
            id="distributed-recorded",
        ),
        pytest.param(
            "docker-compose.distributed.yml",
            (),
            STANDALONE_VOLUMES,
            "",
            None,
            id="existing-standalone-install",
        ),
        pytest.param(
            "docker-compose.distributed.yml",
            (),
            {},
            "",
            "For the distributed stack, run ./bin/install --distributed.",
            id="fresh-project",
        ),
        pytest.param(
            "docker-compose.yml",
            ("--wipe-volumes",),
            DISTRIBUTED_VOLUMES,
            "",
            "set COMPOSE_FILE=docker-compose.distributed.yml instead",
            id="wipe-volumes",
        ),
        # Both stacks merged: Standalone's app next to the Distributed services.
        pytest.param(
            "docker-compose.yml:docker-compose.distributed.yml",
            (),
            DISTRIBUTED_VOLUMES,
            "",
            None,
            id="both-stack-files",
        ),
        pytest.param(
            "docker-compose.override.yml", (), {}, "", None, id="no-stack-file"
        ),
        # Set but empty, Compose reads no file at all.
        pytest.param("", (), {}, "", None, id="empty"),
    ],
)
def test_a_compose_file_in_the_shell_for_another_stack_stops_before_anything_changes(
    tmp_path: Path,
    shell_compose_file: str,
    args: tuple[str, ...],
    project: dict[str, str],
    env_line: str,
    hint: str | None,
) -> None:
    """Compose reads COMPOSE_FILE from the shell over .env, so the installer
    would report and record one stack while its compose commands ran another."""
    script, environment, state = _installer_sandbox(
        tmp_path, CI="1", COMPOSE_FILE=shell_compose_file, **project
    )
    repo = script.parents[1]
    env_before = SANDBOX_ENV_EXAMPLE + env_line
    (repo / ".env").write_text(env_before, encoding="utf-8")

    code, stdout, stderr = _run_installer(
        script, environment, "--skip-user-creation", *args
    )

    assert code == 1
    assert f"COMPOSE_FILE={shell_compose_file} is set in your shell" in stderr
    assert "unset COMPOSE_FILE" in stderr
    # .env does not record --distributed yet, so the re-run has to repeat it.
    rerun = (
        "./bin/install --distributed" if "--distributed" in args else "./bin/install"
    )
    assert f"then re-run {rerun}." in stderr
    if hint:
        assert hint in stderr
    else:
        assert "instead." not in stderr
        assert "--distributed." not in stderr
    assert "Mode:" not in stdout
    # Nothing recorded, generated, wiped, pulled or started.
    assert _read(repo / ".env") == env_before
    assert not (state / "compose_down.log").exists()
    assert not (state / "volume_rm.log").exists()
    assert not (state / "pull.argv").exists()
    assert not (state / "compose_file_at_up").exists()


@pytest.mark.parametrize(
    ("args", "env_line"),
    [
        pytest.param(("--distributed",), "", id="distributed-requested"),
        pytest.param(
            (),
            "COMPOSE_FILE=docker-compose.distributed.yml\n",
            id="distributed-recorded",
        ),
    ],
)
def test_a_standalone_install_is_refused_distributed_before_the_shell_is_checked(
    tmp_path: Path, args: tuple[str, ...], env_line: str
) -> None:
    """Unsetting the shell's COMPOSE_FILE would not get past this refusal, and
    the shell's docker-compose.yml is the file that matches the data."""
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", COMPOSE_FILE="docker-compose.yml", **STANDALONE_VOLUMES
    )
    repo = script.parents[1]
    env_before = SANDBOX_ENV_EXAMPLE + env_line
    (repo / ".env").write_text(env_before, encoding="utf-8")

    code, _, stderr = _run_installer(script, environment, "--no-up", *args)

    assert code == 1
    assert "already holds a standalone install" in stderr
    assert ("delete the COMPOSE_FILE line from .env" in stderr) == bool(env_line)
    assert "is set in your shell" not in stderr
    assert _read(repo / ".env") == env_before


@pytest.mark.parametrize(
    ("shell_compose_file", "project", "mode", "recorded"),
    [
        pytest.param(
            "docker-compose.distributed.yml:docker-compose.override.yml",
            DISTRIBUTED_VOLUMES,
            "Mode: Distributed",
            "docker-compose.distributed.yml",
            id="distributed",
        ),
        pytest.param(
            "docker-compose.yml:docker-compose.override.yml",
            {},
            "Mode: Standalone",
            None,
            id="standalone",
        ),
    ],
)
def test_a_compose_file_in_the_shell_for_the_same_stack_keeps_its_other_files(
    tmp_path: Path,
    shell_compose_file: str,
    project: dict[str, str],
    mode: str,
    recorded: str | None,
) -> None:
    script, environment, state = _installer_sandbox(
        tmp_path, CI="1", COMPOSE_FILE=shell_compose_file, **project
    )

    code, stdout, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 0, stderr
    assert mode in stdout
    assert f"Compose uses COMPOSE_FILE={shell_compose_file} from your shell" in stdout
    # The stack starts from the shell's files, and .env still records the
    # stack for a shell without them.
    assert _lines(state / "compose_file_at_up") == [shell_compose_file]
    assert _env_values(script.parents[1]).get("COMPOSE_FILE") == recorded


def _power_shell_mode(
    tmp_path: Path, shell_compose_file: str, project: dict[str, str], distributed: bool
) -> subprocess.CompletedProcess[str]:
    """install.ps1's project-state and mode sections under pwsh, against a
    docker that knows only the project's volumes."""
    stub = tmp_path / "docker"
    stub.write_text(
        '#!/bin/bash\n[ "$1 $2" = "volume inspect" ] || exit 0\n'
        'case " $FAGI_STUB_VOLUMES " in *" $3 "*) exit 0 ;; esac\nexit 1\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    for compose in ("docker-compose.yml", "docker-compose.distributed.yml"):
        (tmp_path / compose).write_text("services: {}\n", encoding="utf-8")
    (tmp_path / ".env").write_text(SANDBOX_ENV_EXAMPLE, encoding="utf-8")
    installer = _read(INSTALL_PS1)
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'; Set-StrictMode -Version 3.0",
            f". '{INSTALL_LIB / 'env.ps1'}'; . '{INSTALL_LIB / 'secrets.ps1'}'",
            "function Invoke-Probe { param($Command) & $Command }",
            "function Die { param($m) Write-Output $m; exit 1 }",
            "function Warn { param($m) Write-Output $m }",
            "function Ok { param($m) Write-Output $m }",
            f"$Distributed = ${str(distributed).lower()}; $WipeVolumes = $false",
            _power_shell_section(installer, "this project's existing state"),
            _power_shell_section(installer, "mode"),
        ]
    )
    return subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "COMPOSE_FILE": shell_compose_file,
            **project,
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    ("shell_compose_file", "project", "distributed", "code", "output"),
    [
        pytest.param(
            "docker-compose.yml",
            DISTRIBUTED_VOLUMES,
            False,
            1,
            r"then re-run .\bin\install.ps1. To keep your other compose files, "
            "set COMPOSE_FILE=docker-compose.distributed.yml instead.",
            id="existing-distributed-install",
        ),
        pytest.param(
            "docker-compose.yml;docker-compose.override.yml",
            {},
            True,
            1,
            r"then re-run .\bin\install.ps1 -Distributed. To keep your other compose "
            "files, set COMPOSE_FILE=docker-compose.distributed.yml;"
            "docker-compose.override.yml instead.",
            id="distributed-requested",
        ),
        pytest.param(
            "docker-compose.distributed.yml",
            STANDALONE_VOLUMES,
            False,
            1,
            "chose the standalone stack",
            id="existing-standalone-install",
        ),
        pytest.param(
            "docker-compose.yml;docker-compose.distributed.yml",
            DISTRIBUTED_VOLUMES,
            False,
            1,
            "chose the distributed stack",
            id="both-stack-files",
        ),
        pytest.param("", {}, False, 1, "chose the standalone stack", id="empty"),
        pytest.param(
            "docker-compose.distributed.yml;docker-compose.override.yml",
            DISTRIBUTED_VOLUMES,
            False,
            0,
            "Mode: Distributed (COMPOSE_FILE=docker-compose.distributed.yml)",
            id="same-stack",
        ),
    ],
)
def test_power_shell_installer_checks_a_compose_file_in_the_shell(
    tmp_path: Path,
    shell_compose_file: str,
    project: dict[str, str],
    distributed: bool,
    code: int,
    output: str,
) -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")

    result = _power_shell_mode(tmp_path, shell_compose_file, project, distributed)

    assert result.returncode == code, result.stdout + result.stderr
    assert output in result.stdout
    if code:
        assert (
            f"COMPOSE_FILE={shell_compose_file} is set in your shell" in result.stdout
        )
        assert _read(tmp_path / ".env") == SANDBOX_ENV_EXAMPLE
    else:
        assert "from your shell, which selects the same stack" in result.stdout
        assert _env_values(tmp_path)["COMPOSE_FILE"] == "docker-compose.distributed.yml"


def test_power_shell_installer_refuses_a_standalone_install_distributed_before_the_shell_check(
    tmp_path: Path,
) -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")

    result = _power_shell_mode(
        tmp_path, "docker-compose.yml", STANDALONE_VOLUMES, distributed=True
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "already holds a standalone install" in result.stdout
    assert "is set in your shell" not in result.stdout
    assert _read(tmp_path / ".env") == SANDBOX_ENV_EXAMPLE


SECRET_KEYS = (
    "SECRET_KEY",
    "PG_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "AGENTCC_INTERNAL_API_KEY",
    "AGENTCC_ADMIN_TOKEN",
    "CH_PASSWORD",
    "REDIS_PASSWORD",
)


def test_a_fresh_install_generates_every_secret_and_a_rerun_keeps_them(
    tmp_path: Path,
) -> None:
    script, environment, _ = _installer_sandbox(tmp_path, CI="1")
    repo = script.parents[1]
    # The shipped example carries a placeholder and an empty gateway key.
    (repo / ".env.example").write_text(
        SANDBOX_ENV_EXAMPLE
        + "MINIO_ROOT_PASSWORD=CHANGEME-set-by-bin-install\n"
        + "AGENTCC_INTERNAL_API_KEY=\n",
        encoding="utf-8",
    )

    code, stdout, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    first = _env_values(repo)
    for key in SECRET_KEYS:
        # Hex: safe inside the Redis and Postgres URLs built from them.
        assert re.fullmatch(r"[0-9a-f]{64}", first[key]), key
    assert len({first[key] for key in SECRET_KEYS}) == len(SECRET_KEYS)
    fernet_key = first["INTEGRATION_ENCRYPTION_KEY"]
    assert len(base64.urlsafe_b64decode(fernet_key)) == 32
    assert re.fullmatch(r"[0-9a-f]{64}", first["AGENTCC_WEBHOOK_SECRET"])
    assert "Generated SECRET_KEY" in stdout

    code, stdout, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    assert _env_values(repo) == first
    assert "Generated" not in stdout


def test_an_existing_install_keeps_its_secrets_and_fills_only_stateless_keys(
    tmp_path: Path,
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_STUB_VOLUMES="futureagi_app-data futureagi_postgres-data",
    )
    repo = script.parents[1]
    (repo / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + "SECRET_KEY=kept-secret\nAGENTCC_ADMIN_TOKEN=\n",
        encoding="utf-8",
    )

    code, stdout, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    values = _env_values(repo)
    assert values["SECRET_KEY"] == "kept-secret"
    # Postgres keeps its first password in the volume, and the ClickHouse
    # this install runs has none: never generated here.
    for key in (
        "PG_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "AGENTCC_INTERNAL_API_KEY",
        "CH_PASSWORD",
    ):
        assert key not in values
    assert values["AGENTCC_ADMIN_TOKEN"] == ""
    assert (
        "PG_PASSWORD MINIO_ROOT_PASSWORD AGENTCC_INTERNAL_API_KEY AGENTCC_ADMIN_TOKEN "
        "CH_PASSWORD still use the defaults published in this repository"
    ) in stderr
    # No state depends on these three, so an existing install gets them too.
    assert re.fullmatch(r"[0-9a-f]{64}", values["REDIS_PASSWORD"])
    assert len(base64.urlsafe_b64decode(values["INTEGRATION_ENCRYPTION_KEY"])) == 32
    # Without it the app refuses the gateway's request logs.
    assert re.fullmatch(r"[0-9a-f]{64}", values["AGENTCC_WEBHOOK_SECRET"])


CLICKHOUSE_BEFORE_CH_PASSWORD = (
    "PATH=/usr/bin CLICKHOUSE_SKIP_USER_SETUP=1 CLICKHOUSE_DB=default"
)
RUNNING_PASSWORD = "ch-running-4f2a"
DOT_ENV_PASSWORD = "ch-dot-env-9b1c"
SHELL_PASSWORD = "ch-shell-7d3e"


def _clickhouse_running(password: str) -> str:
    return f"{CLICKHOUSE_BEFORE_CH_PASSWORD} CLICKHOUSE_PASSWORD={password}"


# The clickhouse container's environment ("": no container), CH_PASSWORD in
# .env, the one exported in the shell, and whether the installer refuses.
CLICKHOUSE_PASSWORD_CASES = [
    pytest.param(
        CLICKHOUSE_BEFORE_CH_PASSWORD,
        DOT_ENV_PASSWORD,
        None,
        True,
        id="release-ignored-it",
    ),
    pytest.param(
        _clickhouse_running(""), DOT_ENV_PASSWORD, None, True, id="recreated-empty"
    ),
    pytest.param(
        _clickhouse_running(RUNNING_PASSWORD),
        DOT_ENV_PASSWORD,
        None,
        True,
        id="rotated",
    ),
    pytest.param(_clickhouse_running(RUNNING_PASSWORD), "", None, True, id="lost"),
    pytest.param(
        _clickhouse_running(RUNNING_PASSWORD),
        RUNNING_PASSWORD.upper(),
        None,
        True,
        id="differs-in-case",
    ),
    pytest.param(
        _clickhouse_running(DOT_ENV_PASSWORD), DOT_ENV_PASSWORD, None, False, id="same"
    ),
    pytest.param(CLICKHOUSE_BEFORE_CH_PASSWORD, "", None, False, id="both-empty"),
    # Compose takes the exported one over .env's.
    pytest.param(
        _clickhouse_running(SHELL_PASSWORD),
        DOT_ENV_PASSWORD,
        SHELL_PASSWORD,
        False,
        id="shell-same",
    ),
    pytest.param(
        _clickhouse_running(DOT_ENV_PASSWORD),
        DOT_ENV_PASSWORD,
        SHELL_PASSWORD,
        True,
        id="shell-differs",
    ),
    # No container to tell by (after `docker compose down`).
    pytest.param("", DOT_ENV_PASSWORD, None, False, id="no-container"),
]
CLICKHOUSE_PASSWORD_REFUSAL = (
    "Set CH_PASSWORD back to the password ClickHouse runs with"
)


def _no_password_in(output: str) -> None:
    for password in (RUNNING_PASSWORD, DOT_ENV_PASSWORD, SHELL_PASSWORD):
        assert password not in output
        assert password.upper() not in output


@pytest.mark.parametrize(
    ("container_env", "dot_env_password", "shell_password", "refused"),
    CLICKHOUSE_PASSWORD_CASES,
)
def test_a_clickhouse_password_never_changes_an_existing_install_silently(
    tmp_path: Path,
    container_env: str,
    dot_env_password: str,
    shell_password: str | None,
    refused: bool,
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_STUB_VOLUMES="futureagi_app-data futureagi_postgres-data",
        FAGI_STUB_CLICKHOUSE_ENV=container_env,
    )
    if shell_password is not None:
        environment["CH_PASSWORD"] = shell_password
    repo = script.parents[1]
    (repo / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + f"CH_PASSWORD={dot_env_password}\n", encoding="utf-8"
    )

    code, stdout, stderr = _run_installer(script, environment, "--no-up")

    assert code == (1 if refused else 0), stderr
    assert (CLICKHOUSE_PASSWORD_REFUSAL in stderr) == refused
    _no_password_in(stdout + stderr)
    assert _env_values(repo)["CH_PASSWORD"] == dot_env_password
    if refused:
        code, _, stderr = _run_installer(script, environment, "--force", "--no-up")
        assert code == 0, stderr
        assert "(continuing: --force)" in stderr


def _power_shell_write_secrets(
    directory: Path, fresh: bool, container_env: str, shell_password: str | None = None
) -> subprocess.CompletedProcess[str]:
    script = "\n".join(
        [
            f". '{INSTALL_LIB / 'env.ps1'}'; . '{INSTALL_LIB / 'secrets.ps1'}'",
            "function Ok { param($m) Write-Output $m }",
            "function Warn { param($m) Write-Output $m }",
            'function Fail-Preflight { param($m) Write-Output "PREFLIGHT $m" }',
            "function Invoke-Probe { param($Command) & $Command }",
            f"$containerEnv = '{container_env}'",
            "function docker {",
            "  if ($args[0] -eq 'ps' -and $containerEnv) { 'cid-clickhouse' }",
            "  elseif ($args[0] -eq 'inspect') { $containerEnv -split ' ' }",
            "}",
            f"Write-Secrets ${str(fresh).lower()} $false 'futureagi'",
        ]
    )
    environment = {k: v for k, v in os.environ.items() if k != "CH_PASSWORD"}
    if shell_password is not None:
        environment["CH_PASSWORD"] = shell_password
    return subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        cwd=directory,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    ("fresh", "dot_env", "expected"),
    [
        (True, "", "Generated CH_PASSWORD"),
        (False, "", "CH_PASSWORD still use the defaults published"),
    ],
)
def test_power_shell_installer_writes_the_same_secrets(
    tmp_path: Path, fresh: bool, dot_env: str, expected: str
) -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")
    (tmp_path / ".env").write_text(dot_env, encoding="utf-8")

    result = _power_shell_write_secrets(tmp_path, fresh, "")

    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    values = _env_values(tmp_path)
    if fresh:
        # The keys bin/lib/secrets.sh generates, in the same format.
        for key in SECRET_KEYS:
            assert re.fullmatch(r"[0-9a-f]{64}", values[key]), key
    else:
        assert values.get("CH_PASSWORD", "") == ""


@pytest.mark.parametrize(
    ("container_env", "dot_env_password", "shell_password", "refused"),
    CLICKHOUSE_PASSWORD_CASES,
)
def test_power_shell_installer_compares_the_clickhouse_password_the_same_way(
    tmp_path: Path,
    container_env: str,
    dot_env_password: str,
    shell_password: str | None,
    refused: bool,
) -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")
    (tmp_path / ".env").write_text(
        f"CH_PASSWORD={dot_env_password}\n", encoding="utf-8"
    )

    result = _power_shell_write_secrets(tmp_path, False, container_env, shell_password)

    assert result.returncode == 0, result.stderr
    assert ("PREFLIGHT CH_PASSWORD differs" in result.stdout) == refused
    assert (CLICKHOUSE_PASSWORD_REFUSAL in result.stdout) == refused
    _no_password_in(result.stdout + result.stderr)
    assert _env_values(tmp_path)["CH_PASSWORD"] == dot_env_password


@pytest.mark.parametrize(
    ("args", "expected", "absent"),
    [
        (
            (),
            {
                "FRONTEND_PORT",
                "BACKEND_PORT",
                "FI_COLLECTOR_OTLP_PORT",
                "FI_COLLECTOR_OTLP_HTTP_PORT",
                "AGENTCC_GATEWAY_PORT",
                "MINIO_API_PORT",
            },
            {"PG_PORT", "REDIS_PORT", "TEMPORAL_PORT", "FI_COLLECTOR_ADMIN_PORT"},
        ),
        (
            ("--distributed",),
            {
                "PG_PORT",
                "PROPERTY_CATALOG_KAFKA_PORT",
                "FI_COLLECTOR_OTLP_PORT",
                "FI_COLLECTOR_OTLP_HTTP_PORT",
                "FI_COLLECTOR_ADMIN_PORT",
                "PEERDB_PORT",
            },
            {"PEERDB_UI_PORT"},
        ),
    ],
)
def test_installer_checks_the_host_ports_of_the_chosen_stack(
    tmp_path: Path, args: tuple[str, ...], expected: set[str], absent: set[str]
) -> None:
    script, environment, _ = _installer_sandbox(tmp_path, CI="1")

    code, stdout, stderr = _run_installer(script, environment, *args, "--no-up")

    assert code == 0, stderr
    checked = set(re.findall(r"✓ +([A-Z_]+)=\d+ +free", stdout))
    assert expected <= checked
    assert not absent & checked
    if not args:
        assert checked == expected


def _published_ports(compose: Path, profile: str) -> set[str]:
    """VAR:DEFAULT of each host port the compose file publishes with only
    `profile` in COMPOSE_PROFILES ("": none)."""
    published = set()
    for service in yaml.safe_load(_read(compose))["services"].values():
        if service.get("profiles") and profile not in service["profiles"]:
            continue
        for mapping in service.get("ports", []):
            found = re.fullmatch(
                r"(?:127\.0\.0\.1:)?\$\{([A-Z_]+):-(\d+)\}:\d+", mapping
            )
            assert found, (
                f"{compose.name}: a port the installers cannot check: {mapping}"
            )
            published.add(f"{found[1]}:{found[2]}")
    return published


def _setups_and_profiles() -> list[tuple[Path, bool, str]]:
    return [
        (compose, compose == COMPOSE_FILE, profile)
        for compose in (STANDALONE_COMPOSE_FILE, COMPOSE_FILE)
        for profile in [
            "",
            *sorted(
                {
                    name
                    for service in yaml.safe_load(_read(compose))["services"].values()
                    for name in service.get("profiles", [])
                }
            ),
        ]
    ]


@pytest.mark.parametrize(
    ("compose", "distributed", "profile"),
    _setups_and_profiles(),
    ids=lambda value: value.name if isinstance(value, Path) else str(value),
)
def test_the_installers_check_every_port_the_compose_file_publishes(
    compose: Path, distributed: bool, profile: str
) -> None:
    expected = _published_ports(compose, profile)
    assert expected

    shell = subprocess.run(
        ["bash", "-c", '. bin/lib/ports.sh; stack_ports "$1" "$2"', "-"]
        + [str(int(distributed)), profile],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(shell.stdout.split()) == expected

    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")
    powershell = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-Command",
            f". '{INSTALL_LIB / 'ports.ps1'}'; "
            f"$ports = Get-StackPorts ${str(distributed).lower()} @('{profile}'); "
            '$ports.Keys | ForEach-Object { "${_}:$($ports[$_])" }',
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert powershell.returncode == 0, powershell.stderr
    assert set(powershell.stdout.split()) == expected


def test_from_source_builds_every_image_in_order_and_never_pulls_them(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(tmp_path, CI="1")

    code, stdout, stderr = _run_installer(
        script, environment, "--from-source", "--skip-user-creation"
    )

    assert code == 0, stderr
    builds = _lines(state / "build.log")
    assert builds[:4] == [
        "build -t futureagi/future-agi:local -f futureagi/Dockerfile.oss "
        "--build-arg IMAGE_VARIANT=slim futureagi",
        "build -t futureagi/frontend:local frontend",
        "build -t futureagi/fi-collector:local fi-collector",
        "build -t futureagi/agentcc-gateway:local agentcc-gateway",
    ]
    assert builds[4].startswith(
        "build -t futureagi/standalone:local -f deploy/standalone/Dockerfile"
    )
    for arg in (
        "BACKEND_IMAGE=futureagi/future-agi:local",
        "FRONTEND_IMAGE=futureagi/frontend:local",
        "FI_COLLECTOR_IMAGE=futureagi/fi-collector:local",
        "AGENTCC_GATEWAY_IMAGE=futureagi/agentcc-gateway:local",
    ):
        assert f"--build-arg {arg}" in builds[4]
    assert _env_values(script.parents[1])["FUTURE_AGI_VERSION"] == "local"
    assert _lines(state / "pull.argv") == ["pull", "clickhouse", "postgres"]


def test_distributed_from_source_tags_its_own_images_local(tmp_path: Path) -> None:
    script, environment, state = _installer_sandbox(tmp_path, CI="1")

    code, _, stderr = _run_installer(
        script, environment, "--distributed", "--from-source", "--skip-user-creation"
    )

    assert code == 0, stderr
    builds = _lines(state / "build.log")
    assert len(builds) == 4
    assert not any("standalone" in build for build in builds)
    # The standard backend variant, as the published futureagi/future-agi.
    assert builds[0] == (
        "build -t futureagi/future-agi:local -f futureagi/Dockerfile.oss futureagi"
    )
    values = _env_values(script.parents[1])
    for key in (
        "FUTURE_AGI_VERSION",
        "FRONTEND_VERSION",
        "AGENTCC_GATEWAY_VERSION",
        "FI_COLLECTOR_VERSION",
    ):
        assert values[key] == "local"
    assert _lines(state / "pull.argv") == ["pull", "--ignore-buildable", "postgres"]


def test_a_local_tag_without_its_image_points_at_from_source(tmp_path: Path) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_MISSING_IMAGES="futureagi/standalone:local"
    )
    (script.parents[1] / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + "FUTURE_AGI_VERSION=local\n", encoding="utf-8"
    )

    code, _, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 1
    assert "futureagi/standalone:local is not built yet" in stderr
    assert "--from-source" in stderr


def test_placeholders_next_to_existing_volumes_are_never_replaced(
    tmp_path: Path,
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_VOLUMES="futureagi_app-data futureagi_postgres-data"
    )
    repo = script.parents[1]
    (repo / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + "MINIO_ROOT_PASSWORD=CHANGEME-set-by-bin-install\n",
        encoding="utf-8",
    )

    code, _, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    assert "Non-interactive mode: leaving .env as it is." in stderr
    values = _env_values(repo)
    assert values["MINIO_ROOT_PASSWORD"] == "CHANGEME-set-by-bin-install"
    assert "PG_PASSWORD" not in values


def test_a_project_name_exported_in_the_shell_wins_over_env(tmp_path: Path) -> None:
    """As in Compose, which the installer's compose commands run under: the
    installer inspects that project's volumes, not .env's."""
    script, environment, _ = _installer_sandbox(
        tmp_path,
        CI="1",
        COMPOSE_PROJECT_NAME="custom",
        FAGI_STUB_VOLUMES="custom_app-data custom_postgres-data",
    )
    repo = script.parents[1]
    (repo / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + "COMPOSE_PROJECT_NAME=futureagi\n", encoding="utf-8"
    )

    code, _, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    # An existing install: its secrets are never generated.
    assert "PG_PASSWORD" not in _env_values(repo)
    assert "still use the defaults published in this repository" in stderr


def test_power_shell_installer_takes_the_project_name_exported_in_the_shell(
    tmp_path: Path,
) -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")
    stub = tmp_path / "docker"
    stub.write_text(
        '#!/bin/bash\n[ "$1 $2" = "volume inspect" ] || exit 1\n'
        'case " $FAGI_STUB_VOLUMES " in *" $3 "*) exit 0 ;; esac\nexit 1\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    (tmp_path / ".env").write_text("COMPOSE_PROJECT_NAME=futureagi\n", encoding="utf-8")
    script = "\n".join(
        [
            f". '{INSTALL_LIB / 'env.ps1'}'; . '{INSTALL_LIB / 'secrets.ps1'}'",
            "function Invoke-Probe { param($Command) & $Command }",
            "function Die { param($m) throw $m }",
            "$WipeVolumes = $false",
            _power_shell_section(_read(INSTALL_PS1), "this project's existing state"),
            "Write-Output $projectName; $existingVolumes",
        ]
    )

    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "COMPOSE_PROJECT_NAME": "custom",
            "FAGI_STUB_VOLUMES": "custom_app-data custom_postgres-data",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "custom",
        "custom_app-data",
        "custom_postgres-data",
    ]


def test_a_new_instance_next_to_existing_volumes_gets_fresh_secrets(
    tmp_path: Path,
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_VOLUMES="futureagi_app-data futureagi_postgres-data"
    )
    repo = script.parents[1]
    (repo / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + "MINIO_ROOT_PASSWORD=CHANGEME-set-by-bin-install\n",
        encoding="utf-8",
    )

    code, stdout, stderr = _run_installer(
        script, environment, "--new-instance", "--no-up"
    )

    assert code == 0, stderr
    values = _env_values(repo)
    assert values["COMPOSE_PROJECT_NAME"] == "futureagi-2"
    assert "COMPOSE_FILE" not in values
    for key in SECRET_KEYS:
        assert re.fullmatch(r"[0-9a-f]{64}", values[key]), key


@pytest.mark.parametrize(
    ("args", "vm", "message"),
    [
        ((), "1960 2 aarch64", "The standalone install needs at least 3 GB"),
        (
            ("--distributed",),
            "3900 4 x86_64",
            "The distributed stack needs at least 6 GB",
        ),
    ],
)
def test_an_undersized_docker_vm_fails_preflight_with_the_fix(
    tmp_path: Path, args: tuple[str, ...], vm: str, message: str
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_VM=vm, FAGI_STUB_CONTEXT="colima"
    )
    repo = script.parents[1]

    code, _, stderr = _run_installer(script, environment, *args, "--skip-user-creation")

    assert code == 1
    assert message in stderr
    assert "colima stop && colima start --cpu" in stderr
    if args:
        assert "./bin/install without --distributed" in stderr
        # A refused fresh --distributed is not recorded.
        assert "COMPOSE_FILE" not in _env_values(repo)

    code, _, stderr = _run_installer(
        script, environment, *args, "--force", "--skip-user-creation"
    )
    assert code == 0, stderr
    assert "(continuing: --force)" in stderr
    if args:
        assert _env_values(repo)["COMPOSE_FILE"] == "docker-compose.distributed.yml"


def test_distributed_on_an_8_gb_docker_vm_only_warns(tmp_path: Path) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_VM="7960 4 x86_64", FAGI_STUB_IMAGE_ARCH="amd64"
    )

    code, _, stderr = _run_installer(
        script, environment, "--distributed", "--skip-user-creation"
    )

    assert code == 0, stderr
    assert "Docker has 7960 MB of memory; 12 GB is recommended" in stderr


def test_an_existing_distributed_install_is_never_blocked_by_the_memory_check(
    tmp_path: Path,
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_STUB_VM="3900 4 aarch64",
        FAGI_STUB_VOLUMES="futureagi_postgres-data futureagi_minio-data",
    )

    code, _, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 0, stderr
    assert "The distributed stack needs at least 6 GB" in stderr
    assert "this project already holds a distributed install" in stderr
    assert "without --distributed" not in stderr
    assert "(continuing: --force)" not in stderr


def test_an_upgrade_names_the_retired_rabbitmq_container(tmp_path: Path) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_STUB_VOLUMES="futureagi_postgres-data futureagi_rabbitmq-data",
        FAGI_STUB_SERVICES="backend postgres rabbitmq worker",
    )

    code, _, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 0, stderr
    assert "RabbitMQ is no longer part of the distributed stack" in stderr
    assert "docker compose up -d --remove-orphans" in stderr
    assert "docker volume rm futureagi_rabbitmq-data" in stderr


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ("missing", "Docker Hub has no futureagi/standalone:latest"),
        ("found", "Docker Hub's pull rate limit"),
    ],
)
def test_a_failed_pull_names_the_likely_cause(
    tmp_path: Path, manifest: str, expected: str
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_PULL="fail", FAGI_STUB_MANIFEST=manifest
    )

    code, _, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 1
    assert expected in stderr
    assert ("./bin/install --from-source" in stderr) == (manifest == "missing")


def test_a_sandbox_profile_set_only_in_the_shell_is_called_out(tmp_path: Path) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", COMPOSE_PROFILES="ml,sandbox"
    )

    code, _, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    assert "COMPOSE_PROFILES=sandbox is set in your shell but not in .env" in stderr


def test_the_telemetry_notice_comes_before_the_admin_email_is_asked(
    tmp_path: Path,
) -> None:
    """The first account's email is part of the registration."""
    script, environment, _ = _installer_sandbox(tmp_path)

    code, stdout, stderr = _run_installer(script, environment, typed=b"\n", timeout=120)

    assert code == 0, stderr
    notice = stdout.index("this install registers with api.futureagi.com")
    assert notice < stdout.index("Email    : ")
    # Worded as the telemetry page (https://docs.futureagi.com/docs/self-hosting/configuration/telemetry).
    for fact in (
        "instance id, version, deployment type, and the emails and domains of owners,",
        "admins and staff/superuser accounts",
        "usage counts every 6 h",
        "Opt out: --no-telemetry, or FUTURE_AGI_TELEMETRY_DISABLED=true in .env",
        "registration remains: instance id, version, deployment type, timestamp.",
    ):
        assert fact in stdout


def test_no_telemetry_is_in_env_before_the_stack_starts(tmp_path: Path) -> None:
    script, environment, state = _installer_sandbox(tmp_path, CI="1")

    code, stdout, stderr = _run_installer(
        script, environment, "--no-telemetry", "--skip-user-creation"
    )

    assert code == 0, stderr
    env_at_up = _read(state / "env_at_up").splitlines()
    assert "FUTURE_AGI_TELEMETRY_DISABLED=true" in env_at_up
    assert "Off (FUTURE_AGI_TELEMETRY_DISABLED=true)" in stdout
    assert "instance id, version, deployment type, timestamp" in stdout

    # .env keeps it: a later run without the flag stays opted out.
    code, stdout, stderr = _run_installer(script, environment, "--skip-user-creation")
    assert code == 0, stderr
    assert "Off (FUTURE_AGI_TELEMETRY_DISABLED=true)" in stdout


@pytest.mark.parametrize("configured, passed", [(None, "2"), ("10", "10")])
def test_create_user_caps_the_telemetry_registration_it_waits_for(
    tmp_path: Path, configured: str | None, passed: str
) -> None:
    script, environment, state = _installer_sandbox(
        tmp_path,
        CI="1",
        FAGI_ADMIN_EMAIL="abhijai@futureagi.com",
        FAGI_ADMIN_NAME="Abhijai",
        FAGI_ADMIN_PASSWORD="correct-horse-battery",
    )
    if configured:
        (script.parents[1] / ".env").write_text(
            SANDBOX_ENV_EXAMPLE
            + f"FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS={configured}\n",
            encoding="utf-8",
        )

    code, _, stderr = _run_installer(script, environment)

    assert code == 0, stderr
    argv = _lines(state / "create_user.argv")
    assert (
        argv[argv.index("-e") + 1] == f"FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS={passed}"
    )


@pytest.mark.parametrize(
    "value, warned", [("true", True), ("TRUE", True), ("false", False)]
)
def test_an_unsafe_password_reset_setting_is_called_out(
    tmp_path: Path, value: str, warned: bool
) -> None:
    """Older .env.example files turned it on."""
    script, environment, _ = _installer_sandbox(tmp_path, CI="1")
    (script.parents[1] / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + f"OSS_RETURN_PASSWORD_RESET_LINK={value}\n",
        encoding="utf-8",
    )

    code, _, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    warning = "OSS_RETURN_PASSWORD_RESET_LINK=true hands password-reset links to anyone"
    assert (warning in stderr) == warned


@pytest.mark.parametrize(
    "host_api, looked_up",
    [
        (None, False),
        ("http://localhost:8000", False),
        ("http://203.0.113.5:8000", True),
    ],
)
def test_the_public_ip_is_looked_up_only_when_it_is_printed(
    tmp_path: Path, host_api: str | None, looked_up: bool
) -> None:
    """The lookup tells a third party this host's address."""
    script, environment, state = _installer_sandbox(tmp_path, CI="1")
    if host_api:
        (script.parents[1] / ".env").write_text(
            SANDBOX_ENV_EXAMPLE + f"VITE_HOST_API={host_api}\n", encoding="utf-8"
        )

    code, _, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 0, stderr
    lookups = [
        url
        for url in _lines(state / "curl.log")
        if "ifconfig.io" in url or "ipify.org" in url
    ]
    assert bool(lookups) == looked_up


@pytest.mark.parametrize(
    "host_api, expected",
    [
        (None, "http://localhost:8001"),
        ("http://localhost:8000", "http://localhost:8001"),
        ("https://api.example.com", "https://api.example.com"),
    ],
)
def test_a_moved_backend_port_moves_the_url_the_ui_calls(
    tmp_path: Path, host_api: str | None, expected: str
) -> None:
    """Unset, the UI calls http://localhost:8000; a URL naming another host is
    the user's own."""
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_BUSY_PORTS="8000"
    )
    repo = script.parents[1]
    if host_api:
        (repo / ".env").write_text(
            SANDBOX_ENV_EXAMPLE + f"VITE_HOST_API={host_api}\n", encoding="utf-8"
        )

    code, _, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    values = _env_values(repo)
    assert values["BACKEND_PORT"] == "8001"
    assert values["VITE_HOST_API"] == expected


STANDALONE_PORTS = {
    "FRONTEND_PORT": "3000",
    "BACKEND_PORT": "8000",
    "FI_COLLECTOR_OTLP_PORT": "4317",
    "FI_COLLECTOR_OTLP_HTTP_PORT": "4318",
    "AGENTCC_GATEWAY_PORT": "8090",
    "MINIO_API_PORT": "9005",
}


def _standalone_ports(repo: Path) -> dict[str, str]:
    values = _env_values(repo)
    return {var: values.get(var, default) for var, default in STANDALONE_PORTS.items()}


def test_a_switched_port_never_lands_on_another_services_port(tmp_path: Path) -> None:
    """4317 taken: the collector's gRPC port moves past 4318, which its HTTP
    port is set to, instead of onto it."""
    script, environment, state = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_BUSY_PORTS="4317 8000 8001"
    )

    code, stdout, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 0, stderr
    ports = _standalone_ports(script.parents[1])
    assert ports["FI_COLLECTOR_OTLP_PORT"] == "4319"
    assert ports["FI_COLLECTOR_OTLP_HTTP_PORT"] == "4318"
    assert ports["BACKEND_PORT"] == "8002"
    assert len(set(ports.values())) == len(ports)
    assert "auto-switching to FI_COLLECTOR_OTLP_PORT=4319" in stdout
    assert (state / "env_at_up").exists()


def test_a_rerun_separates_two_ports_an_earlier_run_set_equal(tmp_path: Path) -> None:
    """What an older installer left behind: compose failed with "port is
    already allocated" and nothing listens, so only the settings show it."""
    script, environment, _ = _installer_sandbox(tmp_path, CI="1")
    repo = script.parents[1]
    (repo / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + "FI_COLLECTOR_OTLP_PORT=4318\n", encoding="utf-8"
    )

    code, stdout, stderr = _run_installer(script, environment, "--no-up")

    assert code == 0, stderr
    ports = _standalone_ports(repo)
    assert ports["FI_COLLECTOR_OTLP_PORT"] == "4318"
    assert ports["FI_COLLECTOR_OTLP_HTTP_PORT"] == "4319"
    assert "FI_COLLECTOR_OTLP_HTTP_PORT=4318  is taken by FI_COLLECTOR_OTLP_PORT" in (
        stderr
    )


def test_two_ports_set_equal_are_unresolved_when_the_switch_is_declined(
    tmp_path: Path,
) -> None:
    script, environment, state = _installer_sandbox(tmp_path)
    repo = script.parents[1]
    (repo / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + "MINIO_API_PORT=3000\n", encoding="utf-8"
    )

    code, stdout, _ = _run_installer(script, environment, "--no-up", typed=b"n\n")

    assert code == 1
    assert "MINIO_API_PORT=3000  ←  FRONTEND_PORT (same port)" in stdout
    assert _env_values(repo)["MINIO_API_PORT"] == "3000"
    assert not (state / "env_at_up").exists()


def test_power_shell_installer_reports_two_ports_set_equal() -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")
    text = _read(INSTALL_LIB / "ports.ps1")
    check = text[text.index("$conflicts = @()") : text.index("if ($conflicts.Count")]
    script = "\n".join(
        [
            "$portsToCheck = [ordered]@{ 'FI_COLLECTOR_OTLP_PORT' = 4317;"
            " 'FI_COLLECTOR_OTLP_HTTP_PORT' = 4318; 'MINIO_API_PORT' = 9005 }",
            "function Get-EnvValue { param($n)"
            " if ($n -eq 'FI_COLLECTOR_OTLP_PORT') { '4318' } else { '' } }",
            "function Test-PortFree { param([int]$Port) $true }",
            "function Ok { param($m) }",
            "function Warn { param($m) Write-Output $m }",
            "$ownPorts = ''",
            check,
            '$conflicts | ForEach-Object { Write-Output "conflict: $_" }',
        ]
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (
        "conflict: FI_COLLECTOR_OTLP_HTTP_PORT=4318 (same port as"
        " FI_COLLECTOR_OTLP_PORT)" in result.stdout
    )
    assert "MINIO_API_PORT" not in result.stdout


@pytest.mark.parametrize(
    "setting, url",
    [
        ("", "http://localhost:4318"),
        ("FI_COLLECTOR_OTLP_HTTP_PORT=4400\n", "http://localhost:4400"),
        (
            "FI_COLLECTOR_PUBLIC_URL=https://otel.example.com\n",
            "https://otel.example.com",
        ),
    ],
)
def test_the_first_trace_goes_to_the_collector_url_the_app_shows(
    tmp_path: Path, setting: str, url: str
) -> None:
    """The compose files give the app FI_COLLECTOR_PUBLIC_URL with the same
    default; the setup screen and SDK snippet in the UI show it."""
    script, environment, _ = _installer_sandbox(tmp_path, CI="1")
    (script.parents[1] / ".env").write_text(
        SANDBOX_ENV_EXAMPLE + setting, encoding="utf-8"
    )

    code, stdout, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 0, stderr
    assert f'FI_BASE_URL="{url}"' in stdout
    assert f"     Traces       {url}  (OTLP/HTTP" in stdout


def _standalone_build(text: str) -> str:
    """The installer's `docker build` of futureagi/standalone, through its
    context (deploy/standalone)."""
    found = re.search(
        r"futureagi/standalone:(?:local|\$tag).*?deploy/standalone'?$",
        text,
        re.S | re.M,
    )
    assert found, "no futureagi/standalone build"
    return found.group(0)


def test_installers_pass_only_build_args_the_standalone_image_declares() -> None:
    dockerfile = _read(ROOT / "deploy" / "standalone" / "Dockerfile")
    declared = set(re.findall(r"^ARG ([A-Z0-9_]+)", dockerfile, re.MULTILINE))
    shell_text = _read(INSTALL_LIB / "build-local.sh")
    powershell_text = _read(INSTALL_LIB / "build-local.ps1")
    shell = set(re.findall(r"--build-arg ([A-Z0-9_]+)=", _standalone_build(shell_text)))
    powershell = set(
        re.findall(r"'--build-arg', '([A-Z0-9_]+)=", _standalone_build(powershell_text))
    )

    # Docker ignores an undeclared build arg with only a warning, which would
    # silently build the standalone image from published components.
    assert len(shell) == 4
    assert shell == powershell
    assert shell <= declared
    # Standalone's app image sits on the slim backend, as the published one
    # does (bin/install is exercised by the from-source tests).
    assert (
        "if (-not $Distributed) { $backendVariant = @('--build-arg', "
        "'IMAGE_VARIANT=slim') }"
    ) in powershell_text


def test_dev_builds_the_local_images_with_the_installers_recipe() -> None:
    """./bin/dev and ./bin/install --from-source must build the same images,
    tags and backend variant."""
    dev = _read(ROOT / "bin" / "dev")
    assert '. "$ROOT/bin/lib/build-local.sh"' in dev
    assert "build_local_images standalone" in dev
    for script in (dev, _read(INSTALL_SH), _read(INSTALL_PS1)):
        assert "docker build" not in script
        assert "--build-arg" not in script


def test_a_checkout_the_docker_vm_cannot_see_fails_preflight(tmp_path: Path) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_MOUNT="hidden"
    )

    code, _, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 1
    assert "Docker cannot see" in stderr
    assert "under $HOME" in stderr


def test_an_emulated_app_image_is_called_out_before_start(tmp_path: Path) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path, CI="1", FAGI_STUB_IMAGE_ARCH="amd64"
    )

    code, _, stderr = _run_installer(script, environment, "--skip-user-creation")

    assert code == 0, stderr
    assert (
        "futureagi/standalone:latest is built for amd64 but Docker runs on arm64"
        in stderr
    )
    assert "--from-source builds native images" in stderr


def test_standalone_readiness_waits_on_the_ui_and_fails_fast_on_a_crash_loop(
    tmp_path: Path,
) -> None:
    script, environment, _ = _installer_sandbox(
        tmp_path / "ui", CI="1", FAGI_STUB_UI="down"
    )
    code, _, stderr = _run_installer(script, environment, "--skip-user-creation")
    assert code == 1
    assert "still waiting on the UI on port 3000" in stderr

    script, environment, state = _installer_sandbox(
        tmp_path / "loop",
        CI="1",
        FAGI_STUB_HEALTH="down",
        FAGI_STUB_APP_RESTARTS="climbing",
    )
    code, _, stderr = _run_installer(script, environment, "--skip-user-creation")
    assert code == 1
    assert "The app container keeps restarting" in stderr
    assert _readiness_ticks(state) <= 6


UNINSTALL_SH = ROOT / "bin" / "uninstall"

STUB_UNINSTALL_DOCKER = r"""#!/bin/bash
printf '%s\n' "$*" >> "$FAGI_STUB_STATE/calls"
case " $* " in
  *" config --images "*)
    # This project's resolved stack; builds from source carry the local tag.
    printf 'futureagi/standalone:%s\npostgres:16\nclickhouse/clickhouse-server:25.3-alpine\n' \
      "$FAGI_STUB_TAG"
    exit 0 ;;
esac
case "$1 $2" in
  "compose version") exit 0 ;;
  "volume ls")
    printf 'futureagi_postgres-data\nfutureagi-2_app-data\nfutureagi-2_postgres-data\n' ;;
  "images --filter")
    case "$3" in
      reference=futureagi/*)
        printf 'futureagi/standalone:latest\nfutureagi/standalone:local\n'
        printf 'futureagi/future-agi:local\nfutureagi/future-agi:v1.2.0\n<none>:<none>\n' ;;
    esac ;;
esac
exit 0
"""


def test_uninstall_parses() -> None:
    result = subprocess.run(
        ["bash", "-n", str(UNINSTALL_SH)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("compose_file", "stack", "tag"),
    [
        ("", "standalone install", "latest"),
        ("docker-compose.distributed.yml", "distributed stack", "latest"),
        ("", "standalone install", "local"),
    ],
)
def test_uninstall_purges_its_own_project_in_either_stack(
    tmp_path: Path, compose_file: str, stack: str, tag: str
) -> None:
    repo = tmp_path / "repo"
    (repo / "bin").mkdir(parents=True)
    script = repo / "bin" / "uninstall"
    script.write_bytes(UNINSTALL_SH.read_bytes())
    shutil.copytree(INSTALL_LIB, repo / "bin" / "lib")
    (repo / ".env").write_text(
        f"COMPOSE_PROJECT_NAME=futureagi-2\nCOMPOSE_FILE={compose_file}\n",
        encoding="utf-8",
    )
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "docker").write_text(STUB_UNINSTALL_DOCKER, encoding="utf-8")
    (stubs / "docker").chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()

    result = subprocess.run(
        ["bash", str(script), "--purge", "-y"],
        cwd=repo,
        env={
            "PATH": f"{stubs}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(state),
            "FAGI_STUB_STATE": str(state),
            "FAGI_STUB_TAG": tag,
        },
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert stack in result.stdout
    calls = _lines(state / "calls")
    assert "compose -p futureagi-2 down --remove-orphans" in calls
    assert "volume rm futureagi-2_app-data futureagi-2_postgres-data" in calls
    # Resolved while .env still selected the stack and its tags.
    assert "compose -p futureagi-2 config --images" in calls
    removed = [call for call in calls if call.startswith("rmi ")]
    if tag == "latest":
        # Only the released images this project uses; never another
        # release, another project's images or third-party ones.
        assert removed == ["rmi futureagi/standalone:latest"]
        assert "built by --from-source" not in output
    else:
        # Builds from source share their tag across checkouts: -y keeps them.
        assert removed == []
        assert "Kept (-y never removes them)" in output
        assert (
            "docker rmi futureagi/future-agi:local futureagi/standalone:local" in output
        )
    assert not (repo / ".env").exists()


def test_startup_guard_admits_create_user_and_still_blocks_unlisted_commands() -> None:
    guarded_management_command = _guarded_management_command()

    assert guarded_management_command(["manage.py", "create_user"]) is None
    assert (
        guarded_management_command(
            ["manage.py", "create_user", "--email", "abhijai@futureagi.com"]
        )
        is None
    )
    assert guarded_management_command(["python", "-m", "django", "create_user"]) is None
    assert guarded_management_command(["manage.py", "shell"]) == "shell"
    assert guarded_management_command(["manage.py", "migrate"]) == "migrate"
    assert guarded_management_command(["gunicorn", "tfc.wsgi"]) is None

    from model_hub.apps import explicit_management_mutation_authorized

    assert explicit_management_mutation_authorized(["manage.py", "shell"]) is False
