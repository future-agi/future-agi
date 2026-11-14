"""Offline optional-usage startup regressions; doubles are not live DB proof."""

import os
import socket
import subprocess
import sys
import textwrap
from copy import deepcopy
from dataclasses import MISSING, fields, replace
from types import SimpleNamespace
from unittest.mock import Mock, call

import clickhouse_connect
import psycopg
import pytest

from tfc import ee_loader
from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_install as install
from tracer.services.clickhouse import oss_cdc_setup as setup
from tracer.services.clickhouse import oss_cdc_upgrade as upgrade
from tracer.services.clickhouse.tests.test_oss_cdc_bootstrap import (
    DATABASE,
    DEPENDENT_NAMES,
    MemoryClient,
    source_client,
)
from tracer.services.clickhouse.tests.test_oss_cdc_setup import Harness

# Independent closed inventory: never derive the mandatory oracle from LANDING.
CORE_TABLES = (
    "tracer_trace",
    "tracer_eval_logger",
    "trace_annotation",
    "model_hub_score",
    "trace_session",
    "model_hub_dataset",
    "model_hub_column",
    "model_hub_row",
    "model_hub_cell",
    "simulate_test_execution",
    "simulate_call_execution",
    "simulate_scenarios",
    "simulate_agent_definition",
    "simulate_agent_version",
    "simulate_run_test",
    "model_hub_promptversion",
    "model_hub_prompttemplate",
    "model_hub_promptlabel",
    "tracer_enduser",
)
USAGE = "usage_apicalllog"
EE_TABLES = (*CORE_TABLES[:11], USAGE, *CORE_TABLES[11:])
CDC001 = "cdc_001_catalog_target_fields.sql"
CDC002 = "cdc_002_usage_eval_fields.sql"
ENV = {"PG_DB": "source_db", "CH_DATABASE": "offline_native"}


def selected_tables(include_usage_schema):
    return EE_TABLES if include_usage_schema else CORE_TABLES


class SelectedHarness(Harness):
    """Reuse transport/mirror behavior with strict selection-aware read fixtures.

    The original Harness binds its metadata SELECTs to a literal twenty-table
    inventory. These overrides keep exact parameter/settings assertions for the
    chosen independent oracle; they do not patch the original helper or runtime.
    """

    def __init__(self, include_usage_schema=False):
        super().__init__()
        self.expected_tables = selected_tables(include_usage_schema)
        self.profile = core.SourceInventory(
            self.profile.source,
            {name: self.profile.tables[name] for name in self.expected_tables},
        )

    def pg_read(self, statement, parameters):
        if not any(
            text in statement
            for text in (
                "FROM information_schema.columns",
                "FROM pg_catalog.pg_constraint",
            )
        ):
            return super().pg_read(statement, parameters)
        self.events.append(("pg", statement))
        assert statement.lstrip().startswith("SELECT ")
        assert parameters == {
            "database": "source_db",
            "tables": sorted(self.expected_tables),
        }
        if "FROM pg_catalog.pg_constraint" in statement:
            rows = [
                ("source_db", "public", name, "id", 1, 1, True)
                for name in self.profile.tables
            ]
        else:
            rows = []
            for name, profile in self.profile.tables.items():
                for position, (column, data_type) in enumerate(profile.columns, 1):
                    nullable = data_type.startswith("Nullable(")
                    dtype = data_type[9:-1] if nullable else data_type
                    udt = {
                        "String": "text",
                        "UUID": "uuid",
                        "Int64": "int8",
                        "Int32": "int4",
                        "Bool": "bool",
                        "Decimal(5,2)": "numeric",
                    }[dtype]
                    rows.append(
                        (
                            "source_db",
                            "public",
                            name,
                            column,
                            position,
                            "pg_catalog",
                            udt,
                            "YES" if nullable else "NO",
                            5 if udt == "numeric" else None,
                            2 if udt == "numeric" else None,
                            None,
                            "NEVER",
                            len(profile.columns),
                        )
                    )
        return SimpleNamespace(fetchall=lambda: rows)

    def ch_read(self, statement, parameters=None, settings=None):
        if not parameters or parameters.get("names") != self.expected_tables:
            return super().ch_read(statement, parameters, settings)
        self.events.append(("ch", statement))
        assert statement.lstrip().startswith("SELECT ")
        assert parameters == {
            "database": "offline_native",
            "names": self.expected_tables,
        }
        assert settings == {"readonly": 1, "max_threads": 1, "max_execution_time": 5}
        if "FROM system.tables" in statement:
            rows = [
                (name, *self.source.tables[name])
                for name in self.expected_tables
                if name in self.landing
            ]
        elif "FROM system.columns" in statement:
            rows = [
                (name, column, *shape)
                for name in self.expected_tables
                if name in self.landing
                for column, shape in self.source.columns[name].items()
            ]
        else:
            assert "FROM system.data_skipping_indices" in statement
            rows = []
        return SimpleNamespace(result_rows=rows)

    def retained(self, *, state="STATUS_RUNNING"):
        self.add_peer("POSTGRES")
        self.add_peer("CLICKHOUSE")
        self.add_mirror(self.expected_tables, name="selected_batch", state=state)
        return self


