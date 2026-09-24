#!/usr/bin/env python3
"""Provision only a fresh, isolated property catalog DEV catalog."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import secrets
import subprocess
import urllib.parse
import uuid
from pathlib import Path

SUFFIX = os.environ.get("FI_PROPERTY_CATALOG_ROLLOUT_SUFFIX", "0816d")
if re.fullmatch(r"[0-9]{4}[a-z]", SUFFIX) is None:
    raise RuntimeError("FI_PROPERTY_CATALOG_ROLLOUT_SUFFIX must match NNNNx")
_CATALOG_DATABASE_RE = re.compile(r"[a-z][a-z0-9_]{0,127}")
_RESERVED_CATALOG_DATABASES = frozenset(
    {"default", "futureagi", "information_schema", "property_catalog", "system"}
)


def _catalog_database(value: str) -> str:
    database = value.strip()
    if (
        _CATALOG_DATABASE_RE.fullmatch(database) is None
        or database in _RESERVED_CATALOG_DATABASES
    ):
        raise RuntimeError(
            "PROPERTY_CATALOG_DATABASE must be a safe lowercase ClickHouse "
            "identifier isolated from production and source databases"
        )
    return database


CATALOG_EPOCH = int(os.environ.get("FI_PROPERTY_CATALOG_EPOCH", "2"))
if not 1 <= CATALOG_EPOCH <= 65535:
    raise RuntimeError("FI_PROPERTY_CATALOG_EPOCH must be between 1 and 65535")
ORGANIZATION_ID = os.environ.get(
    "FI_PROPERTY_CATALOG_ORGANIZATION_ID", "36ab6a86-28ef-484e-9fa2-0aade2cde52d"
)
WORKSPACE_ID = os.environ.get(
    "FI_PROPERTY_CATALOG_WORKSPACE_ID", "f7f5533e-44a1-438b-9e6d-6f4747f1eb16"
)
PROJECT_IDS = tuple(
    value.strip()
    for value in os.environ.get(
        "FI_PROPERTY_CATALOG_PROJECT_IDS",
        "5272afb0-4b6e-4cc5-8415-d41b297f6a20,d9d8498f-abab-42e0-9794-bb75a7f27350",
    ).split(",")
    if value.strip()
)
SPAN_SINCE = os.environ.get("FI_PROPERTY_CATALOG_SPAN_SINCE", "2025-08-16T07:00:00Z")
SPAN_UNTIL = os.environ.get("FI_PROPERTY_CATALOG_SPAN_UNTIL", "2026-08-16T07:00:00Z")
for field_name, value in (
    ("FI_PROPERTY_CATALOG_ORGANIZATION_ID", ORGANIZATION_ID),
    ("FI_PROPERTY_CATALOG_WORKSPACE_ID", WORKSPACE_ID),
):
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise RuntimeError(f"{field_name} must be a UUIDv4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise RuntimeError(f"{field_name} must be a canonical UUIDv4")
if not 1 <= len(PROJECT_IDS) <= 256 or PROJECT_IDS != tuple(sorted(set(PROJECT_IDS))):
    raise RuntimeError(
        "FI_PROPERTY_CATALOG_PROJECT_IDS must contain 1..256 sorted unique UUIDv4s"
    )
for project_id in PROJECT_IDS:
    parsed = uuid.UUID(project_id)
    if parsed.version != 4 or str(parsed) != project_id:
        raise RuntimeError(
            "FI_PROPERTY_CATALOG_PROJECT_IDS must contain canonical UUIDv4s"
        )
ROOT = Path(f"/home/ubuntu/property-catalog-kartik-{SUFFIX}")
PRIVATE = ROOT / "private"
RUNTIME = ROOT / "runtime"
EVIDENCE = ROOT / "evidence"
TARGET = _catalog_database(
    os.environ.get(
        "PROPERTY_CATALOG_DATABASE",
        f"property_catalog_dev_kartik_{SUFFIX}",
    )
)
OP_IMAGE = os.environ.get(
    "FI_PROPERTY_CATALOG_OPERATOR_IMAGE",
    "sha256:a64747143de3c0865babbc5bf0d161be03ac6eec784e4d49be416e8b202b209f",
)
if re.fullmatch(r"sha256:[0-9a-f]{64}", OP_IMAGE) is None:
    raise RuntimeError("FI_PROPERTY_CATALOG_OPERATOR_IMAGE must be an exact image ID")
OP_RUNTIME_IMAGE = os.environ.get(
    "FI_PROPERTY_CATALOG_OPERATOR_RUNTIME_IMAGE",
    "fi-property-catalog-current-select:0816d-7a7dc207",
)
if (
    re.fullmatch(r"fi-property-catalog-current-select:[0-9a-z-]+", OP_RUNTIME_IMAGE)
    is None
):
    raise RuntimeError(
        "FI_PROPERTY_CATALOG_OPERATOR_RUNTIME_IMAGE must be a property catalog DEV tag"
    )
GO_IMAGE = os.environ.get(
    "FI_PROPERTY_CATALOG_GO_IMAGE",
    "sha256:84c45a5c36b71430e1cc5845ab8ce4f0da84332eb97dc8903bfd9c33bd2be1c1",
)
if re.fullmatch(r"sha256:[0-9a-f]{64}", GO_IMAGE) is None:
    raise RuntimeError("FI_PROPERTY_CATALOG_GO_IMAGE must be an exact image ID")
SOURCE_USER = f"property_catalog_source_ro_{SUFFIX}"
CONTROL_USER = f"property_catalog_control_rw_{SUFFIX}"
CONSUMER_USER = f"property_catalog_consumer_rw_{SUFFIX}"
LEDGER_USER = f"property_catalog_ledger_ro_{SUFFIX}"
API_USER = f"property_catalog_api_ro_{SUFFIX}"
PG_USER = f"property_catalog_pg_ro_{SUFFIX}"
USERS = (SOURCE_USER, CONTROL_USER, CONSUMER_USER, LEDGER_USER, API_USER)
SCHEMA = (
    (
        "025_property_catalog_data.sql",
        "2cc25d270f34a654b46855dd23f1362e854242cda93a465ddab6f3810bab3437",
    ),
    (
        "026_property_catalog_state.sql",
        "5b54ce0ccff8c5ee4a2bb8f391be142933740f86d73d5a0b14af866feb96d7e6",
    ),
    (
        "027_property_catalog_delivery.sql",
        "f3591e491d6a0a0f733b6aada56f02c0956b8f2524dded2b459211e40f8b85d2",
    ),
)
TABLES = (
    "property_catalog_activations",
    "property_catalog_checkpoints",
    "property_catalog_deliveries",
    "property_catalog_source_streams",
    "property_definition_catalog",
    "span_attribute_value_catalog",
)


def run(
    argv: list[str], *, data: str | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, input=data, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"command failed rc={result.returncode}: {argv[0]}")
    return result


def ch(sql: str) -> None:
    run(
        [
            "docker",
            "exec",
            "-i",
            "futureagi-clickhouse-1",
            "clickhouse-client",
            "--multiquery",
        ],
        data=sql + "\n",
    )


def chq(sql: str) -> str:
    return run(
        [
            "docker",
            "exec",
            "futureagi-clickhouse-1",
            "clickhouse-client",
            "--query",
            sql,
        ]
    ).stdout


def pg(sql: str) -> None:
    run(
        [
            "docker",
            "exec",
            "-i",
            "futureagi-postgres-1",
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "user",
            "-d",
            "tfc",
            "-q",
        ],
        data=sql + "\n",
    )


def write_private(name: str, values: dict[str, str]) -> Path:
    path = PRIVATE / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        for key in sorted(values):
            value = values[key]
            if "\n" in value or "\r" in value:
                raise RuntimeError("invalid environment value")
            handle.write(f"{key}={value}\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def main() -> None:
    if ROOT.exists():
        raise RuntimeError(f"{SUFFIX} runtime root already exists")
    if (
        chq(f"SELECT count() FROM system.databases WHERE name='{TARGET}'").strip()
        != "0"
    ):
        raise RuntimeError(f"{SUFFIX} target database is not absent")
    names = ",".join(f"'{name}'" for name in USERS)
    if chq(f"SELECT count() FROM system.users WHERE name IN ({names})").strip() != "0":
        raise RuntimeError(f"{SUFFIX} ClickHouse user collision")
    pg_pre = run(
        [
            "docker",
            "exec",
            "futureagi-postgres-1",
            "psql",
            "-X",
            "-U",
            "user",
            "-d",
            "tfc",
            "-At",
            "-c",
            f"SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname='{PG_USER}'",
        ]
    ).stdout.strip()
    if pg_pre != "0":
        raise RuntimeError(f"{SUFFIX} PostgreSQL role collision")

    ROOT.mkdir(mode=0o700)
    PRIVATE.mkdir(mode=0o700)
    EVIDENCE.mkdir(mode=0o700)
    run(
        [
            "sudo",
            "install",
            "-d",
            "-o",
            "ubuntu",
            "-g",
            "65532",
            "-m",
            "0770",
            str(RUNTIME),
            str(RUNTIME / "cache"),
            str(RUNTIME / "home"),
            str(RUNTIME / "span-dead-letter"),
        ]
    )
    run(
        [
            "sudo",
            "install",
            "-d",
            "-o",
            "65532",
            "-g",
            "65532",
            "-m",
            "0700",
            str(RUNTIME / "catalog-spool"),
        ]
    )

    ch(f"CREATE DATABASE {TARGET}")
    for filename, expected in SCHEMA:
        image_path = "/app/backend/tracer/services/clickhouse/v2/schema/" + filename
        raw = run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "cat",
                OP_IMAGE,
                image_path,
            ]
        ).stdout
        if hashlib.sha256(raw.encode()).hexdigest() != expected:
            raise RuntimeError(f"schema hash mismatch for {filename}")
        run(
            [
                "docker",
                "exec",
                "-i",
                "futureagi-clickhouse-1",
                "clickhouse-client",
                "--database",
                TARGET,
                "--multiquery",
            ],
            data=raw,
        )

    table_rows = chq(
        "SELECT name,engine,total_rows FROM system.tables "
        f"WHERE database='{TARGET}' ORDER BY name FORMAT JSONEachRow"
    ).splitlines()
    if (
        len(table_rows) != 6
        or tuple(json.loads(row)["name"] for row in table_rows) != TABLES
    ):
        raise RuntimeError("fresh target does not contain exactly six pinned tables")
    if any(int(json.loads(row).get("total_rows") or 0) != 0 for row in table_rows):
        raise RuntimeError("fresh target table is nonempty")

    passwords = {name: secrets.token_urlsafe(36) for name in (*USERS, PG_USER)}
    if any("'" in value or "\n" in value for value in passwords.values()):
        raise RuntimeError("generated secret is not SQL-safe")
    source_settings = (
        "readonly=1, max_execution_time=30, max_threads=4, "
        "max_memory_usage=4294967296, max_bytes_to_read=42949672960, "
        "max_result_rows=250000, max_result_bytes=67108864, "
        "read_overflow_mode='throw', result_overflow_mode='throw', "
        "timeout_overflow_mode='throw'"
    )
    api_settings = (
        "readonly=2 CONST, max_execution_time=2 CONST, max_threads=2 CONST, "
        "max_memory_usage=536870912 CONST, max_bytes_to_read=536870912 CONST, "
        "max_rows_to_read=5000000 CONST, max_result_bytes=8388608 CONST, "
        "max_result_rows=256 CONST, read_overflow_mode='throw' CONST, "
        "result_overflow_mode='throw' CONST, timeout_overflow_mode='throw' CONST"
    )
    ch(
        f"CREATE USER {SOURCE_USER} IDENTIFIED WITH sha256_password "
        f"BY '{passwords[SOURCE_USER]}' HOST IP '172.19.0.0/16' SETTINGS {source_settings}"
    )
    for name in (CONTROL_USER, CONSUMER_USER):
        ch(
            f"CREATE USER {name} IDENTIFIED WITH sha256_password "
            f"BY '{passwords[name]}' HOST IP '172.19.0.0/16'"
        )
    ch(
        f"CREATE USER {LEDGER_USER} IDENTIFIED WITH sha256_password "
        f"BY '{passwords[LEDGER_USER]}' HOST IP '172.19.0.0/16' SETTINGS readonly=2"
    )
    ch(
        f"CREATE USER {API_USER} IDENTIFIED WITH sha256_password "
        f"BY '{passwords[API_USER]}' HOST IP '172.19.0.0/16' SETTINGS {api_settings}"
    )
    ch(f"GRANT SELECT ON futureagi.spans TO {SOURCE_USER}")
    ch(f"GRANT SELECT ON system.settings TO {SOURCE_USER}")
    ch(f"GRANT SELECT, INSERT ON {TARGET}.* TO {CONTROL_USER}")
    for system_table in ("databases", "settings", "tables"):
        ch(f"GRANT SELECT ON system.{system_table} TO {CONTROL_USER}")
    for table in (
        "property_definition_catalog",
        "span_attribute_value_catalog",
        "property_catalog_deliveries",
    ):
        ch(f"GRANT INSERT ON {TARGET}.{table} TO {CONSUMER_USER}")
    for table in (
        "property_catalog_activations",
        "property_catalog_checkpoints",
        "property_catalog_deliveries",
        "property_catalog_source_streams",
    ):
        ch(f"GRANT SELECT ON {TARGET}.{table} TO {LEDGER_USER}")
    for table in TABLES:
        ch(f"GRANT SELECT ON {TARGET}.{table} TO {API_USER}")

    expires = (
        (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    pg_secret = passwords[PG_USER]
    pg(
        f"""
