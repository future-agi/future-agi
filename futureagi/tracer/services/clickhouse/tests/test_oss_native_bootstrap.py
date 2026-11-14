"""Service-free native contracts. Metadata doubles are NOT CH runtime proof."""

from __future__ import annotations

import copy
import socket
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse import oss_cdc_bootstrap as cdc
from tracer.services.clickhouse import oss_native_bootstrap as native

DATABASE = "offline_native"
DEFAULT_NAMES = (
    "traces end_users trace_sessions end_user_id_remap trace_session_id_remap "
    "trace_dict end_users_dict trace_sessions_dict spans spans_per_session "
    "spans_hourly_rollup trace_count_rollup spans_per_session_mv "
    "spans_hourly_rollup_mv trace_count_rollup_mv"
).split()
# Independent expected aggregate result headers, not inferred by production code.
DT = "DateTime('UTC')"
DT64 = "DateTime64(6, 'UTC')"
COUNT = "AggregateFunction(count)"
SUM = "AggregateFunction(sum, Int64)"
QUANTILES = "AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Int32)"
ERRORS = "AggregateFunction(countIf, UInt8)"
TOKENS = [
    (n, SUM) for n in ("total_tokens_sum", "prompt_tokens_sum", "completion_tokens_sum")
]
HEADERS = {
    "spans_per_session_mv": [
        ("project_id", "UUID"),
        ("trace_session_id", "Nullable(UUID)"),
        ("hour_first_seen", DT),
        ("span_count", COUNT),
        *TOKENS,
        ("cost_sum", "AggregateFunction(sum, Float64)"),
        ("latency_q", QUANTILES),
        ("first_seen", f"AggregateFunction(min, {DT64})"),
        ("last_seen", f"AggregateFunction(max, Nullable({DT64}))"),
        ("error_count", ERRORS),
    ],
    "spans_hourly_rollup_mv": [
        ("hour", DT),
        ("project_id", "UUID"),
        ("observation_type", "LowCardinality(String)"),
        ("model", "LowCardinality(String)"),
        ("provider", "LowCardinality(String)"),
        ("n", COUNT),
        ("error_count", ERRORS),
        *TOKENS,
        ("cost_sum", "AggregateFunction(sum, Float64)"),
        ("latency_q", QUANTILES),
    ],
    "trace_count_rollup_mv": [
        ("project_id", "UUID"),
        ("hour", DT),
        ("uniq_traces_state", "AggregateFunction(uniqExact, String)"),
    ],
    "dashboard_attr_rollup_mv": [
        ("project_id", "UUID"),
        ("hour", DT),
        ("attr_key", "String"),
        ("attr_value", "String"),
        ("n", COUNT),
        ("latency_sum", SUM),
    ],
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    guards = []
    for owner, name in (
        (socket, "socket"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
        (cdc.apply_schema.clickhouse_connect, "get_client"),
        (cdc.apply_schema, "main"),
        (cdc.apply_schema, "apply_file"),
        (Path, "glob"),
    ):
        guard = Mock(side_effect=AssertionError("external operation forbidden"))
        monkeypatch.setattr(owner, name, guard)
        guards.append(guard)
    yield
    for guard in guards:
        guard.assert_not_called()


class MemoryClient:
    """Mechanical metadata transport double; no SQL execution/CH canonicalizer."""

    def __init__(self, complete=False, **options):
        self.definitions = native.native_definitions(DATABASE, **options)
        self.tables, self.columns, self.data = {}, {}, {}
        self.events = []
        self.database, self.policy = DATABASE, True
        self.fail_query, self.fail_create, self.ignore_create = None, None, None
        self.rewrite_rows = lambda sql, rows: rows
        self.headers = copy.deepcopy(HEADERS)
        self.header_rows = []
        self.formatted = None
        if complete:
            for name, ddl in self.definitions.items():
                self.install(name, ddl)

    @property
    def writes(self):
        return [sql for kind, sql in self.events if kind == "command"]

    def install(self, name, ddl):
        if name.endswith("_dict"):
            row = ("Dictionary", "", "", "", "", ddl)
            cols = {}
        elif name.endswith("_mv"):
            row = ("MaterializedView", "", "", "", "", ddl)
            cols = {n: (t, "", "") for n, t in HEADERS[name]}
        else:
            parsed, _, clauses = cdc._table(ddl)
            row = (
                clauses["ENGINE"][0],
                " ".join(clauses["ENGINE"]),
                " ".join(cdc._key(clauses.get("PARTITION", ()))),
                " ".join(cdc._key(clauses["ORDER"])),
                " ".join(cdc._key(clauses.get("PRIMARY", clauses["ORDER"]))),
                ddl,
            )
            cols = {n: (" ".join(t), k, " ".join(e)) for n, (t, k, e) in parsed.items()}
        self.tables[name], self.columns[name] = row, cols
        self.data[name] = False

    def remove(self, name):
        del self.tables[name]
        del self.columns[name]

    def query(self, sql, parameters=None, settings=None):
        self.events.append(("query", sql))
        assert sql.startswith("SELECT ")
        assert settings == {"readonly": 1, "max_threads": 1, "max_execution_time": 5}
        if self.fail_query and self.fail_query in sql:
            raise RuntimeError("secret-password://sensitive-query-payload")
        if sql == "SELECT currentDatabase()":
            rows = [(self.database,)]
        elif "system.storage_policies" in sql:
            # ClickHouse lists one row per volume, not one per policy.
            rows = [("tiered",), ("tiered",)] if self.policy else []
            if sql.startswith("SELECT DISTINCT policy_name "):
                rows = list(dict.fromkeys(rows))
        elif "FROM system.tables" in sql:
            assert parameters == {
                "database": DATABASE,
                "names": tuple(self.definitions),
            }
            rows = [(n, *row) for n, row in self.tables.items()]
        elif "FROM system.columns" in sql:
            rows = [
                (n, col, *shape)
                for n, cols in self.columns.items()
                for col, shape in cols.items()
            ]
        elif sql.startswith("SELECT 1 FROM "):
            name = sql.split()[3].split(".")[1]
            assert sql.endswith(" LIMIT 1")
            rows = [(1,)] if self.data[name] else []
        elif "formatQuerySingleLine" in sql:
            # Token spelling only; actual server parentheses/type semantics unproven.
            rows = (
                self.formatted
                if self.formatted is not None
                else [
                    tuple(
                        " ".join(cdc._tokens(parameters[key]))
                        for key in ("actual", "expected")
                    )
                ]
            )
        elif sql.startswith("SELECT * FROM (\n"):
            name = next(
                n
                for n, ddl in self.definitions.items()
                if n.endswith("_mv") and native._mv_select(ddl) in sql
            )
            header = self.headers[name]
            return SimpleNamespace(
                result_rows=self.header_rows,
                column_names=tuple(n for n, _ in header),
                column_types=tuple(SimpleNamespace(name=t) for _, t in header),
            )
        else:
            raise AssertionError("unexpected query")
        return SimpleNamespace(result_rows=self.rewrite_rows(sql, rows))

    def command(self, sql):
        self.events.append(("command", sql))
        name = next(n for n, ddl in self.definitions.items() if ddl == sql)
        assert sql.startswith(
            (
                "CREATE TABLE IF NOT EXISTS ",
                "CREATE DICTIONARY IF NOT EXISTS ",
                "CREATE MATERIALIZED VIEW IF NOT EXISTS ",
            )
        )
        assert name not in self.tables
        if name == self.fail_create:
            raise RuntimeError("secret-password://uncertain-write")
        if name != self.ignore_create:
            self.install(name, sql)


def test_default_manifest_final_fields_and_order():
    definitions = native.native_definitions(DATABASE)
    assert list(definitions) == DEFAULT_NAMES
    columns, indexes, clauses = cdc._table(definitions["spans"])
    assert columns["attributes_extra"] == (("String",), "DEFAULT", ("'{}'",))
    assert "attributes_extra String DEFAULT '{}' CODEC(ZSTD(3))" in definitions["spans"]
    assert columns["max_tokens"][0] == ("Nullable", "(", "Int32", ")")
    assert columns["_peerdb_is_deleted"] == (("UInt8",), "ALIAS", ("is_deleted",))
    assert f"'{DATABASE}.trace_dict'" in definitions["spans"]
    assert set(indexes) >= {
        "idx_trace_name",
        "idx_attrs_str_values",
        "idx_attrs_num_values",
        "idx_attrs_str_ngram",
        "auto_minmax_index_created_at",
    }
    assert "storage_policy" in clauses["SETTINGS"]
    for name in (
        "proj_metrics_hourly_by_project",
        "proj_metrics_hourly_by_obs_type",
        "proj_metrics_hourly_by_model",
        "proj_by_session",
        "proj_by_end_user",
    ):
        assert "PROJECTION " + name in definitions["spans"]
    for name, ddl in definitions.items():
        tokens = cdc._tokens(ddl)
        assert tokens[0] == "CREATE"
        assert not set(tokens) & {
            "ALTER",
            "DROP",
            "INSERT",
            "UPDATE",
            "DELETE",
            "POPULATE",
            "TTL",
            "REPLACE",
        }
        assert f"{DATABASE}.{name}" in ddl
    assert "String DEFAULT '{}'" in definitions["spans"]


@pytest.mark.parametrize(
    "options,extra",
    [
        ({"include_backfill": True}, {"spans_v2_dead_letter", "backfill_checkpoints"}),
        (
            {"include_dashboard_rollup": True},
            {"dashboard_attr_rollup", "dashboard_attr_rollup_mv"},
        ),
    ],
)
def test_explicit_optional_paths(options, extra):
    definitions = native.native_definitions(DATABASE, **options)
    assert set(definitions) == set(DEFAULT_NAMES) | extra
    assert (
        not {
            "schema_versions",
            "tracer_eval_logger_v2",
            "eval_per_config",
            "span_user_rollup",
        }
        & definitions.keys()
    )
    client = MemoryClient(**options)
    assert set(native.bootstrap_native(client, database=DATABASE, **options)) == set(
        definitions
    )


def test_fresh_then_retained_complete_is_zero_writes():
    client = MemoryClient()
    assert native.inspect_native(client, database=DATABASE) == tuple(DEFAULT_NAMES)
    assert not client.writes
    assert native.bootstrap_native(client, database=DATABASE) == tuple(DEFAULT_NAMES)
    assert len(client.writes) == 15
    before = (
        copy.deepcopy(client.tables),
        copy.deepcopy(client.columns),
        list(client.writes),
    )
    client.data["spans"] = True
    assert native.bootstrap_native(client, database=DATABASE) == ()
    assert before == (client.tables, client.columns, client.writes)


@pytest.mark.parametrize("name", DEFAULT_NAMES)
def test_each_existing_object_is_inspect_only(name):
    client = MemoryClient(complete=True)
    before = copy.deepcopy(client.tables[name])
    assert native.inspect_native(client, database=DATABASE) == ()
    assert client.tables[name] == before and not client.writes


@pytest.mark.parametrize(
    "missing",
    ["spans_per_session_mv", "trace_count_rollup", "end_user_id_remap", "traces"],
)
def test_missing_prerequisites_over_retained_spans_fail_before_any_write(missing):
    client = MemoryClient(complete=True)
    client.remove(missing)
    client.data["spans"] = True
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


def test_incomplete_layout_with_curated_history_also_refuses():
    client = MemoryClient()
    client.install("traces", client.definitions["traces"])
    client.data["traces"] = True
    with pytest.raises(native.NativeBootstrapError, match="retained rows"):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


def test_empty_partial_prefix_can_be_explicitly_resumed():
    client = MemoryClient()
    for name in DEFAULT_NAMES[:8]:
        client.install(name, client.definitions[name])
    assert native.bootstrap_native(client, database=DATABASE) == tuple(
        DEFAULT_NAMES[8:]
    )
    assert len(client.writes) == 7


@pytest.mark.parametrize("field,value", [(0, "String"), (1, "DEFAULT"), (2, "'wrong'")])
def test_late_existing_column_mismatch_prevents_earlier_creates(field, value):
    client = MemoryClient()
    client.install("trace_count_rollup", client.definitions["trace_count_rollup"])
    shape = list(client.columns["trace_count_rollup"]["project_id"])
    shape[field] = value
    client.columns["trace_count_rollup"]["project_id"] = tuple(shape)
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


@pytest.mark.parametrize(
    "field,value",
    [
        (0, "MergeTree"),
        (1, "ReplacingMergeTree(other)"),
        (2, "project_id"),
        (3, "id"),
        (4, "id"),
    ],
)
def test_existing_engine_and_keys_are_not_repaired(field, value):
    client = MemoryClient(complete=True)
    row = list(client.tables["traces"])
    row[field] = value
    client.tables["traces"] = tuple(row)
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


@pytest.mark.parametrize(
    "name,old,new",
    [
        ("trace_dict", "TABLE 'traces'", "TABLE 'tracer_trace'"),
        ("trace_dict", "TABLE 'traces'", "DB 'foreign' TABLE 'traces'"),
        ("spans", f"'{DATABASE}.trace_dict'", "'foreign.trace_dict'"),
        ("traces", f"{DATABASE}.traces", "foreign.traces"),
        (
            "trace_count_rollup_mv",
            f"TO {DATABASE}.trace_count_rollup",
            "TO foreign.trace_count_rollup",
        ),
        ("trace_count_rollup_mv", f"FROM {DATABASE}.spans", "FROM foreign.spans"),
        ("trace_count_rollup_mv", "is_deleted = 0", "is_deleted = 1"),
        ("traces", "SETTINGS", "TTL created_at + INTERVAL 1 DAY DELETE SETTINGS"),
    ],
)
def test_one_sided_physical_drift_fails(name, old, new):
    client = MemoryClient(complete=True)
    ddl = client.tables[name][-1]
    assert old in ddl
    client.install(name, ddl.replace(old, new))
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


@pytest.mark.parametrize(
    "bad", ["", "system", "INFORMATION_SCHEMA", "foreign.db", "db;DROP", "a'", 5, None]
)
def test_bad_target_fails_without_any_client_call(bad):
    client = MemoryClient()
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=bad)
    assert not client.events


@pytest.mark.parametrize("option", ["include_backfill", "include_dashboard_rollup"])
@pytest.mark.parametrize("value", ["true", 1, None])
def test_options_are_actual_booleans(option, value):
    with pytest.raises(native.NativeBootstrapError):
        native.native_definitions(DATABASE, **{option: value})


@pytest.mark.parametrize(
    "attribute,value", [("database", "foreign"), ("policy", False)]
)
def test_target_and_policy_preflight(attribute, value):
    client = MemoryClient()
    setattr(client, attribute, value)
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


def test_multi_volume_policy_is_a_single_required_policy():
    client = MemoryClient()
    assert native.inspect_native(client, database=DATABASE) == tuple(DEFAULT_NAMES)
    assert not client.writes
    policy_reads = [
        sql for kind, sql in client.events if "system.storage_policies" in sql
    ]
    assert policy_reads == [
        "SELECT DISTINCT policy_name FROM system.storage_policies WHERE policy_name = 'tiered'"
    ]


@pytest.mark.parametrize(
    "query",
    [
        "currentDatabase",
        "system.storage_policies",
        "system.tables",
        "system.columns",
        "SELECT 1 FROM",
        "SELECT * FROM",
    ],
)
def test_failed_read_is_terminal_and_redacted(query):
    client = MemoryClient(complete=True)
    client.remove("trace_count_rollup_mv")
    client.fail_query = query
    with pytest.raises(native.NativeBootstrapError) as exc:
        native.bootstrap_native(client, database=DATABASE)
    assert "secret-password" not in "".join(traceback.format_exception(exc.value))
    assert not client.writes


@pytest.mark.parametrize(
    "malformed", [None, {}, [None], [("bad",)], [("x",) * 7], "raw-secret"]
)
def test_malformed_table_inventory_never_becomes_empty(malformed):
    client = MemoryClient()
    client.rewrite_rows = lambda sql, rows: (
        malformed if "FROM system.tables" in sql else rows
    )
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


@pytest.mark.parametrize("query", ["system.tables", "system.columns"])
def test_duplicate_metadata_rejected(query):
    client = MemoryClient(complete=True)
    client.rewrite_rows = lambda sql, rows: rows + rows[:1] if query in sql else rows
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


@pytest.mark.parametrize("mutation", ["type", "name", "duplicate", "empty", "rows"])
def test_actual_select_header_negative_before_missing_mv_write(mutation):
    client = MemoryClient(complete=True)
    client.remove("trace_count_rollup_mv")
    header = client.headers["trace_count_rollup_mv"]
    if mutation == "type":
        header[-1] = (header[-1][0], "AggregateFunction(uniq, String)")
    elif mutation == "name":
        header[-1] = ("wrong", header[-1][1])
    elif mutation == "duplicate":
        header.append(header[0])
    elif mutation == "empty":
        header.clear()
    else:
        client.header_rows = [(1,)]
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


def test_stored_mv_output_metadata_must_match_inferred_header():
    client = MemoryClient(complete=True)
    client.columns["trace_count_rollup_mv"]["project_id"] = ("String", "", "")
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [("same",)],
        [("same", "same", "extra")],
        [(None, None)],
        [("a", "b")],
        [("", "")],
    ],
)
def test_malformed_or_different_formatter_result(rows):
    client = MemoryClient(complete=True)
    row = client.tables["trace_count_rollup_mv"]
    client.tables["trace_count_rollup_mv"] = (
        *row[:-1],
        row[-1].replace("is_deleted = 0", "(is_deleted = 0)"),
    )
    client.formatted = rows
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


