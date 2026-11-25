"""Live-metric DDL contract. Pure tests need neither Django nor any service.

Opt into live checks with FI_METRIC_SCHEMA_TEST_HTTP_PORT pointing at an owned
loopback sidecar. The fixture creates/removes its own database, never an app DB.
"""

import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    extract_table_name,
    rewrite_for_replicated,
    split_statements,
)
from tracer.services.derived_metrics import storage
from tracer.services.derived_metrics.support import SUPPORT_REDUCERS

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "services/clickhouse/v2/schema"


def _normalized(sql):
    return re.sub(r"\s+", "", sql).replace("`", "")


def _declarations(statement):
    """Split the CREATE body at top-level commas, preserving nested type args."""
    start = statement.index("(")
    depth, quoted, current, declarations = 0, False, [], []
    for char in statement[start + 1 :]:
        if char == "'":
            quoted = not quoted
        if not quoted:
            if char == "(" or char == ")" and depth > 0:
                depth += 1 if char == "(" else -1
            elif char == ")":
                declarations.append("".join(current).strip())
                return declarations
            elif char == "," and depth == 0:
                declarations.append("".join(current).strip())
                current = []
                continue
        current.append(char)
    raise AssertionError("Unterminated CREATE body")


def _columns(statement):
    # SHOW CREATE TABLE over HTTP returns escaped newlines/quotes in a TSV cell.
    statement = statement.replace("\\n", "\n").replace("\\t", "\t").replace("\\'", "'")
    return tuple(
        (name.strip("`"), data_type)
        for declaration in _declarations(statement)
        if not declaration.startswith("INDEX ")
        for name, data_type in (declaration.split(maxsplit=1),)
    )


def _statements():
    return {
        extract_table_name(statement): statement
        for filename in storage.SCHEMA_FILES
        for statement in split_statements((SCHEMA_DIR / filename).read_text())
    }


@pytest.mark.parametrize("table", storage.TABLES.values(), ids=lambda t: t.name)
def test_ddl_matches_storage_contract(table):
    statement = _statements()[table.name]
    assert statement.startswith(f"CREATE TABLE IF NOT EXISTS {table.name}\n")
    assert tuple((n, _normalized(t)) for n, t in _columns(statement)) == tuple(
        (n, _normalized(t)) for n, t in table.schema
    )
    assert tuple(n for n, _ in _columns(statement)) == table.columns
    assert len(set(table.columns)) == len(table.columns)
    assert f"ENGINE = ReplacingMergeTree({table.version_column})\n" in statement
    assert f"PARTITION BY {table.partition_by}\n" in statement
    assert f"ORDER BY ({', '.join(table.order_by)});" in statement
    assert table.order_by[-2:] == ("generation", "attempt_id")
    assert table.logical_key == table.order_by[:-2]
    assert "is_deleted" not in table.order_by
    assert not re.search(r"\b(TTL|PROJECTION)\b", statement, re.I)
    assert (
        f"-- cluster-shard-key: {table.name} = {storage.PROJECT_SHARD_KEY}"
        in (SCHEMA_DIR / table.schema_file).read_text()
    )


@pytest.mark.parametrize("table", storage.TABLES.values(), ids=lambda t: t.name)
def test_every_create_supports_replicated_rewrite(table):
    rewritten = rewrite_for_replicated(
        _statements()[table.name],
        table_name=table.name,
        cluster="metric_test_cluster",
        zk_prefix="/clickhouse/tables/ch25",
    )
    assert (
        f"CREATE TABLE IF NOT EXISTS {table.name} ON CLUSTER 'metric_test_cluster'"
        in rewritten
    )
    assert (
        "ENGINE = ReplicatedReplacingMergeTree("
        f"'/clickhouse/tables/ch25/{{shard}}/{table.name}', '{{replica}}', "
        f"{table.version_column})\n"
    ) in rewritten
    # In particular, do not use the engine's deleted-column cleanup argument.
    assert "is_deleted)" not in rewritten


def test_only_owned_additive_tables_and_numbers():
    assert set(_statements()) == set(storage.TABLES)
    assert storage.SCHEMA_FILES == (
        "031_metric_field_values.sql",
        "032_metric_bucket_totals.sql",
        "033_metric_unit_state.sql",
        "034_metric_source_dependencies.sql",
        "035_metric_bucket_retired_parts.sql",
        "036_list_current_root_facts.sql",
        "037_list_current_user_activity.sql",
    )
    for filename in storage.SCHEMA_FILES:
        assert len(list(SCHEMA_DIR.glob(filename[:3] + "_*.sql"))) == 1
    # Only span activity is persisted, never per-window/per-user metric totals.
    assert {name for name in storage.TABLES if "user" in name} == {
        "list_current_user_activity"
    }
    assert "span_id" in storage.LIST_USER_ACTIVITY.logical_key
    assert "unit_start" not in storage.LIST_USER_ACTIVITY.logical_key
    assert "field_id" not in storage.LIST_USER_ACTIVITY.columns
    # Attempt preimages are a write-once PG ledger, not a CH state-transition copy.
    assert not any("attempt" in name or "ledger" in name for name in storage.TABLES)


