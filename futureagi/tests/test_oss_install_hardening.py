from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.yml"
INSTALL_SH = ROOT / "bin" / "install"
INSTALL_PS1 = ROOT / "bin" / "install.ps1"
BACKFILL_SH = ROOT / "bin" / "property-catalog-backfill"
BACKFILL_PS1 = ROOT / "bin" / "property-catalog-backfill.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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
    shell = _read(INSTALL_SH)
    powershell = _read(INSTALL_PS1)
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
    shell = _read(INSTALL_SH)
    powershell = _read(INSTALL_PS1)
    for installer in (shell, powershell):
        assert "PROPERTY_CATALOG_KAFKA_PORT" in installer
        assert "property-catalog-kafka-data" in installer
        assert "property-catalog-sequencer-data" not in installer
        assert "fi-collector-data" in installer
        assert "--ignore-buildable" in installer
        assert "fi-property-catalog-consumer" in installer

    assert "fi-collector|fi-property-catalog-consumer" in shell
    assert "'fi-collector', 'fi-property-catalog-consumer'" in powershell

    assert "--wipe-volumes" in shell
    assert "WipeVolumes" in powershell
    assert "docker volume prune" not in shell
    assert "docker system prune" not in shell
    assert "docker volume prune" not in powershell
    assert "docker system prune" not in powershell


def test_shell_installer_parses() -> None:
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
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


def test_power_shell_installer_parses_when_pwsh_is_available() -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")
    command = (
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{INSTALL_PS1}', [ref]$tokens, [ref]$errors) > $null; "
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

last_arg=""
for arg in "$@"; do last_arg="$arg"; done

if [ "$1" = "compose" ]; then
  shift
  case "$1" in
    version) printf '2.31.0\n'; exit 0 ;;
    pull)
      case " $* " in
        *" --help "*) printf '      --ignore-buildable   Ignore buildable images\n' ;;
      esac
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
    case "${last_arg#cid-}" in
      property-catalog-kafka)
        printf 'running|0|0|%s|%s\n' "$FAGI_STUB_KAFKA_HEALTH" "$started" ;;
      fi-collector|fi-property-catalog-consumer)
        printf 'running|0|0|none|%s\n' "$started" ;;
      *) printf 'exited|0|0|none|%s\n' "$started" ;;
    esac
    exit 0 ;;
  volume)
    [ "$2" = "inspect" ] && exit 1
    exit 0 ;;
  *) exit 0 ;;
esac
"""

STUB_CURL = r"""#!/bin/bash
url=""
for arg in "$@"; do
  case "$arg" in http*) url="$arg" ;; esac
done
case "$url" in
  *'/health/'*) ;;
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

STUB_LSOF = "#!/bin/bash\nexit 1\n"

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
    (repo / ".env.example").write_text(SANDBOX_ENV_EXAMPLE, encoding="utf-8")

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

    for outcome in ("ok", "exists"):
        code, stdout, stderr = runs[outcome]
        assert code == 0, stderr
        assert "Sign in as abhijai@futureagi.com" in stdout
        assert "ACTION REQUIRED" not in stdout

    code, stdout, stderr = runs["fail"]
    assert code == 1
    assert "ACTION REQUIRED" in stdout
    assert "no account was created" in stdout
    assert "Sign in as" not in stdout
    assert "docker exec -it futureagi-backend-1 python manage.py create_user" in stdout


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
    assert "Sign in as abhijai+test123@futureagi.com" in stdout
    argv = (state / "create_user.argv").read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--email") + 1] == "abhijai+test123@futureagi.com"


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

    code, stdout, stderr = _run_installer(script, environment, "--skip-user-creation")

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