def test_uncertain_create_stops_without_retry_or_cleanup():
    client = MemoryClient()
    client.fail_create = "trace_dict"
    with pytest.raises(
        native.NativeBootstrapError, match="partial state retained, no retry"
    ) as exc:
        native.bootstrap_native(client, database=DATABASE)
    assert len(client.writes) == 6
    assert set(client.tables) == set(DEFAULT_NAMES[:5])
    assert "secret-password" not in "".join(traceback.format_exception(exc.value))


def test_silent_create_failure_is_not_success_or_replayed():
    client = MemoryClient()
    client.ignore_create = "trace_count_rollup_mv"
    with pytest.raises(native.NativeBootstrapError, match="postflight incomplete"):
        native.bootstrap_native(client, database=DATABASE)
    assert len(client.writes) == 15


@pytest.mark.parametrize(
    "replacement",
    ["DROP TABLE traces;\n", "CREATE TABLE IF NOT EXISTS foreign (x UInt8);\n"],
)
def test_fixed_file_selector_rejects_changed_statement_before_calls(
    monkeypatch, replacement
):
    client = MemoryClient()
    read_text = Path.read_text

    def changed(path, *args, **kwargs):
        return (
            replacement
            if path.name == "015_traces_and_trace_dict.sql"
            else read_text(path, *args, **kwargs)
        )

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.events


