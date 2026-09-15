"""Service-free core contracts. These doubles are not ClickHouse/PeerDB proof."""

from __future__ import annotations

import copy
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_upgrade as upgrade
from tracer.services.clickhouse.v2 import apply_schema

DATABASE = "offline_cdc"
LANDING_NAMES = (
    "tracer_trace tracer_eval_logger trace_annotation model_hub_score trace_session "
    "model_hub_dataset model_hub_column model_hub_row model_hub_cell "
    "simulate_test_execution simulate_call_execution usage_apicalllog "
    "simulate_scenarios simulate_agent_definition simulate_agent_version simulate_run_test "
    "model_hub_promptversion model_hub_prompttemplate model_hub_promptlabel tracer_enduser"
).split()
DEPENDENT_NAMES = (
    "prompt_dict prompt_label_dict column_dict dataset_dict dataset_cells "
    "simulate_scenario_dict simulate_agent_dict simulate_version_dict "
    "simulate_run_test_dict simulate_test_execution_dict simulate_calls"
).split()
ADDS = {
    ("model_hub_score", "tracer_project_id"): ("Nullable(UUID)", "", ""),
    ("model_hub_score", "value_history"): ("String", "DEFAULT", "'[]'"),
    ("simulate_agent_definition", "target_speaks_first"): ("Nullable(UInt8)", "", ""),
    ("usage_apicalllog", "eval_score"): (
        "Float64",
        "MATERIALIZED",
        "if(JSONType(JSONExtractString(config), 'output', 'output', 'score') IN ('Double', 'Int64', 'UInt64'), JSONExtractFloat(JSONExtractString(config), 'output', 'output', 'score'), JSONExtractFloat(JSONExtractString(config), 'output', 'output'))",
    ),
    ("usage_apicalllog", "eval_output_str"): (
        "String",
        "MATERIALIZED",
        "JSONExtractString(JSONExtractString(config), 'output', 'output')",
    ),
    ("usage_apicalllog", "eval_trace_id"): (
        "String",
        "MATERIALIZED",
        "JSONExtractString(JSONExtractString(config), 'trace_id')",
    ),
    ("usage_apicalllog", "eval_dataset_id"): (
        "String",
        "MATERIALIZED",
        "JSONExtractString(JSONExtractString(config), 'dataset_id')",
    ),
}
MIGRATION_NAMES = ("cdc_001_catalog_target_fields.sql", "cdc_002_usage_eval_fields.sql")
# Deliberately synthetic transport headers, NOT claimed ClickHouse inference of
# the packaged views. Keep independent of install()'s physical metadata and of
# production parsing. Only a later real server probe can qualify actual types.
STUB_HEADER = (
    ("stub_id", "UUID"),
    ("stub_number", "Nullable(Float64)"),
    ("stub_status", "LowCardinality(String)"),
    ("stub_time", "DateTime64(3)"),
    ("stub_flag", "Nullable(UInt8)"),
)
VIEW_PREREQUISITES = {
    "dataset_cells": (
        "model_hub_cell",
        "model_hub_column",
        "model_hub_dataset",
        "column_dict",
        "dataset_dict",
    ),
    "simulate_calls": (
        "simulate_call_execution",
        "simulate_scenarios",
        "simulate_agent_definition",
        "simulate_agent_version",
        "simulate_scenario_dict",
        "simulate_agent_dict",
        "simulate_version_dict",
    ),
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("offline test attempted a connection"))
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(apply_schema.clickhouse_connect, "get_client", forbidden)
    monkeypatch.setattr(apply_schema, "main", forbidden)
    yield
    forbidden.assert_not_called()


def _sql(tokens):
    return " ".join(tokens)


class MemoryClient:
    """Metadata reads + explicitly whitelisted CREATE/ADD/ledger writes only."""

    def __init__(self, *, complete=False, old=False, ledger=False, database=DATABASE):
        self.create, self.native = core._definitions(database)
        self.tables, self.columns, self.indexes, self.versions = {}, {}, {}, {}
        self.events = []
        self.database = database
        self.mappings = ()
        self.fail_on = None
        self.ignore = None
        self.corrupt_after_create = None
        self.headers = {
            name: SimpleNamespace(
                column_names=tuple(n for n, _ in STUB_HEADER),
                column_types=tuple(SimpleNamespace(name=t) for _, t in STUB_HEADER),
                result_rows=[],
            )
            for name in VIEW_PREREQUISITES
        }
        for name, ddl in self.native.items():
            self.install(name, ddl)
        if complete or old:
            for name, ddl in self.create.items():
                self.install(name, ddl)
        if old:
            for table, column in ADDS:
                del self.columns[table][column]
        if ledger:
            self.install("schema_versions", core._LEDGER_DDL)
        if complete and ledger:
            for name in MIGRATION_NAMES:
                self.versions[name] = upgrade.migration_file(name).sha256

    def install(self, name, ddl):
        self.columns[name], self.indexes[name] = {}, {}
        if (
            name in core.LANDING
            or name in core._NATIVE_FILES
            or name == "schema_versions"
        ):
            columns, indexes, clauses = core._table(ddl)
            # A double models final native metadata, not native migration execution.
            if name == "spans":
                columns.update(
                    {
                        key: core._column(core._tokens(value))
                        for key, value in core._SPAN_COLUMNS.items()
                    }
                )
                ddl = ddl[: ddl.index("\nTTL ")] + ";"
            self.columns[name] = {
                key: (_sql(t), k, _sql(e)) for key, (t, k, e) in columns.items()
            }
            for (table, column), shape in ADDS.items():
                if table == name and column in self.columns[name]:
                    self.columns[name][column] = shape
            self.indexes[name] = indexes
            self.tables[name] = (
                clauses["ENGINE"][0],
                _sql(clauses["ENGINE"]),
                _sql(core._key(clauses.get("PARTITION", ()))),
                _sql(core._key(clauses["ORDER"])),
                _sql(core._key(clauses.get("PRIMARY", clauses["ORDER"]))),
                ddl,
            )
        else:
            kind = (
                "View" if name in {"dataset_cells", "simulate_calls"} else "Dictionary"
            )
            self.tables[name] = (kind, kind, "", "", "", ddl)
            if kind == "View":
                # Separate physical fixture: never populate this from headers.
                self.columns[name] = {
                    "stub_id": ("UUID", "", ""),
                    "stub_number": ("Nullable(Float64)", "", ""),
                    "stub_status": ("LowCardinality(String)", "", ""),
                    "stub_time": ("DateTime64(3)", "", ""),
                    "stub_flag": ("Nullable(UInt8)", "", ""),
                }

    def remove(self, name):
        self.tables.pop(name)
        self.columns.pop(name)
        self.indexes.pop(name)

    def query(self, sql, parameters=None, settings=None):
        self.events.append(("SELECT", sql))
        if sql.startswith("SELECT formatQuerySingleLine("):
            assert set(parameters) == {"actual", "expected"}
            assert settings == {
                "readonly": 1,
                "max_threads": 1,
                "max_execution_time": 5,
            }
            # Deliberately NOT a SQL formatter. Formatting-specific fixtures
            # supply captured outputs separately; changed syntax stays different.
            return SimpleNamespace(
                result_rows=[(parameters["actual"], parameters["expected"])]
            )
        if sql.startswith("SELECT * FROM ("):
            # Independently obtain the canonical SELECT, not core._view_select,
            # and never SELECT from the actual view/its CREATE or column fixture.
            name = next(
                name
                for name in VIEW_PREREQUISITES
                if sql
                == "SELECT * FROM (\n"
                + self.create[name].split(" AS\n", 1)[1].strip().removesuffix(";")
                + "\n) LIMIT 0"
            )
            assert parameters is None
            assert settings == {
                "readonly": 1,
                "max_threads": 1,
                "max_execution_time": 5,
            }
            assert all(p in self.tables for p in VIEW_PREREQUISITES[name])
            self.events.append(("INFER", name))
            result = self.headers[name]
            if isinstance(result, Exception):
                raise result
            return result
        sql = " ".join(sql.split())
        assert sql.strip().startswith("SELECT "), sql
        if sql == "SELECT currentDatabase()":
            assert settings == {"readonly": 1}
            return SimpleNamespace(result_rows=[(self.database,)])
        if "FROM schema_versions" in sql:
            assert parameters == {
                "topology_identity": "schema-topology/v1/local",
                "include_legacy_local": 1,
            }
            return SimpleNamespace(result_rows=list(self.versions.items()))
        assert "database = currentDatabase()" in sql
        assert settings == {"readonly": 1}
        # Core reads whole objects; the upgrade helper binds narrower columns.
        assert parameters
        whole_objects = "names" in parameters
        names = parameters.get("names", parameters.get("tables", ()))
        if "FROM system.tables" in sql:
            rows = [
                (n, *v) if whole_objects else (n,)
                for n, v in self.tables.items()
                if n in names
            ]
        elif "FROM system.columns" in sql:
            rows = [
                (n, c, *v)
                for n, cols in self.columns.items()
                for c, v in cols.items()
                if (n in names if whole_objects else (n, c) in parameters["columns"])
            ]
        else:
            assert "FROM system.data_skipping_indices" in sql
            rows = []
            for name, indexes in self.indexes.items():
                if name not in names:
                    continue
                for index, tokens in indexes.items():
                    type_at, gran_at = tokens.index("TYPE"), tokens.index("GRANULARITY")
                    rows.append(
                        (
                            name,
                            index,
                            _sql(tokens[:type_at]),
                            _sql(tokens[type_at + 1 : gran_at]),
                            int(tokens[-1]),
                        )
                    )
        return SimpleNamespace(result_rows=rows)

    def inspect_mirrors(self):
        self.events.append(("MIRRORS",))
        return core.MirrorInventory(self.database, self.mappings)

    def command(self, sql):
        sql_tokens = core._tokens(sql)
        if sql_tokens[:1] == ("CREATE",):
            name = sql_tokens[5]
            assert name in self.create or name == "schema_versions"
            expected = self.create.get(name, core._LEDGER_DDL)
            assert sql_tokens == core._tokens(expected)
            self.events.append(("CREATE", name))
            if name == self.fail_on:
                raise RuntimeError("injected CREATE failure")
            if name != self.ignore and name not in self.tables:
                self.install(name, expected)
            if name == self.corrupt_after_create:
                column = "stub_id" if name in VIEW_PREREQUISITES else "id"
                self.columns[name][column] = ("String", "", "")
        else:
            statements = [
                sql
                for name in MIGRATION_NAMES
                for sql in apply_schema.split_statements(
                    upgrade.migration_file(name).path.read_text()
                )
            ]
            index = next(
                i
                for i, statement in enumerate(statements)
                if core._tokens(statement) == sql_tokens
            )
            table, column = list(ADDS)[index]
            self.events.append(("ADD", table, column))
            if column == self.fail_on:
                raise RuntimeError("injected ADD failure")
            if column != self.ignore:
                self.columns[table].setdefault(column, ADDS[table, column])

    def insert(self, table, rows, column_names):
        assert table == "schema_versions"
        assert column_names == ["filename", "sha256", "applied_by", "notes"]
        [(filename, sha, who, notes)] = rows
        assert filename in MIGRATION_NAMES
        assert notes == "schema-topology/v1/local" and who == "offline-test"
        self.events.append(("RECORD", filename))
        if self.ignore != "record":
            self.versions[filename] = sha.encode("ascii")

    @property
    def writes(self):
        return [
            event
            for event in self.events
            if event[0] not in {"SELECT", "MIRRORS", "INFER"}
        ]


