"""Execute normal bootstrap against a recording, non-network CLI fake."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "futureagi/scripts/property_catalog_oss/bootstrap_clickhouse.sh"
_SCHEMAS = _ROOT / "futureagi/tracer/services/clickhouse/v2/schema"
_FAKE = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
query = args[args.index("--query") + 1] if "--query" in args else ""
with open(os.environ["CAPTURE_TEST_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "stdin": sys.stdin.read()}) + "\\n")
if query.startswith("SELECT engine FROM system.databases"):
    print(os.environ.get("CAPTURE_TEST_ENGINE", "Atomic"))
elif query.startswith("SELECT type FROM system.columns"):
    print("Enum8('activate' = 1, 'disable' = 2, 'rollback' = 3, 'follow' = 4)")
elif query.startswith("SELECT count() FROM system.tables"):
    print(7)
"""


def _run(tmp_path, **overrides):
    fake = tmp_path / "clickhouse-client"
    fake.write_text(_FAKE)
    fake.chmod(0o700)
    log = tmp_path / "queries.jsonl"
    environment = {
        "PATH": str(tmp_path)
        + os.pathsep
        + os.defpath
        + os.pathsep
        + "/opt/homebrew/bin",
        "PROPERTY_CATALOG_SCHEMA_DIRECTORY": str(_SCHEMAS),
        "PROPERTY_CATALOG_SOURCE_DATABASE": "canonical_local",
        "PROPERTY_CATALOG_TARGET_DATABASE": "property_catalog_dev_fixture",
        "CLICKHOUSE_HOST": "installer-fixture.invalid",
        "CLICKHOUSE_PORT": "9000",
        "CAPTURE_TEST_LOG": str(log),
        **{
            "PROPERTY_CATALOG_" + role + "_PASSWORD": "offline-fixture-only-0000"
            for role in ("SOURCE", "CONTROL", "CONSUMER", "LEDGER", "API")
        },
        **overrides,
    }
    result = subprocess.run(
        ["/bin/sh", str(_SCRIPT)],
        env=environment,
        input="",
        text=True,
        capture_output=True,
        check=False,
    )
    entries = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )
    queries = [
        item["args"][item["args"].index("--query") + 1]
        for item in entries
        if "--query" in item["args"]
    ]
    return result, entries, queries


def test_bootstrap_derives_isolated_namespace_and_reuses_only_control_writer(tmp_path):
    result, entries, queries = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (
        "CREATE DATABASE IF NOT EXISTS `property_catalog_dev_fixture_source_capture` ENGINE = Atomic"
        in queries
    )
    capture_grants = {
        query
        for query in queries
        if query.startswith("GRANT ")
        and " ON `property_catalog_dev_fixture_source_capture`.* " in query
    }
    assert capture_grants == {
        "GRANT SELECT ON `property_catalog_dev_fixture_source_capture`.* TO property_catalog_oss_source",
        "GRANT SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL, DROP TABLE ON `property_catalog_dev_fixture_source_capture`.* TO property_catalog_oss_control",
    }
    assert (
        "GRANT SELECT ON `canonical_local`.spans TO property_catalog_oss_control"
        in queries
    )
    source_grants = [
        query
        for query in queries
        if query.startswith("GRANT ") and " ON `canonical_local`." in query
    ]
    assert all(query.startswith("GRANT SELECT ON ") for query in source_grants)
    source_metadata_grants = {
        query
        for query in queries
        if query.startswith("GRANT ")
        and " ON system." in query
        and query.endswith(" TO property_catalog_oss_source")
    }
    assert source_metadata_grants == {
        "GRANT SELECT ON system.settings TO property_catalog_oss_source",
        "GRANT SELECT(database, table, active, name, hash_of_all_files, rows, bytes_on_disk, disk_name) ON system.parts TO property_catalog_oss_source",
        "GRANT SELECT(name, uuid, engine) ON system.databases TO property_catalog_oss_source",
        "GRANT SELECT(database, name, uuid, engine, create_table_query, storage_policy) ON system.tables TO property_catalog_oss_source",
        "GRANT SELECT(policy_name, disks) ON system.storage_policies TO property_catalog_oss_source",
        "GRANT SELECT(database, table, name, type, default_kind, default_expression) ON system.columns TO property_catalog_oss_source",
        "GRANT SELECT(database, table, active, name, rows, bytes_on_disk, column, type) ON system.parts_columns TO property_catalog_oss_source",
        "GRANT SELECT(name, path, total_space, unreserved_space, keep_free_space, type, is_read_only) ON system.disks TO property_catalog_oss_source",
        "GRANT SELECT(database, table, zookeeper_path, zookeeper_name) ON system.replicas TO property_catalog_oss_source",
    }
    assert [query for query in queries if " ON system.disks " in query] == [
        "GRANT SELECT(name, path, total_space, unreserved_space, keep_free_space, type, is_read_only) ON system.disks TO property_catalog_oss_source"
    ]
    assert [query for query in queries if " ON system.storage_policies " in query] == [
        "GRANT SELECT(policy_name, disks) ON system.storage_policies TO property_catalog_oss_source"
    ]
    for entry in entries:
        args = entry["args"]
        assert args[args.index("--host") + 1] == "installer-fixture.invalid"
        assert args[args.index("--user") + 1] == "default"
        assert entry["stdin"] == "" or "--database" in args
        if "--database" in args:
            assert args[args.index("--database") + 1] == "property_catalog_dev_fixture"
    assert any(
        "ALTER USER property_catalog_oss_source " in query and "readonly=1" in query
        for query in queries
    )
    assert any(
        "ALTER USER property_catalog_oss_ledger " in query and "readonly=2" in query
        for query in queries
    )
    ledger_grants = [
        query
        for query in queries
        if query.startswith("GRANT ")
        and query.endswith(" TO property_catalog_oss_ledger")
    ]
    assert all(
        query.startswith("GRANT SELECT ON ") and "source_capture" not in query
        for query in ledger_grants
    )
    assert not any(
        query.startswith(("DROP ", "REVOKE ", "TRUNCATE ")) for query in queries
    )


@pytest.mark.parametrize("engine", ["Replicated", "Ordinary", ""])
def test_wrong_existing_snapshot_database_engine_stops_without_grants(tmp_path, engine):
    result, _, queries = _run(tmp_path, CAPTURE_TEST_ENGINE=engine)
    assert result.returncode == 65
    assert not any(query.startswith("GRANT ") for query in queries)


def test_source_capture_namespace_collision_stops_before_first_cli(tmp_path):
    result, entries, _ = _run(
        tmp_path,
        PROPERTY_CATALOG_SOURCE_DATABASE="property_catalog_dev_fixture_source_capture",
    )
    assert result.returncode == 64
    assert entries == []


def test_no_operator_capture_database_override_is_read(tmp_path):
    result, _, queries = _run(
        tmp_path, PROPERTY_CATALOG_CAPTURE_DATABASE="wrong_database"
    )
    assert result.returncode == 0, result.stderr
    assert not any("wrong_database" in query for query in queries)