def test_retained_performance_differences_are_not_silently_repaired():
    # This core's existing-table gate is functional, not codec/projection parity.
    client = MemoryClient(complete=True)
    row = client.tables["traces"]
    client.tables["traces"] = (
        *row[:-1],
        row[-1].replace("CODEC(ZSTD(3))", "CODEC(LZ4)"),
    )
    assert native.bootstrap_native(client, database=DATABASE) == ()
    assert not client.writes


def test_qualification_preserves_quoted_literals_and_identifiers():
    raw = "SELECT 'FROM spans', `FROM spans`, 'NULL', 'a;--b' FROM spans WHERE x = 'TO target';"
    assert native._qualify_reference(raw, "FROM", "spans", DATABASE) == raw.replace(
        " FROM spans WHERE", f" FROM {DATABASE}.spans WHERE"
    )
    assert (
        native._clean("SELECT '--no', '/*no*/', `a.b` -- comment\nFROM spans;")
        == "SELECT '--no', '/*no*/', `a.b` \n\nFROM spans;"
    )


@pytest.mark.parametrize(
    "source", ["foreign.spans", "spans.foreign", "other", "`spans`"]
)
def test_fixed_reference_rejects_unknown_or_already_qualified_source(source):
    with pytest.raises(native.NativeBootstrapError):
        native._qualify_reference(
            f"SELECT 'FROM spans' FROM {source};", "FROM", "spans", DATABASE
        )