def run(client):
    return core.bootstrap_cdc(
        client,
        database=DATABASE,
        inspect_mirrors=client.inspect_mirrors,
        applied_by="offline-test",
    )


def test_fixed_inventory_excludes_all_native_and_legacy_writes():
    assert list(core.LANDING) == LANDING_NAMES
    assert list(core.DEPENDENT) == DEPENDENT_NAMES
    create, native = core._definitions("another_database")
    assert len(create) == 31 and len(native) == 7
    assert not create.keys() & native.keys()
    for name, ddl in create.items():
        assert len(apply_schema.split_statements(ddl)) == 1
        assert core._tokens(ddl)[:1] == ("CREATE",)
        assert "Replicated" not in ddl
        if name.endswith("_dict"):
            assert "another_database." in ddl and "DB 'another_database'" in ddl
    assert "TABLE 'traces'" in native["trace_dict"]
    assert core._ADDITIONS == set(ADDS)


@pytest.mark.parametrize("name", ["dataset_cells", "simulate_calls"])
def test_packaged_views_bind_each_fixed_dependency_to_explicit_database(name):
    create, _ = core._definitions("a_nondefault_database")
    ddl = create[name]
    for dependency in VIEW_PREREQUISITES[name]:
        if dependency.endswith("_dict"):
            assert f"'a_nondefault_database.{dependency}'" in ddl
            assert f"'{dependency}'" not in ddl
    source = "model_hub_cell" if name == "dataset_cells" else "simulate_call_execution"
    assert f"FROM a_nondefault_database.{source} AS c FINAL" in ddl


def test_server_view_formatting_is_checked_without_executing_stored_select():
    client = MemoryClient(complete=True, ledger=True)
    row = list(client.tables["dataset_cells"])
    row[-1] = row[-1].replace(
        "column_data_type IN ('float', 'integer') OR column_source = 'evaluation'",
        "(column_data_type IN ('float', 'integer')) OR (column_source = 'evaluation')",
    )
    assert row[-1] != client.tables["dataset_cells"][-1]
    client.tables["dataset_cells"] = tuple(row)
    original = client.query
    formats = []

    def query(sql, **kwargs):
        if sql.startswith("SELECT formatQuerySingleLine("):
            params = kwargs["parameters"]
            assert params["actual"] != params["expected"]
            assert "OR" in params["actual"] and "OR" in params["expected"]
            formats.append(params)
            return SimpleNamespace(
                result_rows=[("SELECT canonical_shape", "SELECT canonical_shape")]
            )
        return original(sql, **kwargs)

    client.query = query
    assert run(client) == core.BootstrapResult((), False)
    assert formats and client.writes == []


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [("", "")],
        [(None, None)],
        [("same",)],
        [("same", "same", "extra")],
        [("a", "a"), ("a", "a")],
    ],
)
def test_invalid_formatter_response_never_authorizes_bootstrap(rows):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")  # must fail before the missing table is created
    row = list(client.tables["dataset_cells"])
    row[-1] = row[-1].replace("c._peerdb_is_deleted = 0", "c._peerdb_is_deleted = 1")
    client.tables["dataset_cells"] = tuple(row)
    original = client.query

    def query(sql, **kwargs):
        if sql.startswith("SELECT formatQuerySingleLine("):
            return SimpleNamespace(result_rows=rows)
        return original(sql, **kwargs)

    client.query = query
    with pytest.raises(core.BootstrapError, match="view definition|formatter"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "mutation", ["database", "predicate", "dictionary", "source", "boolean"]
)
def test_view_formatting_never_strips_foreign_scope_or_changed_expressions(mutation):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    row = list(client.tables["dataset_cells"])
    old, new = {
        "database": (f"{DATABASE}.", "foreign_database."),
        "predicate": ("c._peerdb_is_deleted = 0", "c._peerdb_is_deleted = 1"),
        "dictionary": ("column_dict", "wrong_dict"),
        "source": ("model_hub_cell", "wrong_table"),
        "boolean": (" OR ", " AND "),
    }[mutation]
    changed = row[-1].replace(old, new)
    assert changed != row[-1]
    row[-1] = changed
    client.tables["dataset_cells"] = tuple(row)
    with pytest.raises(core.BootstrapError, match="view definition"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "original_token,quoted", [("c.id", "`c.id`"), ("NULL", "`NULL`")]
)
def test_view_formatter_preserves_quoted_identifier_meaning(original_token, quoted):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    row = list(client.tables["dataset_cells"])
    row[-1] = row[-1].replace(original_token, quoted, 1)
    client.tables["dataset_cells"] = tuple(row)
    original = client.query
    calls = []

    def query(sql, **kwargs):
        if sql.startswith("SELECT formatQuerySingleLine("):
            actual = kwargs["parameters"]["actual"]
            assert quoted in actual  # identifier, not a qualified name or keyword
            calls.append(actual)
            return SimpleNamespace(
                result_rows=[(f"SELECT {quoted}", f"SELECT {original_token}")]
            )
        return original(sql, **kwargs)

    client.query = query
    with pytest.raises(core.BootstrapError, match="view definition"):
        run(client)
    assert calls and client.writes == []


