"""Offline CLI/transport tests; source/schema and runtime proofs are separate."""

import io
import json
import socket
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import clickhouse_connect
import psycopg
import pytest
import urllib3

from tracer.services.clickhouse import oss_cdc_install as cli


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("offline test attempted a connection"))
    for name in ("PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(clickhouse_connect, "get_client", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    yield
    forbidden.assert_not_called()


def test_defaults_and_custom_connection_settings():
    default = cli.Config.from_env({})
    assert default.source.endpoint == ("postgres", 5432, "futureagi")
    assert default.destination.endpoint == ("clickhouse", 9000, "default")
    assert default.http_port == 8123 and not default.hosted
    config = cli.Config.from_env(
        {
            "SRC_PG_HOST": "pg.private",
            "SRC_PG_PORT": "5434",
            "SRC_PG_DB": "source_db",
            "DST_CH_HOST": "ch.private",
            "DST_CH_PORT": "9001",
            "DST_CH_DB": "tenant_db",
            "SRC_PG_USER": "source_reader",
            "SRC_PG_PASSWORD": "pg-secret",
            "DST_CH_USER": "installer",
            "DST_CH_PASSWORD": "ch-secret",
            "CH_HTTP_PORT": "8124",
            "CH25_HOST": "ch.private",
            "CH25_DATABASE": "tenant_db",
            "FI_CH_DATABASE": "tenant_db",
            "FI_CH_URL": "http://ch.private:8124",
            "CH25_HTTP_PORT": "8124",
            "CH25_TCP_PORT": "9001",
        },
        source_peer="source_alias",
        destination_peer="sink_alias",
        peerdb_url="https://flow.private/",
    )
    assert config.source.endpoint == ("pg.private", 5434, "source_db")
    assert config.destination.endpoint == ("ch.private", 9001, "tenant_db")
    assert (config.source.name, config.destination.name) == (
        "source_alias",
        "sink_alias",
    )
    assert config.peerdb_url == "https://flow.private"
    assert config.pg_password == "pg-secret" and config.ch_password == "ch-secret"
    assert "pg-secret" not in repr(config) and "ch-secret" not in repr(config)


@pytest.mark.parametrize(
    "value", ["", "0", "65536", "-1", "1.1", " 8123", "１２３", None, True]
)
@pytest.mark.parametrize("key", ["SRC_PG_PORT", "DST_CH_PORT", "CH_HTTP_PORT"])
def test_invalid_ports_fail_before_connect(key, value):
    with pytest.raises(cli.InstallError):
        cli.Config.from_env({key: value})


@pytest.mark.parametrize(
    "key,value",
    [
        ("CH25_HOST", "other"),
        ("CH25_DATABASE", "other"),
        ("FI_CH_DATABASE", "other"),
        ("CH25_TCP_PORT", "9001"),
        ("CH25_HTTP_PORT", "8124"),
        ("FI_CH_URL", "http://other:8123"),
        ("FI_CH_URL", "http://clickhouse:8124"),
        ("FI_CH_URL", "https://clickhouse:8123"),
    ],
)
def test_split_native_and_writer_targets_rejected(key, value):
    with pytest.raises(cli.InstallError):
        cli.Config.from_env({key: value})


@pytest.mark.parametrize(
    "key,value", [("CH_HOST", "other"), ("CH_DATABASE", "other"), ("CH_PORT", "9001")]
)
def test_source_override_cannot_hide_other_reader_target(key, value):
    with pytest.raises(cli.InstallError):
        cli.Config.from_env(
            {
                "DST_CH_HOST": "clickhouse",
                "DST_CH_PORT": "9000",
                "DST_CH_DB": "default",
                key: value,
            }
        )


@pytest.mark.parametrize(
    "url",
    [
        None,
        1,
        "",
        "http://",
        "file:///tmp/flow",
        "http://user:secret@flow",
        "http://@flow",
        "http://flow/path",
        "http://flow?secret=x",
        "http://flow#fragment",
        "http://flow:0",
        "http://flow:65536",
        "http://flow:\n80",
        "http://flow\x00",
    ],
)
def test_bad_inventory_origins_rejected(url):
    with pytest.raises(cli.InstallError):
        cli.Config.from_env({"PEERDB_FLOW_SERVER_HTTP": url})


@pytest.mark.parametrize(
    "key,value",
    [
        ("SRC_PG_HOST", "/tmp"),
        ("SRC_PG_HOST", "first,second"),
        ("SRC_PG_HOST", "host x"),
        ("SRC_PG_DB", "host=other"),
        ("SRC_PG_DB", "postgresql://other/source"),
        ("DST_CH_HOST", "user@host"),
        ("DST_CH_DB", "db; DROP TABLE spans"),
        ("SRC_PG_PASSWORD", None),
        ("DST_CH_PASSWORD", "bad\x00"),
        ("SRC_PG_USER", ""),
    ],
)
def test_unsafe_connections_rejected(key, value):
    with pytest.raises(ValueError):
        cli.Config.from_env({key: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"http_port": True},
        {"http_port": 0},
        {"pg_user": ""},
        {"hosted": "false"},
        {"peerdb_url": "http://secret@flow"},
    ],
)
def test_direct_config_cannot_bypass_validation(changes):
    with pytest.raises(cli.InstallError):
        replace(cli.Config.from_env({}), **changes)