def test_mv_formatter_transport_exception_redacted_without_executing_stored_sql():
    client = MemoryClient(complete=True)
    row = client.tables["trace_count_rollup_mv"]
    client.tables["trace_count_rollup_mv"] = (
        *row[:-1],
        row[-1].replace("is_deleted = 0", "(is_deleted = 0)"),
    )
    client.fail_query = "formatQuerySingleLine"
    with pytest.raises(native.NativeBootstrapError) as exc:
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes
    assert "secret-password" not in "".join(traceback.format_exception(exc.value))
    assert not any(
        "FROM (" in sql and "(is_deleted = 0)" in sql for _, sql in client.events
    )


def test_formatter_success_still_requires_exact_mv_header():
    client = MemoryClient(complete=True)
    row = client.tables["trace_count_rollup_mv"]
    client.tables["trace_count_rollup_mv"] = (
        *row[:-1],
        row[-1].replace("is_deleted = 0", "(is_deleted = 0)"),
    )
    client.formatted = [("same normalized SELECT", "same normalized SELECT")]
    assert native.inspect_native(client, database=DATABASE) == ()
    client.columns["trace_count_rollup_mv"]["project_id"] = (
        "UUID",
        "DEFAULT",
        "generateUUIDv4()",
    )
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


def test_missing_existing_column_and_extra_metadata_are_rejected():
    client = MemoryClient(complete=True)
    del client.columns["traces"]["id"]
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes


