"""OSS/self-host boundary checks for the unified property catalog."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import yaml

_REPOSITORY = Path(__file__).resolve().parents[3]
_BACKEND = _REPOSITORY / "futureagi"
_CATALOG_PACKAGE = (
    _BACKEND / "tracer" / "services" / "clickhouse" / "v2" / "property_catalog"
)


def _import_targets(path: Path) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    ):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)
    return targets


def test_unified_catalog_modules_do_not_import_ee() -> None:
    modules = sorted(_CATALOG_PACKAGE.glob("*.py"))
    modules.append(
        _BACKEND
        / "tracer"
        / "management"
        / "commands"
        / "ch25_property_catalog_dev_rollout.py"
    )
    modules.append(
        _BACKEND
        / "tracer"
        / "management"
        / "commands"
        / "ch25_property_catalog_oss_supervisor.py"
    )

    violations = {
        str(module.relative_to(_REPOSITORY)): sorted(
            target
            for target in _import_targets(module)
            if target == "ee" or target.startswith("ee.")
        )
        for module in modules
    }
    assert not {path: targets for path, targets in violations.items() if targets}


def test_unified_catalog_imports_with_ee_unavailable() -> None:
    """Exercise the shipped OSS import path in a clean interpreter.

    The checkout may contain licensed modules, so the child process makes
    ``find_spec`` report them absent and rejects any accidental ``ee`` import.
    This mirrors a source distribution in which that package is unavailable
    and catches dynamic imports that the AST check cannot see.
    """

    program = """
import builtins
import importlib
import io
import pkgutil
import sys

original_find_spec = importlib.util.find_spec
importlib.util.find_spec = lambda name, *args, **kwargs: (
    None
    if name == "ee" or name.startswith("ee.")
    else original_find_spec(name, *args, **kwargs)
)
original_import = builtins.__import__
def oss_import(name, *args, **kwargs):
    if name == "ee" or name.startswith("ee."):
        raise ImportError(f"unexpected EE import: {name}")
    return original_import(name, *args, **kwargs)
builtins.__import__ = oss_import

import django
django.setup()
from django.core.management import call_command
import tracer.services.clickhouse.v2.property_catalog as package

modules = sorted(
    module.name
    for module in pkgutil.iter_modules(package.__path__, package.__name__ + ".")
)
for module in modules:
    importlib.import_module(module)