@pytest.fixture
def harness(monkeypatch):
    pg = Mock()
    pg.execute.return_value.fetchall.return_value = [(True,)]
    raw = Mock()
    pg_connect, ch_connect, request = (
        Mock(return_value=pg),
        Mock(return_value=raw),
        Mock(),
    )
    ready = SimpleNamespace(missing=(), upgrade_recorded=True)
    inspect = Mock(return_value=ready)
    apply = Mock(return_value=SimpleNamespace(created=(), upgrade_applied=False))
    monkeypatch.setattr(cli.core, "inspect_bootstrap", inspect)
    monkeypatch.setattr(cli.core, "bootstrap_cdc", apply)

    def run(**kwargs):
        return cli.run(
            cli.Config.from_env({}),
            pg_connect=pg_connect,
            ch_connect=ch_connect,
            request=request,
            **kwargs,
        )

    return SimpleNamespace(
        pg=pg,
        raw=raw,
        inspect=inspect,
        apply=apply,
        run=run,
        pg_connect=pg_connect,
        ch_connect=ch_connect,
        request=request,
    )


def test_default_is_read_only_and_closes_clients(harness):
    assert harness.run() == {
        "ready": True,
        "applied": False,
        "missing_objects": [],
        "derived_upgrade_required": False,
    }
    harness.apply.assert_not_called()
    harness.pg.execute.assert_not_called()  # no advisory lock in inspection mode
    assert harness.pg.read_only is True
    assert harness.pg.isolation_level is psycopg.IsolationLevel.REPEATABLE_READ
    assert (
        harness.pg_connect.call_args.kwargs["options"]
        == "-c statement_timeout=10000 -c lock_timeout=2000"
    )
    ch_args = harness.ch_connect.call_args.kwargs
    assert ch_args["query_retries"] == 0
    assert isinstance(ch_args["pool_mgr"], cli._SingleAttemptPool)
    assert ch_args["autogenerate_session_id"] is False
    harness.pg.close.assert_called_once()
    harness.raw.close.assert_called_once()


def test_missing_schema_is_not_readiness(harness):
    harness.inspect.return_value = SimpleNamespace(
        missing=("dataset_cells",), upgrade_recorded=False
    )
    assert harness.run() == {
        "ready": False,
        "applied": False,
        "missing_objects": ["dataset_cells"],
        "derived_upgrade_required": True,
    }
    harness.apply.assert_not_called()


@pytest.mark.parametrize("apply", [False, True])
def test_mirror_wait_reads_until_ready_before_any_schema_action(
    harness, monkeypatch, apply
):
    inventory = Mock(
        side_effect=[cli.InventoryPending("snapshot in progress"), object()]
    )
    monkeypatch.setattr(cli.core.MirrorInventory, "from_peerdb", inventory)

    def sleep(seconds):
        assert 0 < seconds <= 2
        harness.inspect.assert_not_called()
        harness.apply.assert_not_called()
        harness.raw.command.assert_not_called()
        harness.raw.insert.assert_not_called()

    sleeping = Mock(side_effect=sleep)
    monkeypatch.setattr(cli.time, "sleep", sleeping)
    assert harness.run(apply=apply, wait_for_mirrors=True)["ready"]
    assert inventory.call_count == 2
    sleeping.assert_called_once()
    harness.inspect.assert_called_once()
    assert harness.apply.call_count == int(apply)


