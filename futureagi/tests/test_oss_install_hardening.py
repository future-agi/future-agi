from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
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


@pytest.mark.parametrize(
    "ignore_file,required_pattern",
    [
        (
            ROOT / ".dockerignore",
            "futureagi/scripts/property_catalog_oss/tests/replicated_smoke/runs/",
        ),
        (
            ROOT / "futureagi" / ".dockerignore",
            "scripts/property_catalog_oss/tests/replicated_smoke/runs/",
        ),
    ],
    ids=["root-build-context", "backend-build-context"],
)
def test_docker_context_excludes_private_replicated_smoke_runs(
    ignore_file: Path, required_pattern: str
) -> None:
    patterns = {
        line.strip()
        for line in _read(ignore_file).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert required_pattern in patterns
    assert f"!{required_pattern}" not in patterns


def _compose_config() -> dict[str, object]:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is unavailable")
    environment = os.environ.copy()
    environment.update(
        {
            "FI_COLLECTOR_VERSION": "local",
            "PROPERTY_CATALOG_KAFKA_PORT": "29092",
            "PROPERTY_CATALOG_KAFKA_CPUS": "1.0",
            "PROPERTY_CATALOG_KAFKA_MEMORY": "1G",
            "PROPERTY_CATALOG_KAFKA_HEAP_OPTS": "-Xms256m -Xmx512m",
            "FI_COLLECTOR_CPUS": "1.0",
            "FI_COLLECTOR_MEMORY": "1G",
            "PROPERTY_CATALOG_SEQUENCER_CPUS": "0.5",
            "PROPERTY_CATALOG_SEQUENCER_MEMORY": "768M",
            "PROPERTY_CATALOG_CONSUMER_CPUS": "0.5",
            "PROPERTY_CATALOG_CONSUMER_MEMORY": "512M",
            "PROPERTY_CATALOG_SUPERVISOR_CPUS": "0.5",
            "PROPERTY_CATALOG_SUPERVISOR_MEMORY": "768M",
        }
    )
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
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
    sequencer = services["fi-property-catalog-sequencer"]
    consumer = services["fi-property-catalog-consumer"]
    assert collector["image"] == "futureagi/fi-collector:local"
    assert sequencer["image"] == "futureagi/fi-collector:local"
    assert consumer["image"] == "futureagi/fi-collector:local"
    assert Path(collector["build"]["context"]).name == "fi-collector"
    assert Path(sequencer["build"]["context"]).name == "fi-collector"
    assert Path(consumer["build"]["context"]).name == "fi-collector"
    assert sequencer["entrypoint"] == ["/usr/local/bin/fi-property-catalog-sequencer"]
    assert consumer["entrypoint"] == ["/usr/local/bin/fi-property-catalog-consumer"]

    collector_env = collector["environment"]
    sequencer_env = sequencer["environment"]
    consumer_env = consumer["environment"]
    candidate_topic = collector_env["FI_PROPERTY_CATALOG_KAFKA_TOPIC"]
    ordered_topic = sequencer_env["FI_PROPERTY_CATALOG_KAFKA_TOPIC"]
    assert collector_env["FI_PROPERTY_CATALOG_MODE"] == "kafka"
    assert sequencer_env["FI_PROPERTY_CATALOG_MODE"] == "sequencer"
    assert sequencer_env["FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC"] == candidate_topic
    assert consumer_env["FI_PROPERTY_CATALOG_KAFKA_TOPIC"] == ordered_topic
    assert candidate_topic != ordered_topic
    assert sequencer_env["FI_PROPERTY_CATALOG_SEQUENCER_TRANSACTIONAL_ID"]
    assert sequencer_env["FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_CONSUMER_GROUP"]
    assert sequencer_env["FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_INSTANCE_ID"]
    sequencer_volumes = {
        (volume["source"], volume["target"], volume.get("read_only", False))
        for volume in sequencer["volumes"]
    }
    assert (
        "property-catalog-sequencer-data",
        "/var/lib/property-catalog-sequencer",
        False,
    ) in sequencer_volumes
    assert (
        "fi-collector-data",
        "/var/lib/property-catalog-control",
        True,
    ) in sequencer_volumes

    topic_init_env = services["property-catalog-topic-init"]["environment"]
    assert topic_init_env["PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC"] == candidate_topic
    assert topic_init_env["PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC"] == ordered_topic

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
    supervisor = services["property-catalog-supervisor"]
    supervisor_env = supervisor["environment"]
    spool = sequencer_env["FI_PROPERTY_CATALOG_SPOOL_DIR"]
    for key in (
        "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE",
        "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE",
    ):
        assert str(Path(supervisor_env[key]).parent) == spool
    assert ("property-catalog-sequencer-data", spool, False) in {
        (volume["source"], volume["target"], volume.get("read_only", False))
        for volume in supervisor["volumes"]
    }
    assert (
        str(Path(supervisor_env["PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE"]).parent)
        == (supervisor_env["PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY"])
    )
    supervisor_command = " ".join(supervisor["command"])
    assert supervisor["entrypoint"] == [
        "python",
        "manage.py",
        "ch25_property_catalog_oss_supervisor",
    ]
    assert "--once" not in supervisor_command
    assert "--initial-backfill" not in supervisor_command
    assert "--health-file" in supervisor_command
    assert supervisor["healthcheck"]["test"][:3] == ["CMD", "python", "-c"]
    for env in (collector_env, sequencer_env, supervisor["environment"]):
        for removed in (
            "FI_PROPERTY_CATALOG_EPOCH",
            "FI_PROPERTY_CATALOG_PROJECTION_VERSION",
            "FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID",
            "PROPERTY_CATALOG_DEV_CATALOG_EPOCH",
            "PROPERTY_CATALOG_DEV_PROJECTION_VERSION",
            "PROPERTY_CATALOG_DEV_HOT_PRODUCER_STREAM_ID",
        ):
            assert removed not in env
    assert (
        supervisor["environment"]["PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC"]
        == candidate_topic
    )
    assert (
        supervisor["environment"]["PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC"]
        == ordered_topic
    )
    for service_name in (
        "property-catalog-kafka",
        "property-catalog-topic-init",
        "fi-collector",
        "fi-property-catalog-sequencer",
        "fi-property-catalog-consumer",
        "property-catalog-supervisor",
    ):
        service = services[service_name]
        assert service.get("profiles") in (None, [])
        assert "default" in service["networks"]
    for service_name in (
        "property-catalog-kafka",
        "fi-collector",
        "fi-property-catalog-sequencer",
        "fi-property-catalog-consumer",
        "property-catalog-supervisor",
    ):
        assert services[service_name]["cpus"] > 0
        assert int(services[service_name]["mem_limit"]) > 0
        limits = services[service_name]["deploy"]["resources"]["limits"]
        assert limits["cpus"] > 0
        assert int(limits["memory"]) > 0

    internal_broker = "property-catalog-kafka:9092"
    assert collector_env["FI_PROPERTY_CATALOG_KAFKA_BROKERS"] == internal_broker
    assert sequencer_env["FI_PROPERTY_CATALOG_KAFKA_BROKERS"] == internal_broker
    assert (
        sequencer_env["FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_BROKERS"] == internal_broker
    )
    assert consumer_env["FI_PROPERTY_CATALOG_KAFKA_BROKERS"] == internal_broker
    supervisor_fence = supervisor["environment"][
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE"
    ]
    sequencer_fence = sequencer_env["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"]
    assert supervisor_fence.removeprefix("/var/lib/fi-collector") == (
        sequencer_fence.removeprefix("/var/lib/property-catalog-control")
    )


@pytest.mark.parametrize(
    "case",
    [
        "healthy",
        "workspace_failure",
        "dependency_failure",
        "stale",
        "future",
        "stuck",
        "old_format",
    ],
)
def test_compose_health_probe_checks_live_readiness_and_freshness(tmp_path, case):
    probe = _compose_config()["services"]["property-catalog-supervisor"]["healthcheck"][
        "test"
    ][-1]
    path = tmp_path / "health.json"
    now = datetime.now(UTC)
    record = {
        "format": "futureagi.property-catalog-lifecycle-health",
        "version": 2,
        "live": True,
        "ready": True,
        "healthy": True,
        "observed_at": now.isoformat(),
    }
    if case == "workspace_failure":
        record.update(healthy=False, detail={"failed_count": 1})
    elif case == "dependency_failure":
        record["ready"] = False
    elif case == "stuck":
        record["live"] = False
    elif case in ("stale", "future"):
        record["observed_at"] = (
            now + timedelta(seconds=-61 if case == "stale" else 61)
        ).isoformat()
    elif case == "old_format":
        record["version"] = 1
    path.write_text(json.dumps(record))
    code = probe.replace("/tmp/property-catalog-supervisor.health.json", str(path))
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, timeout=5
    )
    assert (result.returncode == 0) == (case in ("healthy", "workspace_failure"))