def selected_source(include_usage_schema=False):
    client = source_client()
    assert isinstance(client, MemoryClient)
    if not include_usage_schema:
        client.remove(USAGE)
        client.mappings = tuple(pair for pair in client.mappings if pair[1] != USAGE)
        snapshot = client.inspect_source()
        snapshot.tables.pop(USAGE)
        client.inspect_source = lambda: snapshot
    assert set(client.inspect_source().tables) == set(
        selected_tables(include_usage_schema)
    )
    return client


def inspect(client, include_usage_schema=False, *, require_complete=False):
    return core.inspect_bootstrap(
        client,
        database=DATABASE,
        inspect_mirrors=client.inspect_mirrors,
        inspect_source=client.inspect_source,
        include_usage_schema=include_usage_schema,
        require_complete=require_complete,
    )


def bootstrap(client, include_usage_schema=False):
    return core.bootstrap_cdc(
        client,
        database=DATABASE,
        inspect_mirrors=client.inspect_mirrors,
        inspect_source=client.inspect_source,
        include_usage_schema=include_usage_schema,
        applied_by="offline-test",
    )


@pytest.fixture(autouse=True)
def offline_guards(monkeypatch):
    popen = subprocess.Popen
    forbidden = Mock(side_effect=AssertionError("offline regression attempted I/O"))
    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(clickhouse_connect, "get_client", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(install, "peerdb_transport", forbidden)
    for name in ("PGHOSTADDR", "PGSERVICE", "EE_LICENSE_KEY", "CLOUD_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)
    yield popen
    forbidden.assert_not_called()


def test_oss_public_setup_accepts_all_19_core_source_tables(monkeypatch):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    harness = SelectedHarness()
    assert set(harness.profile.tables) == set(CORE_TABLES)
    config = setup.Config.from_env(ENV)

    result = harness.run(config=config)

    assert result["ready"] is False
    assert result["missing_tables"] == list(CORE_TABLES)
    assert result["applied"] is result["accepted"] is False
    assert result["missing_peers"] == ["pg_source", "ch_dest"]
    assert len(result["missing_mirrors"]) == 1
    assert result["created_peers"] == result["created_mirrors"] == []
    assert harness.writes == []


@pytest.mark.parametrize("packaged", [False, True])
def test_config_detects_package_presence_without_license_or_operator_flag(
    monkeypatch, packaged
):
    has_ee = Mock(return_value=packaged)
    no_license_gate = Mock(side_effect=AssertionError("license gate consulted"))
    monkeypatch.setattr(ee_loader, "has_ee", has_ee)
    monkeypatch.setattr(ee_loader, "ee_feature_enabled", no_license_gate)
    monkeypatch.setattr(ee_loader, "_is_oss_mode", no_license_gate)
    assert setup.Config is install.Config
    config_field = next(
        f for f in fields(install.Config) if f.name == "include_usage_schema"
    )
    assert config_field.default is MISSING
    assert config_field.default_factory is not MISSING
    for env in ({}, {"EE_LICENSE_KEY": ""}, {"EE_LICENSE_KEY": "irrelevant-license"}):
        config = install.Config.from_env(env)
        assert config.include_usage_schema is packaged
        has_ee.assert_called_with("ee.usage")
    no_license_gate.assert_not_called()


def test_config_default_is_resolved_at_construction_and_explicit_bool_wins(monkeypatch):
    has_ee = Mock(side_effect=[False, True])
    monkeypatch.setattr(ee_loader, "has_ee", has_ee)
    first, second = install.Config.from_env({}), install.Config.from_env({})
    assert first.include_usage_schema is False
    assert second.include_usage_schema is True
    assert has_ee.call_args_list == [call("ee.usage"), call("ee.usage")]
    has_ee.reset_mock(side_effect=True)
    has_ee.side_effect = AssertionError("explicit selection must not redetect")
    assert replace(first, include_usage_schema=True).include_usage_schema is True
    assert replace(second, include_usage_schema=False).include_usage_schema is False
    has_ee.assert_not_called()


@pytest.mark.parametrize("value", [None, 0, 1, "false", "true", [], {}])
def test_config_rejects_non_bool_selection(monkeypatch, value):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    with pytest.raises(install.InstallError, match="usage|boolean|bool"):
        replace(install.Config.from_env({}), include_usage_schema=value)


@pytest.mark.parametrize("include_usage_schema", [False, True])
def test_closed_table_and_migration_selectors_and_definitions(include_usage_schema):
    expected = selected_tables(include_usage_schema)
    migrations = (CDC001, CDC002) if include_usage_schema else (CDC001,)
    assert core.landing_tables(include_usage_schema=include_usage_schema) == expected
    assert core.migration_names(include_usage_schema=include_usage_schema) == migrations
    declarations, native = core._definitions(
        DATABASE, include_usage_schema=include_usage_schema
    )
    assert tuple(declarations) == (*expected, *DEPENDENT_NAMES)
    assert native == core._definitions(DATABASE)[1]
    snapshot = selected_source(include_usage_schema).inspect_source()
    assert set(
        core._source_definitions(snapshot, include_usage_schema=include_usage_schema)
    ) == set(expected)
    assert core.landing_tables() == EE_TABLES
    assert core.migration_names() == (CDC001, CDC002)
    assert tuple(core.LANDING) == EE_TABLES


@pytest.mark.parametrize("apply", [False, True])
def test_retained_oss_19_are_ready_without_creates(monkeypatch, apply):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    harness = SelectedHarness().retained()
    before = deepcopy(
        (harness.peers, harness.mirrors, harness.statuses, harness.landing)
    )
    result = harness.run(config=setup.Config.from_env(ENV), apply=apply)
    assert result["ready"] is True
    assert result["missing_tables"] == result["missing_mirrors"] == []
    assert result["created_peers"] == result["created_mirrors"] == []
    assert harness.writes == []
    assert before == (harness.peers, harness.mirrors, harness.statuses, harness.landing)


def test_oss_fresh_apply_creates_one_exact_19_table_snapshot_then_repeat_noop(
    monkeypatch,
):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    harness = SelectedHarness()
    config = setup.Config.from_env(ENV)
    result = harness.run(config=config, apply=True)
    assert result["ready"] is False
    assert result["accepted"] is result["applied"] is True
    assert result["created_peers"] == ["pg_source", "ch_dest"]
    assert len(result["created_mirrors"]) == 1
    assert [path for path, _ in harness.writes] == [
        "/v1/peers/create",
        "/v1/peers/create",
        "/v1/flows/cdc/create",
    ]
    _, payload = harness.writes[-1]
    mapping = payload["connectionConfigs"]
    assert mapping["tableMappings"] == [
        {"sourceTableIdentifier": "public." + name, "destinationTableIdentifier": name}
        for name in CORE_TABLES
    ]
    assert payload["attachToExisting"] is False
    assert mapping["doInitialSnapshot"] is True
    assert mapping["resync"] is mapping["initialSnapshotOnly"] is False
    assert (
        mapping["snapshotMaxParallelWorkers"]
        == mapping["snapshotNumTablesInParallel"]
        == 1
    )
    assert mapping["env"] == {
        "PEERDB_NULLABLE": "true",
        "PEERDB_CLICKHOUSE_INITIAL_LOAD_ALLOW_NON_EMPTY_TABLES": "false",
    }
    first_write = next(
        i for i, event in enumerate(harness.events) if event[0] == "write"
    )
    assert all(
        event[0] == "write" or event[:2] == ("api", "POST")
        for event in harness.events[first_write:]
    )
    writes = deepcopy(harness.writes)
    assert harness.run(config=config, apply=True)["ready"] is True
    assert harness.writes == writes


@pytest.mark.parametrize("state", ["STATUS_SETUP", "STATUS_SNAPSHOT"])
@pytest.mark.parametrize("apply", [False, True])
def test_pending_oss_19_are_accepted_only_for_apply_and_never_write(
    monkeypatch, state, apply
):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    harness = SelectedHarness().retained(state=state)
    harness.landing.clear()
    before = deepcopy((harness.mirrors, harness.statuses))
    result = harness.run(config=setup.Config.from_env(ENV), apply=apply)
    assert result["ready"] is result["applied"] is False
    assert result["accepted"] is apply
    assert result["pending_mirrors"] == ["selected_batch"]
    assert result["created_peers"] == result["created_mirrors"] == []
    assert harness.writes == []
    assert before == (harness.mirrors, harness.statuses)


def test_oss_wait_reinspects_until_19_table_snapshot_is_ready(monkeypatch):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    harness = SelectedHarness().retained(state="STATUS_SNAPSHOT")
    harness.landing.clear()

    def settle(seconds):
        assert 0 < seconds <= 2
        assert harness.writes == []
        harness.mirrors[0]["status"] = "STATUS_RUNNING"
        harness.statuses["selected_batch"]["currentFlowState"] = "STATUS_RUNNING"
        harness.landing.update(CORE_TABLES)

    sleeping = Mock(side_effect=settle)
    monkeypatch.setattr(setup.time, "sleep", sleeping)
    result = harness.run(config=setup.Config.from_env(ENV), wait_for_mirrors=True)
    assert result["ready"] is True
    assert result["pending_mirrors"] == []
    assert result["applied"] is result["accepted"] is False
    sleeping.assert_called_once()
    assert (
        sum(
            call.args[:2] == ("GET", "/v1/peers/list")
            for call in harness.request.call_args_list
        )
        == 2
    )
    assert harness.writes == []


def test_source_owned_oss_bootstrap_needs_no_usage_ddl_or_receipt_and_repeats_noop():
    client = selected_source()
    before = deepcopy(client.columns)
    assert inspect(client) == core.Inspection(tuple(DEPENDENT_NAMES), True, False)
    assert client.writes == []
    assert bootstrap(client) == core.BootstrapResult(tuple(DEPENDENT_NAMES), False)
    assert client.writes == [("CREATE", name) for name in DEPENDENT_NAMES]
    assert USAGE not in client.tables and "schema_versions" not in client.tables
    assert client.versions == {}
    assert all(client.columns[name] == columns for name, columns in before.items())
    assert inspect(client, require_complete=True) == core.Inspection((), True, False)
    writes = list(client.writes)
    assert bootstrap(client) == core.BootstrapResult((), False)
    assert client.writes == writes


@pytest.mark.parametrize("include_usage_schema", [False, True])
@pytest.mark.parametrize("table", CORE_TABLES)
@pytest.mark.parametrize("missing", ["source", "destination", "mapping"])
def test_every_core_table_is_mandatory_before_bootstrap_writes(
    include_usage_schema, table, missing
):
    client = selected_source(include_usage_schema)
    if missing == "source":
        snapshot = client.inspect_source()
        snapshot.tables.pop(table)
        client.inspect_source = lambda: snapshot
    elif missing == "destination":
        client.remove(table)
    else:
        client.mappings = tuple(pair for pair in client.mappings if pair[1] != table)
    with pytest.raises(core.BootstrapError):
        bootstrap(client, include_usage_schema)
    assert client.writes == []


@pytest.mark.parametrize("packaged", [False, True])
@pytest.mark.parametrize("table", CORE_TABLES)
@pytest.mark.parametrize("failure", ["missing", "truncated", "unreadable"])
def test_every_core_source_table_failure_blocks_setup_before_writes(
    monkeypatch, packaged, table, failure
):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=packaged))
    harness = SelectedHarness(packaged)
    if failure == "missing":
        harness.profile.tables.pop(table)
    else:
        read = harness.pg_read

        def damaged(statement, parameters):
            result = read(statement, parameters)
            if "FROM information_schema.columns" not in statement:
                return result
            if failure == "unreadable":
                raise PermissionError("source metadata unavailable")
            rows = result.fetchall()
            last = max(i for i, row in enumerate(rows) if row[2] == table)
            # Keep the original window count so truncation cannot look complete.
            return SimpleNamespace(fetchall=lambda: rows[:last] + rows[last + 1 :])

        harness.pg.execute.side_effect = damaged
    with pytest.raises(setup.SetupError, match="partial state retained"):
        harness.run(config=setup.Config.from_env(ENV), apply=True)
    assert harness.writes == []
    harness.ch_connect.assert_not_called()
    harness.request.assert_not_called()


