from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from tracer.sql_workbench import runner
from tracer.sql_workbench.catalog import Scope
from tracer.sql_workbench.provision import direct_access_ddl


def test_scalar_output_is_safe_and_lossless():
    assert runner.cell(float("nan")) is None
    assert runner.cell(float("inf")) is None
    assert runner.cell(2**64 - 1) == str(2**64 - 1)
    assert runner.cell(Decimal("1.1234567890123456789")) == "1.1234567890123456789"
    assert runner.cell(True) is True
    assert runner.cell({"locale": "en"}) == '{"locale": "en"}'


def test_unconfigured_or_admin_connection_is_never_used(monkeypatch):
    from django.test import override_settings

    monkeypatch.delenv("SQL_WORKBENCH_CH_USER", raising=False)
    monkeypatch.delenv("SQL_WORKBENCH_CH_PASSWORD", raising=False)
    with override_settings(CLICKHOUSE_V2={"CH25_USER": "admin"}):
        with pytest.raises(runner.QueryUnavailable):
            runner.connection_config()
        for username in ["admin", "default"]:
            monkeypatch.setenv("SQL_WORKBENCH_CH_USER", username)
            monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", "secret")
            with pytest.raises(runner.QueryUnavailable):
                runner.connection_config()


FILTERS = "{'analytics.spans': 'project_id = toUUID(\\'1\\')'}"


class Client:
    def __init__(self, records):
        self.records = records
        self.cancelled = False
        self.closed = False
        from types import SimpleNamespace

        self.connection = SimpleNamespace(send_cancel=self.send_cancel)

    def execute(self, query):
        return [list(runner.QUERY_SETTINGS.values()) + [2]]

    def execute_iter(self, query, **kwargs):
        assert kwargs["with_column_types"] is True
        assert kwargs["query_id"]
        # The scope filter is the only per-query setting ever sent.
        assert kwargs["settings"] == {"additional_table_filters": FILTERS}
        return iter(self.records)

    def send_cancel(self):
        self.cancelled = True

    def disconnect(self):
        self.closed = True


def test_result_rows_are_bounded_and_connection_closed(monkeypatch):
    monkeypatch.setattr(runner, "MAX_RESULT_ROWS", 2)
    client = Client([[("n", "Int64")], (1,), (2,), (3,)])
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(runner, "connection_config", return_value={}),
    ):
        result = runner.run(
            "SELECT count() FROM scoped", uuid4(), lambda: False, table_filters=FILTERS
        )
    assert result["rows"] == [[1], [2]]
    assert result["truncated"] is True
    assert client.cancelled and client.closed


def test_output_bytes_are_bounded(monkeypatch):
    monkeypatch.setattr(runner, "MAX_RESULT_BYTES", 100)
    client = Client([[("text", "String")], ("x" * 101,)])
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(runner, "connection_config", return_value={}),
    ):
        with pytest.raises(runner.QueryUnavailable):
            runner.run(
                "SELECT name FROM scoped", uuid4(), lambda: False, table_filters=FILTERS
            )
    assert client.closed


def test_cancelled_job_never_starts_query():
    client = Client([])
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(runner, "connection_config", return_value={}),
    ):
        with pytest.raises(runner.QueryCancelled):
            runner.run(
                "SELECT id FROM scoped", uuid4(), lambda: True, table_filters=FILTERS
            )
    assert client.closed


def test_query_budgets_include_each_resource():
    for key in [
        "max_rows_to_read",
        "max_bytes_to_read",
        "max_memory_usage",
        "max_bytes_in_join",
        "max_execution_time",
        "max_result_rows",
        "max_result_bytes",
        "max_threads",
    ]:
        assert runner.QUERY_SETTINGS[key] > 0
    assert runner.QUERY_SETTINGS["readonly"] == 1
    for key in [
        "read_overflow_mode",
        "timeout_overflow_mode",
        "result_overflow_mode",
        "join_overflow_mode",
    ]:
        assert runner.QUERY_SETTINGS[key] == "throw"


def test_direct_access_has_per_table_policies_and_no_base_grants_to_login():
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    scope = Scope(uuid4(), uuid4(), uuid4(), now - timedelta(days=1), now, (uuid4(),))
    statements = direct_access_ddl(
        scope,
        database="analytics",
        principal="bi_reader",
        password="secret" * 5,
        cidr="10.3.0.0/24",
    )
    text = "\n".join(statements)
    assert "secretsecret" not in text
    assert "HOST NONE" in text and "HOST IP '10.3.0.0/24'" in text
    assert text.count("CREATE ROW POLICY") == 6
    assert text.count("AS RESTRICTIVE TO sqld_bi_reader") == 6
    assert text.count("SQL SECURITY DEFINER") == 6
    assert "readonly = 1 READONLY" in text
    login_grants = [
        s for s in statements if s.startswith("GRANT") and s.endswith(" TO bi_reader")
    ]
    assert len(login_grants) == 6
    assert all("ON sql_bi_reader." in s for s in login_grants)
    assert not any("ALL" in s or "*" in s for s in login_grants)