def test_silent_late_corruption_fails_postflight_without_retry():
    client = MemoryClient()
    command = client.command

    def corrupt(sql):
        command(sql)
        if sql == client.definitions["trace_count_rollup_mv"]:
            client.columns["traces"]["id"] = ("String", "", "")

    client.command = corrupt
    with pytest.raises(native.NativeBootstrapError, match="traces: incompatible"):
        native.bootstrap_native(client, database=DATABASE)
    assert len(client.writes) == 15


def test_late_incompatible_object_precedes_any_retained_row_scan():
    client = MemoryClient(complete=True)
    client.remove("trace_count_rollup_mv")
    client.columns["trace_count_rollup"]["project_id"] = ("String", "", "")
    with pytest.raises(native.NativeBootstrapError):
        native.bootstrap_native(client, database=DATABASE)
    assert not client.writes
    assert not any(sql.startswith("SELECT 1 FROM") for _, sql in client.events)


def test_expected_aggregate_state_types_are_independent_of_declaration_generation():
    definitions = native.native_definitions(DATABASE)
    for name in ("spans_per_session", "spans_hourly_rollup", "trace_count_rollup"):
        columns = cdc._table(definitions[name])[0]
        assert {n: shape[0] for n, shape in columns.items()} == {
            n: cdc._tokens(
                "UUID" if name == "spans_per_session" and n == "trace_session_id" else t
            )
            for n, t in HEADERS[name + "_mv"]
        }


