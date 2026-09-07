"""Private Linux fixture driver for the replicated application lane.

Not a runtime factory: child processes invoke the checked-in production command,
Go services and authenticated Django views. No installation/admission/checkpoint/
activation is constructed here. The host harness owns Docker and cleanup.
The three replicas are catalog replicas; canonical spans remains local to replica1.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
NODES = ("replica1", "replica2", "replica3")
SERVICE_PORTS = {
    **dict.fromkeys(NODES, (8123, 9000)),
    "postgres": (5432,),
    "kafka": (9092,),
    "redis": (6379,),
}
REQUIRED_IMPORTS = {
    "django": "Django",
    "rest_framework": "djangorestframework",
    "clickhouse_driver": "clickhouse-driver",
    "psycopg": "psycopg",
    "requests": "requests",
}
TABLES = (
    "property_definition_catalog",
    "span_attribute_value_catalog",
    "property_catalog_checkpoints",
    "property_catalog_activations",
    "property_catalog_deliveries",
    "property_catalog_source_streams",
    "property_catalog_activation_control_events",
)
METADATA = (
    "clusters",
    "databases",
    "tables",
    "replicas",
    "zookeeper",
    "zookeeper_connection",
    "query_log",
    "parts",
)


def save(path, value):
    from application_lane import canonical, save_new

    save_new(path, canonical(value))


def dependency_evidence():
    # Fail before database I/O; never install into the base or fetch packages.
    result = {}
    for module, distribution in REQUIRED_IMPORTS.items():
        importlib.import_module(module)
        result[module] = importlib.metadata.version(distribution)
    return {"python": sys.version.split()[0], "packages": result}


def service_addresses():
    result = {}
    for service in SERVICE_PORTS:
        addresses = {
            row[4][0]
            for row in socket.getaddrinfo(
                service, None, socket.AF_INET, socket.SOCK_STREAM
            )
        }
        if len(addresses) != 1:
            raise RuntimeError("ambiguous private fixture DNS")
        address = addresses.pop()
        parsed = ipaddress.ip_address(address)
        if not parsed.is_private or parsed.is_loopback or parsed.is_unspecified:
            raise RuntimeError("fixture service is not a private network endpoint")
        result[service] = address
    if len(set(result.values())) != len(result):
        raise RuntimeError("fixture services must have distinct direct addresses")
    return result


def network_guard(addresses):
    allowed = {
        (addresses[service], port)
        for service, ports in SERVICE_PORTS.items()
        for port in ports
    } | {("127.0.0.1", 4318)}

    def audit(event, args):
        if event != "socket.connect":
            return
        address = args[1]
        if not isinstance(address, tuple) or len(address) < 2:
            raise RuntimeError("fixture refuses non-TCP socket access")
        host, port = address[:2]
        if host in addresses:
            host = addresses[host]
        if (host, port) not in allowed:
            raise RuntimeError("fixture refused an unowned socket endpoint")

    return audit


def python_environment(directory, m, addresses, *, admin=False, reader_node=None):
    runtime, spool = directory / "application-runtime", directory / "application-spool"
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/tmp",
        "PYTHONPATH": os.pathsep.join(
            (
                str(directory / "backend"),
                str(HERE),
                str(HERE.parent / "replicated_smoke"),
            )
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "MANAGED_SMOKE_DIRECTORY": str(directory),
        "DJANGO_SETTINGS_MODULE": "application_settings",
        "ENV_TYPE": "production",
        "CLOUD_DEPLOYMENT": "US",
        "EE_LICENSE_KEY": "",
        "SECRET_KEY": "disposable-" + m["run_id"],
        "SERVICE_TYPE": "bootstrap",
        "STARTUP_DB_MUTATION_MODE": "disabled",
        "NO_STARTUP_DB_MUTATIONS": "true",
        "FAST_STARTUP": "true",
        "FUTURE_AGI_TELEMETRY_DISABLED": "true",
        "OTEL_ENABLED": "false",
        "SENTRY_ENABLED": "false",
        "AWS_EC2_METADATA_DISABLED": "true",
        "CH_ENABLED": "false",
        "FI_SKIP_CH25_SCHEMA_APPLY": "1",
        "FI_SKIP_CH25_MIGRATION": "1",
        "PG_DB": "managed_smoke",
        "PGBOUNCER_HOST": addresses["postgres"],
        "PGBOUNCER_PORT": "5432",
        "PG_HOST": addresses["postgres"],
        "PG_PORT": "5432",
        "PG_USER": "smoke_admin" if admin else "property_catalog_oss_reader",
        "PG_PASSWORD": m["password"]
        if admin
        else "oss-catalog-postgres-reader-local-only",
        "CH25_HOST": "replica1",
        "CH25_TCP_PORT": "9000",
        "CH25_HTTP_PORT": "8123",
        "CH25_DATABASE": "default",
        "CH25_USER": "pc_source",
        "CH25_PASSWORD": m["password"],
        "CH25_SERVER_ENFORCED_READONLY": "true",
        "CH25_DROP_LEGACY_CDC_CHAIN": "false",
        "CH25_TRACE_DUAL_WRITE": "false",
        "CH_DUAL_WRITE": "false",
        "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "off",
        "PROPERTY_CATALOG_READ_MODE": "off",
        "PROPERTY_CATALOG_PRODUCTION_DATABASE": m["database"],
        "PROPERTY_CATALOG_DATABASE": m["database"],
        "PROPERTY_CATALOG_CH_HOST": reader_node or "replica1",
        "PROPERTY_CATALOG_CH_PORT": "9000",
        "PROPERTY_CATALOG_CH_USER": "pc_api",
        "PROPERTY_CATALOG_CH_PASSWORD": m["password"],
        "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED": "false",
        "PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC": m["candidate_topic"],
        "PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC": m["ordered_topic"],
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_ACK": "PROPERTY_CATALOG_ACTIVATION_CONTROL_V1",
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER": "pc_activation",
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_PASSWORD": m["password"],
        "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "pc_ledger",
        "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": m["password"],
    }
    lifecycle = {
        "ENABLED": "true",
        "ACK": "PROPERTY_CATALOG_PRODUCTION_LIFECYCLE_V1",
        "IDENTITY": "prod:pcapplication:" + m["run_id"],
        "SOURCE_DATABASE": "default",
        "TARGET_DATABASE": m["database"],
        "WRITE_CH_HOST": "replica1",
        "WRITE_CH_PORT": "9000",
        "WRITE_CH_USER": "pc_lifecycle",
        "WRITE_CH_PASSWORD": m["password"],
        "EXPECTED_WRITE_CH_HOSTNAMES": ",".join(m["project"] + "-" + n for n in NODES),
        "EXPECTED_SOURCE_CH_HOSTNAME": m["project"] + "-replica1",
        "EXPECTED_PG_DATABASE": "managed_smoke",
        "EXPECTED_PG_USER": "property_catalog_oss_reader",
        "EXPECTED_PG_SERVER_ADDRESS": addresses["postgres"],
        "EXPECTED_PG_SERVER_PORT": "5432",
        "RUNTIME_DIRECTORY": str(runtime),
        "HEALTH_FILE": str(runtime / "health.json"),
        "REVISION_FENCE_FILE": str(runtime / "revision-fence-v2.json"),
        "DRAIN_PROOF_FILE": str(spool / "producer-drain-proof-v2.json"),
        "PRODUCER_RETIREMENT_FILE": str(spool / "producer-state-retirements-v1.json"),
        "WORKSPACE_SCOPE_MODE": "all",
        "WORKSPACE_ALLOWLIST": "",
        "POLL_SECONDS": "5",
        "FAILURE_BACKOFF_SECONDS": "5",
        "SPAN_WINDOW_DAYS": "1",
        "MAX_WALL_MS": "100000",
        "SCHEDULED_RECONCILE_WALL_MS": "180000",
    }
    env.update({"PROPERTY_CATALOG_LIFECYCLE_" + k: v for k, v in lifecycle.items()})
    if reader_node is not None:
        if reader_node not in NODES:
            raise ValueError("reader must be one exact direct member")
        env.update(
            PROPERTY_CATALOG_READ_MODE="read",
            PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE="all",
            PROPERTY_CATALOG_PROD_READ_ACK="I_ACKNOWLEDGE_PROD_READ_ONLY_UNIFIED_PROPERTY_CATALOG",
        )
    return env


def go_environments(directory, m):
    common = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/tmp",
        "GOMAXPROCS": "2",
        "FI_PROPERTY_CATALOG_ENVIRONMENT": "production",
        "FI_PROPERTY_CATALOG_PROD_ACK": "UNIFIED_PROPERTY_CATALOG_V1_PRODUCTION",
        "FI_PROPERTY_CATALOG_PRODUCTION_DATABASE": m["database"],
        "FI_PROPERTY_CATALOG_KAFKA_BROKERS": "kafka:9092",
        "FI_PROPERTY_CATALOG_KAFKA_TOPIC": m["ordered_topic"],
        "FI_PROPERTY_CATALOG_REVISION_FENCE_FILE": str(
            directory / "application-runtime/revision-fence-v2.json"
        ),
        "FI_PROPERTY_CATALOG_DELIVERY_TIMEOUT": "5s",
    }
    seq = {
        **common,
        "FI_PROPERTY_CATALOG_MODE": "sequencer",
        "FI_PROPERTY_CATALOG_WORKSPACE_SCOPE_MODE": "revision_fence",
        "FI_PROPERTY_CATALOG_SPOOL_DIR": str(directory / "application-spool"),
        "FI_PROPERTY_CATALOG_SEQUENCER_TRANSACTIONAL_ID": m["project"] + "-owner",
        "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_BROKERS": "kafka:9092",
        "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC": m["candidate_topic"],
        "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_CONSUMER_GROUP": m["project"]
        + "-candidates",
        "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_INSTANCE_ID": m["project"] + "-instance",
        "FI_PROPERTY_CATALOG_SEQUENCER_STARTUP_TIMEOUT": "15s",
        "FI_PROPERTY_CATALOG_REPLAY_INTERVAL": "100ms",
        "FI_PROPERTY_CATALOG_SHUTDOWN_TIMEOUT": "5s",
        "FI_PROPERTY_CATALOG_MAX_SPOOL_FILES": "64",
        "FI_PROPERTY_CATALOG_MAX_SPOOL_BYTES": "4194304",
        "FI_PROPERTY_CATALOG_CANDIDATE_RECEIPT_MAX_FILES": "64",
        "FI_PROPERTY_CATALOG_CANDIDATE_RECEIPT_MAX_BYTES": "4194304",
    }
    consumer = {
        **common,
        "FI_PROPERTY_CATALOG_CONSUMER_MODE": "kafka",
        "FI_PROPERTY_CATALOG_KAFKA_CONSUMER_GROUP": m["project"] + "-delivery",
        "FI_PROPERTY_CATALOG_CH_URL": "http://replica1:8123",
        "FI_PROPERTY_CATALOG_CH_DATABASE": m["database"],
        "FI_PROPERTY_CATALOG_CH_USERNAME": "pc_consumer",
        "FI_PROPERTY_CATALOG_CH_PASSWORD": m["password"],
        "FI_PROPERTY_CATALOG_LEDGER_CH_URL": "http://replica1:8123",
        "FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE": m["database"],
        "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "pc_ledger",
        "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": m["password"],
        "FI_PROPERTY_CATALOG_CHECKPOINT_MAX_STREAMS": "64",
        "FI_PROPERTY_CATALOG_CHECKPOINT_MAX_INVENTORY_BYTES": "1048576",
        "FI_PROPERTY_CATALOG_CHECKPOINT_INVENTORY_TIMEOUT": "5s",
    }
    # Collector has no catalog writer/reader credentials and does not receive a
    # manual catalog identity. Normal authenticated ingestion emits v2 candidates.
    collector = {
        k: v
        for k, v in common.items()
        if k
        not in {
            "FI_PROPERTY_CATALOG_REVISION_FENCE_FILE",
            "FI_PROPERTY_CATALOG_DELIVERY_TIMEOUT",
        }
    }
    collector.update(
        FI_CH_URL="http://replica1:8123",
        FI_CH_DATABASE="default",
        FI_CH_USERNAME="pc_ingest",
        FI_CH_PASSWORD=m["password"],
        FI_PG_WRITE=f"postgres://pc_collector:{m['password']}@postgres:5432/managed_smoke?sslmode=disable",
        FI_PG_READ=f"postgres://pc_collector:{m['password']}@postgres:5432/managed_smoke?sslmode=disable",
        FI_AUTH_REDIS_ADDR="redis:6379",
        FI_GRPC_ADDR="127.0.0.1:4317",
        FI_HTTP_ADDR="127.0.0.1:4318",
        FI_CATALOG_MODE="disabled",
        FI_PROPERTY_CATALOG_MODE="kafka",
        FI_PROPERTY_CATALOG_KAFKA_TOPIC=m["candidate_topic"],
        FI_PROPERTY_CATALOG_SPOOL_DIR=str(directory / "collector-spool"),
        FI_DEAD_LETTER_FILE=str(directory / "collector-dead-letter.jsonl"),
    )
    return seq, consumer, collector


def grants(m):
    database, password = m["database"], m["password"]
    capture_database = database + "_source_capture"
    # Keep the six table-exact catalog grants. Only the separately reviewed
    # source-capture bundle may name this exact derived namespace with a wildcard.
    # The unmodified admission producer checks actual SHOW GRANTS.
    statements = [
        f"CREATE USER {name} IDENTIFIED WITH sha256_password BY '{password}' SETTINGS readonly={readonly}"
        for name, readonly in (
            ("pc_lifecycle", 0),
            ("pc_activation", 0),
            ("pc_consumer", 0),
            ("pc_ledger", 2),
            ("pc_api", 2),
            ("pc_source", 1),
            ("pc_ingest", 0),
        )
    ]
    for table in TABLES:
        if table != TABLES[-1]:
            statements.append(
                f"GRANT SELECT, INSERT ON {database}.{table} TO pc_lifecycle"
            )
        for name in ("pc_ledger", "pc_api"):
            statements.append(f"GRANT SELECT ON {database}.{table} TO {name}")
    for table in (TABLES[0], TABLES[1], TABLES[4]):
        statements.append(f"GRANT INSERT ON {database}.{table} TO pc_consumer")
    statements.extend(
        (
            f"GRANT SELECT ON {database}.property_catalog_activations TO pc_activation",
            f"GRANT SELECT, INSERT ON {database}.{TABLES[-1]} TO pc_activation",
            "GRANT SELECT ON default.spans TO pc_source",
            f"GRANT SELECT ON {capture_database}.* TO pc_source",
            "GRANT SELECT ON system.settings TO pc_source",
            "GRANT SELECT(database, table, active, name, hash_of_all_files, rows, bytes_on_disk, disk_name) ON system.parts TO pc_source",
            "GRANT SELECT(name, uuid, engine) ON system.databases TO pc_source",
            "GRANT SELECT(database, name, uuid, engine, create_table_query, storage_policy) ON system.tables TO pc_source",
            "GRANT SELECT(policy_name, disks) ON system.storage_policies TO pc_source",
            "GRANT SELECT(database, table, name, type, default_kind, default_expression) ON system.columns TO pc_source",
            "GRANT SELECT(database, table, active, name, rows, bytes_on_disk, column, type) ON system.parts_columns TO pc_source",
            "GRANT SELECT(name, path, total_space, unreserved_space, keep_free_space, type, is_read_only) ON system.disks TO pc_source",
            "GRANT SELECT(database, table, zookeeper_path, zookeeper_name) ON system.replicas TO pc_source",
            "GRANT SELECT ON default.spans TO pc_lifecycle",
            "GRANT SELECT(database, table, is_readonly, is_session_expired, queue_size, active_replicas, total_replicas) ON system.replicas TO pc_lifecycle",
            f"GRANT SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL, DROP TABLE ON {capture_database}.* TO pc_lifecycle",
            "GRANT INSERT ON default.spans TO pc_ingest",
        )
    )
    statements += [f"GRANT SELECT ON system.{table} TO pc_ledger" for table in METADATA]
    statements.append(
        "ALTER USER pc_source SETTINGS readonly=1, max_execution_time=30, max_threads=4, "
        "max_memory_usage=4294967296, max_bytes_to_read=42949672960, max_result_rows=250000, "
        "max_result_bytes=67108864, read_overflow_mode='throw', result_overflow_mode='throw', timeout_overflow_mode='throw'"
    )
    return tuple(statements)


def client(m, node="replica1", *, user="smoke_admin", database="default"):
    from clickhouse_driver import Client

    return Client(
        node,
        port=9000,
        user=user,
        password=m["password"],
        database=database,
        connect_timeout=3,
        send_receive_timeout=35,
        settings={
            "max_threads": 1,
            "max_execution_time": 30,
            "max_memory_usage": 268435456,
        },
    )


def bootstrap(directory, m):
    from application_lane import canonical, digest, read_file, schema

    from tracer.services.clickhouse.v2.catalog_prod_schema import (
        install_catalog_prod_schema,
    )

    planned = read_file(directory / "schema.json")
    if digest(planned) != m["files"]["schema.json"] or canonical(schema(m)) != planned:
        raise RuntimeError(
            "frozen public production renderer differs from the exact plan"
        )

    ch = client(m)

    class Adapter:
        def query_rows(self, sql, parameters=None):
            return ch.execute(sql, parameters)

        def command(self, sql):
            return ch.execute(sql)

    try:
        evidence = json.loads(
            install_catalog_prod_schema(
                Adapter(),
                target_database=m["database"],
                cluster="smoke_cluster",
                keeper_path_prefix="/clickhouse/tables",
            )
        )
        save(directory / "production-schema-evidence.json", evidence)
    finally:
        ch.disconnect()
    for node in NODES:
        ch = client(m, node)
        try:
            # Fresh local namespace on each owned member; never adopt an existing
            # database. Runtime creates only member-local derived tables in it.
            ch.execute(
                f"CREATE DATABASE `{m['database']}_source_capture` ENGINE = Atomic"
            )
            for sql in grants(m):
                ch.execute(sql)
        finally:
            ch.disconnect()


def seed(directory, m):
    """Synthetic canonical source/ORM only; never write a catalog table."""
    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import OrgApiKey, User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.roles import OrganizationRoles
    from tracer.models.project import Project

    organization, workspace, project, foreign = (str(uuid.uuid4()) for _ in range(4))
    org = Organization.objects.create(id=organization, name="Managed isolated smoke")
    user = User.objects.create_user(
        email=m["run_id"] + "@managed-smoke.invalid",
        name="Synthetic smoke owner",
        organization=org,
    )
    work = Workspace.no_workspace_objects.create(
        id=workspace, organization=org, name="smoke", is_default=True, created_by=user
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        organization=org, user=user, defaults={"role": OrganizationRoles.OWNER}
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        workspace=work,
        user=user,
        defaults={
            "role": OrganizationRoles.WORKSPACE_ADMIN,
            "organization_membership": membership,
        },
    )
    OrgApiKey.objects.create(
        name="managed-smoke-api",
        type="user",
        user=user,
        organization=org,
        workspace=work,
    )
    Project.no_workspace_objects.create(
        id=project,
        organization=org,
        workspace=work,
        name="Managed source fixture",
        model_type="GenerativeLLM",
        trace_type="observe",
        user=user,
    )
    at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=2
    )
    row = {
        "project_id": project,
        "org_id": organization,
        "observation_type": "span",
        "service_name": "managed-smoke",
        "start_time": at,
        "created_at": at,
        "updated_at": at,
        "trace_id": "smoke-trace",
        "id": "span-main",
        "name": "Smoke span",
        "model": "Smoke-Model",
        "attrs_string": {"plan": "Pro", "quote": 'a"b\\c', "unicode": "café"},
        "attrs_number": {"score": 1.5, "negative": -12.5, "zero": 0},
        "attrs_bool": {"enabled": 1, "disabled": 0},
        "attributes_extra": json.dumps(
            {
                "tags": ["A", "B", "A", None, 3, True, {"nested": 1}],
                "map_only": {"nested": 4},
                "null_only": None,
                "empty_array": [],
            },
            separators=(",", ":"),
        ),
        "is_deleted": 0,
        "_version": 1,
    }
    old = {
        **row,
        "id": "span-versioned",
        "attrs_string": {"versioned": "old"},
        "attrs_number": {},
        "attrs_bool": {},
        "attributes_extra": "{}",
        "model": "",
    }
    deleted = {
        **old,
        "id": "span-deleted",
        "attrs_string": {"deleted_value": "MUST_NOT_APPEAR"},
    }
    rows = [
        row,
        old,
        {**old, "attrs_string": {"versioned": "new"}, "_version": 2},
        deleted,
        {**deleted, "is_deleted": 1, "_version": 2},
        {
            **old,
            "id": "span-foreign",
            "project_id": foreign,
            "attrs_string": {"foreign_value": "MUST_NOT_APPEAR"},
        },
    ]
    ch = client(m)
    try:
        ch.execute(
            f"INSERT INTO default.spans ({','.join(row)}) VALUES",
            [tuple(r[k] for k in row) for r in rows],
        )
    finally:
        ch.disconnect()
    save(
        directory / "source-fixture.json",
        {
            "organization_id": organization,
            "workspace_id": workspace,
            "project_id": project,
            "since": at.isoformat(),
            "physical_rows": 6,
            "logical_scoped_rows": 2,
        },
    )


def api_checks(directory, *, ingested=False, restarted=False):
    from rest_framework.test import APIClient
    from workspace_smoke import active_result, request

    from accounts.models.user import OrgApiKey

    fixture = json.loads((directory / "source-fixture.json").read_bytes())
    key = OrgApiKey.objects.get(
        name="managed-smoke-api", workspace_id=fixture["workspace_id"]
    )
    api = APIClient()
    api.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_WORKSPACE_ID=fixture["workspace_id"],
    )
    expected = {
        "plan": ["Pro"],
        "quote": ['a"b\\c'],
        "unicode": ["café"],
        "score": [1.5],
        "negative": [-12.5],
        "zero": [0],
        "enabled": [True],
        "disabled": [False],
        "versioned": ["new"],
    }
    if ingested:
        expected.update(
            otlp_ordered=["initial"],
            otlp_live=["initial"],
            otlp_late=["initial"],
            otlp_number=[123],
            otlp_boolean=[False],
        )
    if restarted:
        expected.update(
            otlp_restart_live=["initial"],
            otlp_restart_late=["initial"],
            otlp_restart_ordered=["initial"],
        )
    reports = []
    for scope in ({"project_ids": fixture["project_id"]}, {}):
        for name, values in expected.items():
            params = {
                "source": "traces",
                "cursor_mode": "true",
                "page_size": 50,
                **scope,
            }
            properties = active_result(
                *request(api, "metrics", {**params, "search": name})
            )
            matching = [p for p in properties["metrics"] if p.get("name") == name]
            if len(matching) != 1:
                raise RuntimeError("expected exact typed property is not visible")
            result = active_result(
                *request(
                    api,
                    "filter_values",
                    {**params, "property_id": matching[0]["property_id"]},
                )
            )
            if [v["value"] for v in result["values"]] != values:
                raise RuntimeError("canonical typed API values differ")
            reports.append(
                {
                    "name": name,
                    "project_scope": bool(scope),
                    "provenance": result["query_provenance"],
                    "catalog_epoch": result["catalog_epoch"],
                    "catalog_revision": result["catalog_revision"],
                    "activation_fingerprint": result["activation_fingerprint"],
                    "values": values,
                }
            )
        for name in ("deleted_value", "foreign_value"):
            result = active_result(
                *request(
                    api,
                    "metrics",
                    {
                        "source": "traces",
                        "cursor_mode": "true",
                        "page_size": 50,
                        "search": name,
                        **scope,
                    },
                )
            )
            if any(p.get("name") == name for p in result["metrics"]):
                raise RuntimeError("deleted or foreign source leaked through the API")
    return reports


class OrderedBuildNotReady(RuntimeError):
    """The controller has not yet opened the next ordered source window."""


def ordered_observation(directory, fixture):
    """Choose a real observation inside the controller's current build window."""
    record = json.loads(
        (directory / "application-runtime/revision-fence-v2.json").read_bytes()
    )
    matches = [
        fence
        for fence in record["fences"]
        if fence["organization_id"] == fixture["organization_id"]
        and fence["workspace_id"] == fixture["workspace_id"]
        and fixture["project_id"] in fence["project_ids"]
        and fence["status"] == "building"
    ]
    if not matches:
        raise OrderedBuildNotReady(
            "ordered probe is waiting for a current workspace build"
        )
    if len(matches) != 1:
        raise RuntimeError("ordered probe requires one current workspace build")
    fence = matches[0]
    since, until = fence["span_since_us"], fence["span_until_us"]
    if until - since < 2000:
        raise RuntimeError("ordered probe requires a nonempty source window")
    return ((since + until) // 2) * 1000


def ingest(directory, m, *, restarted=False, ordered=False):
    import requests

    from accounts.models.user import OrgApiKey

    fixture = json.loads((directory / "source-fixture.json").read_bytes())
    key = OrgApiKey.objects.get(
        name="managed-smoke-api", workspace_id=fixture["workspace_id"]
    )
    spans = []
    prefix = "otlp_restart_" if restarted else "otlp_"
    observations = (
        (prefix + "live", time.time_ns()),
        (
            prefix + "late",
            int(datetime.fromisoformat(fixture["since"]).timestamp() * 1e9),
        ),
    )
    if ordered:
        until = time.monotonic() + 180
        while True:
            try:
                stamp = ordered_observation(directory, fixture)
                break
            except OrderedBuildNotReady:
                if time.monotonic() >= until:
                    raise
                time.sleep(0.2)
        observations = ((prefix + "ordered", stamp),)
    for label, stamp in observations:
        identity = uuid.uuid5(uuid.NAMESPACE_URL, m["run_id"] + label).hex
        spans.append(
            {
                "traceId": identity,
                "spanId": identity[:16],
                "name": label,
                "kind": 1,
                "startTimeUnixNano": str(stamp),
                "endTimeUnixNano": str(stamp + 1000000),
                "attributes": [
                    {"key": label, "value": {"stringValue": "initial"}},
                    {"key": "otlp_number", "value": {"intValue": "123"}},
                    {"key": "otlp_boolean", "value": {"boolValue": False}},
                    {
                        "key": "otlp_array",
                        "value": {
                            "arrayValue": {
                                "values": [
                                    {"stringValue": "one"},
                                    {"intValue": "2"},
                                    {"boolValue": True},
                                ]
                            }
                        },
                    },
                ],
            }
        )
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "project_name",
                            "value": {"stringValue": "Managed source fixture"},
                        },
                        {
                            "key": "service.name",
                            "value": {"stringValue": "managed-smoke-otlp"},
                        },
                    ]
                },
                "scopeSpans": [{"scope": {"name": "managed-smoke"}, "spans": spans}],
            }
        ]
    }
    with requests.Session() as session:
        session.trust_env = False
        url = "http://127.0.0.1:4318/v1/traces"
        rejected = session.post(url, json=payload, timeout=5, allow_redirects=False)
        if rejected.status_code != 401:
            raise RuntimeError("unauthenticated OTLP was not rejected")
        # Exactly one authenticated submission; a timeout is unresolved, not permission to resend.
        response = session.post(
            url,
            json=payload,
            headers={"X-Api-Key": key.api_key, "X-Secret-Key": key.secret_key},
            timeout=10,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise RuntimeError("authenticated OTLP did not acknowledge")
    report = "otlp-submission" + ("-restarted" if restarted else "")
    report += "-ordered" if ordered else ""
    save(
        directory / (report + ".json"),
        {
            "status": "acknowledged",
            "names": [label for label, _ in observations],
            "organization_id": fixture["organization_id"],
            "project_id": fixture["project_id"],
        },
    )


class Children:
    """Only owned Popen handles, bounded log files, no process-name signalling."""

    def __init__(self, directory, deadline):
        self.directory, self.deadline, self.children = directory, deadline, []

    def start(self, label, argv, env):
        path = self.directory / (label + ".log")
        stream = path.open("xb")
        try:
            child = subprocess.Popen(
                argv,
                env=env,
                cwd=self.directory / "backend",
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            stream.close()
            raise
        self.children.append((child, stream, path))
        return child

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("application wall budget expired")
        for child, _, path in self.children:
            if child.poll() is not None or path.stat().st_size > 2_000_000:
                raise RuntimeError(
                    "owned application child exited or exceeded log budget: "
                    + path.name
                )

    def stop(self):
        for child, _, _ in self.children:
            if child.poll() is None:
                child.terminate()
        for child, stream, _ in self.children:
            try:
                child.wait(timeout=7)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
            finally:
                stream.close()
        self.children.clear()


def child_action(action, directory, m):
    if action == "bootstrap":
        bootstrap(directory, m)
        return
    import django

    django.setup()
    from django.core.management import call_command

    if action == "migrate":
        call_command("migrate", interactive=False, skip_checks=True, verbosity=0)
    elif action == "fixture":
        seed(directory, m)
    elif action == "controller":
        # Keep the underlying failure in private fixture logs; the command's
        # ordinary retry message intentionally reports only the outer error.
        import traceback

        from tracer.services.clickhouse.v2.property_catalog import dev_runtime

        inventory_probe = dev_runtime._postgres_workspace_project_inventory

        def diagnostic_inventory_probe(*args, **kwargs):
            try:
                return inventory_probe(*args, **kwargs)
            except Exception:
                traceback.print_exc()
                raise

        dev_runtime._postgres_workspace_project_inventory = diagnostic_inventory_probe
        drain_parser = dev_runtime.parse_producer_drain_proof

        def diagnostic_drain_parser(raw):
            try:
                return drain_parser(raw)
            except Exception:
                # Local synthetic fixture only: preserve the exact rejected
                # observation rather than infer its bytes from a later file.
                with (directory / "rejected-drain-proof.bin").open("xb") as evidence:
                    evidence.write(raw)
                traceback.print_exc()
                raise

        dev_runtime.parse_producer_drain_proof = diagnostic_drain_parser
        call_command("ch25_property_catalog_lifecycle_controller", skip_checks=True)
    elif action in {
        "ingest",
        "ingest-restarted",
        "ingest-ordered",
        "ingest-restarted-ordered",
    }:
        ingest(
            directory,
            m,
            restarted="restarted" in action,
            ordered=action.endswith("ordered"),
        )
    elif action.startswith("read-"):
        node = os.environ["PROPERTY_CATALOG_CH_HOST"]
        # Only read observations may repeat while reconciliation publishes. No
        # ingestion, delivery, checkpoint or activation is retried or synthesized.
        read_started = time.monotonic()
        read_deadline = read_started + 180
        while True:
            try:
                reports = api_checks(
                    directory,
                    ingested=action != "read-initial",
                    restarted=action == "read-restarted",
                )
                break
            except (RuntimeError, AssertionError):
                if time.monotonic() >= read_deadline:
                    raise
                time.sleep(1)
        save(
            directory / (action + "-" + node + ".json"),
            reports,
        )
        elapsed = time.monotonic() - read_started
        save(
            directory / (action + "-" + node + "-timing.json"),
            {
                "validation_elapsed_seconds": elapsed,
                "correctness_budget_seconds": 180,
                "per_request_latency_benchmark": False,
            },
        )
    else:
        raise ValueError("unknown application child action")


def health_observation_ready(raw, previous=None):
    if raw == previous:
        return False
    record = json.loads(raw)
    if record.get("phase") == "retrying" or record.get("detail", {}).get(
        "failed_count"
    ):
        raise RuntimeError(
            "production controller failed safely; see owned controller log"
        )
    return record.get("ready") is True and record.get("phase") == "idle"


def execute(directory, m, addresses):
    from application_lane import read_file

    # Catalog package imports resolve Django-backed limits immediately. The
    # orchestrator needs the same isolated settings as its application children.
    base = python_environment(directory, m, addresses)
    os.environ.update(base)
    from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
        load_identity,
    )
    from tracer.services.clickhouse.v2.property_catalog.write_admission import (
        WriteAdmission,
    )

    for name in ("application-runtime", "application-spool", "collector-spool"):
        (directory / name).mkdir(mode=0o700)
    deadline = time.monotonic() + 840
    children = Children(directory, deadline)
    admin = python_environment(directory, m, addresses, admin=True)
    helper = str(HERE / "linux_application_runner.py")
    health_before_restart = None

    def once(action, env, timeout=120):
        children.check()
        if action.startswith("read-") or action.endswith("ordered"):
            timeout = 210  # Bounded observations plus Django startup overhead.
        # These actions are each invoked once, never retried after an uncertain write.
        with (
            directory
            / (action + "-" + env.get("PROPERTY_CATALOG_CH_HOST", "setup") + ".log")
        ).open("xb") as log:
            subprocess.run(
                [sys.executable, "-B", helper, action, str(directory)],
                env=env,
                cwd=directory / "backend",
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=min(timeout, max(1, deadline - time.monotonic())),
                check=True,
            )

    def wait_for(predicate, seconds=180):
        until = min(deadline, time.monotonic() + seconds)
        while not predicate():
            children.check()
            if time.monotonic() >= until:
                raise TimeoutError(
                    "application observation did not converge within its bound"
                )
            time.sleep(1)

    def ready():
        path = directory / "application-runtime/health.json"
        if not path.exists():
            return False
        return health_observation_ready(read_file(path), health_before_restart)

    def active_everywhere():
        # This is a wait predicate, not publication proof. The API and normal
        # reader-control proofs remain the qualification gate on every node.
        fixture = json.loads(read_file(directory / "source-fixture.json"))
        for node in NODES:
            ch = client(m, node, user="pc_ledger", database=m["database"])
            try:
                rows = ch.execute(
                    "SELECT status FROM property_catalog_activations WHERE organization_id=%(org)s "
                    "AND workspace_id=%(workspace)s ORDER BY catalog_revision DESC, _version DESC LIMIT 1",
                    {
                        "org": fixture["organization_id"],
                        "workspace": fixture["workspace_id"],
                    },
                )
                if rows != [("active",)]:
                    return False
                controls = ch.execute(
                    "SELECT action FROM property_catalog_activation_control_events "
                    "WHERE organization_id=%(org)s AND workspace_id=%(workspace)s "
                    "ORDER BY control_sequence DESC LIMIT 1",
                    {
                        "org": fixture["organization_id"],
                        "workspace": fixture["workspace_id"],
                    },
                )
                if controls != [("follow",)]:
                    return False
            finally:
                ch.disconnect()
        return ready()

    try:
        once("bootstrap", admin)
        once("migrate", admin, 180)
        controller = [sys.executable, "-B", helper, "controller", str(directory)]
        children.start("controller-1", controller, base)
        wait_for(ready, 90)
        identity_path = directory / "application-runtime/runtime-identity-v1.json"
        identity = load_identity(identity_path)
        identity_sha256 = json.loads(identity.encode())["identity_sha256"]
        save(
            directory / "empty-installation.json",
            {"identity_sha256": identity_sha256, "ready": True},
        )
        once("fixture", admin)
        wait_for(
            lambda: (
                directory / "application-runtime/write-admission-v1.json"
            ).is_file(),
            90,
        )
        seq, consumer, collector = go_environments(directory, m)

        def start_chain(generation):
            children.start(
                "sequencer-" + str(generation),
                [str(directory / "bin/fi-property-catalog-sequencer")],
                seq,
            )
            children.start(
                "consumer-" + str(generation),
                [
                    str(directory / "bin/fi-property-catalog-consumer"),
                    "--seed-from-delivery-ledger",
                ],
                consumer,
            )

        start_chain(1)
        wait_for(active_everywhere)
        for node in NODES:
            once(
                "read-initial",
                python_environment(directory, m, addresses, reader_node=node),
            )
        collector_argv = [
            str(directory / "bin/fi-collector"),
            "-config",
            str(directory / "go-source/config/collector.yaml"),
        ]
        children.start("collector-1", collector_argv, collector)

        def collector_ready():
            try:
                with socket.create_connection(("127.0.0.1", 4318), timeout=1):
                    return True
            except OSError:
                return False

        wait_for(collector_ready, 20)
        once("ingest", base)
        once("ingest-ordered", base)

        # Wait only on positive normal Go ledger output; no synthetic delivery,
        # checkpoint or activation is inserted by this driver.
        def delivery_count():
            ch = client(m, user="pc_ledger", database=m["database"])
            try:
                return ch.execute(
                    "SELECT count() FROM property_catalog_deliveries WHERE transport='kafka' "
                    "AND envelope_format='futureagi.property-catalog-envelope' AND value_rows>0"
                )[0][0]
            finally:
                ch.disconnect()

        wait_for(lambda: delivery_count() > 0 and active_everywhere())
        for node in NODES:
            once(
                "read-ingested",
                python_environment(directory, m, addresses, reader_node=node),
            )
        admission = WriteAdmission.decode(
            read_file(directory / "application-runtime/write-admission-v1.json")
        )
        if (
            admission.family != "replicated"
            or admission.environment != "production"
            or admission.installation_sha256 != identity_sha256
            or len(admission.members) != 3
            or admission.database != m["database"]
        ):
            raise RuntimeError(
                "actual production admission differs from the frozen run"
            )
        children.stop()
        health_before_restart = read_file(directory / "application-runtime/health.json")
        before_restart = delivery_count()
        children.start("controller-2", controller, base)
        start_chain(2)
        wait_for(active_everywhere)
        children.start("collector-2", collector_argv, collector)
        wait_for(collector_ready, 20)
        once("ingest-restarted", base)
        once("ingest-restarted-ordered", base)
        wait_for(lambda: delivery_count() > before_restart and active_everywhere())
        for node in NODES:
            once(
                "read-restarted",
                python_environment(directory, m, addresses, reader_node=node),
            )
        children.check()
        if load_identity(identity_path) != identity:
            raise RuntimeError("ordinary restart changed the managed installation")
        save(
            directory / "replicated-application-result.json",
            {
                "status": "passed",
                "run_id": m["run_id"],
                "environment": "production",
                "members": [member.name for member in admission.members],
                "installation_sha256": identity_sha256,
                "topology_sha256": admission.topology_sha256,
                "stages": ["empty-installation", "initial", "ingested", "restarted"],
                "api_transport": "authenticated Django APIClient using real URL views",
                "replication_claim": "three ClickHouse members; one Keeper and one Kafka broker",
                "full_standalone_scenario_matrix": False,
            },
        )
    finally:
        children.stop()


def main():
    # This entry point is intentionally not a general remote application runner.
    if len(sys.argv) != 3 or Path(sys.argv[2]) != Path("/fixture") or os.getuid() == 0:
        raise RuntimeError("requires the exact private nonroot Linux fixture mount")
    directory = Path("/fixture")
    if (
        directory.is_symlink()
        or directory.stat().st_uid != os.getuid()
        or directory.stat().st_mode & 0o077
    ):
        raise RuntimeError("fixture root is not private and owned")
    sys.path[:0] = [
        str(directory / "backend"),
        str(HERE),
        str(HERE.parent / "replicated_smoke"),
    ]
    from application_lane import canonical, read_file, validate_manifest

    raw = read_file(directory / "manifest.json")
    m = json.loads(raw)
    validate_manifest(m)
    if canonical(m) != raw or m["owner_uid"] != os.getuid():
        raise RuntimeError("fixture manifest is not exact and owned")
    dependencies = dependency_evidence()
    addresses = service_addresses()
    sys.addaudithook(network_guard(addresses))
    if sys.argv[1] == "execute":
        save(directory / "linux-dependencies.json", dependencies)
        execute(directory, m, addresses)
    else:
        child_action(sys.argv[1], directory, m)


if __name__ == "__main__":
    main()
