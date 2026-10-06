"""The current observation reader keeps a lazy SELECT-only table boundary."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2.property_catalog.connection import (
    PROPERTY_CATALOG_READ_MAX_WALL_MS,
    PROPERTY_CATALOG_TABLES,
    PropertyCatalogConnectionConfig,
    PropertyCatalogReadExecutor,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS

pytestmark = pytest.mark.unit

CONFIG = PropertyCatalogConnectionConfig(
    "clickhouse", 9000, "test_index", "reader", "test"
)
SQL = "SELECT attribute_key FROM test_index.observed_attribute_keys LIMIT 1"


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    """Keep these mock transport tests independent of integration DDL."""
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM test_index.spans",
        "SELECT * FROM system.tables",
        "SELECT * FROM test_index.property_catalog_bindings",
        "INSERT INTO test_index.observed_attribute_keys VALUES (1)",
        "DROP TABLE test_index.observed_attribute_values",
        "SELECT * FROM other.observed_attribute_keys",
        "SELECT * FROM test_index.observed_attribute_keys; SELECT 1",
    ],
)
def test_rejects_other_tables_and_mutations_before_opening_client(query):
    factory = Mock()
    executor = PropertyCatalogReadExecutor(config=CONFIG, client_factory=factory)
    with pytest.raises(RuntimeError):
        executor.execute(query, {}, timeout_ms=1000, settings={})
    factory.assert_not_called()


def test_current_tables_and_lazy_reusable_transport():
    factory = Mock()
    factory.return_value.execute_read.return_value = (
        [("key",)],
        [("attribute_key", "String")],
        1,
    )
    executor = PropertyCatalogReadExecutor(config=CONFIG, client_factory=factory)
    factory.assert_not_called()
    for _ in range(2):
        result = executor.execute(
            "SELECT attribute_key FROM test_index.observed_attribute_keys LIMIT 1",
            {},
            timeout_ms=1000,
            settings={},
        )
        assert result.data == [{"attribute_key": "key"}]
    factory.assert_called_once_with(CONFIG)
    assert PROPERTY_CATALOG_TABLES == {
        "observed_attribute_keys",
        "observed_attribute_values",
    }


def test_current_settings_need_no_activation_or_deployment_flags():
    config = PropertyCatalogConnectionConfig.from_settings(
        SimpleNamespace(
            PROPERTY_CATALOG_CH_HOST="clickhouse",
            PROPERTY_CATALOG_CH_PORT=9000,
            PROPERTY_CATALOG_CH_USER="reader",
            PROPERTY_CATALOG_CH_PASSWORD="test",
            PROPERTY_CATALOG_DATABASE="test_index",
        )
    )
    assert config == CONFIG
    assert "password" not in repr(config)


def test_dedicated_credentials_reject_source_identity():
    with pytest.raises(ValueError, match="dedicated catalog read user"):
        CONFIG.validate(source_users={CONFIG.user})


def test_maintenance_defaults_enforce_finite_catalog_bounds():
    client = Mock()
    client.execute_read.return_value = ([], [], 0)
    executor = PropertyCatalogReadExecutor(
        config=CONFIG, client_factory=lambda _config: client, clock=lambda: 0
    )
    executor.execute(SQL, {}, timeout_ms=123, settings={})
    applied = client.execute_read.call_args.kwargs["settings"]
    assert applied["max_execution_time"] == 0.123
    for key, expected in RUNTIME_LIMITS.clickhouse_read_settings.items():
        assert applied[key] == expected
    assert applied["max_result_rows"] == RUNTIME_LIMITS.max_page_size + 1


@pytest.mark.parametrize("progress", [False, True])
def test_catalog_reuses_remaining_deadline_across_statements(progress):
    now = 0
    calls = []

    class BasicClient:
        def execute_read(self, query, params, *, timeout_ms, settings):
            calls.append((timeout_ms, settings["max_execution_time"]))
            return [], [], 0

    class ProgressClient(BasicClient):
        def execute_read_with_progress(self, *args, **kwargs):
            return (*self.execute_read(*args, **kwargs), 25, 1024)

    factory = Mock(return_value=ProgressClient() if progress else BasicClient())
    executor = PropertyCatalogReadExecutor(
        config=CONFIG, client_factory=factory, clock=lambda: now, max_wall_ms=1000
    )
    for elapsed in (0, 0.25, 0.75):
        now = elapsed
        page = executor.execute(SQL, {}, timeout_ms=1000, settings={})
        assert page.read_rows == (25 if progress else None)
        assert page.read_bytes == (1024 if progress else None)
    now = 1
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        executor.execute(SQL, {}, timeout_ms=1000, settings={})
    assert calls == [(1000, 1), (750, 0.75), (250, 0.25)]
    factory.assert_called_once_with(CONFIG)


def test_expired_catalog_deadline_never_opens_client():
    now = 0
    factory = Mock()
    executor = PropertyCatalogReadExecutor(
        config=CONFIG, client_factory=factory, clock=lambda: now
    )
    now = PROPERTY_CATALOG_READ_MAX_WALL_MS / 1000
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        executor.execute(SQL, {}, timeout_ms=1000, settings={})
    factory.assert_not_called()


@pytest.mark.parametrize("acquire_seconds", [0.25, 1])
def test_lazy_client_acquisition_consumes_catalog_deadline(acquire_seconds):
    now = 0
    client = Mock()
    client.execute_read.return_value = ([], [], 0)

    def factory(_config):
        nonlocal now
        now = acquire_seconds
        return client

    executor = PropertyCatalogReadExecutor(
        config=CONFIG, client_factory=factory, clock=lambda: now, max_wall_ms=1000
    )
    if acquire_seconds == 1:
        with pytest.raises(TimeoutError, match="deadline exhausted"):
            executor.execute(SQL, {}, timeout_ms=1000, settings={})
        client.execute_read.assert_not_called()
    else:
        executor.execute(SQL, {}, timeout_ms=1000, settings={})
        dispatched = client.execute_read.call_args.kwargs
        assert dispatched["timeout_ms"] == 750
        assert dispatched["settings"]["max_execution_time"] == 0.75


@pytest.mark.parametrize(
    "key", ["max_bytes_to_read", "max_memory_usage", "max_result_bytes"]
)
def test_caller_cannot_raise_catalog_resource_ceilings(key):
    client = Mock()
    client.execute_read.return_value = ([], [], 0)
    executor = PropertyCatalogReadExecutor(
        config=CONFIG, client_factory=lambda _: client
    )
    ceiling = RUNTIME_LIMITS.clickhouse_read_settings[key]
    executor.execute(SQL, {}, timeout_ms=123, settings={key: ceiling * 2})
    assert client.execute_read.call_args.kwargs["settings"][key] == ceiling


@pytest.mark.parametrize(
    "key",
    ["max_bytes_to_read", "max_memory_usage", "max_result_rows", "max_result_bytes"],
)
@pytest.mark.parametrize("value", [0, -1, None, float("inf")])
def test_invalid_resource_bounds_never_open_client(key, value):
    factory = Mock()
    executor = PropertyCatalogReadExecutor(config=CONFIG, client_factory=factory)
    with pytest.raises(ValueError, match="positive integer"):
        executor.execute(SQL, {}, timeout_ms=123, settings={key: value})
    factory.assert_not_called()


@pytest.mark.parametrize("database", ["", "test;DROP TABLE x", "database.table", "a`b"])
def test_database_is_an_identifier(database):
    with pytest.raises(ValueError):
        PropertyCatalogConnectionConfig(
            "clickhouse", 9000, database, "reader", "test"
        ).validate()
