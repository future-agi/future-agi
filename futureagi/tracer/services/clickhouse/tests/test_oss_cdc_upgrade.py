"""Offline additive/ledger contract using real apply helpers and an in-memory double.

No server semantics or fresh/upgrade runtime qualification is claimed here.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse import oss_cdc_upgrade as upgrade
from tracer.services.clickhouse.v2 import apply_schema

EXPECTED = {
    ("model_hub_score", "tracer_project_id"): ("Nullable(UUID)", "", ""),
    ("model_hub_score", "value_history"): ("String", "DEFAULT", "'[]'"),
    ("simulate_agent_definition", "target_speaks_first"): ("Nullable(UInt8)", "", ""),
}
STATEMENTS = [
    "ALTER TABLE model_hub_score ADD COLUMN IF NOT EXISTS tracer_project_id Nullable(UUID);",
    "ALTER TABLE model_hub_score ADD COLUMN IF NOT EXISTS value_history String DEFAULT '[]';",
    "ALTER TABLE simulate_agent_definition ADD COLUMN IF NOT EXISTS target_speaks_first Nullable(UInt8);",
]
EVAL_MIGRATION = "cdc_002_usage_eval_fields.sql"
EVAL_EXPECTED = {
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


@pytest.fixture(autouse=True)
def no_database_client(monkeypatch):
    connect = Mock(side_effect=AssertionError("offline contract must not connect"))
    monkeypatch.setattr(apply_schema.clickhouse_connect, "get_client", connect)
    yield
    connect.assert_not_called()


class MemoryClient:
    """Only the two metadata SELECTs, existing ledger helpers and three ADDs."""

    def __init__(
        self, *, complete=False, ledger=False, migration_name=upgrade.MIGRATION_NAME
    ):
        self.migration_name = migration_name
        self.expected = EVAL_EXPECTED if migration_name == EVAL_MIGRATION else EXPECTED
        self.statements = apply_schema.split_statements(
            upgrade.migration_file(migration_name).path.read_text()
        )
        self.tables = {table for table, _ in self.expected}
        if ledger:
            self.tables.add("schema_versions")
        self.columns = dict(self.expected) if complete else {}
        if migration_name == EVAL_MIGRATION:
            self.columns[("usage_apicalllog", "config")] = ("String", "", "")
        self.columns[("model_hub_score", "project_id")] = ("Nullable(UUID)", "", "")
        self.versions = {}
        self.queries = []
        self.writes = []
        self.fail_on = None

    def query(self, sql, parameters=None, settings=None):
        sql = " ".join(sql.split())
        self.queries.append(sql)
        assert sql.startswith("SELECT "), sql
        if "FROM system.tables" in sql:
            assert "database = currentDatabase()" in sql
            assert settings == {"readonly": 1}
            rows = [
                (name,) for name in sorted(self.tables) if name in parameters["tables"]
            ]
        elif "FROM system.columns" in sql:
            assert "database = currentDatabase()" in sql
            assert settings == {"readonly": 1}
            rows = [
                (*key, *value)
                for key, value in self.columns.items()
                if key in parameters["columns"]
            ]
        else:
            assert "FROM schema_versions" in sql
            assert "schema_versions" in self.tables
            assert parameters == {
                "topology_identity": "schema-topology/v1/local",
                "include_legacy_local": 1,
            }
            rows = list(self.versions.items())
        return SimpleNamespace(result_rows=rows)

    def command(self, sql):
        sql = " ".join(sql.split())
        self.writes.append(sql)
        if sql.startswith("CREATE TABLE IF NOT EXISTS schema_versions "):
            self.tables.add("schema_versions")
            return
        assert sql in self.statements, sql
        if sql == self.fail_on:
            raise RuntimeError("injected offline DDL failure")
        key = list(self.expected)[self.statements.index(sql)]
        # Model IF NOT EXISTS without changing already-present columns.
        self.columns.setdefault(key, self.expected[key])

    def insert(self, table, rows, column_names):
        assert table == "schema_versions"
        assert column_names == ["filename", "sha256", "applied_by", "notes"]
        [(filename, sha, user, notes)] = rows
        assert notes == "schema-topology/v1/local"
        assert filename == self.migration_name
        self.writes.append((table, filename, sha, user, notes))
        self.versions[filename] = sha.encode("ascii")


def _exercise_caller_seam(client):
    """Example future caller sequence, not new production bootstrap wiring."""
    before = upgrade.inspect_upgrade(client, migration_name=client.migration_name)
    if not before.recorded:
        apply_schema.ensure_versions_table(client)
        apply_schema.apply_file(client, before.migration, "offline-test")
    return upgrade.inspect_upgrade(
        client, migration_name=client.migration_name, require_complete=True
    )


def test_exact_three_adds_have_a_unique_explicit_filename_outside_default_discovery():
    migration = upgrade.migration_file()
    assert migration.path.name == "cdc_001_catalog_target_fields.sql"
    assert apply_schema.split_statements(migration.path.read_text()) == STATEMENTS
    assert migration.sha256 == hashlib.sha256(migration.path.read_bytes()).hexdigest()
    default_dir = apply_schema.build_argparser().parse_args([]).schema_dir
    assert not migration.path.is_relative_to(default_dir)
    assert (
        migration.sha256
        == "70fbc6d415c486ba9ff12640c188cfe0f38a49cfb0b8270d293b4bd1181ab42b"
    )
    assert {p.name for p in upgrade.MIGRATION_DIR.glob("*.sql")} == {
        "cdc_001_catalog_target_fields.sql",
        "cdc_002_usage_eval_fields.sql",
    }
    default_names = {
        sf.path.name for sf in apply_schema.discover_files(default_dir, None)
    }
    assert migration.path.name not in default_names


def test_eval_fields_are_four_fixed_adds_matching_fresh_schema():
    from tracer.services.clickhouse import oss_cdc_bootstrap as core
    from tracer.services.clickhouse import schema

    migration = upgrade.migration_file("cdc_002_usage_eval_fields.sql")
    statements = apply_schema.split_statements(migration.path.read_text())
    assert len(statements) == 4
    expected, _, _ = core._table(schema.CDC_USAGE_APICALLLOG)
    names = ("eval_score", "eval_output_str", "eval_trace_id", "eval_dataset_id")
    for name, statement in zip(names, statements, strict=True):
        tokens = core._tokens(statement)
        assert tokens[:8] == (
            "ALTER",
            "TABLE",
            "usage_apicalllog",
            "ADD",
            "COLUMN",
            "IF",
            "NOT",
            "EXISTS",
        )
        assert tokens[8] == name
        assert core._column(tokens[9:]) == expected[name]


def test_unknown_migration_cannot_select_arbitrary_files():
    with pytest.raises(upgrade.CDCUpgradeError):
        upgrade.migration_file("../schema.py")


@pytest.mark.parametrize("ledger", [False, True])
def test_inspection_is_select_only_and_does_not_bootstrap_an_absent_ledger(ledger):
    client = MemoryClient(ledger=ledger)
    result = upgrade.inspect_upgrade(client)
    assert result.missing_columns == tuple(
        f"{table}.{name}" for table, name in EXPECTED
    )
    assert result.recorded is False
    assert client.writes == []
    assert ("schema_versions" in client.tables) is ledger
    assert len(client.queries) == (3 if ledger else 2)
    assert all(sql.startswith("SELECT ") for sql in client.queries)


@pytest.mark.parametrize("table", ["model_hub_score", "simulate_agent_definition"])
def test_missing_destination_is_rejected_without_any_write(table):
    client = MemoryClient()
    client.tables.remove(table)
    with pytest.raises(upgrade.CDCUpgradeError, match="missing CDC destination tables"):
        upgrade.inspect_upgrade(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "key, wrong",
    [
        (("model_hub_score", "tracer_project_id"), ("UUID", "", "")),
        (("model_hub_score", "tracer_project_id"), ("Nullable(String)", "", "")),
        (("model_hub_score", "value_history"), ("Nullable(String)", "DEFAULT", "'[]'")),
        (("model_hub_score", "value_history"), ("String", "DEFAULT", "'{}'")),
        (("model_hub_score", "value_history"), ("String", "", "")),
        (("model_hub_score", "value_history"), ("String", "MATERIALIZED", "'[]'")),
        (("simulate_agent_definition", "target_speaks_first"), ("UInt8", "", "")),
        (
            ("simulate_agent_definition", "target_speaks_first"),
            ("Nullable(UInt8)", "DEFAULT", "0"),
        ),
    ],
)
def test_incompatible_preexisting_type_or_default_cannot_hide_behind_if_not_exists(
    key, wrong
):
    client = MemoryClient(complete=True, ledger=True)
    client.versions[upgrade.MIGRATION_NAME] = upgrade.migration_file().sha256
    client.columns[key] = wrong
    with pytest.raises(
        upgrade.CDCUpgradeError, match=f"incompatible {key[0]}.{key[1]}"
    ):
        _exercise_caller_seam(client)
    assert client.writes == []


@pytest.mark.parametrize("complete", [False, True])
def test_hash_drift_is_rejected_without_force_or_write(complete):
    client = MemoryClient(complete=complete, ledger=True)
    client.versions[upgrade.MIGRATION_NAME] = "0" * 64
    with pytest.raises(upgrade.CDCUpgradeError, match="hash drift"):
        _exercise_caller_seam(client)
    assert client.writes == []


def test_applied_hash_does_not_mask_a_missing_column():
    client = MemoryClient(complete=True, ledger=True)
    client.versions[upgrade.MIGRATION_NAME] = upgrade.migration_file().sha256.encode(
        "ascii"
    )
    del client.columns[("model_hub_score", "value_history")]
    with pytest.raises(upgrade.CDCUpgradeError, match="incomplete CDC upgrade"):
        _exercise_caller_seam(client)
    assert client.writes == []


def test_postcondition_requires_every_column_without_creating_a_ledger():
    client = MemoryClient()
    with pytest.raises(upgrade.CDCUpgradeError, match="incomplete CDC upgrade"):
        upgrade.inspect_upgrade(client, require_complete=True)
    assert client.writes == []


def source_owned_client(*, ledger=False):
    client = MemoryClient(complete=True, ledger=ledger)
    # Independently captured source-created fields, local PeerDB v0.36.9:
    # PG NOT NULL jsonb and nullable boolean, not our legacy ADD defaults.
    client.columns[("model_hub_score", "value_history")] = ("String", "", "")
    client.columns[("simulate_agent_definition", "target_speaks_first")] = (
        "Nullable(Bool)",
        "",
        "",
    )
    return client


@pytest.mark.parametrize("ledger", [False, True])
def test_source_owned_fields_are_prerequisites_not_an_unapplied_migration(ledger):
    client = source_owned_client(ledger=ledger)
    assert upgrade.inspect_source_fields(client) is None
    assert client.versions == {} and client.writes == []
    assert ("schema_versions" in client.tables) is ledger
    assert all(sql.startswith("SELECT ") for sql in client.queries)


@pytest.mark.parametrize("key", list(EXPECTED))
@pytest.mark.parametrize("recorded", [False, True])
def test_source_owned_missing_fields_must_not_be_added(key, recorded):
    client = source_owned_client(ledger=recorded)
    if recorded:
        client.versions[upgrade.MIGRATION_NAME] = upgrade.migration_file().sha256
    before = dict(client.versions)
    del client.columns[key]
    with pytest.raises(upgrade.CDCUpgradeError, match="incomplete CDC upgrade"):
        upgrade.inspect_source_fields(client)
    assert client.writes == [] and client.versions == before


@pytest.mark.parametrize(
    "key,wrong",
    [
        (("model_hub_score", "tracer_project_id"), ("UUID", "", "")),
        (("model_hub_score", "value_history"), ("Nullable(String)", "", "")),
        (("model_hub_score", "value_history"), ("String", "DEFAULT", "'[]'")),
        (("model_hub_score", "value_history"), ("String", "MATERIALIZED", "'[]'")),
        (("simulate_agent_definition", "target_speaks_first"), ("Bool", "", "")),
        (
            ("simulate_agent_definition", "target_speaks_first"),
            ("Nullable(UInt8)", "", ""),
        ),
        (
            ("simulate_agent_definition", "target_speaks_first"),
            ("Nullable(Bool)", "DEFAULT", "false"),
        ),
    ],
)
def test_source_owned_fields_do_not_relax_missing_value_or_expression_contract(
    key, wrong
):
    client = source_owned_client()
    client.columns[key] = wrong
    with pytest.raises(upgrade.CDCUpgradeError, match="incompatible"):
        upgrade.inspect_source_fields(client)
    assert client.writes == []


@pytest.mark.parametrize("hash_matches", [False, True])
def test_source_owned_fields_preserve_and_validate_any_old_migration_record(
    hash_matches,
):
    client = source_owned_client(ledger=True)
    client.versions[upgrade.MIGRATION_NAME] = (
        upgrade.migration_file().sha256 if hash_matches else "0" * 64
    )
    before = dict(client.versions)
    if hash_matches:
        assert upgrade.inspect_source_fields(client) is None
    else:
        with pytest.raises(upgrade.CDCUpgradeError, match="hash drift"):
            upgrade.inspect_source_fields(client)
    assert client.writes == [] and client.versions == before


def test_source_owned_acceptance_does_not_change_legacy_upgrade_contract():
    client = source_owned_client()
    with pytest.raises(upgrade.CDCUpgradeError, match="incompatible"):
        upgrade.inspect_upgrade(client)
    assert client.writes == []


@pytest.mark.parametrize("foreign", [False, True])
def test_unexpected_or_duplicate_column_metadata_fails_closed(foreign):
    client = MemoryClient(complete=True)
    original_query = client.query

    def query(sql, **kwargs):
        result = original_query(sql, **kwargs)
        if "FROM system.columns" in sql:
            extra = ("spans", "tracer_project_id", "Nullable(UUID)", "", "")
            result.result_rows.append(extra if foreign else result.result_rows[0])
        return result

    client.query = query
    with pytest.raises(upgrade.CDCUpgradeError, match="unexpected/duplicate"):
        upgrade.inspect_upgrade(client)
    assert client.writes == []


@pytest.mark.parametrize("complete", [False, True])
def test_existing_apply_helpers_add_or_noop_then_record_once_and_skip_second_apply(
    complete,
):
    client = MemoryClient(complete=complete)
    original = dict(client.columns)
    result = _exercise_caller_seam(client)
    assert result.recorded is True and result.missing_columns == ()
    assert client.columns == {**original, **EXPECTED}
    assert [
        sql for sql in client.writes if isinstance(sql, str) and sql.startswith("ALTER")
    ] == STATEMENTS
    assert len([write for write in client.writes if isinstance(write, tuple)]) == 1
    writes = list(client.writes)
    assert _exercise_caller_seam(client) == result
    assert client.writes == writes


def test_partial_failure_is_unrecorded_and_reapplication_preserves_existing_columns():
    client = MemoryClient()
    client.fail_on = STATEMENTS[1]
    with pytest.raises(RuntimeError, match="injected offline DDL failure"):
        _exercise_caller_seam(client)
    assert client.versions == {}
    assert (
        client.columns[("model_hub_score", "tracer_project_id")]
        == EXPECTED[("model_hub_score", "tracer_project_id")]
    )
    assert len(upgrade.inspect_upgrade(client).missing_columns) == 2
    client.fail_on = None
    assert _exercise_caller_seam(client).missing_columns == ()
    assert client.columns[("model_hub_score", "project_id")] == (
        "Nullable(UUID)",
        "",
        "",
    )


def test_read_error_propagates_without_mutation_or_fallback():
    client = MemoryClient()
    client.query = Mock(side_effect=PermissionError("SELECT denied"))
    with pytest.raises(PermissionError, match="SELECT denied"):
        _exercise_caller_seam(client)
    assert client.writes == []


@pytest.mark.parametrize("present_mask", range(16))
def test_every_partial_eval_upgrade_preserves_existing_fields_and_repeats_without_writes(
    present_mask,
):
    client = MemoryClient(migration_name=EVAL_MIGRATION)
    for bit, (key, shape) in enumerate(EVAL_EXPECTED.items()):
        if present_mask & (1 << bit):
            client.columns[key] = shape
    # An already completed unrelated migration must neither gate nor be replaced.
    client.tables.add("schema_versions")
    client.versions[upgrade.MIGRATION_NAME] = upgrade.migration_file().sha256
    original = dict(client.columns)
    assert _exercise_caller_seam(client).recorded
    assert client.columns == {**original, **EVAL_EXPECTED}
    assert client.versions[upgrade.MIGRATION_NAME] == upgrade.migration_file().sha256
    writes = list(client.writes)
    assert _exercise_caller_seam(client).missing_columns == ()
    assert client.writes == writes


@pytest.mark.parametrize("key", EVAL_EXPECTED)
@pytest.mark.parametrize("field", range(3))
def test_wrong_eval_type_kind_or_expression_is_rejected_before_any_write(key, field):
    client = MemoryClient(complete=True, ledger=True, migration_name=EVAL_MIGRATION)
    shape = list(client.columns[key])
    shape[field] = ("UInt64", "ALIAS", "JSONExtractString(config, 'wrong_scope')")[
        field
    ]
    client.columns[key] = tuple(shape)
    with pytest.raises(upgrade.CDCUpgradeError, match="incompatible usage_apicalllog"):
        _exercise_caller_seam(client)
    assert client.writes == []


@pytest.mark.parametrize(
    "shape",
    [
        None,
        ("Nullable(String)", "", ""),
        ("JSON", "", ""),
        ("String", "ALIAS", "'{}'"),
        ("String", "DEFAULT", "'null'"),
    ],
)
def test_missing_or_incompatible_eval_config_cannot_be_filled_by_migration(shape):
    client = MemoryClient(migration_name=EVAL_MIGRATION)
    key = ("usage_apicalllog", "config")
    if shape is None:
        del client.columns[key]
    else:
        client.columns[key] = shape
    with pytest.raises(upgrade.CDCUpgradeError, match="config prerequisite"):
        _exercise_caller_seam(client)
    assert client.writes == []


@pytest.mark.parametrize("key", EVAL_EXPECTED)
def test_each_eval_add_failure_stops_before_record_and_resumes_without_rewriting_survivors(
    key,
):
    client = MemoryClient(migration_name=EVAL_MIGRATION)
    index = list(EVAL_EXPECTED).index(key)
    client.fail_on = client.statements[index]
    with pytest.raises(RuntimeError, match="injected offline DDL failure"):
        _exercise_caller_seam(client)
    assert client.versions == {}
    assert client.writes[-1] == client.fail_on
    survivors = dict(client.columns)
    assert {k: v for k, v in survivors.items() if k in EVAL_EXPECTED} == dict(
        list(EVAL_EXPECTED.items())[:index]
    )
    client.fail_on = None
    assert _exercise_caller_seam(client).recorded
    assert all(client.columns[k] == v for k, v in survivors.items())


@pytest.mark.parametrize("key", EVAL_EXPECTED)
def test_recorded_eval_upgrade_with_a_missing_field_refuses_instead_of_repairing(key):
    client = MemoryClient(complete=True, ledger=True, migration_name=EVAL_MIGRATION)
    client.versions[EVAL_MIGRATION] = upgrade.migration_file(EVAL_MIGRATION).sha256
    del client.columns[key]
    with pytest.raises(upgrade.CDCUpgradeError, match="incomplete CDC upgrade"):
        _exercise_caller_seam(client)
    assert client.writes == []


def test_eval_migration_hash_drift_is_not_overwritten():
    client = MemoryClient(complete=True, ledger=True, migration_name=EVAL_MIGRATION)
    client.versions[EVAL_MIGRATION] = "0" * 64
    with pytest.raises(upgrade.CDCUpgradeError, match="hash drift"):
        _exercise_caller_seam(client)
    assert client.writes == []
