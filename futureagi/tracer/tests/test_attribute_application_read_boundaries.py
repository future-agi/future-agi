"""Offline public/maintenance dispatch contracts; no database or SLO claims."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import override_settings

from tracer.services.clickhouse.application_read_policy import (
    UNLIMITED_STATEMENT_SETTINGS,
    application_read_context,
    is_application_read,
)
from tracer.services.clickhouse.attribute_reads import (
    AttributeReadSelector,
    V2AttributeQueryExecutor,
)
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.property_catalog.connection import (
    PropertyCatalogConnectionConfig,
    PropertyCatalogReadExecutor,
)

pytestmark = pytest.mark.unit
GiB = 1024**3
MAINTENANCE_MEMORY = 512 * 1024**2
CONFIG = PropertyCatalogConnectionConfig(
    host="catalog.invalid",
    port=9440,
    database="property_catalog_dev_clean",
    user="property_catalog_reader",
    password="test-only",
)
SQL = (
    "SELECT status FROM `property_catalog_dev_clean`.property_catalog_activations "
    "WHERE status = %(status)s LIMIT 25"
)
PARAMS = {"status": "ok"}


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    """Override integration DDL when this module joins the regular suite."""
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


class Socket:
    def __init__(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout


def _native_client(monkeypatch, *, original_timeout=10, locked=False, allow=False):
    owner = ClickHouseClient(
        host="clickhouse.invalid",
        pool_size=1,
        connect_timeout=3,
        receive_timeout=10,
        send_timeout=10,
        server_enforced_readonly=locked,
        allow_query_settings_with_server_readonly=allow,
    )
    connection = SimpleNamespace(
        connected=True,
        socket=Socket(original_timeout),
        connect_timeout=3,
        send_receive_timeout=original_timeout,
        sync_request_timeout=1,
    )
    native = Mock(
        connection=connection,
        last_query=SimpleNamespace(progress=SimpleNamespace(rows=25, bytes=1024)),
    )
    native.execute.return_value = ([("ok",)], [("status", "String")])
    monkeypatch.setattr(owner, "_get_client", Mock(return_value=native))
    monkeypatch.setattr(owner, "_return_client", Mock())
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
        lambda: owner,
    )
    return owner, native, connection


def _executor(lane, owner, *, application_read=None):
    options = {} if application_read is None else {"application_read": application_read}
    if lane == "raw":
        return V2AttributeQueryExecutor(owner, **options)
    return PropertyCatalogReadExecutor(
        config=CONFIG, client_factory=lambda _config: owner, clock=lambda: 0, **options
    )


def _request_settings():
    return {
        "max_memory_usage": MAINTENANCE_MEMORY,
        "max_execution_time": 0.001,
        "max_bytes_to_read": 1024,
        "max_result_rows": 2,
        "max_result_bytes": 1024,
        "max_threads": 1,
        "max_concurrent_queries_for_user": 2,
        "max_bytes_before_external_group_by": 32 * 1024**2,
        "max_bytes_before_external_sort": 32 * 1024**2,
    }


@pytest.mark.parametrize("lane", ["raw", "catalog"])
@pytest.mark.parametrize("ceiling", [256 * 1024**2, 8 * GiB, 36 * GiB, 48 * GiB])
@pytest.mark.parametrize("original_timeout", [10, None])
def test_public_dispatch_then_bounded_maintenance_restores_same_pool(
    monkeypatch, lane, ceiling, original_timeout
):
    owner, native, connection = _native_client(
        monkeypatch, original_timeout=original_timeout
    )
    # Use the actual default public-discovery constructor, not a forced context.
    public = (
        AttributeReadSelector().executor
        if lane == "raw"
        else _executor(lane, owner, application_read=True)
    )
    requested = _request_settings()
    if lane == "raw":
        requested.pop("max_memory_usage")
        requested["max_block_size"] = 8192
    original = dict(requested)

    def application_execute(sql, params, **kwargs):
        assert is_application_read()
        assert sql == SQL and params == PARAMS
        assert connection.send_receive_timeout is None
        assert connection.socket.gettimeout() is None
        assert connection.connect_timeout == 3
        assert connection.sync_request_timeout == 1
        applied = kwargs["settings"]
        assert all(applied[key] == 0 for key in UNLIMITED_STATEMENT_SETTINGS)
        expected_memory = ceiling if lane == "raw" else min(MAINTENANCE_MEMORY, ceiling)
        assert applied["max_memory_usage"] == expected_memory > 0
        for key in (
            "max_threads",
            "max_concurrent_queries_for_user",
            "max_bytes_before_external_group_by",
            "max_bytes_before_external_sort",
        ):
            assert applied[key] == requested[key]
        if lane == "raw":
            assert applied["max_block_size"] == 8192
        return [("ok",)], [("status", "String")]

    native.execute.side_effect = application_execute
    with override_settings(CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES=ceiling):
        page = public.execute(SQL, PARAMS, timeout_ms=1, settings=requested)
    assert page.data == [{"status": "ok"}]
    assert (page.read_rows, page.read_bytes) == (25, 1024)
    assert requested == original
    assert not is_application_read()
    assert connection.socket.gettimeout() == original_timeout
    assert connection.send_receive_timeout == original_timeout
    owner._return_client.assert_called_once_with(native)

    def maintenance_execute(_sql, _params, **kwargs):
        assert not is_application_read()  # Must override an outer public context.
        assert 0 < connection.socket.gettimeout() <= 0.123
        applied = kwargs["settings"]
        assert applied["max_execution_time"] == 0.123
        for key in (
            "max_memory_usage",
            "max_bytes_to_read",
            "max_result_rows",
            "max_result_bytes",
        ):
            assert applied[key] == maintenance_settings[key]
        return [("ok",)], [("status", "String")]

    native.execute.side_effect = maintenance_execute
    maintenance_settings = _request_settings()
    with application_read_context():
        _executor(lane, owner).execute(
            SQL, PARAMS, timeout_ms=123, settings=maintenance_settings
        )
        assert is_application_read()
    assert not is_application_read()
    assert connection.socket.gettimeout() == original_timeout
    assert connection.send_receive_timeout == original_timeout
    assert owner._return_client.call_count == 2
    assert owner._read_admission.acquire(blocking=False)
    owner._read_admission.release()


@pytest.mark.parametrize("progress", [False, True])
@pytest.mark.parametrize("application_read", [False, True])
def test_raw_facade_normalizes_all_statement_caps_and_preserves_progress(
    progress, application_read
):
    class BasicClient:
        def execute_read(self, query, params, *, timeout_ms, settings):
            assert is_application_read() is application_read
            assert query == SQL and params == PARAMS
            assert timeout_ms == (None if application_read else 123)
            assert all(
                settings[key] == (0 if application_read else 1)
                for key in UNLIMITED_STATEMENT_SETTINGS
            )
            # Raw optional probes may still request a smaller finite memory budget.
            assert settings["max_memory_usage"] == MAINTENANCE_MEMORY
            return [("ok",)], [("status", "String")], 2.5

    if progress:

        class ProgressClient(BasicClient):
            def execute_read_with_progress(self, *args, **kwargs):
                return (*super().execute_read(*args, **kwargs), 25, 1024)

        client = ProgressClient()
    else:
        client = BasicClient()
    requested = dict.fromkeys(UNLIMITED_STATEMENT_SETTINGS, 1)
    requested["max_memory_usage"] = MAINTENANCE_MEMORY
    original = dict(requested)
    with application_read_context():
        page = V2AttributeQueryExecutor(
            client, application_read=application_read
        ).execute(SQL, PARAMS, timeout_ms=123, settings=requested)
        assert is_application_read()
    assert not is_application_read()
    assert page.query_time_ms == 2.5
    assert page.read_rows == (25 if progress else None)
    assert page.read_bytes == (1024 if progress else None)
    assert requested == original


def test_raw_benchmark_optout_and_injected_executor_keep_bounded_policy(monkeypatch):
    owner, native, _ = _native_client(monkeypatch)
    injected = V2AttributeQueryExecutor(owner)
    selector = AttributeReadSelector(executor=injected)
    assert selector.executor is injected
    for executor in (
        selector.executor,
        AttributeReadSelector(application_read=False).executor,
    ):
        with application_read_context():
            executor.execute(SQL, PARAMS, timeout_ms=123, settings=_request_settings())
            assert is_application_read()
        applied = native.execute.call_args.kwargs["settings"]
        assert applied["max_execution_time"] == 0.123
        assert applied["max_memory_usage"] == MAINTENANCE_MEMORY
        assert applied["max_result_rows"] == 2
    assert not is_application_read()


@pytest.mark.parametrize("lane", ["raw", "catalog"])
@pytest.mark.parametrize("allow", [False, True])
def test_public_read_preserves_locked_server_profile_policy(monkeypatch, lane, allow):
    owner, native, _ = _native_client(monkeypatch, locked=True, allow=allow)
    with override_settings(CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES=8 * GiB):
        _executor(lane, owner, application_read=True).execute(
            SQL, PARAMS, timeout_ms=1, settings={}
        )
    applied = native.execute.call_args.kwargs["settings"]
    if allow:
        assert all(applied[key] == 0 for key in UNLIMITED_STATEMENT_SETTINGS)
        assert applied["max_memory_usage"] == 8 * GiB
        assert "readonly" not in applied
    else:
        # No claim that the client can lift constraints locked by the server.
        assert applied is None


@pytest.mark.parametrize("lane", ["raw", "catalog"])
@pytest.mark.parametrize("application_read", [False, True])
@pytest.mark.parametrize("failure_type", [RuntimeError, TimeoutError])
def test_failure_restores_outer_context_native_timeouts_and_admission(
    monkeypatch, lane, application_read, failure_type
):
    owner, native, connection = _native_client(monkeypatch, original_timeout=None)

    def fail(*_args, **_kwargs):
        assert is_application_read() is application_read
        raise failure_type("test-only failure")

    native.execute.side_effect = fail
    expected = (
        ReadDeadlineExceeded
        if lane == "raw" and failure_type is TimeoutError
        else failure_type
    )
    with application_read_context():
        with pytest.raises(expected):
            _executor(lane, owner, application_read=application_read).execute(
                SQL, PARAMS, timeout_ms=123, settings=_request_settings()
            )
        assert is_application_read()
    assert not is_application_read()
    assert connection.send_receive_timeout is None
    assert connection.socket.gettimeout() is None
    owner._return_client.assert_called_once_with(native)
    assert owner._read_admission.acquire(blocking=False)
    owner._read_admission.release()


@pytest.mark.parametrize("lane", ["raw", "catalog"])
@pytest.mark.parametrize("ceiling", [0, -1])
def test_invalid_application_memory_ceiling_never_dispatches(
    monkeypatch, lane, ceiling
):
    owner, native, _ = _native_client(monkeypatch)
    with override_settings(CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES=ceiling):
        with pytest.raises(ValueError, match="memory ceiling must be positive"):
            _executor(lane, owner, application_read=True).execute(
                SQL, PARAMS, timeout_ms=1, settings=_request_settings()
            )
    native.execute.assert_not_called()
    assert not is_application_read()


@pytest.mark.parametrize("value", [None, 1, "true"])
@pytest.mark.parametrize(
    "constructor", [AttributeReadSelector, V2AttributeQueryExecutor]
)
def test_raw_application_mode_requires_explicit_bool_before_client_acquisition(
    monkeypatch, value, constructor
):
    factory = Mock(side_effect=AssertionError("must not acquire a client"))
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.get_v2_query_client", factory
    )
    with pytest.raises(TypeError, match="must be bool"):
        constructor(application_read=value)
    factory.assert_not_called()