def test_packaged_ee_without_license_still_requires_usage_before_setup_create(
    monkeypatch,
):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=True))
    harness = SelectedHarness(True)
    harness.profile.tables.pop(USAGE)
    config = setup.Config.from_env(ENV)
    assert config.include_usage_schema is True
    with pytest.raises(setup.SetupError):
        harness.run(config=config, apply=True)
    assert harness.writes == []
    harness.ch_connect.assert_not_called()


@pytest.mark.parametrize("missing", ["source", "destination", "mapping"])
def test_ee_expected_usage_is_mandatory_before_bootstrap_create(missing):
    client = selected_source(True)
    if missing == "source":
        snapshot = client.inspect_source()
        snapshot.tables.pop(USAGE)
        client.inspect_source = lambda: snapshot
    elif missing == "destination":
        client.remove(USAGE)
    else:
        client.mappings = tuple(pair for pair in client.mappings if pair[1] != USAGE)
    with pytest.raises(core.BootstrapError):
        bootstrap(client, True)
    assert client.writes == []


@pytest.mark.parametrize("state", ["STATUS_RUNNING", "STATUS_SNAPSHOT"])
@pytest.mark.parametrize("unexpected", [USAGE, "unlisted_table"])
def test_retained_unselected_mapping_is_rejected_by_setup(
    monkeypatch, state, unexpected
):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    harness = SelectedHarness().retained(state=state)
    harness.add_mirror(unexpected, state=state)
    before = deepcopy((harness.mirrors, harness.statuses, harness.landing))
    with pytest.raises(setup.SetupError, match="unknown|mapping"):
        harness.run(config=setup.Config.from_env(ENV), apply=True)
    assert harness.writes == []
    assert before == (harness.mirrors, harness.statuses, harness.landing)