BEGIN;
CREATE ROLE {PG_USER}
  LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 2 PASSWORD '{pg_secret}' VALID UNTIL '{expires}';
GRANT pg_read_all_data TO {PG_USER};
ALTER ROLE {PG_USER} SET default_transaction_read_only = 'on';
ALTER ROLE {PG_USER} SET statement_timeout = '9500ms';
ALTER ROLE {PG_USER} SET lock_timeout = '1000ms';
ALTER ROLE {PG_USER} SET idle_in_transaction_session_timeout = '10000ms';
COMMIT;
"""
    )

    encoded_pg_password = urllib.parse.quote(pg_secret, safe="")
    write_private(
        "producer.env",
        {
            "FI_CH_PASSWORD": passwords[SOURCE_USER],
            "FI_CH_USERNAME": SOURCE_USER,
            "FI_PG_WRITE": (
                f"postgres://{PG_USER}:{encoded_pg_password}@postgres:5432/tfc?sslmode=disable"
            ),
        },
    )
    write_private(
        "operator-runtime.env",
        {"SECRET_KEY": secrets.token_hex(32)},
    )
    write_private(
        "operator-postgres.env",
        {
            "PGBOUNCER_HOST": "postgres",
            "PGBOUNCER_PORT": "5432",
            "PG_DB": "tfc",
            "PG_PASSWORD": pg_secret,
            "PG_USER": PG_USER,
        },
    )
    write_private(
        "operator-source-clickhouse.env",
        {"CH25_PASSWORD": passwords[SOURCE_USER], "CH25_USER": SOURCE_USER},
    )
    write_private(
        "operator-target-clickhouse.env",
        {
            "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD": passwords[CONTROL_USER],
            "PROPERTY_CATALOG_DEV_WRITE_CH_USER": CONTROL_USER,
        },
    )
    write_private(
        "consumer-write-clickhouse.env",
        {
            "FI_PROPERTY_CATALOG_CH_PASSWORD": passwords[CONSUMER_USER],
            "FI_PROPERTY_CATALOG_CH_USERNAME": CONSUMER_USER,
        },
    )
    write_private(
        "consumer-ledger-clickhouse.env",
        {
            "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": passwords[LEDGER_USER],
            "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": LEDGER_USER,
        },
    )
    write_private(
        "catalog-api.env",
        {
            "PROPERTY_CATALOG_CH_PASSWORD": passwords[API_USER],
            "PROPERTY_CATALOG_CH_USER": API_USER,
        },
    )

    stream_id = str(uuid.uuid4())
    project_yaml = "\n".join(f"    - {project_id}" for project_id in PROJECT_IDS)
    config = f"""format: futureagi.property-catalog-dev-docker