def test_only_field_checkpoint_subpartitions_by_immutable_unit():
    assert storage.FIELD_CHECKPOINT_BUCKETS == 8
    assert storage.FIELD_CHECKPOINT.partition_by == (
        "(cityHash64(project_id) % 32, cityHash64(unit_id) % 8)"
    )
    assert "unit_id" in storage.FIELD_CHECKPOINT.logical_key
    assert storage.FIELD_VALUES.partition_by == storage.PROJECT_PARTITION
    assert storage.PROJECT_PARTITION == "cityHash64(project_id) % 32"
    for table in storage.TABLES.values():
        if table != storage.FIELD_CHECKPOINT:
            assert "unit_id" not in table.partition_by
    assert storage.PROJECT_SHARD_KEY == "cityHash64(project_id)"


def test_published_snapshot_schema_is_unchanged():
    # Frozen snapshot store at the pre-rebuild head. Changes require a NEW file.
    expected = {
        "028_derived_metrics.sql": "2be6a15fb720d2d626218612f2db85f41a1e2992b9b5caf87367819a77556386",
        "029_derived_metric_uncertainty.sql": "dff689bb225f2bcfb88b91a939360dc7662aeb0f506ac94d29f5246f6a3c39e5",
        "030_derived_metric_view_refresh.sql": "e88235107f75989a91a34d95a8ebedf9a80ffbf023dd5b5fbcc2f2e71340a8e0",
    }
    for filename, digest in expected.items():
        assert (
            hashlib.sha256((SCHEMA_DIR / filename).read_bytes()).hexdigest() == digest
        )
    snapshot_ddl = (SCHEMA_DIR / "028_derived_metrics.sql").read_text()
    assert (
        "ALTER TABLE tracer_eval_logger_v2 ADD COLUMN IF NOT EXISTS status "
        "LowCardinality(String) DEFAULT 'completed';" in snapshot_ddl
    )
    assert (
        "ALTER TABLE tracer_eval_logger_v2 ADD COLUMN IF NOT EXISTS config_hash "
        "Nullable(String);" in snapshot_ddl
    )


@pytest.mark.parametrize("table", (storage.FIELD_VALUES, storage.FIELD_CHECKPOINT))
def test_complete_field_evidence_and_immutable_partition(table):
    schema = dict(table.schema)
    assert len(schema) == 36
    assert schema["inputs"] == "Map(LowCardinality(String), Nullable(Float64))"
    assert schema["inputs_bool"] == "Map(String, Nullable(UInt8))"
    assert schema["inputs_text"] == "Map(String, Nullable(String))"
    for column, data_type in (
        ("value_bool", "Nullable(UInt8)"),
        ("value_number", "Nullable(Float64)"),
        ("value_text", "Nullable(String)"),
        ("part_n", "Nullable(Float64)"),
        ("part_d", "Nullable(Float64)"),
        ("sample_probability", "Nullable(Float64)"),
        ("population_size", "Nullable(UInt64)"),
    ):
        assert schema[column] == data_type
    assert table.partition_by == (
        storage.FIELD_CHECKPOINT_PARTITION
        if table == storage.FIELD_CHECKPOINT
        else storage.PROJECT_PARTITION
    )
    assert not {"unit_start", "bucket_start", "user_id", "session_id"} & set(
        table.order_by
    )
    statement = _statements()[table.name]
    assert "INDEX ix_start unit_start TYPE minmax GRANULARITY 1" in statement
    assert "INDEX ix_settle settled_at TYPE minmax GRANULARITY 1" in statement
    assert "INDEX ix_user user_id TYPE bloom_filter GRANULARITY 1" in statement


def test_due_queue_has_both_measured_minmax_indexes():
    statement = _statements()[storage.UNIT_STATE.name]
    assert "INDEX ix_due due_at TYPE minmax GRANULARITY 1" in statement
    assert "INDEX ix_deleted is_deleted TYPE minmax GRANULARITY 1" in statement
    assert "due_at" not in storage.UNIT_STATE.order_by
    assert storage.UNIT_STATE.partition_by == storage.PROJECT_PARTITION