@pytest.mark.parametrize("unexpected", [USAGE, "unlisted_table"])
def test_retained_unselected_mapping_is_rejected_by_bootstrap(unexpected):
    client = selected_source()
    client.mappings += (("retained_unexpected", unexpected),)
    with pytest.raises(core.BootstrapError, match="unknown|mapping"):
        bootstrap(client)
    assert client.writes == []


def test_setup_create_allowlist_cannot_add_excluded_usage(monkeypatch):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    with pytest.raises(setup.SetupError, match="CREATE plan"):
        setup._creates(setup.Config.from_env(ENV), [], [USAGE])


@pytest.mark.parametrize(
    "table,column",
    [
        ("model_hub_score", "tracer_project_id"),
        ("model_hub_score", "value_history"),
        ("simulate_agent_definition", "target_speaks_first"),
    ],
)
@pytest.mark.parametrize("failure", ["missing", "wrong_type"])
def test_oss_keeps_strict_cdc001_source_field_checks(table, column, failure):
    client = selected_source()
    if failure == "missing":
        client.columns[table].pop(column)
    else:
        client.columns[table][column] = ("Int64", "", "")
    with pytest.raises(upgrade.CDCUpgradeError):
        bootstrap(client)
    assert client.writes == []


@pytest.mark.parametrize("table", CORE_TABLES)
def test_oss_retained_physical_shape_conflict_blocks_setup_and_preserves_state(
    monkeypatch, table
):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    harness = SelectedHarness().retained()
    harness.source.columns[table]["id"] = ("String", "", "")
    before = deepcopy((harness.mirrors, harness.statuses, harness.source.columns))
    with pytest.raises(setup.SetupError):
        harness.run(config=setup.Config.from_env(ENV), apply=True)
    assert harness.writes == []
    assert before == (harness.mirrors, harness.statuses, harness.source.columns)


