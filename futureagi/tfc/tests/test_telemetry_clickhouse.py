"""ClickHouse clients created by the shared factories emit query spans."""

from unittest import mock

import pytest
from clickhouse_connect.driver.query import QueryResult
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from tfc.telemetry import config, instrumentation
from tracer.services.clickhouse import application_read_transport
from tracer.services.clickhouse import client as client_module
from tracer.services.clickhouse.client import ClickHouseClient


@pytest.fixture
def exporter(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        instrumentation, "_clickhouse_tracer", provider.get_tracer("tfc.clickhouse")
    )
    monkeypatch.setattr(config, "OTEL_ENABLED", True)
    return exporter


class _Driver:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.fail = False

    def execute(self, query, params=None, **kwargs):
        if self.fail:
            raise RuntimeError("boom")
        rows = [(1,), (2,), (3,)]
        if kwargs.get("with_column_types"):
            return rows, [("x", "UInt8")]
        return rows

    def execute_iter(self, query, params=None, **kwargs):
        yield (1,)
        if self.fail:
            raise RuntimeError("boom")
        yield (2,)


def _native_client(monkeypatch) -> ClickHouseClient:
    monkeypatch.setattr(client_module, "CHDriver", _Driver)
    monkeypatch.setattr(client_module, "CLICKHOUSE_AVAILABLE", True)
    return ClickHouseClient(
        host="clickhouse.invalid", port=9000, user="u", password="", database="db"
    )


@pytest.mark.unit
def test_native_pool_client_query_produces_span(exporter, monkeypatch):
    driver = _native_client(monkeypatch)._create_client()
    long_query = "SELECT " + "x" * 2000

    assert driver.execute(long_query, {"a": 1}) == [(1,), (2,), (3,)]

    (span,) = exporter.get_finished_spans()
    assert span.name == "clickhouse.select"
    assert span.kind is SpanKind.CLIENT
    assert span.attributes["db.system"] == "clickhouse"
    assert span.attributes["db.rows_returned"] == 3
    assert span.attributes["db.operation"] == "SELECT"
    assert span.attributes["db.statement"] == long_query[:1000]
    assert span.status.status_code is StatusCode.UNSET
    assert driver._otel_instrumented is True


@pytest.mark.unit
def test_with_column_types_counts_rows_not_tuple_parts(exporter, monkeypatch):
    driver = _native_client(monkeypatch)._create_client()

    rows, _types = driver.execute("SELECT 1", with_column_types=True)

    (span,) = exporter.get_finished_spans()
    assert span.attributes["db.rows_returned"] == len(rows) == 3


@pytest.mark.unit
def test_native_pool_client_error_marks_span_error(exporter, monkeypatch):
    driver = _native_client(monkeypatch)._create_client()
    driver.fail = True

    with pytest.raises(RuntimeError):
        driver.execute("INSERT INTO t VALUES (1)")

    (span,) = exporter.get_finished_spans()
    assert span.name == "clickhouse.insert"
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "RuntimeError"


@pytest.mark.unit
def test_execute_iter_span_ends_when_rows_are_consumed(exporter, monkeypatch):
    driver = _native_client(monkeypatch)._create_client()

    rows = driver.execute_iter("SELECT 1")
    assert exporter.get_finished_spans() == ()
    assert list(rows) == [(1,), (2,)]

    (span,) = exporter.get_finished_spans()
    assert span.name == "clickhouse.select"
    assert span.kind is SpanKind.CLIENT
    assert span.status.status_code is StatusCode.UNSET


@pytest.mark.unit
def test_execute_iter_error_while_consuming_marks_span_error(exporter, monkeypatch):
    driver = _native_client(monkeypatch)._create_client()
    driver.fail = True

    rows = driver.execute_iter("SELECT 1")
    assert next(rows) == (1,)
    with pytest.raises(RuntimeError):
        next(rows)

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "RuntimeError"


@pytest.mark.unit
def test_stream_call_failure_marks_span_error(exporter):
    class Broken:
        def execute_iter(self, query, params=None):
            raise ConnectionError("refused")

    client = instrumentation.instrument_clickhouse_client(Broken())

    with pytest.raises(ConnectionError):
        client.execute_iter("SELECT 1")

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "ConnectionError"


@pytest.mark.unit
def test_singleton_client_is_instrumented(exporter, monkeypatch):
    from tfc.utils import clickhouse as utils_module

    monkeypatch.setattr(utils_module, "Client", _Driver)
    singleton = utils_module.ClickHouseClientSingleton()

    singleton._client.execute("SELECT 1")

    assert singleton._client._otel_instrumented is True
    assert [s.name for s in exporter.get_finished_spans()] == ["clickhouse.select"]


def _http_client():
    http_client = mock.Mock(
        spec=["query", "command", "query_row_block_stream", "timeout"]
    )
    # Built like the HTTP client does: rows materialize from column blocks.
    http_client.query = mock.Mock(
        return_value=QueryResult(block_gen=iter([[[1, 2]]]), column_names=("x",))
    )
    http_client.command = mock.Mock(return_value="OK")
    stream = mock.MagicMock()
    stream.__enter__.return_value = iter([[(1,)], [(2,)]])
    http_client.query_row_block_stream = mock.Mock(return_value=stream)
    with mock.patch("clickhouse_connect.get_client", return_value=http_client):
        return application_read_transport.create_application_read_http_client(
            host="clickhouse.invalid"
        )


@pytest.mark.unit
def test_http_application_client_query_produces_span(exporter):
    client = _http_client()

    result = client.query("SELECT 1", parameters={})

    client.query.__wrapped__.assert_called_once_with("SELECT 1", parameters={})
    assert result.result_rows == [(1,), (2,)]
    (span,) = exporter.get_finished_spans()
    assert span.name == "clickhouse.select"
    assert span.kind is SpanKind.CLIENT
    assert span.attributes["db.system"] == "clickhouse"
    assert span.attributes["db.rows_returned"] == 2


@pytest.mark.unit
def test_http_application_client_command_records_no_row_count(exporter):
    client = _http_client()

    assert client.command("OPTIMIZE TABLE t") == "OK"

    (span,) = exporter.get_finished_spans()
    assert span.name == "clickhouse.optimize"
    assert "db.rows_returned" not in span.attributes


@pytest.mark.unit
def test_http_application_client_block_stream_span_ends_on_exit(exporter):
    client = _http_client()

    with client.query_row_block_stream("SELECT 2", parameters={}) as stream:
        assert exporter.get_finished_spans() == ()
        assert list(stream) == [[(1,)], [(2,)]]

    (span,) = exporter.get_finished_spans()
    assert span.name == "clickhouse.select"
    assert span.attributes["db.statement"] == "SELECT 2"


@pytest.mark.unit
def test_instrumentation_is_noop_when_otel_disabled(monkeypatch):
    monkeypatch.setattr(config, "OTEL_ENABLED", False)
    driver = _Driver()
    original = driver.execute

    assert instrumentation.instrument_clickhouse_client(driver) is driver
    assert driver.execute == original
    assert not hasattr(driver, "_otel_instrumented")


@pytest.mark.unit
def test_instrumentation_is_idempotent(exporter):
    driver = instrumentation.instrument_clickhouse_client(_Driver())
    wrapped = driver.execute

    assert instrumentation.instrument_clickhouse_client(driver).execute is wrapped
