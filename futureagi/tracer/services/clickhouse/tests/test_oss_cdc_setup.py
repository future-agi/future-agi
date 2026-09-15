"""Offline setup contracts; transport doubles do not qualify live snapshots."""

import io
import json
import re
import socket
import traceback
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import clickhouse_connect
import psycopg
import pytest

from tracer.services.clickhouse import oss_cdc_setup as setup
from tracer.services.clickhouse.tests.test_oss_cdc_bootstrap import source_client
from tracer.services.clickhouse.tests.test_oss_native_bootstrap import (
    MemoryClient as NativeClient,
)

TABLES = (
    "tracer_trace tracer_eval_logger trace_annotation model_hub_score trace_session "
    "model_hub_dataset model_hub_column model_hub_row model_hub_cell "
    "simulate_test_execution simulate_call_execution usage_apicalllog "
    "simulate_scenarios simulate_agent_definition simulate_agent_version simulate_run_test "
    "model_hub_promptversion model_hub_prompttemplate model_hub_promptlabel tracer_enduser"
).split()
CONFIG = setup.Config.from_env({"PG_DB": "source_db", "CH_DATABASE": "offline_native"})
MIRROR = "futureagi_cdc_c7f7453b73702158"


@pytest.fixture(autouse=True)
def denyconnections(monkeypatch):
    guard = Mock(side_effect=AssertionError("offline test attempted a connection"))
    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, guard)
    monkeypatch.setattr(psycopg, "connect", guard)  # libpq bypasses Python sockets
    monkeypatch.setattr(clickhouse_connect, "get_client", guard)
    for name in ("PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)
    yield
    guard.assert_not_called()


class Harness:
    def __init__(self):
        self.source = source_client()
        self.profile = self.source.inspect_source()
        self.native = NativeClient(complete=True)
        self.peers, self.mirrors, self.statuses = {}, [], {}
        self.landing = set()
        self.events, self.writes = [], []
        self.fail_write = None
        self.retain_failed_write = True
        self.fail_read = None
        self.pg = SimpleNamespace(execute=Mock(side_effect=self.pg_read), close=Mock())
        self.raw = SimpleNamespace(query=Mock(side_effect=self.ch_read), close=Mock())
        self.writer = SimpleNamespace(
            execute=Mock(side_effect=self.create), close=Mock()
        )
        self.pg_connect = Mock(return_value=self.pg)
        self.ch_connect = Mock(return_value=self.raw)
        self.request = Mock(side_effect=self.api)

    def add_peer(self, kind):
        target = CONFIG.source if kind == "POSTGRES" else CONFIG.destination
        user = CONFIG.pg_user if kind == "POSTGRES" else CONFIG.ch_user
        self.peers[target.name] = {
            "name": target.name,
            "type": kind,
            "postgresConfig" if kind == "POSTGRES" else "clickhouseConfig": {
                "host": target.host,
                "port": target.port,
                "database": target.database,
                "user": user,
                "password": "secret-do-not-log",
            },
        }

    def add_mirror(self, tables, *, name=None, state="STATUS_RUNNING"):
        if isinstance(tables, str):
            tables = [tables]
        name = name or "mirror_" + tables[0]
        self.mirrors.append(
            {
                "name": name,
                "sourceName": "pg_source",
                "destinationName": "ch_dest",
                "sourceType": "POSTGRES",
                "destinationType": "CLICKHOUSE",
                "isCdc": True,
                "status": state,
            }
        )
        self.statuses[name] = {
            "flowJobName": name,
            "currentFlowState": state,
            "cdcStatus": {
                "config": {
                    "flowJobName": name,
                    "sourceName": "pg_source",
                    "destinationName": "ch_dest",
                    "softDeleteColName": "_peerdb_is_deleted",
                    "syncedAtColName": "_peerdb_synced_at",
                    "resync": False,
                    "initialSnapshotOnly": False,
                    "doInitialSnapshot": False,
                    "script": "",
                    "system": "Q",
                    "env": {},
                    "flags": [],
                    "tableMappings": [
                        {
                            "sourceTableIdentifier": "public." + table,
                            "destinationTableIdentifier": table,
                            "exclude": [],
                            "columns": [],
                            "partitionKey": "",
                            "shardingKey": "",
                            "policyName": "",
                            "partitionByExpr": "",
                            "engine": "CH_ENGINE_REPLACING_MERGE_TREE",
                        }
                        for table in tables
                    ],
                }
            },
        }
        self.landing.update(tables)

    def retained(self):
        self.add_peer("POSTGRES")
        self.add_peer("CLICKHOUSE")
        for table in TABLES:
            self.add_mirror(table)
        return self

    def pg_read(self, statement, parameters):
        self.events.append(("pg", statement))
        assert statement.lstrip().startswith("SELECT ")
        if "pg_try_advisory_xact_lock" in statement:
            rows = [(True,)]
        elif "current_database()" in statement and "FROM" not in statement:
            rows = [("source_db",)]
        elif "FROM information_schema.columns" in statement:
            assert parameters == {"database": "source_db", "tables": sorted(TABLES)}
            rows = []
            for table, profile in self.profile.tables.items():
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
                            table,
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
        else:
            assert "FROM pg_catalog.pg_constraint" in statement
            rows = [
                ("source_db", "public", t, "id", 1, 1, True)
                for t in self.profile.tables
            ]
        return SimpleNamespace(fetchall=lambda: rows)

    def ch_read(self, statement, parameters=None, settings=None):
        self.events.append(("ch", statement))
        assert statement.lstrip().startswith("SELECT ")
        assert settings == {"readonly": 1, "max_threads": 1, "max_execution_time": 5}
        if parameters and parameters.get("names") == tuple(TABLES):
            assert parameters["database"] == "offline_native"
            if "FROM system.tables" in statement:
                rows = [
                    (t, *self.source.tables[t]) for t in TABLES if t in self.landing
                ]
            elif "FROM system.columns" in statement:
                rows = [
                    (t, c, *s)
                    for t in TABLES
                    if t in self.landing
                    for c, s in self.source.columns[t].items()
                ]
            else:
                assert "FROM system.data_skipping_indices" in statement
                rows = []
            return SimpleNamespace(result_rows=rows)
        return self.native.query(statement, parameters, settings)

    def api(self, method, path, body=None):
        self.events.append(("api", method, path))
        if self.fail_read == path:
            raise OSError("secret-do-not-log")
        if (method, path) == ("GET", "/v1/peers/list"):
            # Actual v0.36.9 envelope verified independently by main.
            return {
                "items": [
                    {"name": n, "type": p["type"]} for n, p in self.peers.items()
                ],
                "sourceItems": [],
                "destinationItems": [],
            }
        if (method, path) == ("GET", "/v1/mirrors/list"):
            return {"mirrors": self.mirrors}
        if method == "GET" and path.startswith("/v1/peers/info/"):
            return {"peer": self.peers[path.rsplit("/", 1)[1]]}
        if method == "POST" and path in ("/v1/peers/create", "/v1/flows/cdc/create"):
            return self.create(path, body)
        assert (method, path) == ("POST", "/v1/mirrors/status")
        assert body == {
            "flowJobName": body["flowJobName"],
            "includeFlowInfo": True,
            "excludeBatches": True,
        }
        return self.statuses[body["flowJobName"]]

    def create(self, path, payload):
        self.events.append(("write", path))
        self.writes.append(deepcopy((path, payload)))
        failed = self.fail_write == len(self.writes)
        if not failed or self.retain_failed_write:
            if path == "/v1/peers/create":
                assert payload["allowUpdate"] is payload["disableValidation"] is False
                peer = payload["peer"]
                self.peers[peer["name"]] = deepcopy(peer)
            else:
                assert path == "/v1/flows/cdc/create"
                assert payload["attachToExisting"] is False
                config = payload["connectionConfigs"]
                tables = [
                    mapping["destinationTableIdentifier"]
                    for mapping in config["tableMappings"]
                ]
                self.add_mirror(tables, name=config["flowJobName"])
                self.statuses[config["flowJobName"]]["cdcStatus"]["config"].update(
                    {
                        key: deepcopy(value)
                        for key, value in config.items()
                        if key != "tableMappings"
                    }
                )
        if failed:
            raise OSError("secret-do-not-log " + json.dumps(payload))
        return (
            {"status": "CREATED"}
            if path == "/v1/peers/create"
            else {
                "workflowId": payload["connectionConfigs"]["flowJobName"] + "-peerflow"
            }
        )

    def run(self, **kwargs):
        return setup.run(
            kwargs.pop("config", CONFIG),
            pg_connect=self.pg_connect,
            ch_connect=self.ch_connect,
            request=self.request,
            **kwargs,
        )


@pytest.fixture
def harness():
    return Harness()


def test_default_fresh_check_never_opens_writer(harness):
    result = harness.run()
    assert result["ready"] is False and result["applied"] is False
    assert result["missing_peers"] == ["pg_source", "ch_dest"]
    assert result["missing_mirrors"] == [MIRROR]
    assert result["missing_tables"] == TABLES
    assert result["created_peers"] == result["created_mirrors"] == []
    assert harness.writes == []
    assert harness.pg.read_only is True
    assert harness.pg.isolation_level is psycopg.IsolationLevel.REPEATABLE_READ
    harness.pg.close.assert_called_once()
    harness.raw.close.assert_called_once()
    assert all("pg_try_advisory" not in e[1] for e in harness.events if e[0] == "pg")


@pytest.mark.parametrize("legacy_drop_flag", [None, "false", "true"])
def test_fresh_apply_all_preflight_then_one_twenty_table_snapshot(
    harness, monkeypatch, legacy_drop_flag
):
    if legacy_drop_flag is None:
        monkeypatch.delenv("CH25_DROP_LEGACY_CDC_CHAIN", raising=False)
    else:
        monkeypatch.setenv("CH25_DROP_LEGACY_CDC_CHAIN", legacy_drop_flag)
    monkeypatch.setenv("PEERDB_NULLABLE", "false")  # creation pins mirror semantics
    result = harness.run(apply=True)
    assert result["accepted"] is True and result["ready"] is False
    assert result["created_peers"] == ["pg_source", "ch_dest"]
    assert result["created_mirrors"] == [MIRROR]
    assert len(harness.writes) == 3
    first = next(i for i, event in enumerate(harness.events) if event[0] == "write")
    assert all(
        e[0] == "write" or (e[0] == "api" and e[1] == "POST")
        for e in harness.events[first:]
    )
    assert harness.writes[:2] == [
        (
            "/v1/peers/create",
            {
                "peer": {
                    "name": "pg_source",
                    "type": "POSTGRES",
                    "postgresConfig": {
                        "host": "postgres",
                        "port": 5432,
                        "database": "source_db",
                        "user": "futureagi",
                        "password": "futureagi",
                    },
                },
                "allowUpdate": False,
                "disableValidation": False,
            },
        ),
        (
            "/v1/peers/create",
            {
                "peer": {
                    "name": "ch_dest",
                    "type": "CLICKHOUSE",
                    "clickhouseConfig": {
                        "host": "clickhouse",
                        "port": 9000,
                        "database": "offline_native",
                        "user": "default",
                        "password": "",
                        "disableTls": True,
                    },
                },
                "allowUpdate": False,
                "disableValidation": False,
            },
        ),
    ]
    assert harness.writes[-1] == (
        "/v1/flows/cdc/create",
        {
            "connectionConfigs": {
                "flowJobName": MIRROR,
                "sourceName": "pg_source",
                "destinationName": "ch_dest",
                "tableMappings": [
                    {
                        "sourceTableIdentifier": "public." + t,
                        "destinationTableIdentifier": t,
                    }
                    for t in TABLES
                ],
                "doInitialSnapshot": True,
                "resync": False,
                "initialSnapshotOnly": False,
                "snapshotMaxParallelWorkers": 1,
                "snapshotNumTablesInParallel": 1,
                "idleTimeoutSeconds": "10",
                "softDeleteColName": "_peerdb_is_deleted",
                "syncedAtColName": "_peerdb_synced_at",
                "system": "Q",
                "env": {
                    "PEERDB_NULLABLE": "true",
                    "PEERDB_CLICKHOUSE_INITIAL_LOAD_ALLOW_NON_EMPTY_TABLES": "false",
                },
            },
            "attachToExisting": False,
        },
    )


@pytest.mark.parametrize("side", ["source", "destination"])
@pytest.mark.parametrize(
    "name",
    [
        "PG_SOURCE",
        "pg_source; DROP PEER ch_dest",
        '"pg_source"',
        "pg.source",
        "pg_source -- comment",
        "pg_source\n",
        "pg-source",
        "pg_é",
        "x" * 64,
        "pg_source\x00",
    ],
)
def test_create_plan_rejects_unsafe_peer_names(side, name):
    config = replace(CONFIG, **{side: replace(getattr(CONFIG, side), name=name)})
    with pytest.raises(setup.SetupError):
        setup._creates(config, [config.source.name, config.destination.name], TABLES)


@pytest.mark.parametrize(
    "peers,tables",
    [
        (["pg_source", "unrelated"], TABLES),
        (["pg_source", "pg_source"], TABLES),
        (["pg_source; DROP PEER ch_dest"], TABLES),
        ([None], TABLES),
        (["pg_source"], ["tracer_trace", "unlisted"]),
        (["pg_source"], ["tracer_trace", "public.tracer_enduser"]),
        (["pg_source"], ["tracer_trace", "tracer_enduser; DROP PEER pg_source"]),
        (["pg_source"], ["tracer_trace", '"tracer_enduser"']),
        (["pg_source"], ["tracer_trace", "tracer_trace"]),
        (["pg_source"], ["tracer_trace", None]),
        (["pg_source"], ["tracer_trace", []]),
        ("pg_source", TABLES),
        ([], "tracer_trace"),
        (None, TABLES),
        ([], None),
    ],
)
def test_create_plan_rejects_unknown_duplicate_or_injected_identifiers(peers, tables):
    with pytest.raises(setup.SetupError):
        setup._creates(CONFIG, peers, tables)


@pytest.mark.parametrize(
    "name", ["bad; DROP PEER pg_source", '"quoted"', "UPPER", "", None]
)
def test_create_plan_checks_generated_name(monkeypatch, name):
    monkeypatch.setattr(setup, "_mirror_name", Mock(return_value=name))
    with pytest.raises(setup.SetupError):
        setup._creates(CONFIG, ["pg_source", "ch_dest"], TABLES)


def test_create_plan_rejects_shared_peer_name():
    config = replace(
        CONFIG, destination=replace(CONFIG.destination, name=CONFIG.source.name)
    )
    with pytest.raises(setup.SetupError):
        setup._creates(config, [], TABLES)


@pytest.mark.parametrize(
    "failure,kind,name",
    [
        (1, "PEER", "pg_source"),
        (2, "PEER", "ch_dest"),
        (3, "MIRROR", MIRROR),
    ],
)
def test_create_failure_reports_only_validated_identity(harness, failure, kind, name):
    harness.fail_write = failure
    with pytest.raises(setup.SetupError) as exc:
        harness.run(apply=True)
    assert f"CREATE {kind} {name} failed (transport error)" in str(exc.value)
    assert "no automatic retry" in str(exc.value)
    assert "secret-do-not-log" not in "".join(traceback.format_exception(exc.value))
    assert "password" not in str(exc.value) and "connectionConfigs" not in str(
        exc.value
    )
    assert exc.value.__suppress_context__
    assert len(harness.writes) == failure


@pytest.mark.parametrize("apply", [False, True])
def test_retained_running_twenty_exactly_zero_creates(harness, apply):
    harness.retained()
    before = deepcopy(
        (harness.peers, harness.mirrors, harness.statuses, harness.source.columns)
    )
    result = harness.run(apply=apply)
    assert result["ready"] is True
    assert result["created_peers"] == result["created_mirrors"] == []
    assert harness.writes == []
    assert before == (
        harness.peers,
        harness.mirrors,
        harness.statuses,
        harness.source.columns,
    )


@pytest.mark.parametrize("table", TABLES)
def test_every_source_table_is_required_before_first_create(harness, table):
    del harness.profile.tables[table]
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


def test_source_missing_application_field_is_not_hidden_by_new_mirror(harness):
    profile = harness.profile.tables["tracer_trace"]
    harness.profile.tables["tracer_trace"] = replace(
        profile, columns=tuple(c for c in profile.columns if c[0] != "name")
    )
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


def test_native_prerequisites_block_all_creation(harness):
    harness.native.remove("spans_hourly_rollup_mv")
    result = harness.run(apply=True)
    assert not result["ready"] and not result["accepted"]
    assert result["missing_native"] == ["spans_hourly_rollup_mv"]
    assert harness.writes == []


def test_native_dictionary_source_mismatch_blocks_creation(harness):
    row = harness.native.tables["trace_dict"]
    harness.native.tables["trace_dict"] = (
        *row[:-1],
        row[-1].replace("TABLE 'traces'", "TABLE 'foreign'"),
    )
    assert harness.native.tables["trace_dict"] != row
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


@pytest.mark.parametrize("table", TABLES)
def test_orphan_landing_table_never_authorizes_snapshot_even_if_empty(harness, table):
    harness.landing.add(table)
    with pytest.raises(setup.SetupError, match="no mirror"):
        harness.run(apply=True)
    assert harness.writes == []


@pytest.mark.parametrize("side", ["source", "destination"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "foreign"),
        ("database", "foreign"),
        ("port", 9123),
        ("port", True),
        ("user", "foreign"),
        ("sshConfig", {"host": "tunnel"}),
    ],
)
def test_existing_peer_must_match_exact_endpoint_user_and_no_ssh(
    harness, side, field, value
):
    kind = "POSTGRES" if side == "source" else "CLICKHOUSE"
    harness.add_peer(kind)
    target = CONFIG.source if side == "source" else CONFIG.destination
    config = harness.peers[target.name][
        "postgresConfig" if side == "source" else "clickhouseConfig"
    ]
    config[field] = value
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