def test_mirror_wait_does_not_retry_invalid_inventory(harness, monkeypatch):
    from tracer.services.clickhouse.oss_cdc_inventory import InventoryError

    inventory = Mock(side_effect=InventoryError("foreign source"))
    monkeypatch.setattr(cli.core.MirrorInventory, "from_peerdb", inventory)
    sleeping = Mock()
    monkeypatch.setattr(cli.time, "sleep", sleeping)
    with pytest.raises(InventoryError, match="foreign source"):
        harness.run(apply=True, wait_for_mirrors=True)
    inventory.assert_called_once()
    sleeping.assert_not_called()
    harness.inspect.assert_not_called()
    harness.apply.assert_not_called()


def test_mirror_wait_exhausts_deadline_without_writes(harness, monkeypatch):
    clock = [0.0]

    def deadline(self, seconds):
        self.clock = lambda: clock[0]
        self.end = seconds

    monkeypatch.setattr(cli.Deadline, "__init__", deadline)
    inventory = Mock(side_effect=cli.InventoryPending("snapshot in progress"))
    monkeypatch.setattr(cli.core.MirrorInventory, "from_peerdb", inventory)

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(cli.time, "sleep", sleep)
    with pytest.raises(cli.InstallError, match="deadline"):
        harness.run(apply=True, wait_for_mirrors=True, timeout=3)
    assert clock[0] == 3
    harness.inspect.assert_not_called()
    harness.apply.assert_not_called()
    harness.raw.command.assert_not_called()
    harness.pg.close.assert_called_once()
    harness.raw.close.assert_called_once()


def test_mirror_wait_never_replays_failed_apply(harness, monkeypatch):
    inventory = Mock(return_value=object())
    monkeypatch.setattr(cli.core.MirrorInventory, "from_peerdb", inventory)
    harness.apply.side_effect = RuntimeError("uncertain write")
    with pytest.raises(RuntimeError, match="uncertain write"):
        harness.run(apply=True, wait_for_mirrors=True)
    inventory.assert_called_once()
    harness.apply.assert_called_once()


@pytest.mark.parametrize("phase,wait", [("native", True), ("cdc", 1), ("cdc", "true")])
def test_wait_options_fail_before_connections(harness, phase, wait):
    with pytest.raises(cli.InstallError, match="readiness waiting"):
        harness.run(phase=phase, wait_for_mirrors=wait)
    harness.pg_connect.assert_not_called()
    harness.ch_connect.assert_not_called()


def test_query_enforces_read_only_settings(harness):
    def inspect(client, **kwargs):
        client.query(
            "SELECT 1",
            parameters={"x": 2},
            settings={"readonly": 0, "max_threads": 999},
        )
        with pytest.raises(cli.InstallError):
            client.query("DELETE FROM spans")
        for operation in (
            lambda: client.command("CREATE TABLE x"),
            lambda: client.insert("schema_versions", [], []),
        ):
            with pytest.raises(cli.InstallError, match="apply"):
                operation()
        return SimpleNamespace(missing=(), upgrade_recorded=True)

    harness.inspect.side_effect = inspect
    harness.run()
    settings = harness.raw.query.call_args.kwargs["settings"]
    assert settings["readonly"] == 1 and settings["max_threads"] == 1
    assert 1 <= settings["max_execution_time"] <= 5
    harness.raw.command.assert_not_called()
    harness.raw.insert.assert_not_called()


def test_callbacks_use_same_verified_targets_and_bound_source_query(
    harness, monkeypatch
):
    metadata = Mock(
        side_effect=lambda query, **kwargs: query("SELECT current_database()", ())
    )
    inventory = Mock(return_value="inventory")
    monkeypatch.setattr(cli, "inspect_source", metadata)
    monkeypatch.setattr(cli.core.MirrorInventory, "from_peerdb", inventory)

    def inspect(client, **kwargs):
        assert kwargs["inspect_mirrors"]() == "inventory"
        assert kwargs["inspect_source"]() == [(True,)]
        return SimpleNamespace(missing=(), upgrade_recorded=True)

    harness.inspect.side_effect = inspect
    harness.run()
    assert inventory.call_args.args == (harness.request,)
    assert inventory.call_args.kwargs["source"] == metadata.call_args.kwargs["source"]
    assert set(metadata.call_args.kwargs["tables"]) == set(cli.core.LANDING)
    harness.pg.execute.assert_called_once_with("SELECT current_database()", ())


