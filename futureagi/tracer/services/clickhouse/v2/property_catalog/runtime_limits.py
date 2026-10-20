"""Shared environment-backed runtime limits for the property catalog.

Only operational tuning belongs here. Wire widths, schema/cursor versions, and
cryptographic limits remain protocol invariants in their owning codec modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings as django_settings

from tfc.settings.runtime_limit_loader import load_setting_snapshot, runtime_setting
from tfc.settings.runtime_setting_specs import (
    PROPERTY_CATALOG_RUNTIME_SETTING_SPECS,
    validate_property_catalog_settings,
)


def _setting(name: str) -> Any:
    return runtime_setting(name, PROPERTY_CATALOG_RUNTIME_SETTING_SPECS)


@dataclass(frozen=True, slots=True)
class PropertyCatalogRuntimeLimits:
    max_page_size: int = _setting("PROPERTY_CATALOG_MAX_PAGE_SIZE")
    max_search_bytes: int = _setting("PROPERTY_CATALOG_MAX_SEARCH_BYTES")
    query_wall_ms: int = _setting("PROPERTY_CATALOG_QUERY_WALL_MS")
    read_pool_size: int = _setting("PROPERTY_CATALOG_READ_POOL_SIZE")
    read_transport_timeout_seconds: float = _setting(
        "PROPERTY_CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS"
    )
    read_max_threads: int = _setting("PROPERTY_CATALOG_READ_MAX_THREADS")
    read_max_concurrent_queries_per_user: int = _setting(
        "PROPERTY_CATALOG_READ_MAX_CONCURRENT_QUERIES_PER_USER"
    )
    read_max_bytes: int = _setting("PROPERTY_CATALOG_READ_MAX_BYTES")
    read_max_memory_bytes: int = _setting("PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES")
    read_max_result_bytes: int = _setting("PROPERTY_CATALOG_READ_MAX_RESULT_BYTES")
    read_external_group_by_bytes: int = _setting(
        "PROPERTY_CATALOG_READ_EXTERNAL_GROUP_BY_BYTES"
    )
    read_external_sort_bytes: int = _setting(
        "PROPERTY_CATALOG_READ_EXTERNAL_SORT_BYTES"
    )
    cursor_max_age_seconds: int = _setting("PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS")
    cursor_max_bytes: int = _setting("PROPERTY_CATALOG_CURSOR_MAX_BYTES")

    @property
    def clickhouse_read_settings(self) -> dict[str, Any]:
        return {
            "max_threads": self.read_max_threads,
            "max_concurrent_queries_for_user": (
                self.read_max_concurrent_queries_per_user
            ),
            "max_bytes_to_read": self.read_max_bytes,
            "read_overflow_mode": "throw",
            "max_memory_usage": self.read_max_memory_bytes,
            "max_result_bytes": self.read_max_result_bytes,
            "max_bytes_before_external_group_by": self.read_external_group_by_bytes,
            "max_bytes_before_external_sort": self.read_external_sort_bytes,
            "result_overflow_mode": "throw",
            "timeout_overflow_mode": "throw",
        }


def load_property_catalog_runtime_limits(
    source: Any = django_settings,
) -> PropertyCatalogRuntimeLimits:
    """Build one validated, immutable settings snapshot."""

    return load_setting_snapshot(
        PropertyCatalogRuntimeLimits,
        specs=PROPERTY_CATALOG_RUNTIME_SETTING_SPECS,
        source=source,
        fallback=django_settings,
        validator=validate_property_catalog_settings,
    )


RUNTIME_LIMITS = load_property_catalog_runtime_limits()