@pytest.mark.parametrize("field,value", [("user", "foreign"), ("database", "foreign")])
def test_foreign_source_alias_cannot_hide_writer(harness, field, value):
    harness.retained()
    alias = deepcopy(harness.peers["pg_source"])
    alias.update(name="source_alias")
    alias["postgresConfig"][field] = value
    harness.peers["source_alias"] = alias
    harness.mirrors[-1]["sourceName"] = "source_alias"
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


def test_compatible_alias_keeps_existing_mirror_configuration(harness):
    harness.retained()
    for old, alias in [("pg_source", "source_alias"), ("ch_dest", "destination_alias")]:
        harness.peers[alias] = deepcopy(harness.peers[old])
        harness.peers[alias]["name"] = alias
    for mirror in harness.mirrors:
        mirror.update(sourceName="source_alias", destinationName="destination_alias")
        harness.statuses[mirror["name"]]["cdcStatus"]["config"].update(
            sourceName="source_alias", destinationName="destination_alias"
        )
    assert harness.run(apply=True)["ready"]
    assert harness.writes == []


def test_alias_with_wrong_destination_user_is_rejected(harness):
    harness.retained()
    alias = deepcopy(harness.peers["ch_dest"])
    alias.update(name="alias")
    alias["clickhouseConfig"]["user"] = "foreign"
    harness.peers["alias"] = alias
    harness.mirrors[-1]["destinationName"] = "alias"
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("resync", True),
        ("resync", 0),
        ("script", "transform"),
        ("softDeleteColName", "wrong"),
        ("initialSnapshotOnly", True),
        ("tableMappings", []),
        ("env", {"PEERDB_CLICKHOUSE_INITIAL_LOAD_ALLOW_NON_EMPTY_TABLES": "true"}),
    ],
)
@pytest.mark.parametrize("state", ["STATUS_RUNNING", "STATUS_SNAPSHOT"])
def test_last_mirror_bad_config_blocks_all_new_creates(harness, key, value, state):
    harness.retained()
    harness.mirrors.pop(0)
    harness.landing.remove(TABLES[0])
    harness.mirrors[-1]["status"] = state
    harness.statuses["mirror_" + TABLES[-1]]["cdcStatus"]["config"][key] = value
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


