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
    max_projects: int = _setting("PROPERTY_CATALOG_MAX_PROJECTS")
    max_page_size: int = _setting("PROPERTY_CATALOG_MAX_PAGE_SIZE")
    max_search_bytes: int = _setting("PROPERTY_CATALOG_MAX_SEARCH_BYTES")
    query_wall_ms: int = _setting("PROPERTY_CATALOG_QUERY_WALL_MS")
    revision_lease_seconds: int = _setting("PROPERTY_CATALOG_REVISION_LEASE_SECONDS")
    max_revision_lease_seconds: int = _setting(
        "PROPERTY_CATALOG_MAX_REVISION_LEASE_SECONDS"
    )
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
    max_lineage_revisions: int = _setting("PROPERTY_CATALOG_MAX_LINEAGE_REVISIONS")
    source_max_page_bytes: int = _setting("PROPERTY_CATALOG_SOURCE_MAX_PAGE_BYTES")
    source_max_total_bytes: int = _setting("PROPERTY_CATALOG_SOURCE_MAX_TOTAL_BYTES")
    source_adapter_wall_seconds: float = _setting(
        "PROPERTY_CATALOG_SOURCE_ADAPTER_WALL_SECONDS"
    )
    scheduled_reconcile_source_adapter_wall_seconds: float = _setting(
        "PROPERTY_CATALOG_SCHEDULED_RECONCILE_SOURCE_ADAPTER_WALL_SECONDS"
    )
    initial_backfill_source_adapter_wall_seconds: float = _setting(
        "PROPERTY_CATALOG_INITIAL_BACKFILL_SOURCE_ADAPTER_WALL_SECONDS"
    )
    postgres_statement_timeout_ms: int = _setting(
        "PROPERTY_CATALOG_POSTGRES_STATEMENT_TIMEOUT_MS"
    )
    postgres_page_rows: int = _setting("PROPERTY_CATALOG_POSTGRES_PAGE_ROWS")
    postgres_max_total_rows: int = _setting("PROPERTY_CATALOG_POSTGRES_MAX_TOTAL_ROWS")
    publisher_wall_ms: int = _setting("PROPERTY_CATALOG_PUBLISHER_WALL_MS")
    deadline_max_wall_ms: int = _setting("PROPERTY_CATALOG_DEADLINE_MAX_WALL_MS")
    drain_proof_max_bytes: int = _setting("PROPERTY_CATALOG_DRAIN_PROOF_MAX_BYTES")
    drain_poll_interval_ms: int = _setting("PROPERTY_CATALOG_DRAIN_POLL_INTERVAL_MS")
    drain_poll_cap_ms: int = _setting("PROPERTY_CATALOG_DRAIN_POLL_CAP_MS")
    visibility_retry_cap_ms: int = _setting("PROPERTY_CATALOG_VISIBILITY_RETRY_CAP_MS")
    state_store_timeout_ms: int = _setting("PROPERTY_CATALOG_STATE_STORE_TIMEOUT_MS")
    state_store_min_row_cap: int = _setting("PROPERTY_CATALOG_STATE_STORE_MIN_ROW_CAP")
    current_binding_max_rows: int = _setting(
        "PROPERTY_CATALOG_CURRENT_BINDING_MAX_ROWS"
    )
    cursor_max_age_seconds: int = _setting("PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS")
    cursor_max_bytes: int = _setting("PROPERTY_CATALOG_CURSOR_MAX_BYTES")
    lineage_anchor_max_age_seconds: int = _setting(
        "PROPERTY_CATALOG_LINEAGE_ANCHOR_MAX_AGE_SECONDS"
    )
    full_repair_interval_seconds: int = _setting(
        "PROPERTY_CATALOG_FULL_REPAIR_INTERVAL_SECONDS"
    )
    max_nonterminal_reservations: int = _setting(
        "PROPERTY_CATALOG_MAX_NONTERMINAL_RESERVATIONS"
    )
    canonical_span_max_windows: int = _setting(
        "PROPERTY_CATALOG_CANONICAL_SPAN_MAX_WINDOWS"
    )
    canonical_span_page_rows: int = _setting(
        "PROPERTY_CATALOG_CANONICAL_SPAN_PAGE_ROWS"
    )
    canonical_span_default_page_rows: int = _setting(
        "PROPERTY_CATALOG_CANONICAL_SPAN_DEFAULT_PAGE_ROWS"
    )
    dev_canonical_span_page_rows: int = _setting(
        "PROPERTY_CATALOG_DEV_CANONICAL_SPAN_PAGE_ROWS"
    )
    initial_backfill_canonical_span_page_rows: int = _setting(
        "PROPERTY_CATALOG_INITIAL_BACKFILL_CANONICAL_SPAN_PAGE_ROWS"
    )
    canonical_span_query_timeout_ms: int = _setting(
        "PROPERTY_CATALOG_CANONICAL_SPAN_QUERY_TIMEOUT_MS"
    )
    canonical_span_max_threads: int = _setting(
        "PROPERTY_CATALOG_CANONICAL_SPAN_MAX_THREADS"
    )
    initial_backfill_canonical_span_query_timeout_ms: int = _setting(
        "PROPERTY_CATALOG_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS"
    )
    canonical_span_scan_window_hours: int = _setting(
        "PROPERTY_CATALOG_CANONICAL_SPAN_SCAN_WINDOW_HOURS"
    )
    canonical_span_max_groups: int = _setting(
        "PROPERTY_CATALOG_CANONICAL_SPAN_MAX_GROUPS"
    )
    canonical_span_max_group_bytes: int = _setting(
        "PROPERTY_CATALOG_CANONICAL_SPAN_MAX_GROUP_BYTES"
    )
    authoritative_value_batch_max_rows: int = _setting(
        "PROPERTY_CATALOG_AUTHORITATIVE_VALUE_BATCH_MAX_ROWS"
    )
    authoritative_value_batch_max_bytes: int = _setting(
        "PROPERTY_CATALOG_AUTHORITATIVE_VALUE_BATCH_MAX_BYTES"
    )
    reconcile_incremental_overlap_seconds: int = _setting(
        "PROPERTY_CATALOG_RECONCILE_INCREMENTAL_OVERLAP_SECONDS"
    )
    reconcile_default_envelope_rows: int = _setting(
        "PROPERTY_CATALOG_RECONCILE_DEFAULT_ENVELOPE_ROWS"
    )
    reconcile_max_envelope_rows: int = _setting(
        "PROPERTY_CATALOG_RECONCILE_MAX_ENVELOPE_ROWS"
    )
    reconcile_default_max_envelope_bytes: int = _setting(
        "PROPERTY_CATALOG_RECONCILE_DEFAULT_MAX_ENVELOPE_BYTES"
    )
    reconcile_max_envelope_bytes: int = _setting(
        "PROPERTY_CATALOG_RECONCILE_MAX_ENVELOPE_BYTES"
    )
    producer_retirement_max_bytes: int = _setting(
        "PROPERTY_CATALOG_PRODUCER_RETIREMENT_MAX_BYTES"
    )

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

# Explicit request objects may narrow the configured overlap but can never
# exceed the reviewed setting bound. Derive that bound from the same spec so a
# future operator-range change cannot drift from request validation.
MAX_RECONCILE_INCREMENTAL_OVERLAP_SECONDS = int(
    PROPERTY_CATALOG_RUNTIME_SETTING_SPECS[
        "PROPERTY_CATALOG_RECONCILE_INCREMENTAL_OVERLAP_SECONDS"
    ].maximum
)