@pytest.mark.parametrize("include_usage_schema", [False, True])
def test_installer_propagates_resolved_selection_and_enforces_usage_writes(
    monkeypatch, include_usage_schema
):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=include_usage_schema))
    config = install.Config.from_env({})
    pg, raw = Mock(), Mock()
    pg.execute.return_value.fetchall.return_value = [(True,)]
    metadata, inventory = Mock(return_value="source"), Mock(return_value="mirrors")
    monkeypatch.setattr(install, "inspect_source", metadata)
    monkeypatch.setattr(core.MirrorInventory, "from_peerdb", inventory)
    migration = upgrade.migration_file(CDC002)
    statements = install.apply_schema.split_statements(migration.path.read_text())
    assert len(statements) == 4
    receipt = [
        migration.path.name,
        migration.sha256,
        "oss-cdc-bootstrap",
        "schema-topology/v1/local",
    ]

    def check(client, **kwargs):
        assert kwargs["include_usage_schema"] is include_usage_schema
        assert kwargs["inspect_source"]() == "source"
        assert kwargs["inspect_mirrors"]() == "mirrors"
        assert metadata.call_args.kwargs == {
            "source": config.source,
            "tables": selected_tables(include_usage_schema),
        }
        assert inventory.call_args.kwargs == {
            "source": config.source,
            "destination": config.destination,
        }
        return core.Inspection((), True, False)

    def apply(client, **kwargs):
        check(client, **kwargs)
        # A permitted dependent CREATE still works with usage excluded.
        declarations, _ = core._definitions(config.destination.database)
        client.command(declarations["prompt_dict"])
        if include_usage_schema:
            client.command(core._LEDGER_DDL)
        else:
            with pytest.raises(install.InstallError, match="packaged DDL"):
                client.command(core._LEDGER_DDL)
        for statement in statements:
            if include_usage_schema:
                client.command(statement)
            else:
                with pytest.raises(install.InstallError, match="packaged DDL"):
                    client.command(statement)
        if include_usage_schema:
            client.insert(
                "schema_versions",
                [receipt],
                ["filename", "sha256", "applied_by", "notes"],
            )
        else:
            for name in (CDC001, CDC002):
                excluded = upgrade.migration_file(name)
                row = [
                    name,
                    excluded.sha256,
                    "oss-cdc-bootstrap",
                    "schema-topology/v1/local",
                ]
                with pytest.raises(
                    install.InstallError, match="verified derived migration"
                ):
                    client.insert(
                        "schema_versions",
                        [row],
                        ["filename", "sha256", "applied_by", "notes"],
                    )
        for sql in (
            "DROP TABLE spans",
            "TRUNCATE TABLE tracer_trace",
            "ALTER TABLE model_hub_cell DELETE WHERE 1",
        ):
            with pytest.raises(install.InstallError, match="packaged DDL"):
                client.command(sql)
        return core.BootstrapResult((), include_usage_schema)

    inspecting, applying = Mock(side_effect=check), Mock(side_effect=apply)
    monkeypatch.setattr(core, "inspect_bootstrap", inspecting)
    monkeypatch.setattr(core, "bootstrap_cdc", applying)
    for should_apply in (False, True):
        result = install.run(
            config,
            apply=should_apply,
            pg_connect=Mock(return_value=pg),
            ch_connect=Mock(return_value=raw),
            request=Mock(),
        )
        assert result["ready"] is True
        if not should_apply:
            assert result["derived_upgrade_required"] is False
            raw.command.assert_not_called()
            raw.insert.assert_not_called()
        else:
            assert result["derived_upgrade_applied"] is include_usage_schema
    assert inspecting.call_count == 2
    applying.assert_called_once()
    assert raw.command.call_count == (6 if include_usage_schema else 1)
    assert raw.insert.call_count == int(include_usage_schema)