def test_null_filtered_session_key_preserves_real_nullable_select_header():
    client = MemoryClient()
    assert native.bootstrap_native(client, database=DATABASE) == tuple(DEFAULT_NAMES)
    assert (
        client.columns["spans_per_session_mv"]["trace_session_id"][0]
        == "Nullable(UUID)"
    )
    assert cdc._tokens(client.columns["spans_per_session"]["trace_session_id"][0]) == (
        "UUID",
    )


def test_nullable_session_key_without_fixed_nonnull_predicate_is_rejected(monkeypatch):
    original = native._mv_select
    monkeypatch.setattr(
        native,
        "_mv_select",
        lambda sql: original(sql).replace("AND trace_session_id IS NOT NULL", ""),
    )
    client = MemoryClient()
    with pytest.raises(native.NativeBootstrapError, match="inferred aggregate header"):
        native.bootstrap_native(client, database=DATABASE)
    assert list(client.tables) == DEFAULT_NAMES[:9]


@pytest.mark.parametrize("column", ["project_id", "span_count", "total_tokens_sum"])
def test_other_nullable_inputs_are_not_permitted_by_session_identity_exception(column):
    client = MemoryClient()
    client.headers["spans_per_session_mv"] = [
        (n, f"Nullable({t})" if n == column else t)
        for n, t in client.headers["spans_per_session_mv"]
    ]
    with pytest.raises(native.NativeBootstrapError, match="inferred aggregate header"):
        native.bootstrap_native(client, database=DATABASE)
    assert list(client.tables) == DEFAULT_NAMES[:9]


