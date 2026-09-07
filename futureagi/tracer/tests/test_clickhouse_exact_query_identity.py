"""Exact server statement IDs do not change native retry or read-only policy."""

from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.client import ClickHouseClient


def bound_client(monkeypatch, *, readonly=False):
    client = ClickHouseClient(
        host="clickhouse.invalid",
        port=9000,
        database="catalog_test",
        user="catalog_test",
        password="",
        server_enforced_readonly=readonly,
    )
    native = Mock()
    native.execute.return_value = 1
    acquire, release = Mock(return_value=native), Mock()
    monkeypatch.setattr(client, "_get_client", acquire)
    monkeypatch.setattr(client, "_return_client", release)
    return client, native, acquire, release


def test_exact_query_id_reaches_native_transport_unchanged(monkeypatch):
    client, native, _, release = bound_client(monkeypatch)
    rows, settings = [(1,)], {"async_insert": 0, "insert_deduplication_token": "token"}
    result = client.execute(
        "INSERT INTO catalog_test.rows (id) VALUES",
        rows,
        settings=settings,
        query_id="catalog-write-41ca7c48-3bda-4c91-8a60-c11dc35414f1",
    )
    assert result == 1
    native.execute.assert_called_once_with(
        "INSERT INTO catalog_test.rows (id) VALUES",
        rows,
        with_column_types=False,
        settings=settings,
        query_id="catalog-write-41ca7c48-3bda-4c91-8a60-c11dc35414f1",
    )
    release.assert_called_once_with(native)


def test_omitted_identifier_preserves_old_native_call_signature(monkeypatch):
    client, native, _, _ = bound_client(monkeypatch)
    native.execute.return_value = ([(1,)], [("value", "UInt8")])
    client.execute("SELECT 1", {}, True, {"max_threads": 1})
    native.execute.assert_called_once_with(
        "SELECT 1", {}, with_column_types=True, settings={"max_threads": 1}
    )


def test_response_loss_propagates_without_reusing_exact_query_id(monkeypatch):
    client, native, _, release = bound_client(monkeypatch)
    error = TimeoutError("native response lost; server outcome unknown")
    native.execute.side_effect = error
    with pytest.raises(TimeoutError) as raised:
        client.execute("INSERT INTO catalog_test.rows VALUES", [(1,)], query_id="one")
    assert raised.value is error
    assert native.execute.call_count == 1
    release.assert_called_once_with(native)


def test_query_identity_cannot_bypass_readonly_write_guard(monkeypatch):
    client, native, acquire, _ = bound_client(monkeypatch, readonly=True)
    with pytest.raises(RuntimeError, match="Only read statements"):
        client.execute("INSERT INTO catalog_test.rows VALUES", [(1,)], query_id="one")
    acquire.assert_not_called()
    native.execute.assert_not_called()


def test_readonly_statement_still_strips_settings_with_query_id(monkeypatch):
    client, native, _, _ = bound_client(monkeypatch, readonly=True)
    client.execute("SELECT 1 SETTINGS max_threads = 8", query_id="observed-read")
    native.execute.assert_called_once_with(
        "SELECT 1", {}, with_column_types=False, settings=None, query_id="observed-read"
    )


@pytest.mark.parametrize("invalid", [True, 1, "", "x" * 1025, "x\n", "x\r", "x\0"])
def test_invalid_query_id_rejected_before_native_acquisition(monkeypatch, invalid):
    client, native, acquire, _ = bound_client(monkeypatch)
    with pytest.raises(ValueError, match="query_id"):
        client.execute("SELECT 1", query_id=invalid)
    acquire.assert_not_called()
    native.execute.assert_not_called()
