from __future__ import annotations

import json
import os
import shutil
import subprocess
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
    assert consumer_env["FI_OBSERVED_CATALOG_CH_DATABASE"] == (
        services["backend"]["environment"]["PROPERTY_CATALOG_DATABASE"]
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