importlib.import_module("tracer.management.commands.ch25_property_catalog_dev_rollout")
importlib.import_module("tracer.management.commands.ch25_property_catalog_oss_supervisor")
output = io.StringIO()
call_command(
    "ch25_property_catalog_dev_rollout",
    organization_id="11111111-1111-4111-8111-111111111111",
    workspace_id="22222222-2222-4222-8222-222222222222",
    environment="development",
    cloud_deployment="",
    dev_identity="dev:property-catalog/oss-proof",
    source_database="futureagi",
    target_database="property_catalog_dev_oss_proof",
    acknowledgement="PROPERTY_CATALOG_DEV_ROLLOUT",
    stdout=output,
)
assert '"zero_io":true' in output.getvalue()
print(f"OSS_CATALOG_IMPORT_OK:{len(modules)}")
"""
    environment = os.environ.copy()
    environment.update(
        {
            "CLOUD_DEPLOYMENT": "",
            "DJANGO_SETTINGS_MODULE": "tfc.settings.settings",
            "EE_LICENSE_KEY": "",
            "ENV_TYPE": "development",
            "NO_STARTUP_DB_MUTATIONS": "true",
            "PYTHONPATH": str(_BACKEND),
            "SECRET_KEY": "oss-catalog-import-proof-not-a-runtime-secret",
            "SERVICE_TYPE": "bootstrap",
            "STARTUP_DB_MUTATION_MODE": "operator",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=_BACKEND,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    sentinel = completed.stdout.rstrip().rsplit("\n", 1)[-1]
    assert sentinel.startswith("OSS_CATALOG_IMPORT_OK:")
    assert int(sentinel.rsplit(":", 1)[-1]) > 0


def test_root_oss_compose_defaults_to_the_unified_kafka_catalog() -> None:
    compose = yaml.safe_load(
        (_REPOSITORY / "docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    assert {
        "postgres",
        "clickhouse",
        "fi-collector",
        "fi-property-catalog-consumer",
        "property-catalog-clickhouse-bootstrap",
        "property-catalog-postgres-bootstrap",
        "property-catalog-kafka",
        "property-catalog-topic-init",
        "property-catalog-supervisor",
    } <= services.keys()

    collector_environment = services["fi-collector"]["environment"]
    assert collector_environment["FI_CATALOG_MODE"].endswith(":-disabled}")
    assert collector_environment["FI_PROPERTY_CATALOG_MODE"].endswith(":-kafka}")
    assert (
        collector_environment["FI_PROPERTY_CATALOG_WORKSPACE_SCOPE_MODE"]
        == "revision_fence"
    )
    assert (
        collector_environment["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"]
        == "/var/lib/fi-collector/property-catalog/revision-fence-v2.json"
    )
    assert (
        collector_environment["FI_PROPERTY_CATALOG_KAFKA_BROKERS"]
        == "property-catalog-kafka:9092"
    )
    assert "FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST" not in collector_environment

    kafka = services["property-catalog-kafka"]
    assert (
        "INTERNAL://property-catalog-kafka:9092"
        in kafka["environment"]["KAFKA_ADVERTISED_LISTENERS"]
    )
    assert kafka["environment"]["KAFKA_AUTO_CREATE_TOPICS_ENABLE"] == "false"

    topic = services["property-catalog-topic-init"]
    assert "profiles" not in topic
    assert (
        "futureagi.oss.property-catalog.candidates.v1"
        in topic["environment"]["PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC"]
    )
    assert (
        "futureagi.oss.property-catalog.ordered.v1"
        in topic["environment"]["PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC"]
    )
    topic_command = "\n".join(topic["command"])
    assert "property-catalog.dev.span-attribute-catalog.v1" not in topic_command

    consumer = services["fi-property-catalog-consumer"]
    assert consumer["entrypoint"] == ["/usr/local/bin/fi-property-catalog-consumer"]
    assert consumer["command"] == ["--seed-from-delivery-ledger"]
    consumer_environment = consumer["environment"]
    assert consumer_environment["FI_PROPERTY_CATALOG_CONSUMER_MODE"] == "kafka"
    assert consumer_environment["FI_PROPERTY_CATALOG_CH_DATABASE"].endswith(
        ":-property_catalog_dev_oss}"
    )
    assert (
        consumer_environment["FI_PROPERTY_CATALOG_CH_USERNAME"]
        == "property_catalog_oss_consumer"
    )
    assert (
        consumer_environment["FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME"]
        == "property_catalog_oss_ledger"
    )
    assert (
        consumer_environment["FI_PROPERTY_CATALOG_CH_USERNAME"]
        != consumer_environment["FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME"]
    )
    assert consumer_environment["FI_PROPERTY_CATALOG_CHECKPOINT_MAX_STREAMS"].endswith(
        ":-16384}"
    )
    assert consumer_environment[
        "FI_PROPERTY_CATALOG_CHECKPOINT_MAX_INVENTORY_BYTES"
    ].endswith(":-67108864}")

    supervisor = services["property-catalog-supervisor"]
    assert supervisor["read_only"] is True
    assert supervisor["user"] == "65532:65532"
    assert supervisor["environment"]["PG_USER"] == "property_catalog_oss_reader"
    assert supervisor["environment"]["CH25_USER"] == "property_catalog_oss_source"
    assert supervisor["environment"]["CH25_SERVER_ENFORCED_READONLY"] == "true"
    assert supervisor["environment"]["PROPERTY_CATALOG_READ_MODE"] == "off"
    assert (
        supervisor["environment"]["PROPERTY_CATALOG_OSS_SUPERVISOR_ACK"]
        == "PROPERTY_CATALOG_OSS_SUPERVISOR_V1"
    )
    assert supervisor["environment"][
        "PROPERTY_CATALOG_OSS_SUPERVISOR_WORKSPACE_BATCH_SIZE"
    ].endswith(":-512}")
    assert supervisor["environment"][
        "PROPERTY_CATALOG_OSS_SUPERVISOR_PROJECT_BATCH_SIZE"
    ].endswith(":-512}")
    assert supervisor["environment"]["PROPERTY_CATALOG_DEV_SOURCE_DATABASE"].endswith(
        ":-default}}"
    )
    assert supervisor["environment"]["PROPERTY_CATALOG_DEV_TARGET_DATABASE"].endswith(
        ":-property_catalog_dev_oss}"
    )
    assert (
        supervisor["environment"]["PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE"]
        == supervisor["environment"]["PROPERTY_CATALOG_DEV_TARGET_DATABASE"]
    )
    assert (
        supervisor["environment"]["PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE"]
        == collector_environment["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"]
    )

    backend_environment = compose["x-backend-env"]
    assert backend_environment["PROPERTY_CATALOG_READ_MODE"].endswith(":-off}")
    assert backend_environment["PROPERTY_CATALOG_DEV_RECONCILE_ENABLED"].endswith(
        ":-false}"
    )


def test_dev_compose_runs_catalog_supervisor_from_the_checkout() -> None:
    compose = (_REPOSITORY / "docker-compose.dev.yml").read_text(encoding="utf-8")
    supervisor = compose.split("  property-catalog-supervisor:\n", 1)[1].split(
        "\n  dev-api-proxy:\n", 1
    )[0]

    assert "image: futureagi/future-agi:dev" in supervisor
    assert "pull_policy: never" in supervisor
    assert "- ./futureagi:/app/backend" in supervisor


def test_dev_api_proxy_re_resolves_recreated_compose_services() -> None:
    proxy = (
        _REPOSITORY / "deploy" / "dev-api-proxy" / "default.conf.template"
    ).read_text(encoding="utf-8")

    assert "resolver 127.0.0.11" in proxy
    assert "server backend:80 resolve;" in proxy
    assert "server fi-collector:4318 resolve;" in proxy
    assert "proxy_pass http://dev_backend;" in proxy
    assert "proxy_pass http://dev_fi_collector;" in proxy


def test_oss_bootstrap_scripts_cannot_mutate_source_data() -> None:
    scripts = _REPOSITORY / "futureagi" / "scripts" / "property_catalog_oss"
    clickhouse = (scripts / "bootstrap_clickhouse.sh").read_text(encoding="utf-8")
    postgres = (scripts / "bootstrap_postgres.sh").read_text(encoding="utf-8")

    assert "CREATE DATABASE IF NOT EXISTS" in clickhouse
    assert "CREATE TABLE IF NOT EXISTS" not in clickhouse  # pinned SQL owns DDL
    assert "property_catalog_dev_" in clickhouse
    assert clickhouse.count('case "$TARGET_DATABASE" in') == 2
    assert "default|futureagi|information_schema|property_catalog|system" in clickhouse
    assert r"GRANT SELECT ON \`$SOURCE_DATABASE\`.spans" in clickhouse
    assert r"GRANT SELECT, INSERT ON \`$TARGET_DATABASE\`.*" in clickhouse
    assert "ALTER TABLE" not in clickhouse
    assert "DROP " not in clickhouse
    assert "TRUNCATE " not in clickhouse
    assert "DELETE FROM" not in clickhouse
    assert "INSERT INTO" not in clickhouse
    assert "UPDATE " not in clickhouse

    assert "GRANT pg_read_all_data" in postgres
    assert "default_transaction_read_only = 'on'" in postgres
    assert "ALTER TABLE" not in postgres
    assert "DROP " not in postgres
    assert "TRUNCATE " not in postgres
    assert "DELETE FROM" not in postgres
    assert "INSERT INTO" not in postgres
    assert "UPDATE " not in postgres


def test_oss_collector_image_builds_both_unified_processes() -> None:
    dockerfile = (_REPOSITORY / "fi-collector" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "./cmd/fi-collector" in dockerfile
    assert "./cmd/fi-property-catalog-consumer" in dockerfile
    assert (
        "COPY --from=build /out/fi-property-catalog-consumer "
        "/usr/local/bin/fi-property-catalog-consumer"
    ) in dockerfile
