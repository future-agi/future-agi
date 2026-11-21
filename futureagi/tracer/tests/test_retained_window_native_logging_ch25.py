"""Retained-window logs and exported spans at the native driver/server boundary."""

import logging
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from types import SimpleNamespace

import pytest
from clickhouse_driver.errors import ServerException
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import StatusCode
from structlog.testing import capture_logs

from conftest import _ch_test_native_client, _ch_test_owned_database
from tfc.telemetry import config, instrumentation
from tracer.services.clickhouse.attribute_reads import _GLOBAL_RETAINED_START_SQL
from tracer.services.clickhouse.client import ClickHouseClient


@pytest.mark.integration
@pytest.mark.parametrize("server_error", [False, True], ids=["success", "failure"])
def test_only_retained_driver_call_omits_query_logs(monkeypatch, caplog, server_error):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(config, "OTEL_ENABLED", True)
    monkeypatch.setattr(
        instrumentation, "_clickhouse_tracer", provider.get_tracer("tfc.clickhouse")
    )
    # A string is not a valid argument to fromUnixTimestamp64Micro. The error
    # case must reach the server after the driver has sent the bound query.
    bound = "invalid-retained-timestamp" if server_error else 1763512345678901
    owner = ClickHouseClient(pool_size=2, server_enforced_readonly=False)
    with (
        _ch_test_owned_database("test_retained_logs_") as db,
        _ch_test_native_client(database=db) as metadata,
        _ch_test_native_client(database=db) as ordinary,
        ThreadPoolExecutor(max_workers=1) as worker,
    ):
        owner._return_client(instrumentation.instrument_clickhouse_client(metadata))
        owner._return_client(instrumentation.instrument_clickhouse_client(ordinary))
        receive = metadata.receive_result

        def receive_after_control(*args, **kwargs):
            # send_query has run, but the retained read hasn't received its
            # result yet. Collect the ordinary query from a different thread.
            control = worker.submit(owner.execute_read, "SELECT 23 AS concurrent_read")
            assert control.result(timeout=5)[0] == [(23,)]
            return receive(*args, **kwargs)

        def lookup():
            return owner.execute_read_with_progress(
                _GLOBAL_RETAINED_START_SQL,
                {"window_end_us": bound},
                retained_until=monotonic() + owner.read_timeout_ceiling_ms / 1000,
            )

        monkeypatch.setattr(metadata, "receive_result", receive_after_control)
        caplog.set_level(logging.DEBUG, logger="clickhouse_driver")
        caplog.clear()
        with capture_logs() as wrapper_logs:
            if server_error:
                with pytest.raises(ServerException, match="fromUnixTimestamp64Micro"):
                    lookup()
            else:
                assert lookup()[0] == [(None,)]  # No parts in this database.
            # Same calling context after the retained read unwinds.
            assert owner.execute_read("SELECT 31 AS subsequent_read")[0] == [(31,)]
            # FIFO: this ordinary error reuses the formerly quiet client.
            monkeypatch.setattr(metadata, "receive_result", receive)
            ordinary_error = "SELECT throwIf(1, 'ordinary-error-control')"
            with pytest.raises(ServerException, match="ordinary-error-control"):
                owner.execute_read(ordinary_error)

        spans = exporter.get_finished_spans()
        assert [span.attributes["db.statement"] for span in spans] == [
            "SELECT 23 AS concurrent_read",
            "SELECT 31 AS subsequent_read",
            ordinary_error,
        ]
        assert spans[-1].status.status_code is StatusCode.ERROR
        assert "ordinary-error-control" in spans[-1].status.description
        assert "ordinary-error-control" in spans[-1].attributes["error.message"]

        query_messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "clickhouse_driver.connection"
            and record.getMessage().startswith("Query:")
        ]
        for marker in ("concurrent_read", "subsequent_read"):
            assert any(marker in message for message in query_messages), marker
        for marker in (
            "system.parts",
            "retained_start",
            "fromUnixTimestamp64Micro",
            str(bound),
        ):
            assert marker not in caplog.text
            assert marker not in repr(wrapper_logs)
            assert all(marker not in span.to_json() for span in spans)


@pytest.mark.integration
@pytest.mark.parametrize("reconnect", [False, True], ids=["ping", "reconnect"])
@pytest.mark.parametrize("expires", [False, True], ids=["shrink", "skip"])
def test_retained_dispatch_uses_time_left_after_connection_check(
    monkeypatch, reconnect, expires
):
    from tracer.services.clickhouse import client as client_module

    # The caller allocated 225 ms. Include time already spent outside the
    # native wrapper, then advance only our clock during actual driver I/O.
    now = [0.025]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    owner = ClickHouseClient(pool_size=1, server_enforced_readonly=False)
    with _ch_test_native_client() as native:
        owner._return_client(native)
        connection = native.connection  # Warm from the fixture's sidecar check.
        ping, connect, send = connection.ping, connection.connect, connection.send_query
        health, dispatch = [], []
        sync = connection.sync_request_timeout
        connect_timeout = connection.connect_timeout
        policy = connection.send_receive_timeout
        original_socket_timeout = connection.socket.gettimeout()

        def delayed_ping():
            health.append("ping")
            assert 0 < connection.sync_request_timeout <= 0.201
            assert ping()
            if reconnect:
                return False  # force_connect must reconnect before sending.
            now[0] = 0.24 if expires else 0.125
            return True

        def delayed_connect():
            health.append("connect")
            assert 0 < connection.connect_timeout <= 0.201
            connect()
            now[0] = 0.24 if expires else 0.125

        def record_send(sql, *args, **kwargs):
            dispatch.append(
                (
                    connection.context.settings["max_execution_time"],
                    connection.socket.gettimeout(),
                )
            )
            return send(sql, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(client_module, "time", clock)
            patch.setattr(connection, "ping", delayed_ping)
            patch.setattr(connection, "connect", delayed_connect)
            patch.setattr(connection, "send_query", record_send)

            def lookup():
                return owner.execute_read_with_progress(
                    "SELECT toFloat64(getSetting('max_execution_time'))",
                    timeout_ms=225,
                    retained_until=0.225,
                )

            if expires:
                with pytest.raises(TimeoutError, match="deadline exhausted"):
                    lookup()
                assert dispatch == []
            else:
                [(server_cap,)] = lookup()[0]
                assert server_cap == pytest.approx(0.1)
                assert len(dispatch) == 1
                assert dispatch[0] == pytest.approx((0.1, 0.1))
                expected = policy if reconnect else original_socket_timeout
                assert connection.socket.gettimeout() == expected
            assert health == (["ping", "connect"] if reconnect else ["ping"])
            assert connection.send_query is record_send
            assert connection.sync_request_timeout == sync
            assert connection.connect_timeout == connect_timeout
            assert connection.send_receive_timeout == policy

        # The same client dispatches with the ordinary server policy after
        # every outcome; it must not inherit the retained statement's cap.
        assert owner.execute_read(
            "SELECT toFloat64(getSetting('max_execution_time'))", timeout_ms=2000
        )[0] == [(2.0,)]