@pytest.mark.parametrize(
    "state", ["STATUS_PAUSED", "STATUS_FAILED", "STATUS_RESYNC", "unknown", None]
)
def test_unhealthy_status_is_not_treated_as_missing(harness, state):
    harness.retained()
    harness.mirrors[-1]["status"] = state
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


@pytest.mark.parametrize("state", ["STATUS_SETUP", "STATUS_SNAPSHOT"])
@pytest.mark.parametrize("apply", [False, True])
def test_pending_complete_batch_hands_off_to_wait_without_create(harness, state, apply):
    harness.add_peer("POSTGRES")
    harness.add_peer("CLICKHOUSE")
    harness.add_mirror(TABLES, name=MIRROR, state=state)
    harness.landing.clear()
    before = deepcopy((harness.mirrors, harness.statuses))
    result = harness.run(apply=apply)
    assert result["ready"] is False and result["accepted"] is apply
    assert result["pending_mirrors"] == [MIRROR]
    assert harness.writes == []
    assert before == (harness.mirrors, harness.statuses)


def test_pending_incomplete_batch_never_claims_acceptance(harness):
    harness.add_peer("POSTGRES")
    harness.add_peer("CLICKHOUSE")
    harness.add_mirror(TABLES[:5], name=MIRROR, state="STATUS_SNAPSHOT")
    result = harness.run(apply=True)
    assert result["ready"] is result["accepted"] is False
    assert harness.writes == []