def test_bucket_statistics_are_complete_and_scope_separated():
    names = set(storage.BUCKET_TOTAL_COLUMNS)
    assert {f"support_{name}" for name in SUPPORT_REDUCERS} <= names
    assert {
        "support_p_min",
        "support_p_max",
        "census_disqualifiers",
        "support_invalid",
        "known_sampled",
        "population_missing",
        "raw_design_min",
        "raw_design_max",
        "scope_min",
        "scope_max",
        "nonempty_scope_count",
    } <= names
    assert {
        f"{prefix}_{component}"
        for prefix in ("b", "c")
        for component in ("yy", "xy", "xx")
    } <= names
    assert {"scope_key", "stratum_key"} <= set(storage.BUCKET_TOTALS.logical_key)
    schema = dict(storage.BUCKET_TOTAL_SCHEMA)
    assert schema["uniq_state"] == "AggregateFunction(uniqCombined64, String)"
    assert schema["quantile_state"] == "AggregateFunction(quantileTDigest, Float64)"


def test_checkpoints_keep_complete_payload_and_pinned_attempt_identity():
    for delta, checkpoint in (
        (storage.FIELD_VALUES, storage.FIELD_CHECKPOINT),
        (storage.BUCKET_TOTALS, storage.BUCKET_CHECKPOINT),
    ):
        assert checkpoint.schema == delta.schema
        assert checkpoint.order_by == delta.order_by
        assert checkpoint.partition_by == (
            storage.FIELD_CHECKPOINT_PARTITION
            if checkpoint == storage.FIELD_CHECKPOINT
            else delta.partition_by
        )
        assert checkpoint.version_column == delta.version_column
        assert checkpoint.name != delta.name


def test_retired_parts_are_anonymous_complete_prefold_statistics():
    assert storage.RETIRED_PART_SCHEMA == storage.BUCKET_TOTAL_SCHEMA
    assert storage.RETIRED_PART_KEY == storage.BUCKET_TOTAL_KEY
    assert not {
        "unit_id",
        "session_id",
        "user_id",
        "raw_user_id",
        "canonical_user_id",
        "raw_session_id",
        "canonical_session_id",
        "inputs",
        "inputs_bool",
        "inputs_text",
        "value_text",
    } & set(storage.RETIRED_PART_COLUMNS)
    assert not any("Map(" in data_type for _, data_type in storage.RETIRED_PART_SCHEMA)


def test_dependencies_preserve_old_and_canonical_membership():
    assert {
        "trace_id",
        "span_id",
        "raw_session_id",
        "canonical_session_id",
        "raw_user_id",
        "canonical_user_id",
        "unit_start",
        "is_deleted",
    } <= set(storage.SOURCE_DEPENDENCY_COLUMNS)
    assert storage.SOURCE_DEPENDENCIES.partition_by == storage.PROJECT_PARTITION
    assert not {"unit_start", "raw_session_id", "canonical_session_id"} & set(
        storage.SOURCE_DEPENDENCIES.order_by
    )


@pytest.fixture(scope="module")
def live_store():
    port = os.environ.get("FI_METRIC_SCHEMA_TEST_HTTP_PORT")
    if not port:
        pytest.skip("owned ClickHouse sidecar not requested")
    import clickhouse_connect

    password = os.environ.get("CH_PASSWORD", "")
    client = clickhouse_connect.get_client(
        host="127.0.0.1", port=int(port), username="default", password=password
    )
    database = "test_metric_schema_" + uuid4().hex
    client.command(f"CREATE DATABASE {database}")
    scoped = None
    try:
        scoped = clickhouse_connect.get_client(
            host="127.0.0.1",
            port=int(port),
            username="default",
            password=password,
            database=database,
            settings={"max_threads": 2, "max_memory_usage": 268435456},
        )
        # The real runner, not a hand-executed subset of the DDL.
        from tracer.services.clickhouse.v2 import apply_schema

        argv = [
            "--schema-dir",
            str(SCHEMA_DIR),
            "--ch-host",
            "127.0.0.1",
            "--ch-http-port",
            str(port),
            "--ch-password",
            password,
            "--ch-database",
            database,
            "--files",
            *storage.SCHEMA_FILES,
        ]
        assert apply_schema.main(argv) == 0
        before = {
            name: scoped.command(f"SHOW CREATE TABLE {name}") for name in storage.TABLES
        }
        assert apply_schema.main(argv) == 0
        after = {
            name: scoped.command(f"SHOW CREATE TABLE {name}") for name in storage.TABLES
        }
        assert before == after
        assert scoped.query("SELECT count() FROM schema_versions").first_row == (7,)
        yield scoped
    finally:
        if scoped is not None:
            scoped.close()
        client.command(f"DROP DATABASE {database} SYNC")
        client.close()


