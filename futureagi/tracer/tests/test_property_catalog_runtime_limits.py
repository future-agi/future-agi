from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.runtime_limits import (
    load_property_catalog_runtime_limits,
)


def test_interactive_reader_defaults_leave_headroom_for_complete_catalog_pages():
    limits = load_property_catalog_runtime_limits(SimpleNamespace())

    assert limits.query_wall_ms == 10_000
    assert limits.read_transport_timeout_seconds == 10.0


def test_runtime_limits_accept_bounded_operator_overrides():
    limits = load_property_catalog_runtime_limits(
        SimpleNamespace(
            PROPERTY_CATALOG_MAX_PAGE_SIZE=25,
            PROPERTY_CATALOG_QUERY_WALL_MS=30_000,
            PROPERTY_CATALOG_READ_POOL_SIZE=4,
            PROPERTY_CATALOG_READ_MAX_THREADS=2,
            PROPERTY_CATALOG_READ_MAX_CONCURRENT_QUERIES_PER_USER=4,
            PROPERTY_CATALOG_READ_MAX_BYTES=512 * 1024**3,
            PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES=6 * 1024**3,
            PROPERTY_CATALOG_READ_MAX_RESULT_BYTES=64 * 1024**2,
            PROPERTY_CATALOG_READ_EXTERNAL_GROUP_BY_BYTES=512 * 1024**2,
            PROPERTY_CATALOG_READ_EXTERNAL_SORT_BYTES=512 * 1024**2,
            PROPERTY_CATALOG_POSTGRES_PAGE_ROWS=250,
            PROPERTY_CATALOG_SCHEDULED_RECONCILE_SOURCE_ADAPTER_WALL_SECONDS=90.0,
            PROPERTY_CATALOG_PUBLISHER_WALL_MS=7_500,
            PROPERTY_CATALOG_STATE_STORE_TIMEOUT_MS=7_500,
            PROPERTY_CATALOG_CURRENT_BINDING_MAX_ROWS=50_000,
        )
    )

    assert limits.max_page_size == 25
    assert limits.query_wall_ms == 30_000
    assert limits.read_pool_size == 4
    assert limits.clickhouse_read_settings == {
        "max_threads": 2,
        "max_concurrent_queries_for_user": 4,
        "max_bytes_to_read": 512 * 1024**3,
        "read_overflow_mode": "throw",
        "max_memory_usage": 6 * 1024**3,
        "max_result_bytes": 64 * 1024**2,
        "max_bytes_before_external_group_by": 512 * 1024**2,
        "max_bytes_before_external_sort": 512 * 1024**2,
        "result_overflow_mode": "throw",
        "timeout_overflow_mode": "throw",
    }
    assert limits.postgres_page_rows == 250
    assert limits.scheduled_reconcile_source_adapter_wall_seconds == 90.0
    assert limits.publisher_wall_ms == 7_500
    assert limits.state_store_timeout_ms == 7_500
    assert limits.current_binding_max_rows == 50_000


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("PROPERTY_CATALOG_MAX_PROJECTS", 0),
        ("PROPERTY_CATALOG_MAX_PAGE_SIZE", 201),
        ("PROPERTY_CATALOG_QUERY_WALL_MS", 30_001),
        ("PROPERTY_CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS", 31.0),
        ("PROPERTY_CATALOG_READ_MAX_BYTES", 1024**4 + 1),
        ("PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES", 16 * 1024**3 + 1),
        ("PROPERTY_CATALOG_READ_MAX_RESULT_BYTES", 256 * 1024**2 + 1),
        ("PROPERTY_CATALOG_READ_EXTERNAL_GROUP_BY_BYTES", 8 * 1024**3 + 1),
        ("PROPERTY_CATALOG_READ_EXTERNAL_SORT_BYTES", 8 * 1024**3 + 1),
        ("PROPERTY_CATALOG_SOURCE_ADAPTER_WALL_SECONDS", 0.0),
        ("PROPERTY_CATALOG_DRAIN_POLL_INTERVAL_MS", 0),
        ("PROPERTY_CATALOG_CURRENT_BINDING_MAX_ROWS", 1_000_001),
    ),
)
def test_runtime_limits_reject_unsafe_overrides(name, value):
    with pytest.raises(ValueError):
        load_property_catalog_runtime_limits(SimpleNamespace(**{name: value}))


def test_source_page_limit_cannot_exceed_total_limit():
    with pytest.raises(ValueError, match="page bytes cannot exceed"):
        load_property_catalog_runtime_limits(
            SimpleNamespace(
                PROPERTY_CATALOG_SOURCE_MAX_PAGE_BYTES=2 * 1024 * 1024,
                PROPERTY_CATALOG_SOURCE_MAX_TOTAL_BYTES=1024 * 1024,
            )
        )


def test_postgres_statement_timeout_must_fit_standard_source_wall():
    with pytest.raises(ValueError, match="statement timeout"):
        load_property_catalog_runtime_limits(
            SimpleNamespace(
                PROPERTY_CATALOG_SOURCE_ADAPTER_WALL_SECONDS=1.0,
                PROPERTY_CATALOG_POSTGRES_STATEMENT_TIMEOUT_MS=1_000,
            )
        )


def test_scheduled_source_wall_must_stay_between_standard_and_initial_walls():
    with pytest.raises(ValueError, match="scheduled reconcile source adapter wall"):
        load_property_catalog_runtime_limits(
            SimpleNamespace(
                PROPERTY_CATALOG_SOURCE_ADAPTER_WALL_SECONDS=8.5,
                PROPERTY_CATALOG_SCHEDULED_RECONCILE_SOURCE_ADAPTER_WALL_SECONDS=8.4,
            )
        )

    with pytest.raises(ValueError, match="initial backfill source adapter wall"):
        load_property_catalog_runtime_limits(
            SimpleNamespace(
                PROPERTY_CATALOG_SCHEDULED_RECONCILE_SOURCE_ADAPTER_WALL_SECONDS=121.0,
                PROPERTY_CATALOG_INITIAL_BACKFILL_SOURCE_ADAPTER_WALL_SECONDS=120.0,
            )
        )


def test_state_store_timeout_must_fit_publisher_wall():
    with pytest.raises(ValueError, match="state-store timeout"):
        load_property_catalog_runtime_limits(
            SimpleNamespace(
                PROPERTY_CATALOG_STATE_STORE_TIMEOUT_MS=8_501,
                PROPERTY_CATALOG_PUBLISHER_WALL_MS=8_500,
            )
        )
