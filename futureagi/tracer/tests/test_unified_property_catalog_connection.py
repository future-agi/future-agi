from __future__ import annotations

import pytest

from tracer.services.clickhouse.v2.attribute_catalog_connection import (
    _validate_catalog_query,
)
from tracer.services.clickhouse.v2.property_catalog.connection import (
    PROPERTY_CATALOG_TABLES,
    PropertyCatalogConnectionConfig,
    PropertyCatalogReadExecutor,
)
from tracer.services.clickhouse.v2.property_catalog.reader import PropertyCatalogReader

CONFIG = PropertyCatalogConnectionConfig(
    host="catalog.internal",
    port=9440,
    database="th7247_catalog_dev_clean",
    user="property_catalog_reader",
    password="not-logged",
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def execute_read(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        return [("ok",)], [("status", "String")], None


def test_property_catalog_table_allowlist_is_exact():
    assert PROPERTY_CATALOG_TABLES == {
        "property_definition_catalog",
        "span_attribute_value_catalog",
        "property_catalog_checkpoints",
        "property_catalog_activations",
        "property_catalog_deliveries",
        "property_catalog_source_streams",
    }


def test_property_catalog_connection_requires_isolated_dev_identity():
    CONFIG.validate(
        qualifier_database="th7247_catalog_dev_clean",
        source_users={"application_reader"},
    )

    with pytest.raises(ValueError):
        PropertyCatalogConnectionConfig(
            host="catalog.internal",
            port=9440,
            database="futureagi",
            user="property_catalog_reader",
            password="secret",
        ).validate(qualifier_database="futureagi", source_users=set())

    with pytest.raises(ValueError):
        CONFIG.validate(
            qualifier_database="th7247_catalog_dev_clean",
            source_users={"property_catalog_reader"},
        )

    for unsafe_database in (
        "catalog_dev_test",
        "production_dev_backup",
        "TH7247_CATALOG_DEV_UPPER",
        "th7247_catalog_dev_bad-name",
    ):
        with pytest.raises(ValueError):
            PropertyCatalogConnectionConfig(
                host="catalog.internal",
                port=9440,
                database=unsafe_database,
                user="property_catalog_reader",
                password="secret",
            ).validate(
                qualifier_database=unsafe_database,
                source_users=set(),
            )


def test_property_catalog_executor_reads_only_allowlisted_qualified_tables():
    client = FakeClient()
    executor = PropertyCatalogReadExecutor(
        config=CONFIG,
        client_factory=lambda _config: client,
    )

    result = executor.execute(
        "SELECT status FROM `th7247_catalog_dev_clean`.property_catalog_activations",
        {},
        timeout_ms=1_500,
        settings={"max_result_rows": 2, "max_result_bytes": 1024},
    )

    assert result.data == [{"status": "ok"}]
    assert client.calls[0][3]["readonly"] == 1
    assert client.calls[0][3]["max_execution_time"] <= 1.5


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM `th7247_catalog_dev_clean`.spans",
        "SELECT * FROM `th7247_catalog_dev_clean`.span_attribute_key_catalog",
        "SELECT * FROM property_definition_catalog",
        "INSERT INTO `th7247_catalog_dev_clean`.property_definition_catalog VALUES ()",
        "SELECT * FROM `other_dev`.property_definition_catalog",
    ],
)
def test_property_catalog_executor_rejects_other_tables_and_mutations(query):
    client = FakeClient()
    executor = PropertyCatalogReadExecutor(
        config=CONFIG,
        client_factory=lambda _config: client,
    )

    with pytest.raises((RuntimeError, ValueError)):
        executor.execute(
            query,
            {},
            timeout_ms=1_000,
            settings={"max_result_rows": 1},
        )

    assert client.calls == []


def test_property_catalog_executor_uses_one_shrinking_wall():
    ticks = iter((10.0, 10.1, 10.2, 10.3, 10.5, 10.6, 10.7))
    client = FakeClient()
    executor = PropertyCatalogReadExecutor(
        config=CONFIG,
        client_factory=lambda _config: client,
        clock=lambda: next(ticks),
    )
    query = "SELECT status FROM `th7247_catalog_dev_clean`.property_catalog_activations"

    executor.execute(query, {}, timeout_ms=2_000, settings={"max_result_rows": 1})
    executor.execute(query, {}, timeout_ms=2_000, settings={"max_result_rows": 1})

    assert client.calls[1][2] < client.calls[0][2]


@pytest.mark.parametrize(
    "max_wall_ms",
    [0, -1, True],
)
def test_property_catalog_executor_rejects_invalid_request_wall(max_wall_ms):
    with pytest.raises(ValueError, match="max_wall_ms"):
        PropertyCatalogReadExecutor(
            config=CONFIG,
            client_factory=lambda _config: FakeClient(),
            max_wall_ms=max_wall_ms,
        )


def test_property_catalog_executor_honors_smaller_request_owned_wall():
    ticks = iter((10.0, 10.04, 10.041, 10.042))
    client = FakeClient()
    executor = PropertyCatalogReadExecutor(
        config=CONFIG,
        client_factory=lambda _config: client,
        clock=lambda: next(ticks),
        max_wall_ms=50,
    )

    executor.execute(
        "SELECT status FROM `th7247_catalog_dev_clean`.property_catalog_activations",
        {},
        timeout_ms=2_000,
        settings={"max_result_rows": 1},
    )

    assert 1 <= client.calls[0][2] <= 10
    assert client.calls[0][3]["max_execution_time"] <= 0.01


def test_property_catalog_reader_sql_stays_inside_physical_allowlist():
    reader = PropertyCatalogReader(
        SimpleExecutor(), catalog_database="th7247_catalog_dev_clean"
    )

    for query in (reader._activation_sql, reader._conflict_sql, reader._page_sql):
        _validate_catalog_query(
            query,
            database="th7247_catalog_dev_clean",
            allowed_tables=PROPERTY_CATALOG_TABLES,
        )


class SimpleExecutor:
    def execute(self, *_args, **_kwargs):
        raise AssertionError("SQL validation must not execute a query")