@pytest.mark.parametrize("table", storage.TABLES.values(), ids=lambda t: t.name)
def test_live_show_create_matches_contract(live_store, table):
    ddl = live_store.command(f"SHOW CREATE TABLE {table.name}")
    assert tuple((n, _normalized(t)) for n, t in _columns(ddl)) == tuple(
        (n, _normalized(t)) for n, t in table.schema
    )
    canonical = _normalized(ddl)
    assert f"ENGINE=ReplacingMergeTree({table.version_column})" in canonical
    assert _normalized(f"PARTITION BY {table.partition_by}") in canonical
    assert _normalized(f"ORDER BY ({', '.join(table.order_by)})") in canonical
    assert not re.search(r"\b(TTL|PROJECTION)\b", ddl, re.I)
    expected_indexes = [
        d for d in _declarations(_statements()[table.name]) if d.startswith("INDEX ")
    ]
    for index in expected_indexes:
        match = re.fullmatch(
            r"INDEX\s+(\w+)\s+(\w+)\s+TYPE\s+([A-Za-z_]+)"
            r"(?:\([^)]*\))?\s+GRANULARITY\s+(\d+)",
            index,
        )
        assert match, index
        index_name, expression, kind, granularity = match.groups()
        assert re.search(
            re.escape(f"INDEX{index_name}{expression}TYPE{kind}")
            + rf"(?:\([^)]*\))?GRANULARITY{granularity}",
            canonical,
        )


@pytest.mark.parametrize("table", storage.TABLES.values(), ids=lambda t: t.name)
def test_live_merge_keeps_attempts_and_logical_tombstones(live_store, table):
    attempts = (uuid4(), uuid4(), uuid4())
    versions = ((1 << 32) | 1, (2 << 32) | 1, (3 << 32) | 1)
    columns = ("generation", "attempt_id", table.version_column, "is_deleted")
    first = (1, attempts[0], versions[0], 0)
    live_store.insert(
        table.name,
        [
            first,
            first,
            (2, attempts[1], versions[1], 0),
            (3, attempts[2], versions[2], 1),
        ],
        column_names=columns,
    )
    live_store.command(f"OPTIMIZE TABLE {table.name} FINAL")
    # Other live tests keep their rows in this module-scoped store.
    rows = live_store.query(
        f"SELECT generation, is_deleted FROM {table.name} FINAL "
        "WHERE attempt_id IN {attempts:Array(UUID)} ORDER BY generation",
        parameters={"attempts": [str(attempt) for attempt in attempts]},
    ).result_rows
    assert rows == [(1, 0), (2, 0), (3, 1)]
    # An excluded higher attempt must not have physically destroyed the old row.
    assert live_store.query(
        f"SELECT {table.version_column}, is_deleted FROM {table.name} "
        "WHERE attempt_id = {attempt:UUID}",
        parameters={"attempt": str(attempts[0])},
    ).result_rows == [(versions[0], 0)]


def test_live_complete_passing_field_evidence_and_month_move(live_store):
    from datetime import UTC, datetime

    org, project, field = uuid4(), uuid4(), uuid4()
    attempts = (uuid4(), uuid4())
    late = datetime(2026, 2, 1, 0, 16, tzinfo=UTC)
    earlier = datetime(2026, 1, 31, 23, 59, tzinfo=UTC)
    for table in (storage.FIELD_VALUES, storage.FIELD_CHECKPOINT):
        columns = (
            "org_id",
            "project_id",
            "field_id",
            "unit_id",
            "session_id",
            "status",
            "unit_start",
            "bucket_start",
            "inputs",
            "inputs_bool",
            "inputs_text",
            "value_bool",
            "generation",
            "attempt_id",
            "_version",
        )
        prefix = (org, project, field, "conversation", "conversation", "ok")
        evidence = (
            {"pass": 1.0, "zero": 0.0, "absent": None},
            {"pass": 1, "false": 0, "absent": None},
            {"locale": "fr", "empty": "", "absent": None},
            1,
        )
        live_store.insert(
            table.name,
            [
                (*prefix, late, late.replace(minute=15), *evidence, 1, attempts[0], 1),
                (
                    *prefix,
                    earlier,
                    earlier.replace(minute=45),
                    *evidence,
                    2,
                    attempts[1],
                    2,
                ),
            ],
            column_names=columns,
        )
        live_store.command(f"OPTIMIZE TABLE {table.name} FINAL")
        rows = live_store.query(
            f"SELECT inputs, inputs_bool, inputs_text, value_bool, _partition_id "
            f"FROM {table.name} FINAL WHERE org_id = {{org:UUID}} ORDER BY generation",
            parameters={"org": str(org)},
        ).result_rows
        assert len(rows) == 2
        assert rows[0][:4] == rows[1][:4] == evidence
        assert rows[0][4] == rows[1][4]
        assert len(str(attempts[0])) == 36
