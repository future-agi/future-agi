#!/usr/bin/env python3
"""Provision short-lived steady-state identities for an activated DEV catalog.

This is intentionally narrower than ``provision_0816d.py``.  It refuses to
create a database or table and never writes source/catalog rows.  It only
re-creates the four non-API ClickHouse identities and one PostgreSQL SELECT
identity needed by the already-activated Docker-host reconciliation sidecar.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import subprocess
import urllib.parse
from pathlib import Path

ACK = "FI_PROPERTY_CATALOG_ENABLE_EXISTING_DEV_CATALOG_STEADY_STATE"
EXPECTED_TABLES = (
    "property_catalog_activations",
    "property_catalog_checkpoints",
    "property_catalog_deliveries",
    "property_catalog_source_streams",
    "property_definition_catalog",
    "span_attribute_value_catalog",
)
_SUFFIX_RE = re.compile(r"[0-9]{4}[a-z]")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CATALOG_DATABASE_RE = re.compile(r"[a-z][a-z0-9_]{0,127}")
_RESERVED_CATALOG_DATABASES = frozenset(
    {"default", "futureagi", "information_schema", "property_catalog", "system"}
)


class ProvisioningError(RuntimeError):
    """The requested existing-catalog identity setup is unsafe."""


def _run(
    argv: list[str], *, data: str | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, input=data, text=True, capture_output=True)
    if result.returncode:
        raise ProvisioningError(f"command failed rc={result.returncode}: {argv[0]}")
    return result


def _ch(sql: str) -> None:
    _run(
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


def _chq(sql: str) -> str:
    return _run(
        [
            "docker",
            "exec",
            "futureagi-clickhouse-1",
            "clickhouse-client",
            "--query",
            sql,
        ]
    ).stdout


def _pg(sql: str) -> None:
    _run(
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


def _pgq(sql: str) -> str:
    return _run(
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
            sql,
        ]
    ).stdout.strip()


def _write_private(path: Path, values: dict[str, str]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        for key in sorted(values):
            value = values[key]
            if "\n" in value or "\r" in value:
                raise ProvisioningError("generated environment value is invalid")
            handle.write(f"{key}={value}\n")
        handle.flush()
        os.fsync(handle.fileno())


def _settings(
    source: str,
    *,
    target_database: str | None = None,
) -> tuple[str, Path, str, dict[str, str]]:
    suffix = source.strip()
    if _SUFFIX_RE.fullmatch(suffix) is None:
        raise ProvisioningError("suffix must match NNNNx")
    root = Path(f"/home/ubuntu/property-catalog-kartik-{suffix}")
    target = (
        target_database.strip()
        if isinstance(target_database, str)
        else f"property_catalog_dev_kartik_{suffix}"
    )
    if (
        _CATALOG_DATABASE_RE.fullmatch(target) is None
        or target in _RESERVED_CATALOG_DATABASES
    ):
        raise ProvisioningError(
            "target database must be a safe lowercase ClickHouse identifier "
            "isolated from production and source databases"
        )
    users = {
        "source": f"property_catalog_source_ro_{suffix}",
        "control": f"property_catalog_control_rw_{suffix}",
        "consumer": f"property_catalog_consumer_rw_{suffix}",
        "ledger": f"property_catalog_ledger_ro_{suffix}",
        "postgres": f"property_catalog_pg_ro_{suffix}",
    }
    return suffix, root, target, users


def _preflight(
    *, root: Path, target: str, users: dict[str, str], activation_sha256: str
) -> dict[str, object]:
    private = root / "private"
    if (
        not root.is_dir()
        or not private.is_dir()
        or root.is_symlink()
        or private.is_symlink()
    ):
        raise ProvisioningError("reviewed runtime/private directories are unavailable")
    if (root / "compose.yaml").is_file() is False:
        raise ProvisioningError("reviewed Docker Compose file is unavailable")

    expected_files = (
        "producer.env",
        "operator-runtime.env",
        "operator-postgres.env",
        "operator-source-clickhouse.env",
        "operator-target-clickhouse.env",
        "consumer-write-clickhouse.env",
        "consumer-ledger-clickhouse.env",
    )
    collisions = [name for name in expected_files if (private / name).exists()]
    if collisions:
        raise ProvisioningError(
            "steady-state private files already exist: " + ",".join(collisions)
        )

    databases = int(
        _chq(f"SELECT count() FROM system.databases WHERE name='{target}'").strip()
    )
    if databases != 1:
        raise ProvisioningError("exact isolated target database is not present")
    table_rows = tuple(
        json.loads(line)
        for line in _chq(
            "SELECT name,engine FROM system.tables "
            f"WHERE database='{target}' ORDER BY name FORMAT JSONEachRow"
        ).splitlines()
    )
    if tuple(row["name"] for row in table_rows) != EXPECTED_TABLES:
        raise ProvisioningError(
            "target does not contain exactly the six catalog tables"
        )

    active = _chq(
        "SELECT activation_sha256,status,catalog_epoch,catalog_revision "
        f"FROM {target}.property_catalog_activations "
        "ORDER BY activation_sequence DESC,_version DESC LIMIT 1 FORMAT JSONEachRow"
    ).strip()
    if not active:
        raise ProvisioningError("existing target has no activation")
    activation = json.loads(active)
    if (
        activation.get("status") != "active"
        or activation.get("activation_sha256") != activation_sha256
    ):
        raise ProvisioningError("reviewed bootstrap activation does not match target")

    nonterminal_reservations = int(
        _chq(
            "SELECT count() FROM ("
            "SELECT organization_id,workspace_id,catalog_epoch,catalog_revision,"
            "build_token,argMax(status,_version) AS latest_status "
            f"FROM {target}.property_catalog_source_streams "
            "WHERE envelope_version=0 AND producer_stream_id=build_token "
            "GROUP BY organization_id,workspace_id,catalog_epoch,catalog_revision,"
            "build_token HAVING latest_status IN ('open','draining'))"
        ).strip()
    )
    if nonterminal_reservations:
        raise ProvisioningError(
            "existing target has an incomplete lifecycle reservation; use a fresh "
            "isolated catalog instead"
        )

    clickhouse_users = tuple(
        users[key] for key in ("source", "control", "consumer", "ledger")
    )
    names = ",".join(f"'{name}'" for name in clickhouse_users)
    if int(_chq(f"SELECT count() FROM system.users WHERE name IN ({names})").strip()):
        raise ProvisioningError(
            "one or more steady-state ClickHouse users already exist"
        )
    if (
        _pgq(
            "SELECT count(*) FROM pg_catalog.pg_roles "
            f"WHERE rolname='{users['postgres']}'"
        )
        != "0"
    ):
        raise ProvisioningError("steady-state PostgreSQL role already exists")
    return {
        "activation": activation,
        "clickhouse_users": clickhouse_users,
        "nonterminal_reservations": nonterminal_reservations,
        "private_files": expected_files,
        "table_count": len(table_rows),
    }


def provision(
    *,
    suffix: str,
    activation_sha256: str,
    execute: bool,
    validity_days: int,
    target_database: str | None = None,
) -> dict[str, object]:
    suffix, root, target, users = _settings(
        suffix,
        target_database=target_database,
    )
    if _SHA256_RE.fullmatch(activation_sha256) is None:
        raise ProvisioningError("bootstrap activation must be lowercase SHA-256")
    if not 1 <= validity_days <= 30:
        raise ProvisioningError("validity-days must be between 1 and 30")
    proof = _preflight(
        root=root,
        target=target,
        users=users,
        activation_sha256=activation_sha256,
    )
    result: dict[str, object] = {
        "activation_revision": proof["activation"]["catalog_revision"],
        "catalog_epoch": proof["activation"]["catalog_epoch"],
        "execute": execute,
        "existing_tables_changed": False,
        "nonterminal_reservations": proof["nonterminal_reservations"],
        "source_rows_changed": False,
        "suffix": suffix,
        "table_count": proof["table_count"],
        "target_database": target,
    }
    if not execute:
        return result

    passwords = {name: secrets.token_urlsafe(36) for name in users.values()}
    if any("'" in value or "\n" in value for value in passwords.values()):
        raise ProvisioningError("generated credential is not SQL-safe")
    source_settings = (
        "readonly=1, max_execution_time=30, max_threads=4, "
        "max_memory_usage=4294967296, max_bytes_to_read=42949672960, "
        "max_result_rows=250000, max_result_bytes=67108864, "
        "read_overflow_mode='throw', result_overflow_mode='throw', "
        "timeout_overflow_mode='throw'"
    )
    _ch(
        f"CREATE USER {users['source']} IDENTIFIED WITH sha256_password "
        f"BY '{passwords[users['source']]}' HOST IP '172.19.0.0/16' "
        f"SETTINGS {source_settings}"
    )
    for key in ("control", "consumer"):
        _ch(
            f"CREATE USER {users[key]} IDENTIFIED WITH sha256_password "
            f"BY '{passwords[users[key]]}' HOST IP '172.19.0.0/16'"
        )
    _ch(
        f"CREATE USER {users['ledger']} IDENTIFIED WITH sha256_password "
        f"BY '{passwords[users['ledger']]}' HOST IP '172.19.0.0/16' "
        "SETTINGS readonly=2"
    )
    _ch(f"GRANT SELECT ON futureagi.spans TO {users['source']}")
    _ch(f"GRANT SELECT ON system.settings TO {users['source']}")
    _ch(f"GRANT SELECT, INSERT ON {target}.* TO {users['control']}")
    for system_table in ("databases", "settings", "tables"):
        _ch(f"GRANT SELECT ON system.{system_table} TO {users['control']}")
    for table in (
        "property_definition_catalog",
        "span_attribute_value_catalog",
        "property_catalog_deliveries",
    ):
        _ch(f"GRANT INSERT ON {target}.{table} TO {users['consumer']}")
    for table in (
        "property_catalog_activations",
        "property_catalog_checkpoints",
        "property_catalog_deliveries",
        "property_catalog_source_streams",
    ):
        _ch(f"GRANT SELECT ON {target}.{table} TO {users['ledger']}")

    expires = (
        datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=validity_days)
    ).replace(microsecond=0)
    expires_text = expires.isoformat().replace("+00:00", "Z")
    pg_secret = passwords[users["postgres"]]
    _pg(
        f"""