def test_formatter_failure_stops_before_any_bootstrap_write():
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    row = list(client.tables["dataset_cells"])
    row[-1] = row[-1].replace("c.id,", "`c.id`,", 1)
    client.tables["dataset_cells"] = tuple(row)
    original = client.query

    def query(sql, **kwargs):
        if sql.startswith("SELECT formatQuerySingleLine("):
            raise TimeoutError("formatter unavailable")
        return original(sql, **kwargs)

    client.query = query
    with pytest.raises(TimeoutError, match="formatter unavailable"):
        run(client)
    assert client.writes == []


def test_inspection_is_select_only_even_with_missing_landing_and_ledger():
    client = MemoryClient()
    before = copy.deepcopy((client.tables, client.columns, client.versions))
    result = core.inspect_bootstrap(
        client, database=DATABASE, inspect_mirrors=client.inspect_mirrors
    )
    assert result == core.Inspection(
        tuple(LANDING_NAMES + DEPENDENT_NAMES), False, False
    )
    assert before == (client.tables, client.columns, client.versions)
    assert client.writes == []


def test_exact_create_upgrade_dependent_order_and_postverify_then_noop():
    client = MemoryClient()
    result = run(client)
    assert result == core.BootstrapResult(tuple(LANDING_NAMES + DEPENDENT_NAMES), True)
    assert client.writes == (
        [("CREATE", n) for n in LANDING_NAMES]
        + [("CREATE", "schema_versions")]
        + [("ADD", t, c) for t, c in list(ADDS)[:3]]
        + [("RECORD", upgrade.MIGRATION_NAME)]
        + [("ADD", t, c) for t, c in list(ADDS)[3:]]
        + [("RECORD", "cdc_002_usage_eval_fields.sql")]
        + [("CREATE", n) for n in DEPENDENT_NAMES]
    )
    first_write = next(i for i, e in enumerate(client.events) if e[0] == "CREATE")
    assert client.events[first_write - 1][0] == "MIRRORS"
    assert client.events[-1] == ("INFER", "simulate_calls")  # final view postcheck
    writes = list(client.writes)
    assert run(client) == core.BootstrapResult((), False)
    assert client.writes == writes


@pytest.mark.parametrize("ledger", [False, True])
def test_known_old_columns_only_get_named_upgrade(ledger):
    client = MemoryClient(old=True, ledger=ledger)
    retained = copy.deepcopy(client.columns)
    assert run(client) == core.BootstrapResult((), True)
    assert not any(
        e[0] == "CREATE" and e[1] != "schema_versions" for e in client.writes
    )
    for table, column in ADDS:
        retained[table][column] = ADDS[table, column]
    if not ledger:
        retained["schema_versions"] = client.columns["schema_versions"]
    assert client.columns == retained


def test_complete_physical_schema_with_no_record_is_recorded_once():
    client = MemoryClient(complete=True)
    assert run(client).upgrade_applied
    assert len([w for w in client.writes if w[0] == "RECORD"]) == 2
    assert not run(client).upgrade_applied


@pytest.mark.parametrize("table", LANDING_NAMES)
def test_every_existing_landing_column_is_checked_before_any_missing_create(table):
    client = MemoryClient()
    client.install(table, client.create[table])
    client.columns[table]["id"] = ("Nullable(String)", "", "")
    with pytest.raises(core.BootstrapError, match=f"{table}.id"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "field",
    [
        "type",
        "default_kind",
        "default_expression",
        "missing",
        "extra",
        "engine",
        "version",
        "partition",
        "sorting",
        "primary",
        "index",
        "ttl",
    ],
)
def test_full_landing_contract_mismatch_is_not_a_create_if_absent_success(field):
    client = MemoryClient(old=True)
    client.remove("tracer_trace")  # would be the first CREATE without full preflight
    table = "usage_apicalllog"
    if field in {"type", "default_kind", "default_expression"}:
        shape = list(ADDS[table, "eval_score"])
        shape[["type", "default_kind", "default_expression"].index(field)] = "wrong"
        client.columns[table]["eval_score"] = tuple(shape)
    elif field == "missing":
        del client.columns[table]["config"]  # source field, never an eligible ADD
    elif field == "extra":
        client.columns[table]["unknown"] = ("String", "", "")
    elif field == "index":
        client.indexes[table].pop("idx_eval_score")
    else:
        row = list(client.tables[table])
        if field == "ttl":
            row[-1] = row[-1].replace(
                "SETTINGS", "TTL created_at + INTERVAL 1 DAY SETTINGS"
            )
        else:
            row[
                {"engine": 0, "version": 1, "partition": 2, "sorting": 3, "primary": 4}[
                    field
                ]
            ] = "wrong"
        client.tables[table] = tuple(row)
    with pytest.raises(core.BootstrapError, match=table):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "survivor,missing",
    [
        ("model_hub_score", "simulate_agent_definition"),
        ("simulate_agent_definition", "model_hub_score"),
    ],
)
def test_incompatible_surviving_upgrade_target_blocks_creation_of_missing_partner(
    survivor, missing
):
    client = MemoryClient(complete=True)
    client.remove(missing)
    column = "value_history" if survivor == "model_hub_score" else "target_speaks_first"
    client.columns[survivor][column] = ("String", "", "")
    with pytest.raises(core.BootstrapError, match=f"{survivor}.{column}"):
        run(client)
    assert client.writes == [] and missing not in client.tables


@pytest.mark.parametrize(
    "name",
    [
        "spans",
        "traces",
        "end_users",
        "trace_sessions",
        "trace_dict",
        "end_users_dict",
        "trace_sessions_dict",
    ],
)
@pytest.mark.parametrize("missing", [True, False])
def test_native_prerequisite_missing_or_wrong_refuses_before_any_write(name, missing):
    client = MemoryClient()
    if missing:
        client.remove(name)
    elif name in core._NATIVE_FILES:
        client.columns[name]["project_id"] = ("String", "", "")
    else:
        row = list(client.tables[name])
        row[-1] = row[-1].replace("TABLE '", "TABLE 'wrong_")
        client.tables[name] = tuple(row)
    with pytest.raises(core.BootstrapError, match=name):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize("name", DEPENDENT_NAMES)
def test_existing_dependent_mismatch_blocks_every_missing_landing_create(name):
    client = MemoryClient()
    client.install(name, client.create[name])
    row = list(client.tables[name])
    row[-1] = row[-1].replace("_peerdb_is_deleted = 0", "_peerdb_is_deleted = 1")
    client.tables[name] = tuple(row)
    with pytest.raises(core.BootstrapError, match=name):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize("table", LANDING_NAMES)
def test_existing_mirror_with_absent_destination_is_never_recreated(table):
    client = MemoryClient()
    client.mappings = ((f"mirror_{table}", table),)
    with pytest.raises(
        core.BootstrapError, match="mirror exists but destination is absent"
    ):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "mappings",
    [
        (("", "model_hub_score"),),
        (("  ", "model_hub_score"),),
        (("mirror_spans", "spans"),),
        (("mirror_model_hub_score", "model_hub_score"),) * 2,
        (("first_writer", "model_hub_score"), ("second_writer", "model_hub_score")),
    ],
)
def test_unknown_or_duplicate_mirror_inventory_refuses(mappings):
    client = MemoryClient(complete=True)
    client.mappings = mappings
    with pytest.raises(core.BootstrapError, match="unknown/duplicate mirror"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "mappings",
    [
        (("custom_scores", "model_hub_score"),),
        (("application_cdc", "model_hub_score"), ("application_cdc", "tracer_trace")),
        tuple((f"existing_{i}", table) for i, table in enumerate(LANDING_NAMES)),
    ],
)
def test_verified_mapping_identity_does_not_depend_on_mirror_name(mappings):
    client = MemoryClient(complete=True, ledger=True)
    client.mappings = mappings
    before = copy.deepcopy((client.tables, client.columns, client.versions))
    assert run(client) == core.BootstrapResult((), False)
    assert before == (client.tables, client.columns, client.versions)
    assert client.writes == []


@pytest.mark.parametrize("inventory", [None, (), core.MirrorInventory("other", ())])
def test_missing_or_wrong_database_inventory_is_not_an_empty_success(inventory):
    client = MemoryClient()
    client.inspect_mirrors = lambda: inventory
    with pytest.raises(core.BootstrapError, match="mirror inventory"):
        run(client)
    assert client.writes == []


def test_inventory_failure_has_no_fallback_or_write():
    client = MemoryClient()
    client.inspect_mirrors = Mock(side_effect=PermissionError("inventory unavailable"))
    with pytest.raises(PermissionError):
        run(client)
    assert client.writes == []