def test_public_oss_installer_real_core_apply_readiness_and_repeat(monkeypatch):
    """Exercise install.run and core together; only transports/source rows are doubles."""
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=False))
    config = install.Config.from_env({"PG_DB": "source_db", "CH_DATABASE": DATABASE})
    client = selected_source()
    source = core.SourceInventory(config.source, client.inspect_source().tables)

    def source_metadata(query, *, source: core.PeerTarget, tables):
        assert callable(query)
        assert source == config.source
        assert tables == CORE_TABLES
        return snapshot

    snapshot = source
    monkeypatch.setattr(install, "inspect_source", source_metadata)
    inventory = Mock(
        return_value=core.MirrorInventory(DATABASE, client.mappings, config.source)
    )
    monkeypatch.setattr(core.MirrorInventory, "from_peerdb", inventory)

    def query(statement, parameters=None, settings=None):
        # The installer strengthens the raw transport settings. MemoryClient's
        # core-facing protocol expects the original per-query settings.
        assert settings["readonly"] == settings["max_threads"] == 1
        assert 1 <= settings["max_execution_time"] <= 5
        assert set(settings) == {"readonly", "max_threads", "max_execution_time"}
        core_settings = (
            settings
            if statement.startswith(
                ("SELECT * FROM (", "SELECT formatQuerySingleLine(")
            )
            else {"readonly": 1}
        )
        return client.query(statement, parameters, core_settings)

    raw = SimpleNamespace(
        query=Mock(side_effect=query),
        command=Mock(side_effect=client.command),
        insert=Mock(
            side_effect=AssertionError("OSS must not emit a migration receipt")
        ),
        close=Mock(),
    )
    pg = Mock()
    pg.execute.return_value.fetchall.return_value = [(True,)]

    def run(*, apply=False):
        return install.run(
            config,
            apply=apply,
            pg_connect=Mock(return_value=pg),
            ch_connect=Mock(return_value=raw),
            request=Mock(),
        )

    assert run() == {
        "ready": False,
        "applied": False,
        "missing_objects": DEPENDENT_NAMES,
        "derived_upgrade_required": False,
    }
    assert client.writes == []
    assert run(apply=True) == {
        "ready": True,
        "applied": True,
        "created_objects": DEPENDENT_NAMES,
        "derived_upgrade_applied": False,
    }
    assert client.writes == [("CREATE", name) for name in DEPENDENT_NAMES]
    writes = list(client.writes)
    assert run() == {
        "ready": True,
        "applied": False,
        "missing_objects": [],
        "derived_upgrade_required": False,
    }
    assert run(apply=True) == {
        "ready": True,
        "applied": True,
        "created_objects": [],
        "derived_upgrade_applied": False,
    }
    assert client.writes == writes
    assert USAGE not in client.tables and "schema_versions" not in client.tables
    raw.insert.assert_not_called()
    assert raw.close.call_count == pg.close.call_count == 4