@pytest.mark.parametrize(
    "field,value",
    [
        ("principal", "x; GRANT ALL ON *.*"),
        ("database", "x.y"),
        ("cidr", "0.0.0.0/0"),
        ("password", "short"),
    ],
)
def test_direct_access_rejects_malicious_or_open_configuration(field, value):
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    scope = Scope(uuid4(), uuid4(), uuid4(), now - timedelta(days=1), now)
    kwargs = dict(
        database="analytics",
        principal="bi_reader",
        password="secret" * 5,
        cidr="10.3.0.0/24",
    )
    kwargs[field] = value
    with pytest.raises(ValueError):
        direct_access_ddl(scope, **kwargs)


def test_loose_server_profile_fails_closed():
    client = Client([])
    client.execute = lambda _: [[0] * (len(runner.QUERY_SETTINGS) + 1)]
    with pytest.raises(runner.QueryUnavailable):
        runner.verify_profile(client)


def test_watchdog_cancellation_has_exactly_one_socket_reader():
    import threading
    from tracer.sql_workbench.runner import QueryCancelled

    reading = threading.Event()
    signalled = threading.Event()
    readers = set()
    main_thread = threading.get_ident()

    class BlockingClient(Client):
        def execute_iter(self, query, **kwargs):
            def stream():
                readers.add(threading.get_ident())
                yield [("n", "Int64")]
                reading.set()
                assert signalled.wait(3), "watchdog did not send native cancel"
                yield (1,)

            return stream()

        def cancel(self):
            raise AssertionError("Client.cancel drains the stream and must never run")

        def send_cancel(self):
            assert threading.get_ident() != main_thread
            self.cancelled = True
            signalled.set()

    client = BlockingClient([])
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(runner, "connection_config", return_value={}),
    ):
        with pytest.raises(QueryCancelled):
            runner.run(
                "SELECT n FROM bounded",
                uuid4(),
                reading.is_set,
                table_filters=FILTERS,
            )
    assert readers == {main_thread}
    assert client.cancelled and client.closed


def test_temporal_dispatch_targets_shipped_exact_aggregation_queue():
    from tracer.sql_workbench.tasks import execute_sql
    from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES

    assert "tracer.sql_workbench.tasks" in TEMPORAL_ACTIVITY_MODULES
    assert execute_sql._is_temporal_activity
    assert execute_sql._metadata["queue"] == "exact_aggregation"
    assert execute_sql._metadata["max_retries"] == 0
    with patch(
        "tfc.temporal.drop_in.runner.start_activity", return_value="sql-query-test"
    ) as start:
        execute_sql.apply_async(
            args=["job"], task_id="sql-query-test", dispatch_timeout_seconds=5
        )
    assert start.call_args.args[0] == "tracer.sql_workbench.execute"
    assert start.call_args.kwargs["queue"] == "exact_aggregation"
    assert start.call_args.kwargs["dispatch_timeout_seconds"] == 5


def test_metrics_probe_is_explicitly_unavailable_not_empty(monkeypatch):
    monkeypatch.setenv("SQL_WORKBENCH_DERIVED_METRICS_ENABLED", "true")
    client = Client([])

    def execute(query):
        if "getSetting" in query:
            return [list(runner.QUERY_SETTINGS.values()) + [2]]
        assert "derived_metrics LIMIT 0" in query
        raise RuntimeError("missing view, private server internals")

    client.execute = execute
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(
            runner, "connection_config", return_value={"database": "analytics"}
        ),
    ):
        available, reason = runner.derived_metrics_availability()
    assert not available
    assert "unavailable" in reason
    assert "private server internals" not in reason
    assert client.closed


@pytest.mark.parametrize("filters", [None, "", "{}", "project_id = 1"])
def test_runner_refuses_to_execute_without_scope_filters(filters):
    client = Client([[("n", "Int64")], (1,)])
    with (
        patch("clickhouse_driver.Client", return_value=client) as connect,
        patch.object(runner, "connection_config", return_value={}),
    ):
        with pytest.raises(runner.QueryUnavailable):
            runner.run("SELECT 1", uuid4(), lambda: False, table_filters=filters)
    assert not connect.called