def test_peerdb_inventory_factory_binds_the_verified_destination(monkeypatch):
    source = core.PeerTarget("pg", "postgres", 5432, "app")
    target = core.PeerTarget("ch", "clickhouse", 9000, DATABASE)
    request = Mock()
    mappings = (("mirror_model_hub_score", "model_hub_score"),)
    inspect = Mock(return_value=mappings)
    monkeypatch.setattr(core, "inspect_mappings", inspect)
    assert core.MirrorInventory.from_peerdb(
        request, source=source, destination=target
    ) == core.MirrorInventory(DATABASE, mappings, source)
    inspect.assert_called_once_with(request, source=source, destination=target)


def test_failed_peerdb_factory_cannot_authorize_bootstrap(monkeypatch):
    client = MemoryClient()
    source = core.PeerTarget("pg", "postgres", 5432, "app")
    target = core.PeerTarget("ch", "clickhouse", 9000, DATABASE)
    monkeypatch.setattr(
        core, "inspect_mappings", Mock(side_effect=ValueError("inspection failed"))
    )
    client.inspect_mirrors = lambda: core.MirrorInventory.from_peerdb(
        Mock(), source=source, destination=target
    )
    with pytest.raises(ValueError, match="inspection failed"):
        run(client)
    assert client.writes == []


def test_existing_unused_session_fields_are_preserved_without_becoming_required():
    client = MemoryClient(complete=True, ledger=True)
    # Literal previous CREATE contract: retained columns are harmless but must
    # not be invented by fresh installs or removed from existing installations.
    client.columns["trace_session"].update(
        {
            "external_id": ("Nullable(String)", "", ""),
            "end_user_id": ("Nullable(UUID)", "", ""),
            "status": ("LowCardinality(Nullable(String))", "", ""),
            "attributes": ("String", "DEFAULT", "'{}'"),
            "started_at": ("Nullable(DateTime64(3))", "", ""),
        }
    )
    before = copy.deepcopy(client.columns["trace_session"])
    run(client)
    assert client.columns["trace_session"] == before
    assert client.writes == []