def test_canonical_oss_bootstrap_selects_only_cdc001_and_repeat_is_noop():
    client = MemoryClient()
    result = core.bootstrap_cdc(
        client,
        database=DATABASE,
        inspect_mirrors=client.inspect_mirrors,
        applied_by="offline-test",
        include_usage_schema=False,
    )
    assert result == core.BootstrapResult((*CORE_TABLES, *DEPENDENT_NAMES), True)
    assert client.writes == [
        *[("CREATE", name) for name in CORE_TABLES],
        ("CREATE", "schema_versions"),
        ("ADD", "model_hub_score", "tracer_project_id"),
        ("ADD", "model_hub_score", "value_history"),
        ("ADD", "simulate_agent_definition", "target_speaks_first"),
        ("RECORD", CDC001),
        *[("CREATE", name) for name in DEPENDENT_NAMES],
    ]
    assert set(client.versions) == {CDC001}
    assert USAGE not in client.tables
    writes = list(client.writes)
    assert core.inspect_bootstrap(
        client,
        database=DATABASE,
        inspect_mirrors=client.inspect_mirrors,
        include_usage_schema=False,
        require_complete=True,
    ) == core.Inspection((), True, True)
    assert core.bootstrap_cdc(
        client,
        database=DATABASE,
        inspect_mirrors=client.inspect_mirrors,
        applied_by="offline-test",
        include_usage_schema=False,
    ) == core.BootstrapResult((), False)
    assert client.writes == writes