def test_fresh_header_mismatch_after_spans_stops_before_rollup_or_mv_create():
    client = MemoryClient()
    client.headers["trace_count_rollup_mv"][-1] = (
        "uniq_traces_state",
        "AggregateFunction(uniq, String)",
    )
    with pytest.raises(native.NativeBootstrapError, match="inferred aggregate header"):
        native.bootstrap_native(client, database=DATABASE)
    assert list(client.tables) == DEFAULT_NAMES[:9]
    assert len(client.writes) == 9
    assert not any("CREATE MATERIALIZED VIEW" in sql for sql in client.writes)
    assert not any(
        name in client.tables
        for name in ("spans_per_session", "spans_hourly_rollup", "trace_count_rollup")
    )


def test_invisible_spans_create_does_not_allow_dependents_or_retry():
    client = MemoryClient()
    client.ignore_create = "spans"
    with pytest.raises(native.NativeBootstrapError, match="spans CREATE not visible"):
        native.bootstrap_native(client, database=DATABASE)
    assert len(client.writes) == 9
    assert not any("CREATE MATERIALIZED VIEW" in sql for sql in client.writes)


def test_all_optional_fresh_tables_have_final_no_ttl_but_retained_ttl_is_untouched():
    client = MemoryClient(include_backfill=True, include_dashboard_rollup=True)
    for name, ddl in client.definitions.items():
        if not name.endswith(("_dict", "_mv")):
            assert "TTL" not in cdc._table(ddl)[2]
    assert (
        len(
            native.bootstrap_native(
                client,
                database=DATABASE,
                include_backfill=True,
                include_dashboard_rollup=True,
            )
        )
        == 19
    )
    row = client.tables["spans_v2_dead_letter"]
    client.tables["spans_v2_dead_letter"] = (
        *row[:-1],
        row[-1].replace(
            "SETTINGS", "TTL attempted_at + INTERVAL 30 DAY DELETE SETTINGS"
        ),
    )
    before = copy.deepcopy(client.tables), list(client.writes)
    with pytest.raises(
        native.NativeBootstrapError, match="spans_v2_dead_letter: incompatible"
    ):
        native.bootstrap_native(
            client,
            database=DATABASE,
            include_backfill=True,
            include_dashboard_rollup=True,
        )
    assert (client.tables, client.writes) == before
