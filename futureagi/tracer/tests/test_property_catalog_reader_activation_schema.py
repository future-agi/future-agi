"""Read-only schema and checked-in OSS bootstrap contract tests."""

import json
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2 import catalog_dev_schema as dev
from tracer.services.clickhouse.v2 import catalog_prod_schema as prod

DATABASE = "property_catalog_dev_oss"
ROOT = Path(__file__).resolve().parents[2]


class Client:
    def __init__(self, tables):
        self.tables, self.writes = tables, []

    def query_rows(self, sql, **kwargs):
        if sql.strip() == "SELECT version()":
            return [("25.3.8.23",)]
        assert "FROM system.tables" in sql
        return self.tables

    def command(self, *args, **kwargs):
        self.writes.append((args, kwargs))
        pytest.fail("runtime schema verification must not write")


def six_tables():
    return [(DATABASE, item.table, item.engine, item.sql) for item in dev._load_pinned_statements()]


def control_table():
    item = prod._activation_control_statement()
    return (DATABASE, item.table, item.engine, item.sql)


def test_oss_seventh_table_is_exact_pinned_append_only_contract():
    path = ROOT / "scripts/property_catalog_oss/reader_activation_control.sql"
    assert path.read_text().strip() == prod._activation_control_statement().sql
    assert "ENGINE = MergeTree" in path.read_text()
    client = Client([*six_tables(), control_table()])
    evidence = json.loads(dev.verify_catalog_schema(client, target_database=DATABASE, deployment="dev"))
    assert evidence["validated_target_table_count"] == 7
    assert len(evidence["target_tables"]) == 7
    assert not client.writes
    legacy = json.loads(dev.verify_catalog_schema(Client(six_tables()), target_database=DATABASE, deployment="dev"))
    assert evidence["pinned_create_schema_sha256"] == legacy["pinned_create_schema_sha256"]


@pytest.mark.parametrize("mutation", ["column", "engine", "order", "extra", "missing_lifecycle", "foreign_database"])
def test_seventh_table_does_not_loosen_lifecycle_or_extra_table_admission(mutation):
    rows = [*six_tables(), control_table()]
    database, name, engine, sql = rows[-1]
    if mutation == "column":
        sql = sql.replace("control_sequence         UInt64", "control_sequence         UInt32")
    elif mutation == "engine":
        engine = "ReplacingMergeTree"
    elif mutation == "order":
        sql = sql.replace("    request_id\n)", "    controlled_at\n)")
    elif mutation == "foreign_database":
        sql = sql.replace(f"EXISTS {name}", f"EXISTS default.{name}")
    rows[-1] = (database, name, engine, sql)
    if mutation == "extra":
        rows.append((DATABASE, "unreviewed", "MergeTree", "CREATE TABLE unreviewed (a Int8) ENGINE=MergeTree ORDER BY a"))
    if mutation == "missing_lifecycle":
        rows.pop(0)
    with pytest.raises(dev.CatalogDevSchemaError):
        dev.verify_catalog_schema(Client(rows), target_database=DATABASE, deployment="dev")


def test_oss_bootstrap_adds_control_schema_and_retains_existing_grants():
    source = (ROOT / "scripts/property_catalog_oss/bootstrap_clickhouse.sh").read_text()
    assert 'clickhouse --database "$TARGET_DATABASE" --multiquery < "$CONTROL_SCHEMA"' in source
    assert '[ "$TABLE_COUNT" != "7" ] || [ "$PINNED_COUNT" != "7" ]' in source
    assert "GRANT SELECT, INSERT ON \\`$TARGET_DATABASE\\`.* TO $CONTROL_USER" in source
    assert "GRANT SELECT ON \\`$TARGET_DATABASE\\`.* TO $API_USER" in source
    assert "GRANT INSERT ON \\`$TARGET_DATABASE\\`.$table TO $CONSUMER_USER" in source