def test_apply_locks_target_then_calls_core_once(harness):
    assert harness.run(apply=True)["applied"] is True
    harness.pg.execute.assert_called_once()
    query, params = harness.pg.execute.call_args.args
    assert query == "SELECT pg_try_advisory_xact_lock(%s)" and type(params[0]) is int
    harness.apply.assert_called_once()
    assert harness.apply.call_args.kwargs["inspect_source"] is not None
    harness.pg.commit.assert_not_called()


def test_apply_only_allows_packaged_derived_ddl_and_receipt(harness):
    def apply(client, **kwargs):
        for sql in (
            "DROP TABLE spans",
            "ALTER TABLE spans DELETE WHERE 1",
            "TRUNCATE TABLE model_hub_cell",
            "CREATE TABLE unrelated (id Int64) ENGINE=Memory",
        ):
            with pytest.raises(cli.InstallError, match="packaged DDL"):
                client.command(sql)
        with pytest.raises(cli.InstallError, match="verified derived migration"):
            client.insert("spans", [[1]], ["id"])
        migration = cli.upgrade.migration_file("cdc_002_usage_eval_fields.sql")
        statements = cli.apply_schema.split_statements(migration.path.read_text())
        for sql in statements:
            client.command(sql)
        client.command(cli.core._LEDGER_DDL)
        row = [
            migration.path.name,
            migration.sha256,
            "oss-cdc-bootstrap",
            "schema-topology/v1/local",
        ]
        client.insert(
            "schema_versions", [row], ["filename", "sha256", "applied_by", "notes"]
        )
        return SimpleNamespace(created=(), upgrade_applied=True)

    harness.apply.side_effect = apply
    assert harness.run(apply=True)["derived_upgrade_applied"] is True
    assert harness.raw.command.call_count == 5
    harness.raw.insert.assert_called_once()


@pytest.mark.parametrize("apply", [False, True])
def test_native_phase_never_reads_peerdb_or_applies_cdc(harness, monkeypatch, apply):
    inspect = Mock(return_value=("spans",))
    bootstrap = Mock(return_value=("spans",))
    monkeypatch.setattr(cli.native, "inspect_native", inspect)
    monkeypatch.setattr(cli.native, "bootstrap_native", bootstrap)
    result = harness.run(phase="native", apply=apply)
    assert result["ready"] is apply
    assert result["applied"] is apply
    assert bootstrap.call_count == int(apply)
    harness.request.assert_not_called()
    harness.inspect.assert_not_called()
    harness.apply.assert_not_called()


def test_native_write_boundary_rejects_cdc_and_source_ddl(harness, monkeypatch):
    monkeypatch.setattr(cli.native, "inspect_native", Mock(return_value=("traces",)))

    def bootstrap(client, **kwargs):
        for sql in (
            cli.core._LEDGER_DDL,
            "DROP TABLE spans",
            "ALTER TABLE spans ADD COLUMN unsafe UInt8",
        ):
            with pytest.raises(cli.InstallError, match="packaged DDL"):
                client.command(sql)
        with pytest.raises(cli.InstallError, match="verified derived migration"):
            client.insert("schema_versions", [], ["filename"])
        client.command(cli.native.native_definitions("default")["traces"])
        return ("traces",)

    monkeypatch.setattr(cli.native, "bootstrap_native", bootstrap)
    assert harness.run(phase="native", apply=True)["created_objects"] == ["traces"]
    harness.raw.command.assert_called_once()
    harness.raw.insert.assert_not_called()


def test_unknown_phase_rejected_without_connection():
    with pytest.raises(cli.InstallError, match="phase"):
        cli.run(cli.Config.from_env({}), phase="all")