@pytest.mark.parametrize("include_usage_schema", [False, True])
@pytest.mark.parametrize("which", ["source", "destination"])
def test_core_inspection_failure_never_falls_back_or_writes(
    include_usage_schema, which
):
    client = selected_source(include_usage_schema)
    failure = Mock(side_effect=PermissionError("metadata unavailable"))
    if which == "source":
        client.inspect_source = failure
    else:
        client.query = failure
    with pytest.raises(PermissionError, match="metadata unavailable"):
        bootstrap(client, include_usage_schema)
    failure.assert_called_once()
    assert client.writes == []


def test_ee_packaged_without_license_setup_still_creates_all_20(monkeypatch):
    monkeypatch.setattr(ee_loader, "has_ee", Mock(return_value=True))
    harness = SelectedHarness(True)
    config = setup.Config.from_env(ENV)
    assert config.include_usage_schema is True
    result = harness.run(config=config, apply=True)
    assert result["accepted"] is True and result["ready"] is False
    assert len(harness.writes) == 3
    assert harness.writes[-1][1]["connectionConfigs"]["tableMappings"] == [
        {"sourceTableIdentifier": "public." + name, "destinationTableIdentifier": name}
        for name in EE_TABLES
    ]


def test_install_import_and_config_work_when_all_ee_imports_are_denied(
    monkeypatch, offline_guards
):
    """Fresh interpreter, inherited OS network deny, and its own I/O guards."""
    script = textwrap.dedent("""
        import importlib.abc
        import os
        import socket
        import subprocess
        import sys
        from unittest.mock import Mock

        import clickhouse_connect
        import psycopg

        blocked_io = Mock(side_effect=AssertionError("child attempted external I/O"))
        for name in ("socket", "create_connection", "getaddrinfo"):
            setattr(socket, name, blocked_io)
        psycopg.connect = clickhouse_connect.get_client = blocked_io
        subprocess.Popen = blocked_io

        denied = []
        class NoEE(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "ee" or fullname.startswith("ee."):
                    denied.append(fullname)
                    raise ModuleNotFoundError("EE deliberately unavailable", name=fullname)

        for name in tuple(sys.modules):
            if name == "ee" or name.startswith("ee."):
                del sys.modules[name]
        sys.meta_path.insert(0, NoEE())

        from tracer.services.clickhouse import oss_cdc_install as install
        from tracer.services.clickhouse import oss_cdc_setup as setup
        from tfc import ee_loader

        def no_license_gate(*args, **kwargs):
            raise AssertionError("license gate must not select usage")
        ee_loader.ee_feature_enabled = no_license_gate
        ee_loader._is_oss_mode = no_license_gate
        assert setup.Config is install.Config
        for env in ({}, {"EE_LICENSE_KEY": "present"},
                    {"CLOUD_DEPLOYMENT": "US"},
                    {"EE_LICENSE_KEY": "present", "CLOUD_DEPLOYMENT": "US"}):
            for key in ("EE_LICENSE_KEY", "CLOUD_DEPLOYMENT"):
                os.environ.pop(key, None)
            os.environ.update(env)
            assert install.Config.from_env(env).include_usage_schema is False
        assert denied
        assert not any(n == "ee" or n.startswith("ee.") for n in sys.modules)
        assert "django.apps.registry" not in sys.modules
        blocked_io.assert_not_called()
        print("EE imports denied; four Config variants false; no Django app registry or I/O")
    """)
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    # The child is launched with ``-c``, so Python does not automatically add
    # the repository's ``futureagi`` package root to sys.path. Keep the
    # import-graph check independent of the caller's working directory.
    package_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (package_root, env.get("PYTHONPATH", "")) if value
    )
    # Only this exact local Python child is allowed through the parent guard.
    # It inherits sandbox-exec's deny network profile; it cannot start children.
    with monkeypatch.context() as child:
        child.setattr(subprocess, "Popen", offline_guards)
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        result.stdout.strip()
        == "EE imports denied; four Config variants false; no Django app registry or I/O"
    )
    assert result.stderr == ""
