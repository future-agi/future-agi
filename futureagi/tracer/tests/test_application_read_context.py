"""The analytics policy must not weaken the shared client's maintenance lane."""

from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.application_read_policy import (
    application_read_context,
    is_application_read,
)
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.query_service import AnalyticsQueryService


def _client(monkeypatch, **kwargs):
    client = ClickHouseClient(host="localhost", port=19999, pool_size=1, **kwargs)
    native = Mock()
    native.execute.return_value = ([(1,)], [("value", "UInt8")])
    monkeypatch.setattr(client, "_get_client", lambda: native)
    monkeypatch.setattr(client, "_return_client", Mock())
    return client, native


def test_shared_native_client_keeps_diagnostic_budgets_after_application_read(
    monkeypatch,
):
    client, native = _client(monkeypatch)
    service = AnalyticsQueryService(ch_client=client)
    sql = "SELECT 1 AS value LIMIT 25"
    service.execute_ch_query(sql, timeout_ms=1, settings={"max_result_rows": 1})
    assert native.execute.call_args.args[0] == sql  # SQL pagination is unchanged.
    app = native.execute.call_args.kwargs["settings"]
    assert app["max_execution_time"] == app["max_result_rows"] == 0
    assert app["max_memory_usage"] > 0
    assert not is_application_read()

    client.execute_read("SELECT 1", timeout_ms=123, settings={"max_result_rows": 2})
    diagnostic = native.execute.call_args.kwargs["settings"]
    assert diagnostic["max_execution_time"] == 0.123
    assert diagnostic["max_result_rows"] == 2
    assert diagnostic["max_bytes_to_read"] > 0


@pytest.mark.parametrize("locked", [False, True])
def test_application_read_does_not_install_a_native_statement_wall(monkeypatch, locked):
    client, native = _client(monkeypatch, server_enforced_readonly=locked)
    execute = Mock(return_value=([(1,)], [("value", "UInt8")]))
    monkeypatch.setattr(client, "_execute_native_read_with_remaining_timeout", execute)
    AnalyticsQueryService(ch_client=client).execute_ch_query("SELECT 1", timeout_ms=250)
    assert execute.call_args.kwargs["timeout_ms"] is None
    if locked:
        assert execute.call_args.kwargs["query_settings"] is None
    else:
        assert execute.call_args.kwargs["query_settings"]["max_execution_time"] == 0
    assert client._read_admission.acquire(blocking=False)
    client._read_admission.release()


def test_application_context_and_admission_restore_after_exception(monkeypatch):
    client, native = _client(monkeypatch)
    native.execute.side_effect = RuntimeError("test failure")
    with pytest.raises(RuntimeError, match="test failure"):
        AnalyticsQueryService(ch_client=client).execute_ch_query("SELECT 1")
    assert not is_application_read()
    assert client._read_admission.acquire(blocking=False)
    client._read_admission.release()


def test_nested_application_context_restores_outer_value():
    assert not is_application_read()
    with application_read_context():
        with application_read_context():
            assert is_application_read()
        assert is_application_read()
    assert not is_application_read()


def test_explicit_diagnostic_context_restores_application_mode_after_error():
    with application_read_context():
        with pytest.raises(RuntimeError, match="test error"):
            with application_read_context(False):
                assert not is_application_read()
                raise RuntimeError("test error")
        assert is_application_read()
    assert not is_application_read()


def test_application_admission_stays_bounded_without_statement_timeout(monkeypatch):
    client, native = _client(monkeypatch)
    admission = Mock()
    admission.acquire.return_value = False
    monkeypatch.setattr(client, "_read_admission", admission)
    with pytest.raises(TimeoutError) as raised:
        AnalyticsQueryService(ch_client=client).execute_ch_query("SELECT 1")
    assert "admission" in str(raised.value.__cause__)
    assert admission.acquire.call_args.kwargs["timeout"] > 0
    native.execute.assert_not_called()
    admission.release.assert_not_called()