def test_acknowledgement_loss_cannot_trigger_driver_retry(monkeypatch):
    from clickhouse_connect.driver.httpclient import HttpClient

    error = urllib3.exceptions.ProtocolError("lost acknowledgement")
    error.__cause__ = ConnectionResetError()
    request = Mock(side_effect=error)
    monkeypatch.setattr(urllib3.PoolManager, "request", request)
    pool = cli._SingleAttemptPool()
    # Execute the real driver's retry loop without constructor/network activity.
    client = SimpleNamespace(
        headers={},
        params={},
        _send_progress=False,
        _progress_interval=0,
        _autogenerate_query_id=False,
        url="http://clickhouse:8123",
        timeout=1,
        http_retries=0,
        server_host_name=None,
        http=pool,
    )
    with pytest.raises(cli.InstallError):
        HttpClient._raw_request(client, b"DDL", {}, retries=0)
    request.assert_called_once()
    pool.clear()


@pytest.mark.parametrize("lock_rows", [[(False,)], [], [(None,)]])
def test_busy_or_unknown_lock_prevents_clickhouse_connection(harness, lock_rows):
    harness.pg.execute.return_value.fetchall.return_value = lock_rows
    with pytest.raises(cli.InstallError, match="holds"):
        harness.run(apply=True)
    harness.ch_connect.assert_not_called()
    harness.apply.assert_not_called()
    harness.pg.close.assert_called_once()


@pytest.mark.parametrize("phase", ["preflight", "apply", "connect"])
def test_failure_stops_without_retry_and_closes_open_resources(harness, phase):
    target = {
        "preflight": harness.inspect,
        "apply": harness.apply,
        "connect": harness.ch_connect,
    }[phase]
    target.side_effect = RuntimeError("sensitive backend detail")
    with pytest.raises(RuntimeError):
        harness.run(apply=True)
    target.assert_called_once()
    if phase != "apply":
        harness.apply.assert_not_called()
    harness.pg.close.assert_called_once()
    assert harness.raw.close.call_count == (phase != "connect")


@pytest.mark.parametrize(
    "env",
    [
        {"ENV_TYPE": "prod", "CLOUD_DEPLOYMENT": "US"},
        {"ENV_TYPE": "production", "CLOUD_DEPLOYMENT": "EU"},
        {"CH_USE_REPLICATED_ENGINES": "true"},
    ],
)
def test_hosted_apply_rejected_before_connection(env):
    with pytest.raises(cli.InstallError, match="hosted/replicated"):
        cli.run(cli.Config.from_env(env), apply=True)


@pytest.mark.parametrize("key", ["PGHOSTADDR", "PGSERVICE"])
def test_inherited_libpq_routing_rejected(monkeypatch, key):
    monkeypatch.setenv(key, "unrelated")
    with pytest.raises(cli.InstallError, match="routing"):
        cli.run(cli.Config.from_env({}))


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "30"])
def test_deadline_requires_positive_integer(value):
    with pytest.raises(cli.InstallError):
        cli.Deadline(value)


def test_deadline_is_whole_job_not_per_query():
    clock = Mock(side_effect=[100, 101, 109.5, 110])
    deadline = cli.Deadline(10, clock=clock)
    assert deadline.remaining(3) == 3
    assert deadline.remaining() == 0.5
    with pytest.raises(cli.InstallError, match="deadline"):
        deadline.remaining()


def test_wall_clock_deadline_restores_signal_on_failure(monkeypatch):
    handler = object()
    monkeypatch.setattr(cli.signal, "getsignal", lambda *_: handler)
    monkeypatch.setattr(cli.signal, "getitimer", lambda *_: (0.0, 0.0))
    signal, timer = Mock(), Mock()
    monkeypatch.setattr(cli.signal, "signal", signal)
    monkeypatch.setattr(cli.signal, "setitimer", timer)
    with pytest.raises(cli.InstallError, match="deadline"):
        with cli._wall_clock_limit(30):
            signal.call_args.args[1]()
    assert timer.call_args_list[0].args == (cli.signal.ITIMER_REAL, 30)
    assert timer.call_args_list[-1].args == (cli.signal.ITIMER_REAL, 0)
    assert signal.call_args.args == (cli.signal.SIGALRM, handler)