version: 1
deployment_id: kartik-{SUFFIX}
images:
  collector_runtime: {GO_IMAGE}
  operator: {OP_RUNTIME_IMAGE}
host:
  root: {ROOT}
workspace:
  organization_id: {ORGANIZATION_ID}
  workspace_id: {WORKSPACE_ID}
  project_ids:
{project_yaml}
catalog:
  epoch: {CATALOG_EPOCH}
  projection_version: 1
  hot_producer_stream_id: {stream_id}
  source_database: futureagi
  target_database: {TARGET}
  span_since: "{SPAN_SINCE}"
  span_until: "{SPAN_UNTIL}"
  dev_identity: dev:property-catalog/kartik-{SUFFIX}
infrastructure:
  application_docker_network: futureagi_default
  kafka_docker_network: property-catalog-dev
  source_clickhouse_host: clickhouse
  source_clickhouse_native_port: 9000
  source_clickhouse_http_port: 8123
  target_clickhouse_host: clickhouse
  target_clickhouse_native_port: 9000
  target_clickhouse_http_port: 8123
  kafka_brokers:
    - property-catalog-kafka-dev:9092
provenance:
  write_clickhouse_hostname: 7c7e694b9c13
  source_clickhouse_hostname: 7c7e694b9c13
  postgres_database: tfc
  postgres_user: {PG_USER}
  postgres_server_address: 172.19.0.7
  postgres_server_port: 5432