def test_incompatible_unused_session_column_is_not_silently_accepted():
    client = MemoryClient(complete=True, ledger=True)
    client.columns["trace_session"]["attributes"] = ("UInt64", "", "")
    with pytest.raises(core.BootstrapError, match="trace_session"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize("corruption", ["hash", "table", "column", "ledger_engine"])
def test_recorded_upgrade_cannot_hide_partial_or_incompatible_physical_state(
    corruption,
):
    client = MemoryClient(complete=True, ledger=True)
    client.remove("tracer_trace")
    if corruption == "hash":
        client.versions[upgrade.MIGRATION_NAME] = "0" * 64
    elif corruption == "table":
        client.remove("model_hub_score")
    elif corruption == "column":
        del client.columns["model_hub_score"]["value_history"]
    else:
        row = list(client.tables["schema_versions"])
        row[0] = "ReplicatedMergeTree"
        client.tables["schema_versions"] = tuple(row)
    with pytest.raises(core.BootstrapError):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize("failure", ["tracer_trace", "value_history", "dataset_cells"])
def test_failure_stops_exactly_there_without_cleanup_or_automatic_retry(failure):
    client = MemoryClient()
    client.fail_on = failure
    with pytest.raises(RuntimeError, match="injected"):
        run(client)
    assert client.writes[-1][-1] == failure
    assert "simulate_calls" not in client.tables
    assert (upgrade.MIGRATION_NAME in client.versions) is (failure == "dataset_cells")
    # Explicit new invocation, not an internal retry, preserves completed work.
    retained = set(client.tables)
    client.fail_on = None
    assert run(client).created
    assert retained <= set(client.tables)


@pytest.mark.parametrize(
    "ignored", ["tracer_trace", "value_history", "record", "dataset_cells"]
)
def test_false_success_from_create_add_record_or_dependent_is_caught_by_postchecks(
    ignored,
):
    client = MemoryClient()
    client.ignore = ignored
    # value_history is already present in new canonical CREATE; use known old row shape.
    if ignored == "value_history":
        client = MemoryClient(old=True)
        client.remove("dataset_cells")
        client.ignore = ignored
    with pytest.raises((core.BootstrapError, upgrade.CDCUpgradeError)):
        run(client)
    if ignored in {"tracer_trace", "value_history", "record"}:
        assert "dataset_cells" not in client.tables


def test_incompatible_create_race_is_detected_before_the_upgrade():
    client = MemoryClient()
    client.corrupt_after_create = "model_hub_score"
    with pytest.raises(core.BootstrapError, match="model_hub_score.id"):
        run(client)
    assert not any(e[0] in {"ADD", "RECORD"} for e in client.writes)


def test_target_and_select_errors_fail_before_any_write():
    client = MemoryClient()
    client.database = "wrong"
    with pytest.raises(core.BootstrapError, match="client database"):
        run(client)
    assert client.writes == []
    client.query = Mock(side_effect=PermissionError("SELECT denied"))
    with pytest.raises(PermissionError, match="SELECT denied"):
        run(client)
    assert client.writes == []


def test_full_column_contract_has_independent_sensitive_field_oracles():
    score, _, keys = core._table(core.LANDING["model_hub_score"])
    assert len(score) == 26
    assert (
        score["project_id"]
        == score["tracer_project_id"]
        == (("Nullable", "(", "UUID", ")"), "", ())
    )
    assert score["value_history"] == (("String",), "DEFAULT", ("'[]'",))
    assert core._key(keys["ORDER"]) == ("label_id", ",", "created_at", ",", "id")
    usage, _, _ = core._table(core.LANDING["usage_apicalllog"])
    assert usage["cost"][0] == ("Decimal", "(", "16", ",", "8", ")")
    assert usage["eval_score"][1] == "MATERIALIZED"
    assert "'score'" in usage["eval_score"][2]
    assert core._tokens("'a  b' -- comment\n, `id`") == ("'a  b'", ",", "id")
    assert core._tokens("'a  b'") != core._tokens("'a b'")


def test_no_arbitrary_multi_statement_or_destructive_definition_can_enter_write_map(
    monkeypatch,
):
    client = MemoryClient()
    monkeypatch.setitem(
        core.LANDING,
        "tracer_trace",
        core.LANDING["tracer_trace"] + "\nDROP TABLE spans;\n",
    )
    with pytest.raises(core.BootstrapError, match="one packaged additive CREATE"):
        run(client)
    assert client.writes == []


def test_native_spans_final_overrides_remain_tied_to_packaged_definitions():
    root = Path(apply_schema.__file__).with_name("schema")
    statements = []
    for name in (
        "002_spans_v2.sql",
        "005_fix_nullable_hot_keys.sql",
        "013_attributes_extra_as_string.sql",
        "014_peerdb_is_deleted_back_compat.sql",
        "015_traces_and_trace_dict.sql",
    ):
        statements.extend(apply_schema.split_statements((root / name).read_text()))
    # Independent source check: each final override declaration occurs in a
    # packaged ADD/MODIFY, without executing/interpreting those migrations.
    source = [core._tokens(s) for s in statements if s.startswith("ALTER TABLE spans")]
    for name, definition in core._SPAN_COLUMNS.items():
        wanted = (name, *core._tokens(definition))
        assert any(
            any(tokens[i : i + len(wanted)] == wanted for i in range(len(tokens)))
            for tokens in source
        ), name


def test_partial_existing_upgrade_refuses_dangling_view_then_preserves_survivor():
    client = MemoryClient(old=True)
    client.remove("simulate_agent_definition")
    client.remove("simulate_agent_dict")
    client.mappings = (("mirror_model_hub_score", "model_hub_score"),)
    with pytest.raises(
        core.BootstrapError, match="simulate_calls: missing view prerequisites"
    ):
        run(client)
    assert client.writes == []
    # A separately modelled partial state without the dependent view is safe to
    # bootstrap. This double removal is not a production cleanup/repair action.
    client.remove("simulate_calls")
    result = run(client)
    assert result.created == (
        "simulate_agent_definition",
        "simulate_agent_dict",
        "simulate_calls",
    )
    assert result.upgrade_applied
    assert client.mappings == (("mirror_model_hub_score", "model_hub_score"),)
    assert ("CREATE", "model_hub_score") not in client.writes


def test_complete_mirrored_database_is_a_strict_write_free_noop():
    client = MemoryClient(complete=True, ledger=True)
    client.mappings = tuple((f"mirror_{name}", name) for name in LANDING_NAMES)
    retained = copy.deepcopy(
        (
            client.tables,
            client.columns,
            client.indexes,
            client.versions,
            client.mappings,
        )
    )
    assert run(client) == core.BootstrapResult((), False)
    assert client.writes == []
    assert retained == (
        client.tables,
        client.columns,
        client.indexes,
        client.versions,
        client.mappings,
    )


@pytest.mark.parametrize("relation", ["tables", "columns", "indexes"])
@pytest.mark.parametrize("foreign", [False, True])
def test_duplicate_or_foreign_metadata_is_rejected_without_writes(relation, foreign):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    original = client.query
    source = {
        "tables": "FROM system.tables",
        "columns": "FROM system.columns",
        "indexes": "FROM system.data_skipping_indices",
    }[relation]

    def query(sql, **kwargs):
        result = original(sql, **kwargs)
        if source in sql:
            row = result.result_rows[0]
            result.result_rows.append(("foreign", *row[1:]) if foreign else row)
        return result

    client.query = query
    with pytest.raises(core.BootstrapError, match="unexpected/duplicate"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "field",
    ["engine", "version", "partition", "sorting", "primary", "ttl", "extra", "alias"],
)
def test_native_physical_engine_key_ttl_and_alias_fail_closed(field):
    client = MemoryClient()
    if field == "alias":
        client.columns["spans"]["_peerdb_is_deleted"] = ("UInt8", "DEFAULT", "0")
    elif field == "extra":
        client.columns["spans"]["unknown"] = ("String", "", "")
    else:
        row = list(client.tables["spans"])
        if field == "ttl":
            row[-1] += " TTL now()"
        else:
            row[
                {"engine": 0, "version": 1, "partition": 2, "sorting": 3, "primary": 4}[
                    field
                ]
            ] = "wrong"
        client.tables["spans"] = tuple(row)
    with pytest.raises(core.BootstrapError, match="spans"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "mappings",
    [
        None,
        [],
        (None,),
        (("mirror_model_hub_score", None),),
        (("mirror_model_hub_score",),),
    ],
)
def test_malformed_inventory_cannot_be_interpreted_as_complete(mappings):
    client = MemoryClient()
    client.inspect_mirrors = lambda: core.MirrorInventory(DATABASE, mappings)
    with pytest.raises(core.BootstrapError, match="mirror inventory"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "database",
    [
        "system",
        "information_schema",
        "db; DROP TABLE spans",
        "",
        "with-dash",
        "a" * 129,
    ],
)
def test_invalid_database_does_not_read_or_write(database):
    client = MemoryClient()
    with pytest.raises(core.BootstrapError):
        core.bootstrap_cdc(
            client,
            database=database,
            inspect_mirrors=client.inspect_mirrors,
            applied_by="offline-test",
        )
    assert client.events == []


def test_bad_packaged_absent_definition_refuses_before_any_create(monkeypatch):
    client = MemoryClient()
    monkeypatch.setitem(
        core.LANDING,
        "tracer_enduser",
        core.LANDING["tracer_enduser"].replace("id UUID,", "id UUID, id UUID,", 1),
    )
    with pytest.raises(core.BootstrapError, match="duplicate packaged column"):
        run(client)
    assert client.events == []


@pytest.mark.parametrize("expression", ["a(", "a)", "a([)]", "a[b)"])
def test_unbalanced_packaged_declarations_fail_closed(expression):
    with pytest.raises(core.BootstrapError, match="unbalanced"):
        core._groups(core._tokens(expression))


def test_ledger_shape_must_match_before_any_missing_create():
    client = MemoryClient(old=True, ledger=True)
    client.remove("tracer_trace")
    client.columns["schema_versions"]["sha256"] = ("String", "", "")
    with pytest.raises(core.BootstrapError, match="schema_versions.sha256"):
        run(client)
    assert client.writes == []


def test_engine_full_storage_clauses_do_not_change_engine_argument_contract():
    client = MemoryClient(complete=True, ledger=True)
    for name in (*LANDING_NAMES, *core._NATIVE_FILES, "schema_versions"):
        row = list(client.tables[name])
        row[1] += f" ORDER BY ({row[3]}) SETTINGS index_granularity = 8192"
        client.tables[name] = tuple(row)
    assert run(client) == core.BootstrapResult((), False)
    assert client.writes == []


# Independent server literal reported by main's SELECT-only CH25.3.14.14/default
# probe. Do not derive this actual tuple from _SPAN_COLUMNS or canonical DDL.
CAPTURED_DEFAULT_TRACE_NAME = (
    "String",
    "MATERIALIZED",
    "ifNull(dictGetOrDefault('default.trace_dict','name',toUUID(trace_id),''),'')",
)


@pytest.mark.parametrize(
    "database,actual",
    [
        ("default", CAPTURED_DEFAULT_TRACE_NAME),
        (
            "offline_cdc",
            (
                "String",
                "MATERIALIZED",
                "ifNull(dictGetOrDefault('offline_cdc.trace_dict','name',toUUID(trace_id),''),'')",
            ),
        ),
        (
            "default",
            (
                "String",
                "MATERIALIZED",
                "ifNull(dictGetOrDefault('trace_dict','name',toUUID(trace_id),''),'')",
            ),
        ),
    ],
)
def test_trace_name_exact_current_database_or_unqualified_form_is_write_free(
    database, actual
):
    client = MemoryClient(complete=True, ledger=True, database=database)
    client.columns["spans"]["trace_name"] = actual
    assert core.bootstrap_cdc(
        client,
        database=database,
        inspect_mirrors=client.inspect_mirrors,
        applied_by="offline-test",
    ) == core.BootstrapResult((), False)
    assert client.columns["spans"]["trace_name"] == actual
    assert client.writes == []


@pytest.mark.parametrize(
    "field,replacement",
    [
        (0, "Nullable(String)"),
        (1, "DEFAULT"),
        (1, "ALIAS"),
        (
            2,
            "ifNull(dictGetOrDefault('foreign.trace_dict','name',toUUID(trace_id),''),'')",
        ),
        (
            2,
            "ifNull(dictGetOrDefault('default.other_dict','name',toUUID(trace_id),''),'')",
        ),
        (
            2,
            "ifNull(dictGetOrDefault('default.trace_dict','external_id',toUUID(trace_id),''),'')",
        ),
        (2, "ifNull(dictGetOrDefault('default.trace_dict','name',toUUID(id),''),'')"),
        (2, "ifNull(dictGetOrDefault('default.trace_dict','name',trace_id,''),'')"),
        (
            2,
            "ifNull(dictGetOrDefault('default.trace_dict','name',toUUID(trace_id),'fallback'),'')",
        ),
        (
            2,
            "ifNull(dictGetOrDefault('default.trace_dict','name',toUUID(trace_id),''),'fallback')",
        ),
        (2, "dictGetOrDefault('default.trace_dict','name',toUUID(trace_id),'')"),
    ],
)
def test_trace_name_equivalence_does_not_change_type_kind_lookup_or_fallback(
    field, replacement
):
    client = MemoryClient(database="default")
    actual = list(CAPTURED_DEFAULT_TRACE_NAME)
    actual[field] = replacement
    client.columns["spans"]["trace_name"] = tuple(actual)
    with pytest.raises(core.BootstrapError, match="spans.trace_name"):
        core.bootstrap_cdc(
            client,
            database="default",
            inspect_mirrors=client.inspect_mirrors,
            applied_by="offline-test",
        )
    assert client.writes == []


def test_trace_name_default_qualification_is_foreign_to_a_nondefault_target():
    client = MemoryClient()
    client.columns["spans"]["trace_name"] = CAPTURED_DEFAULT_TRACE_NAME
    with pytest.raises(core.BootstrapError, match="spans.trace_name"):
        run(client)
    assert client.writes == []


def test_trace_name_qualification_does_not_bypass_native_dictionary_source_check():
    client = MemoryClient(database="default")
    client.columns["spans"]["trace_name"] = CAPTURED_DEFAULT_TRACE_NAME
    row = list(client.tables["trace_dict"])
    row[-1] = row[-1].replace("TABLE 'traces'", "TABLE 'tracer_trace'")
    client.tables["trace_dict"] = tuple(row)
    with pytest.raises(
        core.BootstrapError, match="trace_dict: incompatible dictionary"
    ):
        core.bootstrap_cdc(
            client,
            database="default",
            inspect_mirrors=client.inspect_mirrors,
            applied_by="offline-test",
        )
    assert client.writes == []


@pytest.mark.parametrize("view", VIEW_PREREQUISITES)
@pytest.mark.parametrize(
    "field,replacement",
    [
        ("stub_id", ("String", "", "")),
        ("stub_number", ("Float64", "", "")),
        ("stub_status", ("String", "", "")),
        ("stub_time", ("DateTime64(6)", "", "")),
        ("stub_flag", ("Nullable(Bool)", "", "")),
        ("stub_id", ("UUID", "DEFAULT", "")),
        ("stub_id", ("UUID", "", "generateUUIDv4()")),
    ],
)
def test_view_output_mismatch_refuses_before_any_write(view, field, replacement):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")  # otherwise a pending first write
    client.columns[view][field] = replacement
    row = list(client.tables[view])
    # Real CREATE VIEW metadata can declare an output schema before AS. The
    # SELECT is unchanged; system.columns must not be ignored on that basis.
    declarations = ", ".join(f"{n} {s[0]}" for n, s in client.columns[view].items())
    row[-1] = row[-1].replace(f"{view} AS", f"{view} ({declarations}) AS", 1)
    client.tables[view] = tuple(row)
    with pytest.raises(core.BootstrapError, match=f"{view}: incompatible view output"):
        run(client)
    assert client.writes == []
    assert client.headers[view].column_names == tuple(n for n, _ in STUB_HEADER)
    assert tuple(t.name for t in client.headers[view].column_types) == tuple(
        t for _, t in STUB_HEADER
    )


@pytest.mark.parametrize("view", VIEW_PREREQUISITES)
@pytest.mark.parametrize("change", ["rename", "reorder", "missing", "extra", "empty"])
def test_view_output_inventory_and_order_are_strict(view, change):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    columns = client.columns[view]
    if change == "rename":
        client.columns[view] = {
            ("renamed" if n == "stub_id" else n): s for n, s in columns.items()
        }
    elif change == "reorder":
        client.columns[view] = dict(reversed(list(columns.items())))
    elif change == "missing":
        columns.pop("stub_id")
    elif change == "extra":
        columns["extra"] = ("String", "", "")
    else:
        columns.clear()
    with pytest.raises(core.BootstrapError, match=f"{view}: incompatible view output"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "view,prerequisite",
    [(v, p) for v, ps in VIEW_PREREQUISITES.items() for p in ps],
)
def test_existing_view_missing_prerequisite_refuses_without_inference_or_writes(
    view, prerequisite
):
    client = MemoryClient(complete=True)
    client.remove(prerequisite)
    with pytest.raises(
        core.BootstrapError, match=f"{view}: missing view prerequisites.*{prerequisite}"
    ):
        run(client)
    assert client.writes == []
    assert ("INFER", view) not in client.events


@pytest.mark.parametrize("view", VIEW_PREREQUISITES)
@pytest.mark.parametrize("failure", [TimeoutError, PermissionError, RuntimeError])
def test_existing_view_inference_error_has_no_fallback_or_writes(view, failure):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    client.headers[view] = failure("injected header query refusal")
    with pytest.raises(failure, match="injected header query refusal"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize("view", VIEW_PREREQUISITES)
@pytest.mark.parametrize(
    "malformed",
    ["empty", "duplicate", "short", "bad_name", "bad_type", "rows", "missing_types"],
)
def test_malformed_view_inference_header_refuses_without_fallback(view, malformed):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    header = client.headers[view]
    if malformed == "empty":
        header.column_names = header.column_types = ()
    elif malformed == "duplicate":
        header.column_names = ("stub_id",) * len(header.column_types)
    elif malformed == "short":
        header.column_types = header.column_types[:-1]
    elif malformed == "bad_name":
        header.column_names = (None, *header.column_names[1:])
    elif malformed == "bad_type":
        header.column_types[0].name = ""
    elif malformed == "rows":
        header.result_rows = [("unexpected data",)]
    else:
        del header.column_types
    with pytest.raises(
        core.BootstrapError, match=f"{view}: invalid inferred view header"
    ):
        run(client)
    assert client.writes == []


def test_view_header_oracle_is_independent_of_physical_metadata_and_ddl():
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    client.headers["dataset_cells"].column_types[0].name = "Int64"
    assert client.columns["dataset_cells"]["stub_id"] == ("UUID", "", "")
    with pytest.raises(
        core.BootstrapError, match="dataset_cells: incompatible view output"
    ):
        run(client)
    assert client.writes == []


def test_view_inference_uses_canonical_select_and_ordered_physical_metadata_write_free():
    client = MemoryClient(complete=True, ledger=True)
    assert run(client) == core.BootstrapResult((), False)
    assert {e[1] for e in client.events if e[0] == "INFER"} == set(VIEW_PREREQUISITES)
    # Query construction/settings are independently asserted in MemoryClient.
    assert all(
        " ".join(e[1].split()).endswith("ORDER BY table, position")
        for e in client.events
        if e[0] == "SELECT" and "FROM system.columns" in e[1] and "%(names)s" in e[1]
    )
    assert client.writes == []


@pytest.mark.parametrize("view", VIEW_PREREQUISITES)
def test_absent_view_infers_after_verified_prerequisites_before_create_and_postchecks(
    view,
):
    client = MemoryClient()
    assert run(client).created == tuple(LANDING_NAMES + DEPENDENT_NAMES)
    created = client.events.index(("CREATE", view))
    inferred = client.events.index(("INFER", view))
    assert inferred < created
    assert all(
        client.events.index(("CREATE", p)) < inferred for p in VIEW_PREREQUISITES[view]
    )
    last_prereq = max(
        client.events.index(("CREATE", p)) for p in VIEW_PREREQUISITES[view]
    )
    assert any(
        e[0] == "SELECT" and "FROM system.tables" in e[1]
        for e in client.events[last_prereq + 1 : inferred]
    )
    assert any(
        e[0] == "SELECT" and "FROM system.columns" in e[1]
        for e in client.events[created + 1 :]
    )
    assert ("INFER", view) in client.events[created + 1 :]


@pytest.mark.parametrize("view", VIEW_PREREQUISITES)
def test_absent_view_inference_failure_retains_prerequisites_without_creating_view(
    view,
):
    client = MemoryClient()
    client.headers[view] = TimeoutError("injected zero-row inference timeout")
    with pytest.raises(TimeoutError, match="zero-row inference timeout"):
        run(client)
    assert ("CREATE", view) not in client.writes
    assert all(p in client.tables for p in VIEW_PREREQUISITES[view])
    assert client.events[-1] == ("INFER", view)


@pytest.mark.parametrize("view", VIEW_PREREQUISITES)
@pytest.mark.parametrize("failure", ["ignored", "corrupt"])
def test_view_physical_postcondition_failure_stops_later_creates(view, failure):
    client = MemoryClient()
    if failure == "ignored":
        client.ignore = view
    else:
        client.corrupt_after_create = view
    with pytest.raises(core.BootstrapError):
        run(client)
    assert client.writes[-1] == ("CREATE", view)


@pytest.mark.parametrize("view", VIEW_PREREQUISITES)
@pytest.mark.parametrize("change", ["select", "engine"])
def test_unchanged_header_does_not_mask_wrong_view_body_or_engine(view, change):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    row = list(client.tables[view])
    if change == "select":
        row[-1] = row[-1].replace(
            "c._peerdb_is_deleted = 0", "c._peerdb_is_deleted = 1"
        )
    else:
        row[0] = "MaterializedView"
    client.tables[view] = tuple(row)
    with pytest.raises(
        core.BootstrapError, match=f"{view}: incompatible view definition"
    ):
        run(client)
    assert client.writes == []
    assert not any(e[0] == "INFER" for e in client.events)


@pytest.mark.parametrize(
    "prerequisite",
    [
        "spans",
        "model_hub_cell",
        "simulate_agent_definition",
        "column_dict",
        "simulate_version_dict",
    ],
)
def test_all_physical_contracts_checked_before_any_existing_view_inference(
    prerequisite,
):
    client = MemoryClient(complete=True)
    client.remove("tracer_trace")
    if prerequisite.endswith("_dict"):
        row = list(client.tables[prerequisite])
        row[-1] = row[-1].replace("DB 'offline_cdc'", "DB 'foreign'")
        client.tables[prerequisite] = tuple(row)
    else:
        client.columns[prerequisite]["id"] = ("Nullable(String)", "", "")
    with pytest.raises(core.BootstrapError, match=prerequisite):
        run(client)
    assert client.writes == []
    assert not any(e[0] == "INFER" for e in client.events)


@pytest.mark.parametrize(
    "view,prerequisite",
    [("dataset_cells", "column_dict"), ("simulate_calls", "simulate_version_dict")],
)
@pytest.mark.parametrize("failure", ["ignored", "corrupt"])
def test_new_view_waits_for_physical_dictionary_postcondition(
    view, prerequisite, failure
):
    client = MemoryClient()
    if failure == "ignored":
        client.ignore = prerequisite
    else:
        command = client.command

        def corrupt(sql):
            command(sql)
            if prerequisite in client.tables:
                row = list(client.tables[prerequisite])
                row[-1] = row[-1].replace("DB 'offline_cdc'", "DB 'foreign'")
                client.tables[prerequisite] = tuple(row)

        client.command = corrupt
    with pytest.raises(core.BootstrapError, match=prerequisite):
        run(client)
    assert ("INFER", view) not in client.events
    assert ("CREATE", view) not in client.writes
    assert set(LANDING_NAMES) <= client.tables.keys()


@pytest.mark.parametrize(
    "bad_select", ["SELECT 1; SELECT 2;", "DESCRIBE TABLE spans;", "SELECT 1"]
)
def test_invalid_packaged_view_is_rejected_before_inspection_or_writes(
    monkeypatch, bad_select
):
    client = MemoryClient()
    monkeypatch.setitem(
        core.DEPENDENT,
        "dataset_cells",
        f"CREATE VIEW IF NOT EXISTS dataset_cells AS\n{bad_select}",
    )
    with pytest.raises(core.BootstrapError, match="packaged"):
        run(client)
    assert client.events == []


def test_existing_known_upgrade_allowance_does_not_change_view_inference_inputs():
    # The three already approved additions are not referenced by either view or
    # their dictionary sources. Keep that bounded upgrade behavior, not a wider
    # allowance for missing columns used to obtain a fresh output type oracle.
    for view, prerequisites in VIEW_PREREQUISITES.items():
        ddl = core.DEPENDENT[view] + "\n".join(
            core.DEPENDENT[n] for n in prerequisites if n.endswith("_dict")
        )
        for _, column in ADDS:
            assert column not in core._tokens(ddl)
            assert column not in ddl  # dictionary SELECTs are quoted tokens


def test_existing_driver_result_header_retains_wrappers_without_manual_inference():
    from clickhouse_connect.datatypes.registry import get_from_name
    from clickhouse_connect.driver.query import QueryResult

    client = MemoryClient(complete=True, ledger=True)
    for view in VIEW_PREREQUISITES:
        client.headers[view] = QueryResult(
            result_set=[],
            column_names=tuple(n for n, _ in STUB_HEADER),
            column_types=tuple(get_from_name(t) for _, t in STUB_HEADER),
        )
    assert run(client) == core.BootstrapResult((), False)
    assert client.writes == []


@pytest.mark.parametrize("present_mask", range(16))
def test_recorded_first_migration_does_not_block_missing_second_fields(present_mask):
    client = MemoryClient(complete=True, ledger=True)
    second = "cdc_002_usage_eval_fields.sql"
    del client.versions[second]
    eval_fields = list(ADDS)[3:]
    for bit, (table, column) in enumerate(eval_fields):
        if not present_mask & (1 << bit):
            del client.columns[table][column]
    original_tables = copy.deepcopy(client.tables)
    original_indexes = copy.deepcopy(client.indexes)
    original_mirrors = client.mappings
    assert run(client) == core.BootstrapResult((), True)
    assert client.writes == [("ADD", t, c) for t, c in eval_fields] + [
        ("RECORD", second)
    ]
    assert client.tables == original_tables
    assert client.indexes == original_indexes
    assert client.mappings == original_mirrors
    writes = list(client.writes)
    assert run(client) == core.BootstrapResult((), False)
    assert client.writes == writes


@pytest.mark.parametrize("migration", MIGRATION_NAMES)
def test_one_recorded_migration_does_not_mask_the_others_missing_column(migration):
    client = MemoryClient(complete=True, ledger=True)
    other = next(name for name in MIGRATION_NAMES if name != migration)
    del client.versions[other]
    key = list(ADDS)[0 if migration == MIGRATION_NAMES[0] else 3]
    del client.columns[key[0]][key[1]]
    with pytest.raises(core.BootstrapError, match="missing/unexpected columns"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize("column", [key[1] for key in list(ADDS)[3:]])
def test_second_migration_failure_preserves_first_record_and_never_creates_dependents(
    column,
):
    client = MemoryClient(old=True)
    client.remove("dataset_cells")
    client.fail_on = column
    with pytest.raises(RuntimeError, match="injected ADD"):
        run(client)
    assert upgrade.MIGRATION_NAME in client.versions
    assert "cdc_002_usage_eval_fields.sql" not in client.versions
    assert "dataset_cells" not in client.tables
    before = copy.deepcopy(client.columns)
    client.fail_on = None
    assert run(client) == core.BootstrapResult(("dataset_cells",), True)
    for table, columns in before.items():
        assert all(
            client.columns[table][key] == value for key, value in columns.items()
        )


def test_wrong_second_migration_hash_blocks_even_the_first_add():
    client = MemoryClient(old=True, ledger=True)
    client.versions["cdc_002_usage_eval_fields.sql"] = "0" * 64
    with pytest.raises(core.BootstrapError, match="hash drift"):
        run(client)
    assert client.writes == []


@pytest.mark.parametrize("column", [key[1] for key in list(ADDS)[3:]])
def test_ignored_eval_add_is_detected_and_never_retried_behind_a_record(column):
    client = MemoryClient(old=True)
    client.remove("dataset_cells")
    client.ignore = column
    with pytest.raises(upgrade.CDCUpgradeError, match="incomplete CDC upgrade"):
        run(client)
    assert "dataset_cells" not in client.tables
    assert "cdc_002_usage_eval_fields.sql" in client.versions
    writes = list(client.writes)
    with pytest.raises(core.BootstrapError, match="missing/unexpected columns"):
        run(client)
    assert client.writes == writes


@pytest.mark.parametrize("stored", [False, True])
def test_lost_eval_history_ack_is_reconciled_before_explicit_retry(stored):
    client = MemoryClient(old=True)
    client.remove("dataset_cells")
    original_insert = client.insert

    def fail_record(table, rows, column_names):
        if rows[0][0] == "cdc_002_usage_eval_fields.sql":
            if stored:
                original_insert(table, rows, column_names)
            raise OSError("synthetic history acknowledgement failure")
        return original_insert(table, rows, column_names)

    client.insert = fail_record
    with pytest.raises(OSError, match="history acknowledgement"):
        run(client)
    assert "dataset_cells" not in client.tables
    assert ("cdc_002_usage_eval_fields.sql" in client.versions) is stored
    before = list(client.writes)
    client.insert = original_insert
    assert run(client).created == ("dataset_cells",)
    new_adds = [event for event in client.writes[len(before) :] if event[0] == "ADD"]
    assert new_adds == ([] if stored else [("ADD", t, c) for t, c in list(ADDS)[3:]])


def source_client():
    """Synthetic metadata double, not proof of PG/PeerDB's actual type mapping.

    Reuse only the application's column-name inventory. Independently supplied
    source types/layout differ from canonical CREATEs. Never derive expectations
    from the CH metadata being validated; negatives change only that metadata.
    """
    client = MemoryClient()
    source = core.PeerTarget("source_alias", "postgres", 5432, "source_db")
    profiles = {}
    for name in LANDING_NAMES:
        fields = []
        for column in core._table(client.create[name])[0]:
            if column.startswith("_peerdb_") or (name, column) in list(ADDS)[3:]:
                continue
            data_type = {
                "id": "Int64" if name == "usage_apicalllog" else "UUID",
                "tracer_project_id": "Nullable(UUID)",
                "target_speaks_first": "Nullable(Bool)",
                "prompt_tokens": "Nullable(Int32)",
                "pass_rate": "Nullable(Decimal(5,2))",
            }.get(column, "String")
            fields.append((column, data_type))
        profiles[name] = core.SourceTable(tuple(fields), ("id",))
        # Actual metadata is separate from the production inspection generator.
        client.columns[name] = {c: (t, "", "") for c, t in fields}
        client.columns[name].update(
            {
                "_peerdb_synced_at": ("DateTime64(9)", "DEFAULT", "now64()"),
                "_peerdb_is_deleted": ("UInt8", "", ""),
                "_peerdb_version": ("UInt64", "", ""),
            }
        )
        client.indexes[name] = {}
        client.tables[name] = (
            "ReplacingMergeTree",
            "ReplacingMergeTree(_peerdb_version, _peerdb_is_deleted)",
            "",
            "id",
            "id",
            f"CREATE TABLE {name} (id UUID) ENGINE = "
            "ReplacingMergeTree(_peerdb_version, _peerdb_is_deleted) ORDER BY id",
        )
    client.mappings = tuple(("one_mirror", name) for name in LANDING_NAMES)
    client.inspect_mirrors = lambda: core.MirrorInventory(
        DATABASE, client.mappings, source
    )
    client.inspect_source = lambda: core.SourceInventory(source, profiles)
    return client


def run_source(client):
    return core.bootstrap_cdc(
        client,
        database=DATABASE,
        inspect_mirrors=client.inspect_mirrors,
        inspect_source=client.inspect_source,
        applied_by="offline-test",
    )


def test_source_owned_applies_only_derived_columns_then_dependents_and_repeat_noop():
    client = source_client()
    source_before = copy.deepcopy(client.columns)
    result = run_source(client)
    assert result == core.BootstrapResult(tuple(DEPENDENT_NAMES), True)
    assert client.writes == [
        ("CREATE", "schema_versions"),
        *[("ADD", t, c) for t, c in list(ADDS)[3:]],
        ("RECORD", MIGRATION_NAMES[1]),
        *[("CREATE", name) for name in DEPENDENT_NAMES],
    ]
    assert set(client.versions) == {MIGRATION_NAMES[1]}
    for name, columns in source_before.items():
        assert all(client.columns[name][c] == shape for c, shape in columns.items())
    before = list(client.writes)
    assert run_source(client) == core.BootstrapResult((), False)
    assert client.writes == before


@pytest.mark.parametrize("table", LANDING_NAMES)
@pytest.mark.parametrize("missing", ["mapping", "table", "source"])
def test_source_owned_requires_all_mappings_tables_and_source_metadata(table, missing):
    client = source_client()
    if missing == "mapping":
        client.mappings = tuple(p for p in client.mappings if p[1] != table)
    elif missing == "table":
        client.remove(table)
    else:
        snapshot = client.inspect_source()
        snapshot.tables.pop(table)
        client.inspect_source = lambda: snapshot
    with pytest.raises(core.BootstrapError):
        run_source(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("name", "other_alias"),
        ("host", "other_postgres"),
        ("port", 5433),
        ("database", "other_db"),
    ],
)
def test_source_owned_identity_must_match_verified_mirror_source(attribute, value):
    client = source_client()
    snapshot = client.inspect_source()
    identity = vars(snapshot.source).copy()
    identity[attribute] = value
    client.inspect_source = lambda: core.SourceInventory(
        core.PeerTarget(**identity), snapshot.tables
    )
    with pytest.raises(core.BootstrapError, match="exact PG peer"):
        run_source(client)
    assert client.writes == []


@pytest.mark.parametrize("lost_nullability", [False, True])
@pytest.mark.parametrize(
    "column,scalar",
    [
        ("deleted_at", "DateTime64(6)"),
        ("eval_type_id", "String"),
        ("output_metadata", "String"),
        ("eval_explanation", "String"),
        ("output_bool", "Bool"),
        ("output_float", "Float64"),
        ("output_str", "String"),
        ("eval_id", "String"),
        ("eval_task_id", "String"),
        ("error_message", "String"),
        ("custom_eval_config_id", "UUID"),
        ("observation_span_id", "String"),
        ("trace_id", "UUID"),
        ("trace_session_id", "UUID"),
        ("skipped_reason", "String"),
        ("config_hash", "String"),
    ],
)
def test_source_owned_eval_nullable_fields_never_accept_lossy_retained_layout(
    column, scalar, lost_nullability
):
    # Literal PG field contracts, independently checked against EvalLogger and
    # information_schema. Metadata proof only: not recovery of already lost NULLs.
    client = source_client()
    snapshot = client.inspect_source()
    name = "tracer_eval_logger"
    original = snapshot.tables[name]
    assert column in dict(original.columns)
    nullable = f"Nullable({scalar})"
    snapshot.tables[name] = core.SourceTable(
        tuple((c, nullable if c == column else t) for c, t in original.columns),
        original.primary_key,
    )
    client.inspect_source = lambda: snapshot
    client.columns[name][column] = (
        scalar if lost_nullability else nullable,
        "",
        "",
    )
    before = copy.deepcopy(client.columns[name])
    if lost_nullability:
        with pytest.raises(
            core.BootstrapError,
            match=rf"tracer_eval_logger\.{column}: incompatible type/default",
        ):
            run_source(client)
        assert client.writes == []
    else:
        assert run_source(client).upgrade_applied
    assert client.columns[name] == before


@pytest.mark.parametrize(
    "column,shape",
    [
        ("tracer_project_id", ("UUID", "", "")),
        ("value_history", ("String", "DEFAULT", "'[]'")),
        ("unknown_field", ("String", "", "")),
        ("_peerdb_version", ("Int64", "", "")),
        ("_peerdb_is_deleted", ("Bool", "", "")),
        ("_peerdb_synced_at", ("DateTime64(6)", "", "")),
    ],
)
def test_source_owned_rejects_changed_or_invented_column_without_writes(column, shape):
    client = source_client()
    client.columns["model_hub_score"][column] = shape
    with pytest.raises((core.BootstrapError, upgrade.CDCUpgradeError)):
        run_source(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "index,value",
    [
        (0, "MergeTree"),
        (1, "ReplacingMergeTree(_peerdb_version)"),
        (2, "toYYYYMM(created_at)"),
        (3, "dataset_id, column_id, row_id"),
        (4, "dataset_id"),
    ],
)
def test_source_owned_rejects_wrong_row_identity_or_transport(index, value):
    client = source_client()
    row = list(client.tables["model_hub_cell"])
    row[index] = value
    client.tables["model_hub_cell"] = tuple(row)
    with pytest.raises(core.BootstrapError):
        run_source(client)
    assert client.writes == []


def test_source_owned_cannot_hide_missing_application_column_in_both_schemas():
    client = source_client()
    snapshot = client.inspect_source()
    name = "model_hub_cell"
    original = snapshot.tables[name]
    snapshot.tables[name] = core.SourceTable(
        tuple(p for p in original.columns if p[0] != "dataset_id"), ("id",)
    )
    client.inspect_source = lambda: snapshot
    client.columns[name].pop("dataset_id")
    with pytest.raises(core.BootstrapError, match="application source columns"):
        run_source(client)
    assert client.writes == []


@pytest.mark.parametrize("migration", MIGRATION_NAMES)
def test_source_owned_rejects_existing_known_hash_drift(migration):
    client = source_client()
    client.install("schema_versions", core._LEDGER_DDL)
    client.versions[migration] = "0" * 64
    with pytest.raises(
        (core.BootstrapError, upgrade.CDCUpgradeError), match="hash drift"
    ):
        run_source(client)
    assert client.writes == []


def test_source_owned_existing_cdc001_record_is_preserved_not_required_or_replayed():
    client = source_client()
    client.install("schema_versions", core._LEDGER_DDL)
    client.versions[MIGRATION_NAMES[0]] = upgrade.migration_file(
        MIGRATION_NAMES[0]
    ).sha256
    assert run_source(client).upgrade_applied
    assert set(client.versions) == set(MIGRATION_NAMES)
    assert ("RECORD", MIGRATION_NAMES[0]) not in client.writes
    assert not any(event[:2] == ("ADD", "model_hub_score") for event in client.writes)


def test_source_owned_partial_derived_failure_stops_then_explicit_resume_is_idempotent():
    client = source_client()
    client.fail_on = "eval_trace_id"
    with pytest.raises(RuntimeError, match="ADD failure"):
        run_source(client)
    assert client.versions == {}
    assert not set(DEPENDENT_NAMES) & client.tables.keys()
    assert "eval_score" in client.columns["usage_apicalllog"]
    client.fail_on = None
    assert run_source(client).upgrade_applied
    before = list(client.writes)
    assert run_source(client) == core.BootstrapResult((), False)
    assert client.writes == before


def test_source_owned_source_read_failure_has_no_canonical_fallback():
    client = source_client()
    client.inspect_source = Mock(side_effect=PermissionError("source unavailable"))
    with pytest.raises(PermissionError):
        run_source(client)
    assert client.writes == []