@pytest.mark.parametrize(
    "listed,observed",
    [
        ("STATUS_SETUP", "STATUS_SETUP"),
        ("STATUS_SNAPSHOT", "STATUS_SNAPSHOT"),
        ("STATUS_RUNNING", "STATUS_SNAPSHOT"),
        ("STATUS_SNAPSHOT", "STATUS_RUNNING"),
    ],
)
def test_readonly_wait_reinspects_pending_until_running(
    harness, monkeypatch, listed, observed
):
    harness.add_peer("POSTGRES")
    harness.add_peer("CLICKHOUSE")
    harness.add_mirror(TABLES, name=MIRROR, state=listed)
    harness.statuses[MIRROR]["currentFlowState"] = observed
    harness.landing.clear()

    def sleep(seconds):
        assert 0 < seconds <= 2
        assert harness.writes == []
        harness.mirrors[0]["status"] = "STATUS_RUNNING"
        harness.statuses[MIRROR]["currentFlowState"] = "STATUS_RUNNING"
        harness.landing.update(TABLES)

    sleeping = Mock(side_effect=sleep)
    monkeypatch.setattr(setup.time, "sleep", sleeping)
    result = harness.run(wait_for_mirrors=True)
    assert result["ready"] is True
    assert result["accepted"] is result["applied"] is False
    assert result["pending_mirrors"] == []
    sleeping.assert_called_once()
    assert (
        sum(
            c.args[:2] == ("GET", "/v1/peers/list")
            for c in harness.request.call_args_list
        )
        == 2
    )
    assert not any(
        "pg_try_advisory" in c.args[0] for c in harness.pg.execute.call_args_list
    )
    assert harness.writes == []
    harness.pg.close.assert_called_once()
    harness.raw.close.assert_called_once()