"""
    config_path = ROOT / "reviewed-config.yaml"
    descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(config)
        handle.flush()
        os.fsync(handle.fileno())

    role_count = int(
        chq(f"SELECT count() FROM system.users WHERE name IN ({names})").strip()
    )
    grant_count = int(
        chq(
            "SELECT count() FROM system.grants "
            f"WHERE user_name IN ({names}) AND grant_option=0 AND is_partial_revoke=0"
        ).strip()
    )
    pg_meta = run(
        [
            "docker",
            "exec",
            "futureagi-postgres-1",
            "psql",
            "-X",
            "-U",
            "user",
            "-d",
            "tfc",
            "-At",
            "-F",
            "|",
            "-c",
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolreplication,rolbypassrls,rolconnlimit FROM pg_roles "
            f"WHERE rolname='{PG_USER}'",
        ]
    ).stdout.strip()
    evidence = {
        "clickhouse_grant_rows": grant_count,
        "clickhouse_user_count": role_count,
        "config_sha256": hashlib.sha256(config.encode()).hexdigest(),
        "existing_resources_modified": False,
        "hot_stream_id_sha256": hashlib.sha256(stream_id.encode()).hexdigest(),
        "ok": True,
        "postgres_expiry_utc": expires,
        "postgres_role_metadata": pg_meta,
        "production_touched": False,
        "runtime_root": str(ROOT),
        "secret_sha256": {
            name: hashlib.sha256(value.encode()).hexdigest()
            for name, value in passwords.items()
        },
        "target_database": TARGET,
        "target_rows": 0,
        "target_table_count": len(table_rows),
    }
    evidence_path = EVIDENCE / f"provisioning-{SUFFIX}.json"
    descriptor = os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(evidence, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    public = {key: value for key, value in evidence.items() if key != "secret_sha256"}
    print(json.dumps(public, sort_keys=True))


if __name__ == "__main__":
    main()