def test_installers_gate_success_on_the_full_catalog_path() -> None:
    shell = _read(INSTALL_SH)
    powershell = _read(INSTALL_PS1)
    required_services = (
        "property-catalog-kafka",
        "property-catalog-kafka-volume-init",
        "property-catalog-runtime-volume-init",
        "property-catalog-topic-init",
        "property-catalog-clickhouse-bootstrap",
        "property-catalog-postgres-bootstrap",
        "fi-collector",
        "fi-property-catalog-sequencer",
        "fi-property-catalog-consumer",
        "property-catalog-supervisor",
        "backend",
    )
    for service in required_services:
        assert service in shell
        assert service in powershell

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
        assert "property-catalog-sequencer-data" in installer
        assert "fi-collector-data" in installer
        assert "--ignore-buildable" in installer
        assert "fi-property-catalog-sequencer" in installer

    assert (
        "fi-collector|fi-property-catalog-sequencer|fi-property-catalog-consumer"
        in shell
    )
    assert (
        "'fi-collector', 'fi-property-catalog-sequencer', 'fi-property-catalog-consumer'"
        in powershell
    )

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


def test_explicit_backfill_entrypoints_are_pinned_and_gated() -> None:
    shell = _read(BACKFILL_SH)
    powershell = _read(BACKFILL_PS1)
    for script in (shell, powershell):
        assert "ch25_property_catalog_oss_supervisor" in script
        assert "initial-backfill" in script
        assert "property-catalog-supervisor" in script
        assert "docker compose pull" not in script
        assert "docker-compose pull" not in script
        assert "git checkout" not in script
        assert "git fetch" not in script
        assert "git pull" not in script
    assert "--execute" in shell
    assert "-Execute" in powershell
    assert os.access(BACKFILL_SH, os.X_OK)

    result = subprocess.run(
        ["bash", "-n", str(BACKFILL_SH)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


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