@pytest.mark.parametrize("missing", ["mirror", "native", "peer"])
def test_readonly_wait_does_not_wait_for_missing_prerequisites(
    harness, monkeypatch, missing
):
    harness.add_peer("POSTGRES")
    harness.add_peer("CLICKHOUSE")
    harness.add_mirror(
        TABLES[:5] if missing == "mirror" else TABLES,
        name=MIRROR,
        state="STATUS_SNAPSHOT",
    )
    if missing == "native":
        harness.native = NativeClient(complete=False)
    elif missing == "peer":
        # An absent unused configured alias cannot hand off as a complete plan.
        harness.peers["retained_pg"] = {
            **harness.peers.pop("pg_source"),
            "name": "retained_pg",
        }
        harness.mirrors[0]["sourceName"] = "retained_pg"
        harness.statuses[MIRROR]["cdcStatus"]["config"]["sourceName"] = "retained_pg"
    sleeping = Mock()
    monkeypatch.setattr(setup.time, "sleep", sleeping)
    result = harness.run(wait_for_mirrors=True)
    assert result["ready"] is result["accepted"] is False
    sleeping.assert_not_called()
    assert harness.writes == []


@pytest.mark.parametrize("failure", ["status", "config", "transport", "landing"])
def test_readonly_wait_does_not_hide_later_failure(harness, monkeypatch, failure):
    harness.add_peer("POSTGRES")
    harness.add_peer("CLICKHOUSE")
    harness.add_mirror(TABLES, name=MIRROR, state="STATUS_SNAPSHOT")

    def sleep(_):
        if failure == "status":
            harness.statuses[MIRROR]["currentFlowState"] = "STATUS_PAUSED"
        elif failure == "config":
            harness.statuses[MIRROR]["cdcStatus"]["config"]["resync"] = True
        elif failure == "transport":
            harness.request.side_effect = OSError("secret-do-not-log")
        else:
            harness.mirrors[0]["status"] = "STATUS_RUNNING"
            harness.statuses[MIRROR]["currentFlowState"] = "STATUS_RUNNING"
            harness.landing.clear()

    sleeping = Mock(side_effect=sleep)
    monkeypatch.setattr(setup.time, "sleep", sleeping)
    with pytest.raises(setup.SetupError) as exc:
        harness.run(wait_for_mirrors=True)
    assert "secret-do-not-log" not in str(exc.value)
    sleeping.assert_called_once()
    assert harness.writes == []
    harness.pg.close.assert_called_once()
    harness.raw.close.assert_called_once()


def test_readonly_wait_exhausts_one_deadline_without_writes(harness, monkeypatch):
    harness.add_peer("POSTGRES")
    harness.add_peer("CLICKHOUSE")
    harness.add_mirror(TABLES, name=MIRROR, state="STATUS_SNAPSHOT")
    clock = [0.0]

    def deadline(self, seconds):
        self.clock = lambda: clock[0]
        self.end = seconds

    def sleep(seconds):
        clock[0] += seconds

    sleeping = Mock(side_effect=sleep)
    monkeypatch.setattr(setup.install.Deadline, "__init__", deadline)
    monkeypatch.setattr(setup.time, "sleep", sleeping)
    with pytest.raises(setup.SetupError):
        harness.run(wait_for_mirrors=True, timeout=5)
    assert clock[0] == 5
    assert [c.args[0] for c in sleeping.call_args_list] == [2, 2, 1]
    assert harness.writes == []
    harness.pg.close.assert_called_once()
    harness.raw.close.assert_called_once()


@pytest.mark.parametrize("apply,wait", [(True, True), (False, 1), (False, "true")])
def test_readonly_wait_rejects_apply_and_nonboolean_before_connect(
    harness, apply, wait
):
    with pytest.raises(setup.SetupError, match="read-only"):
        harness.run(apply=apply, wait_for_mirrors=wait)
    harness.pg_connect.assert_not_called()
    harness.ch_connect.assert_not_called()
    harness.request.assert_not_called()


def test_only_missing_mappings_enter_new_batch(harness):
    harness.add_peer("POSTGRES")
    harness.add_peer("CLICKHOUSE")
    for table in TABLES[:4]:
        harness.add_mirror(table)
    assert harness.run(apply=True)["created_mirrors"] == [
        "futureagi_cdc_640610b3a9a9a09e"
    ]
    assert len(harness.writes) == 1
    assert harness.writes[0][1]["connectionConfigs"]["tableMappings"] == [
        {"sourceTableIdentifier": "public." + t, "destinationTableIdentifier": t}
        for t in TABLES[4:]
    ]


