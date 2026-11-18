from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.runtime_limits import (
    load_property_catalog_runtime_limits,
)


def test_only_reader_and_cursor_limits_remain():
    limits = load_property_catalog_runtime_limits(SimpleNamespace())
    assert limits.query_wall_ms == 10000
    assert limits.max_page_size == 50
    assert limits.read_transport_timeout_seconds == 10.0
    assert not hasattr(limits, "postgres_page_rows")
    assert not hasattr(limits, "source_adapter_wall_seconds")


def test_reader_accepts_sixty_second_timeouts_without_removing_memory_guards():
    limits = load_property_catalog_runtime_limits(
        SimpleNamespace(
            PROPERTY_CATALOG_QUERY_WALL_MS=60_000,
            PROPERTY_CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS=60.0,
            PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES=12 * 1024**3,
        )
    )
    assert limits.query_wall_ms == 60_000
    assert limits.read_transport_timeout_seconds == 60.0
    assert limits.clickhouse_read_settings["max_memory_usage"] == 12 * 1024**3
    assert limits.clickhouse_read_settings["read_overflow_mode"] == "throw"


@pytest.mark.parametrize(
    "name,value",
    [
        ("PROPERTY_CATALOG_MAX_PAGE_SIZE", 201),
        ("PROPERTY_CATALOG_QUERY_WALL_MS", 60001),
        ("PROPERTY_CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS", 60.001),
        ("PROPERTY_CATALOG_READ_MAX_BYTES", 1024**4 + 1),
        ("PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES", 16 * 1024**3 + 1),
        ("PROPERTY_CATALOG_READ_MAX_RESULT_BYTES", 256 * 1024**2 + 1),
    ],
)
def test_runtime_limits_reject_unsafe_overrides(name, value):
    with pytest.raises(ValueError):
        load_property_catalog_runtime_limits(SimpleNamespace(**{name: value}))