def test_wall_clock_deadline_does_not_override_other_task(monkeypatch):
    monkeypatch.setattr(cli.signal, "getitimer", lambda *_: (15.0, 0.0))
    setter = Mock()
    monkeypatch.setattr(cli.signal, "setitimer", setter)
    with pytest.raises(cli.InstallError, match="existing"):
        with cli._wall_clock_limit(30):
            pytest.fail("must not start")
    setter.assert_not_called()


def test_clickhouse_remote_close_is_never_replayed(monkeypatch):
    error = urllib3.exceptions.ProtocolError("lost acknowledgement")
    error.__cause__ = ConnectionResetError("could have been applied")
    request = Mock(side_effect=error)
    monkeypatch.setattr(urllib3.PoolManager, "request", request)
    pool = cli._SingleAttemptPool()
    with pytest.raises(cli.InstallError, match="partial state"):
        pool.request(
            "POST", "http://clickhouse:8123", body=b"DDL", retries=5, redirect=True
        )
    request.assert_called_once()
    assert request.call_args.kwargs["retries"] is False
    assert request.call_args.kwargs["redirect"] is False
    pool.clear()


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/peers/create"),
        ("POST", "/v1/mirrors/create"),
        ("DELETE", "/v1/mirrors/x"),
        ("GET", "/v1/mirrors/status"),
    ],
)
def test_inventory_rejects_mutating_or_unknown_routes(monkeypatch, method, path):
    opener = Mock()
    monkeypatch.setattr(cli.urllib.request, "build_opener", Mock(return_value=opener))
    with pytest.raises(cli.InstallError, match="read-only"):
        cli.peerdb_transport("http://flow:8113", cli.Deadline(10))(method, path)
    opener.open.assert_not_called()


@pytest.mark.parametrize("body", [b"invalid JSON", b"x" * 1048577])
def test_inventory_failure_never_becomes_empty_success(monkeypatch, body):
    opener = Mock()
    opener.open.return_value = io.BytesIO(body)
    monkeypatch.setattr(cli.urllib.request, "build_opener", Mock(return_value=opener))
    with pytest.raises(cli.InstallError, match="inventory request failed"):
        cli.peerdb_transport("http://flow:8113", cli.Deadline(10))(
            "GET", "/v1/mirrors/list"
        )
    opener.open.assert_called_once()


def test_inventory_transport_uses_no_proxy_or_redirect(monkeypatch):
    opener = Mock()
    opener.open.return_value = io.BytesIO(b'{"mirrors": []}')
    factory = Mock(return_value=opener)
    monkeypatch.setattr(cli.urllib.request, "build_opener", factory)
    assert cli.peerdb_transport("http://flow:8113", cli.Deadline(10))(
        "GET", "/v1/mirrors/list"
    ) == {"mirrors": []}
    assert factory.call_args.args[0].proxies == {}
    with pytest.raises(cli.InstallError, match="redirect"):
        factory.call_args.args[1].redirect_request()


@pytest.mark.parametrize("ready", [False, True])
def test_main_returns_nonzero_when_incomplete(monkeypatch, capsys, ready):
    monkeypatch.setattr(
        cli.Config, "from_env", Mock(return_value=cli.Config.from_env({}))
    )
    monkeypatch.setattr(cli, "_wall_clock_limit", lambda *_: nullcontext())
    run = Mock(return_value={"ready": ready, "applied": False})
    monkeypatch.setattr(cli, "run", run)
    assert cli.main([]) == (0 if ready else 1)
    assert json.loads(capsys.readouterr().out)["ready"] is ready
    assert run.call_args.kwargs["apply"] is False


def test_main_sanitizes_driver_error_and_never_retries(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.Config, "from_env", Mock(return_value=cli.Config.from_env({}))
    )
    monkeypatch.setattr(cli, "_wall_clock_limit", lambda *_: nullcontext())
    run = Mock(side_effect=RuntimeError("password=secret; customer-value"))
    monkeypatch.setattr(cli, "run", run)
    assert cli.main(["--apply"]) == 1
    run.assert_called_once()
    out = capsys.readouterr()
    assert (
        "RuntimeError" in out.err
        and "secret" not in out.err
        and "customer-value" not in out.err
    )