@pytest.mark.parametrize("foreign", [False, True])
def test_derived_mirror_name_collision_fails_without_replace(harness, foreign):
    harness.add_peer("POSTGRES")
    harness.add_peer("CLICKHOUSE")
    name = MIRROR if foreign else "futureagi_cdc_38d691beb48fccca"
    harness.add_mirror(TABLES[:5], name=name)
    if foreign:
        other = deepcopy(harness.peers["ch_dest"])
        other.update(name="other")
        other["clickhouseConfig"]["database"] = "another_db"
        harness.peers["other"] = other
        harness.mirrors[-1]["destinationName"] = "other"
        harness.landing.clear()
    with pytest.raises(setup.SetupError, match="occupied"):
        harness.run(apply=True)
    assert harness.writes == []


@pytest.mark.parametrize(
    "phase",
    [
        "/v1/peers/list",
        "/v1/mirrors/list",
        "/v1/peers/info/ch_dest",
        "/v1/mirrors/status",
    ],
)
def test_inventory_read_failure_is_not_absence_and_is_sanitized(harness, phase):
    harness.retained()
    harness.fail_read = phase
    with pytest.raises(setup.SetupError) as exc:
        harness.run(apply=True)
    assert "secret-do-not-log" not in "".join(traceback.format_exception(exc.value))
    assert harness.writes == []


@pytest.mark.parametrize("retain", [False, True])
@pytest.mark.parametrize("failure", [1, 2, 3])
def test_partial_apply_stops_once_then_explicit_invocation_reconciles(
    harness, retain, failure
):
    harness.fail_write = failure
    harness.retain_failed_write = retain
    with pytest.raises(setup.SetupError) as exc:
        harness.run(apply=True)
    assert len(harness.writes) == failure
    assert "secret-do-not-log" not in "".join(traceback.format_exception(exc.value))
    retained = failure if retain else failure - 1
    harness.fail_write = None
    result = harness.run(apply=True)
    assert result["accepted"]
    assert len(harness.writes[failure:]) == 3 - retained
    assert all(w not in harness.writes[:retained] for w in harness.writes[failure:])
    assert harness.run()["ready"]


def test_credentials_and_endpoints_are_exact_json_values_not_sql():
    secret = "p'ass\\word; $(ignored) " + chr(96) + "ignored" + chr(96) + "\nsecret"
    config = replace(CONFIG, pg_user="u'ser", pg_password=secret, ch_password=secret)
    plan = setup._creates(config, ["pg_source", "ch_dest"], TABLES)
    assert plan[0][2]["peer"]["postgresConfig"]["password"] == secret
    assert plan[0][2]["peer"]["postgresConfig"]["user"] == "u'ser"
    assert plan[1][2]["peer"]["clickhouseConfig"]["password"] == secret
    assert json.loads(json.dumps(plan[0][2])) == plan[0][2]


@pytest.mark.parametrize(
    "env",
    [
        {"ENV_TYPE": "prod", "CLOUD_DEPLOYMENT": "US"},
        {"CH_USE_REPLICATED_ENGINES": "true"},
    ],
)
def test_hosted_and_replicated_apply_rejected_before_connect(harness, env):
    with pytest.raises(setup.SetupError, match="hosted/replicated"):
        harness.run(config=setup.Config.from_env(env), apply=True)
    harness.pg_connect.assert_not_called()


@pytest.mark.parametrize("key", ["PGSERVICE", "PGHOSTADDR"])
def test_hidden_source_routes_rejected_before_connect(harness, monkeypatch, key):
    monkeypatch.setenv(key, "hidden")
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    harness.pg_connect.assert_not_called()


@pytest.mark.parametrize(
    "args,ready,accepted,code",
    [
        ([], False, False, 1),
        ([], True, False, 0),
        (["--wait-for-mirrors"], False, False, 1),
        (["--wait-for-mirrors"], False, True, 1),
        (["--wait-for-mirrors"], True, False, 0),
        (["--apply"], False, True, 0),
        (["--apply"], False, False, 1),
    ],
)
def test_cli_distinguishes_acceptance_from_readiness(
    monkeypatch, capsys, args, ready, accepted, code
):
    run = Mock(return_value={"ready": ready, "accepted": accepted})
    monkeypatch.setattr(setup, "run", run)
    monkeypatch.setattr(setup.Config, "from_env", Mock(return_value=CONFIG))
    assert setup.main(args) == code
    assert run.call_args.kwargs["apply"] is ("--apply" in args)
    assert run.call_args.kwargs["wait_for_mirrors"] is ("--wait-for-mirrors" in args)
    assert json.loads(capsys.readouterr().out) == {"ready": ready, "accepted": accepted}


def test_cli_wait_and_apply_are_mutually_exclusive(monkeypatch):
    run = Mock()
    monkeypatch.setattr(setup, "run", run)
    with pytest.raises(SystemExit) as exc:
        setup.main(["--apply", "--wait-for-mirrors"])
    assert exc.value.code == 2
    run.assert_not_called()