def test_profile_without_changeable_filter_fails_closed_with_guidance():
    class ReadonlyRejection(Exception):
        code = 164

    class Rejecting(Client):
        def execute_iter(self, query, **kwargs):
            raise ReadonlyRejection("Cannot modify setting in readonly mode")

    client = Rejecting([])
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(runner, "connection_config", return_value={}),
    ):
        with pytest.raises(runner.QueryUnavailable, match="locked read-only"):
            runner.run("SELECT 1", uuid4(), lambda: False, table_filters=FILTERS)
    assert client.closed


def _no_sleep(monkeypatch):
    """Skip the runner's retry pauses without touching time.sleep elsewhere."""
    import time
    from types import SimpleNamespace

    monkeypatch.setattr(
        runner, "time", SimpleNamespace(monotonic=time.monotonic, sleep=lambda _: None)
    )


class SimultaneousQueries(Exception):
    code = runner.TOO_MANY_SIMULTANEOUS_QUERIES


class Contended(Client):
    """Turned away by the principal's concurrency cap ``busy`` times first."""

    def __init__(self, records, *, busy, code=runner.TOO_MANY_SIMULTANEOUS_QUERIES):
        super().__init__(records)
        self.busy, self.code, self.sent, self.disconnects = busy, code, 0, 0

    def execute_iter(self, query, **kwargs):
        stream = super().execute_iter(query, **kwargs)
        self.sent += 1
        if self.sent <= self.busy:
            error = SimultaneousQueries("too many simultaneous queries")
            error.code = self.code

            def rejected():
                raise error
                yield  # pragma: no cover

            return rejected()
        return stream

    def disconnect(self):
        self.disconnects += 1
        super().disconnect()


def test_a_run_turned_away_by_the_principal_cap_retries_briefly(monkeypatch):
    """An advisory estimate may hold one of the principal's slots for a moment;
    the run's first statement read nothing, so it is retried, not failed."""
    _no_sleep(monkeypatch)
    client = Contended([[("n", "Int64")], (7,)], busy=2)
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(runner, "connection_config", return_value={}),
    ):
        result = runner.run(
            "SELECT n FROM scoped", uuid4(), lambda: False, table_filters=FILTERS
        )
    assert result["rows"] == [[7]]
    assert client.sent == 3
    # Reconnected before each retry, then closed once at the end.
    assert client.disconnects == 3


def test_a_run_still_contended_after_the_retry_window_fails(monkeypatch):
    monkeypatch.setattr(runner, "START_RETRY_SECONDS", 0.3)
    client = Contended([[("n", "Int64")], (1,)], busy=10_000)
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(runner, "connection_config", return_value={}),
    ):
        with pytest.raises(SimultaneousQueries):
            runner.run(
                "SELECT n FROM scoped", uuid4(), lambda: False, table_filters=FILTERS
            )
    assert 1 < client.sent < 10
    assert client.closed


def test_other_server_errors_are_never_retried(monkeypatch):
    _no_sleep(monkeypatch)
    client = Contended([[("n", "Int64")], (1,)], busy=1, code=241)
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(runner, "connection_config", return_value={}),
    ):
        with pytest.raises(SimultaneousQueries):
            runner.run(
                "SELECT n FROM scoped", uuid4(), lambda: False, table_filters=FILTERS
            )
    assert client.sent == 1


def test_a_cancel_during_the_retry_window_stops_the_run(monkeypatch):
    _no_sleep(monkeypatch)
    client = Contended([[("n", "Int64")], (1,)], busy=10_000)
    checks = iter([False, True])
    with (
        patch("clickhouse_driver.Client", return_value=client),
        patch.object(runner, "connection_config", return_value={}),
    ):
        with pytest.raises(runner.QueryCancelled):
            runner.run(
                "SELECT n FROM scoped",
                uuid4(),
                lambda: next(checks, True),
                table_filters=FILTERS,
            )
    assert client.sent == 1


def test_service_profile_allows_only_the_scope_filter_to_change():
    from tracer.sql_workbench.provision import service_access_ddl

    statement = service_access_ddl(
        database="analytics",
        principal="sql_service",
        password="secret" * 5,
        cidr="10.3.0.0/24",
    )[0]
    assert "additional_table_filters CHANGEABLE_IN_READONLY" in statement
    assert statement.count("CHANGEABLE_IN_READONLY") == 1
    assert "readonly = 1 READONLY" in statement