BEGIN;
CREATE ROLE {users["postgres"]}
  LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 2 PASSWORD '{pg_secret}' VALID UNTIL '{expires_text}';
GRANT pg_read_all_data TO {users["postgres"]};
ALTER ROLE {users["postgres"]} SET default_transaction_read_only = 'on';
ALTER ROLE {users["postgres"]} SET statement_timeout = '9500ms';
ALTER ROLE {users["postgres"]} SET lock_timeout = '1000ms';
ALTER ROLE {users["postgres"]} SET idle_in_transaction_session_timeout = '10000ms';
COMMIT;
"""
    )

    private = root / "private"
    encoded_pg_password = urllib.parse.quote(pg_secret, safe="")
    _write_private(
        private / "producer.env",
        {
            "FI_CH_PASSWORD": passwords[users["source"]],
            "FI_CH_USERNAME": users["source"],
            "FI_PG_WRITE": (
                f"postgres://{users['postgres']}:{encoded_pg_password}"
                "@postgres:5432/tfc?sslmode=disable"
            ),
        },
    )
    _write_private(
        private / "operator-runtime.env", {"SECRET_KEY": secrets.token_hex(32)}
    )
    _write_private(
        private / "operator-postgres.env",
        {
            "PGBOUNCER_HOST": "postgres",
            "PGBOUNCER_PORT": "5432",
            "PG_DB": "tfc",
            "PG_PASSWORD": pg_secret,
            "PG_USER": users["postgres"],
        },
    )
    _write_private(
        private / "operator-source-clickhouse.env",
        {
            "CH25_PASSWORD": passwords[users["source"]],
            "CH25_USER": users["source"],
        },
    )
    _write_private(
        private / "operator-target-clickhouse.env",
        {
            "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD": passwords[users["control"]],
            "PROPERTY_CATALOG_DEV_WRITE_CH_USER": users["control"],
        },
    )
    _write_private(
        private / "consumer-write-clickhouse.env",
        {
            "FI_PROPERTY_CATALOG_CH_PASSWORD": passwords[users["consumer"]],
            "FI_PROPERTY_CATALOG_CH_USERNAME": users["consumer"],
        },
    )
    _write_private(
        private / "consumer-ledger-clickhouse.env",
        {
            "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": passwords[users["ledger"]],
            "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": users["ledger"],
        },
    )

    result.update(
        {
            "credential_sha256": {
                name: hashlib.sha256(value.encode()).hexdigest()
                for name, value in passwords.items()
            },
            "postgres_valid_until": expires_text,
            "private_file_count": len(proof["private_files"]),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suffix", default="0816h")
    parser.add_argument(
        "--target-database",
        default=os.environ.get("PROPERTY_CATALOG_DATABASE"),
    )
    parser.add_argument("--bootstrap-activation-sha256", required=True)
    parser.add_argument("--validity-days", type=int, default=7)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute and os.environ.get("FI_PROPERTY_CATALOG_STEADY_ACK") != ACK:
        raise ProvisioningError(
            f"execution requires FI_PROPERTY_CATALOG_STEADY_ACK={ACK}"
        )
    result = provision(
        suffix=args.suffix,
        target_database=args.target_database,
        activation_sha256=args.bootstrap_activation_sha256,
        execute=args.execute,
        validity_days=args.validity_days,
    )
    public = dict(result)
    public.pop("credential_sha256", None)
    print(json.dumps(public, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