def test_cli_driver_errors_never_print_secret(monkeypatch, capsys):
    monkeypatch.setattr(setup, "run", Mock(side_effect=OSError("secret-do-not-log")))
    monkeypatch.setattr(setup.Config, "from_env", Mock(return_value=CONFIG))
    assert setup.main(["--apply"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "secret-do-not-log" not in captured.err


@pytest.mark.parametrize("field", ["name", "host", "port", "database"])
@pytest.mark.parametrize("side", ["source", "destination"])
def test_mirror_hash_binds_exact_peer_pair_and_missing_mapping_set(side, field):
    assert setup._mirror_name(CONFIG, TABLES) == MIRROR
    assert setup._mirror_name(CONFIG, list(reversed(TABLES))) == MIRROR
    target = getattr(CONFIG, side)
    changed = replace(
        target,
        **{
            field: target.port + 1
            if field == "port"
            else getattr(target, field) + "_other"
        },
    )
    assert setup._mirror_name(replace(CONFIG, **{side: changed}), TABLES) != MIRROR
    assert setup._mirror_name(CONFIG, TABLES[1:]) != MIRROR
    assert re.fullmatch(
        r"futureagi_cdc_[a-f0-9]{16}", setup._mirror_name(CONFIG, TABLES)
    )


@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"peers": []},
        {"items": None},
        {"items": {}},
        {"items": [{"name": "pg_source"}]},
        {"items": [{"name": "pg_source", "type": "POSTGRES"}] * 2},
    ],
)
def test_missing_or_duplicate_complete_peer_list_never_proves_absence(
    harness, envelope
):
    request = harness.request.side_effect
    harness.request.side_effect = lambda method, path, body=None: (
        envelope if path == "/v1/peers/list" else request(method, path, body)
    )
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


@pytest.mark.parametrize(
    "change", ["duplicate", "foreign_source", "unknown_table", "transform"]
)
def test_effective_mapping_rejection_before_any_create(harness, change):
    harness.retained()
    mapping = harness.statuses["mirror_" + TABLES[-1]]["cdcStatus"]["config"][
        "tableMappings"
    ][0]
    if change == "duplicate":
        mapping.update(
            sourceTableIdentifier="public." + TABLES[0],
            destinationTableIdentifier=TABLES[0],
        )
    elif change == "foreign_source":
        mapping["sourceTableIdentifier"] = "other." + TABLES[-1]
    elif change == "unknown_table":
        mapping.update(
            sourceTableIdentifier="public.unknown", destinationTableIdentifier="unknown"
        )
    else:
        mapping["exclude"] = ["id"]
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


@pytest.mark.parametrize(
    "change", ["missing", "nullable", "default", "engine", "primary"]
)
def test_retained_landing_shape_validated_before_missing_batch(harness, change):
    harness.retained()
    harness.mirrors.pop(0)
    harness.landing.remove(TABLES[0])
    table = TABLES[-1]
    if change == "missing":
        del harness.source.columns[table]["id"]
    elif change == "nullable":
        harness.source.columns[table]["id"] = ("Nullable(UUID)", "", "")
    elif change == "default":
        harness.source.columns[table]["id"] = ("UUID", "DEFAULT", "generateUUIDv4()")
    else:
        row = list(harness.source.tables[table])
        row[0 if change == "engine" else 4] = "wrong"
        harness.source.tables[table] = tuple(row)
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


def test_running_mirror_with_absent_landing_is_not_recreated(harness):
    harness.retained()
    harness.landing.remove(TABLES[-1])
    with pytest.raises(setup.SetupError, match="no landing"):
        harness.run(apply=True)
    assert harness.writes == []


def test_lost_snapshot_create_ack_retains_pending_batch_no_second_attempt(harness):
    harness.fail_write = 3
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    harness.mirrors[0]["status"] = "STATUS_SNAPSHOT"
    harness.statuses[MIRROR]["currentFlowState"] = "STATUS_SNAPSHOT"
    harness.landing.clear()
    harness.fail_write = None
    before = list(harness.writes)
    result = harness.run(apply=True)
    assert not result["ready"] and result["accepted"]
    assert harness.writes == before
    assert len(harness.writes) == 3


def test_read_only_transport_failures_do_not_open_writer_and_close_readers(harness):
    harness.raw.query.side_effect = OSError("secret-do-not-log")
    with pytest.raises(setup.SetupError) as exc:
        harness.run(apply=True)
    assert "secret-do-not-log" not in "".join(traceback.format_exception(exc.value))
    assert harness.writes == []
    harness.pg.close.assert_called_once()
    harness.raw.close.assert_called_once()


@pytest.mark.parametrize("timeout", [0, -1, True, "300"])
def test_invalid_deadline_never_connects(harness, timeout):
    with pytest.raises(setup.SetupError):
        harness.run(apply=True, timeout=timeout)
    harness.pg_connect.assert_not_called()


def test_expired_deadline_does_not_issue_first_create(harness, monkeypatch):
    original = setup._creates

    def expired(*args):
        result = original(*args)
        monkeypatch.setattr(
            setup.install.Deadline,
            "remaining",
            Mock(side_effect=setup.install.InstallError("deadline exceeded")),
        )
        return result

    monkeypatch.setattr(setup, "_creates", expired)
    with pytest.raises(setup.SetupError):
        harness.run(apply=True)
    assert harness.writes == []


def test_closed_readers_reject_non_select_operations(harness, monkeypatch):
    original = setup.native.inspect_native

    def inspect(client, **kwargs):
        with pytest.raises(setup.SetupError):
            client.query("CREATE TABLE prohibited")
        assert not hasattr(client, "command")
        assert not hasattr(client, "insert")
        return original(client, **kwargs)

    monkeypatch.setattr(setup.native, "inspect_native", inspect)
    harness.run(apply=True)
    assert len(harness.writes) == 3


@pytest.mark.parametrize(
    "kind,response",
    [
        ("peer", None),
        ("peer", []),
        ("peer", {}),
        ("peer", {"status": 1}),
        ("peer", {"status": "FAILED", "message": "secret-do-not-log"}),
        ("peer", {"status": "VALIDATION_UNKNOWN"}),
        ("mirror", None),
        ("mirror", {}),
        ("mirror", {"workflowId": ""}),
        ("mirror", {"workflowId": " "}),
        ("mirror", {"workflowId": 1}),
        ("mirror", {"error": "secret-do-not-log"}),
    ],
)
def test_bad_create_ack_stops_then_explicit_read_reconciles(harness, kind, response):
    request = harness.request.side_effect
    path = "/v1/peers/create" if kind == "peer" else "/v1/flows/cdc/create"

    def corrupt_ack(method, route, payload=None):
        result = request(method, route, payload)
        return response if (method, route) == ("POST", path) else result

    harness.request.side_effect = corrupt_ack
    with pytest.raises(
        setup.SetupError, match="invalid creation acknowledgement"
    ) as exc:
        harness.run(apply=True)
    assert "secret-do-not-log" not in "".join(traceback.format_exception(exc.value))
    assert len(harness.writes) == (1 if kind == "peer" else 3)
    retained = deepcopy(harness.writes)
    harness.request.side_effect = request
    assert harness.run(apply=True)["accepted"]
    assert all(write not in retained for write in harness.writes[len(retained) :])
    assert harness.run()["ready"]


@pytest.mark.parametrize(
    "error,category",
    [
        (TimeoutError("secret-do-not-log"), "timeout"),
        (OSError("secret-do-not-log"), "transport error"),
        (setup.install.InstallError("secret-do-not-log"), "transport error"),
        (RuntimeError("secret-do-not-log"), "operation error"),
    ],
)
def test_api_create_error_has_safe_object_identity_and_one_attempt(error, category):
    request = Mock(side_effect=error)
    with pytest.raises(setup.SetupError) as exc:
        setup._create_once(request, "mirror", MIRROR, {})
    assert f"CREATE MIRROR {MIRROR} failed ({category})" in str(exc.value)
    assert "secret-do-not-log" not in "".join(traceback.format_exception(exc.value))
    assert exc.value.__suppress_context__
    request.assert_called_once()


@pytest.mark.parametrize("apply", [False, True])
def test_setup_uses_same_inventory_origin_with_explicit_create_optin(
    harness, monkeypatch, apply
):
    factory = Mock(return_value=harness.request)
    monkeypatch.setattr(setup.install, "peerdb_transport", factory)
    setup.run(
        CONFIG,
        apply=apply,
        pg_connect=harness.pg_connect,
        ch_connect=harness.ch_connect,
    )
    assert factory.call_args.args[0] == CONFIG.peerdb_url
    assert factory.call_args.kwargs == {"allow_create": apply}
    assert len(harness.writes) == (3 if apply else 0)


@pytest.mark.parametrize("path", ["/v1/peers/create", "/v1/flows/cdc/create"])
def test_transport_create_optin_is_required_and_default_remains_readonly(
    monkeypatch, path
):
    opener = Mock()
    monkeypatch.setattr(
        setup.install.urllib.request, "build_opener", Mock(return_value=opener)
    )
    transport = setup.install.peerdb_transport(
        "http://flow:8113", setup.install.Deadline(10)
    )
    with pytest.raises(setup.install.InstallError):
        transport("POST", path, {})
    opener.open.assert_not_called()
    opener.open.return_value = io.BytesIO(b'{"status":"CREATED"}')
    transport = setup.install.peerdb_transport(
        "http://flow:8113", setup.install.Deadline(10), allow_create=True
    )
    payload = {"literal": "p'ass; $(ignored)"}
    assert transport("POST", path, payload) == {"status": "CREATED"}
    opener.open.assert_called_once()
    req = opener.open.call_args.args[0]
    assert req.full_url == "http://flow:8113" + path and req.method == "POST"
    assert json.loads(req.data) == payload


@pytest.mark.parametrize(
    "method,path",
    [
        ("DELETE", "/v1/peers/create"),
        ("GET", "/v1/peers/create"),
        ("POST", "/v1/peers/drop"),
        ("POST", "/v1/mirrors/state/change"),
        ("POST", "/v1/flows/cdc/create/"),
        ("POST", "/v1/flows/cdc/create?resync=true"),
        ("POST", "/v1/flows/qrep/create"),
        ("POST", "/v1/mirrors/resync"),
    ],
)
def test_transport_create_optin_does_not_enable_other_mutations(
    monkeypatch, method, path
):
    opener = Mock()
    monkeypatch.setattr(
        setup.install.urllib.request, "build_opener", Mock(return_value=opener)
    )
    transport = setup.install.peerdb_transport(
        "http://flow:8113", setup.install.Deadline(10), allow_create=True
    )
    with pytest.raises(setup.install.InstallError):
        transport(method, path, {})
    opener.open.assert_not_called()


@pytest.mark.parametrize("path", ["/v1/peers/create", "/v1/flows/cdc/create"])
def test_create_transport_failure_is_sanitized_and_never_retried(monkeypatch, path):
    opener = Mock()
    opener.open.side_effect = OSError("secret-do-not-log")
    monkeypatch.setattr(
        setup.install.urllib.request, "build_opener", Mock(return_value=opener)
    )
    transport = setup.install.peerdb_transport(
        "http://flow:8113", setup.install.Deadline(10), allow_create=True
    )
    payload = {"password": "secret-do-not-log"}
    with pytest.raises(setup.install.InstallError) as exc:
        transport("POST", path, payload)
    assert "secret-do-not-log" not in "".join(traceback.format_exception(exc.value))
    opener.open.assert_called_once()
