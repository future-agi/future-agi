"""Single source of truth for bounded, operator-tunable numeric settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

Numeric = int | float
NumericType = type[int] | type[float]
SpecRow = tuple[str, Numeric, Numeric, Numeric]

_MISSING = object()


@dataclass(frozen=True, slots=True)
class NumericSettingSpec:
    """A numeric setting's default, parser, and reviewed safety bounds."""

    value_type: NumericType
    default: Numeric
    minimum: Numeric
    maximum: Numeric

    def parse(self, name: str, raw_value: object = _MISSING) -> Numeric:
        value_source = self.default if _is_missing(raw_value) else raw_value
        if isinstance(value_source, bool):
            raise ValueError(f"{name} must be numeric, not boolean")
        if self.value_type is int and isinstance(value_source, float):
            raise ValueError(f"{name} must be an integer")
        try:
            value = self.value_type(value_source)
        except (TypeError, ValueError) as exc:
            expected = "an integer" if self.value_type is int else "numeric"
            raise ValueError(f"{name} must be {expected}") from exc
        if not self.minimum <= value <= self.maximum:
            raise ValueError(
                f"{name} must be between {self.minimum} and {self.maximum}"
            )
        return value


def _is_missing(value: object) -> bool:
    return (
        value is _MISSING
        or value is None
        or (isinstance(value, str) and not value.strip())
    )


def _specs(
    rows: tuple[SpecRow, ...],
    *,
    value_type: NumericType = int,
    prefix: str = "",
) -> dict[str, NumericSettingSpec]:
    return {
        f"{prefix}{name}": NumericSettingSpec(
            value_type=value_type,
            default=default,
            minimum=minimum,
            maximum=maximum,
        )
        for name, default, minimum, maximum in rows
    }


PROPERTY_CATALOG_RUNTIME_SETTING_SPECS = {
    **_specs(
        (
            ("MAX_PROJECTS", 64, 1, 256),
            ("MAX_PAGE_SIZE", 50, 1, 200),
            ("MAX_SEARCH_BYTES", 512, 1, 4096),
            ("QUERY_WALL_MS", 2_000, 100, 30_000),
            ("DEV_STANDARD_MAX_WALL_MS", 100_000, 100, 1_740_000),
            ("DEV_INITIAL_BACKFILL_MAX_WALL_MS", 1_740_000, 100, 3_600_000),
            ("DEV_SCHEDULED_RECONCILE_MAX_WALL_MS", 1_740_000, 100, 3_600_000),
            ("REVISION_LEASE_SECONDS", 600, 60, 1_800),
            ("MAX_REVISION_LEASE_SECONDS", 1_800, 60, 3_600),
            ("INITIAL_BACKFILL_LEASE_HEADROOM_MS", 60_000, 1_000, 600_000),
            ("RECONCILE_INTERVAL_SECONDS", 120, 30, 86_400),
            ("RECONCILE_MAX_WORKSPACES", 1, 1, 256),
            ("RECONCILE_DEFAULT_EXTENDED_WALL_MS", 1_200_000, 100, 3_600_000),
            ("RECONCILE_ACTIVITY_TIME_LIMIT_SECONDS", 1_800, 60, 7_200),
            ("READ_POOL_SIZE", 4, 1, 32),
            ("READ_MAX_THREADS", 2, 1, 16),
            ("READ_MAX_CONCURRENT_QUERIES_PER_USER", 4, 1, 16),
            ("READ_MAX_BYTES", 512 * 1024**2, 1024**2, 1024**4),
            ("READ_MAX_MEMORY_BYTES", 512 * 1024**2, 1024**2, 16 * 1024**3),
            ("READ_MAX_RESULT_BYTES", 8 * 1024**2, 64 * 1024, 256 * 1024**2),
            (
                "READ_EXTERNAL_GROUP_BY_BYTES",
                128 * 1024**2,
                32 * 1024**2,
                8 * 1024**3,
            ),
            (
                "READ_EXTERNAL_SORT_BYTES",
                128 * 1024**2,
                32 * 1024**2,
                8 * 1024**3,
            ),
            ("MAX_LINEAGE_REVISIONS", 2_048, 1, 16_384),
            ("SOURCE_MAX_PAGE_BYTES", 2 * 1024**2, 64 * 1024, 32 * 1024**2),
            ("SOURCE_MAX_TOTAL_BYTES", 32 * 1024**2, 64 * 1024, 64 * 1024**2),
            ("POSTGRES_STATEMENT_TIMEOUT_MS", 8_000, 100, 60_000),
            ("POSTGRES_PAGE_ROWS", 1_000, 1, 10_000),
            ("POSTGRES_MAX_TOTAL_ROWS", 100_000, 1, 1_000_000),
            ("PUBLISHER_WALL_MS", 8_500, 100, 60_000),
            ("DEADLINE_MAX_WALL_MS", 7_200_000, 100, 24 * 60 * 60 * 1_000),
            ("DRAIN_PROOF_MAX_BYTES", 1024**2, 64 * 1024, 16 * 1024**2),
            ("DRAIN_POLL_INTERVAL_MS", 50, 1, 1_000),
            ("DRAIN_POLL_CAP_MS", 1_000, 1, 5_000),
            ("VISIBILITY_RETRY_CAP_MS", 250, 1, 5_000),
            ("STATE_STORE_TIMEOUT_MS", 8_500, 100, 60_000),
            ("STATE_STORE_MIN_ROW_CAP", 256, 1, 16_384),
            ("CURRENT_BINDING_MAX_ROWS", 100_000, 1, 1_000_000),
            ("CURSOR_MAX_AGE_SECONDS", 24 * 60 * 60, 60, 7 * 24 * 60 * 60),
            ("CURSOR_MAX_BYTES", 16 * 1024, 1024, 64 * 1024),
            ("LINEAGE_ANCHOR_MAX_AGE_SECONDS", 26 * 60 * 60, 60 * 60, 604_800),
            ("FULL_REPAIR_INTERVAL_SECONDS", 24 * 60 * 60, 60 * 60, 604_800),
            ("MAX_NONTERMINAL_RESERVATIONS", 64, 1, 1024),
            ("CANONICAL_SPAN_MAX_WINDOWS", 366 * 24, 24, 5 * 366 * 24),
            ("CANONICAL_SPAN_PAGE_ROWS", 1024, 1, 4096),
            ("CANONICAL_SPAN_DEFAULT_PAGE_ROWS", 8, 1, 4096),
            ("DEV_CANONICAL_SPAN_PAGE_ROWS", 256, 1, 4096),
            ("INITIAL_BACKFILL_CANONICAL_SPAN_PAGE_ROWS", 1024, 1, 4096),
            ("CANONICAL_SPAN_QUERY_TIMEOUT_MS", 8_500, 100, 60_000),
            ("CANONICAL_SPAN_MAX_THREADS", 1, 1, 16),
            ("INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS", 30_000, 100, 120_000),
            ("CANONICAL_SPAN_SCAN_WINDOW_HOURS", 7 * 24, 1, 31 * 24),
            ("CANONICAL_SPAN_MAX_GROUPS", 100_000, 1000, 1_000_000),
            ("CANONICAL_SPAN_MAX_GROUP_BYTES", 64 * 1024**2, 1024**2, 512 * 1024**2),
            ("AUTHORITATIVE_VALUE_BATCH_MAX_ROWS", 2000, 1, 10_000),
            ("AUTHORITATIVE_VALUE_BATCH_MAX_BYTES", 400 * 1024, 64 * 1024, 8 * 1024**2),
            ("RECONCILE_INCREMENTAL_OVERLAP_SECONDS", 120, 0, 86_400),
            ("RECONCILE_DEFAULT_ENVELOPE_ROWS", 500, 1, 1000),
            ("RECONCILE_MAX_ENVELOPE_ROWS", 1000, 1, 10_000),
            ("RECONCILE_DEFAULT_MAX_ENVELOPE_BYTES", 1024**2, 64 * 1024, 2 * 1024**2),
            ("RECONCILE_MAX_ENVELOPE_BYTES", 2 * 1024**2, 64 * 1024, 8 * 1024**2),
            ("PRODUCER_RETIREMENT_MAX_BYTES", 8 * 1024**2, 64 * 1024, 64 * 1024**2),
        ),
        prefix="PROPERTY_CATALOG_",
    ),
    **_specs(
        (
            ("READ_TRANSPORT_TIMEOUT_SECONDS", 2.0, 0.1, 30.0),
            ("SOURCE_ADAPTER_WALL_SECONDS", 8.5, 0.1, 540.0),
            (
                "SCHEDULED_RECONCILE_SOURCE_ADAPTER_WALL_SECONDS",
                120.0,
                0.1,
                540.0,
            ),
            ("INITIAL_BACKFILL_SOURCE_ADAPTER_WALL_SECONDS", 540.0, 0.1, 540.0),
        ),
        value_type=float,
        prefix="PROPERTY_CATALOG_",
    ),
}

DATASET_READ_SETTING_SPECS = {
    **_specs(
        (
            ("TABLE_CURSOR_MAX_AGE_SECONDS", 30 * 60, 60, 86_400),
            ("TABLE_EXACT_MAX_COLUMNS", 128, 1, 1_024),
            ("TABLE_EXACT_MAX_CELLS", 12_800, 1, 1_000_000),
            ("TABLE_EXACT_MAX_CELL_VALUE_BYTES", 256 * 1024, 1_024, 16 * 1024**2),
            (
                "TABLE_EXACT_MAX_CELL_VARIABLE_BYTES",
                6 * 1024**2,
                64 * 1024,
                64 * 1024**2,
            ),
            ("TABLE_EXACT_MAX_SCHEMA_BYTES", 1024**2, 64 * 1024, 16 * 1024**2),
            ("TABLE_EXACT_MAX_SERIALIZED_BYTES", 8 * 1024**2, 64 * 1024, 64 * 1024**2),
            ("INTERACTIVE_MAX_PAGE_SIZE", 100, 1, 500),
            ("INTERACTIVE_MAX_OFFSET_ROWS", 100_000, 1_000, 1_000_000),
            ("ROW_ADJACENCY_MAX_ROWS", 50, 1, 500),
        ),
        prefix="DATASET_",
    ),
    **_specs(
        (("TABLE_SERVER_WALL_SECONDS", 8.5, 0.5, 30.0),),
        value_type=float,
        prefix="DATASET_",
    ),
}

INTERACTIVE_READ_SETTING_SPECS = {
    **_specs(
        (
            ("INTERACTIVE_READ_DEFAULT_WALL_MS", 30_000, 100, 60_000),
            ("INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS", 30_000, 100, 60_000),
            ("INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE", 100, 1, 500),
            ("ANALYTICS_DEFAULT_LOOKBACK_DAYS", 30, 1, 3_660),
            ("PG_CONNECT_TIMEOUT_SECONDS", 1, 1, 5),
            (
                "CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES",
                36 * 1024**3,
                64 * 1024**2,
                128 * 1024**3,
            ),
            (
                "CLICKHOUSE_APPLICATION_READ_MAX_BYTES",
                1024**4,
                64 * 1024**2,
                2 * 1024**4,
            ),
            ("CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS", 4, 1, 16),
            ("CLICKHOUSE_APPLICATION_READ_MAX_THREADS", 8, 1, 32),
            ("CLICKHOUSE_APPLICATION_READ_MAX_RESULT_ROWS", 1_000_000, 1, 10_000_000),
            (
                "CLICKHOUSE_APPLICATION_READ_MAX_RESULT_BYTES",
                512 * 1024**2,
                64 * 1024,
                2 * 1024**3,
            ),
            ("CLICKHOUSE_READ_ADMISSION_RETRY_FIRST_MS", 25, 0, 10_000),
            ("CLICKHOUSE_READ_ADMISSION_RETRY_SECOND_MS", 75, 0, 10_000),
            ("CLICKHOUSE_READ_ADMISSION_RETRY_THIRD_MS", 150, 0, 10_000),
            ("EXACT_GRAPH_MAX_BUCKETS_PER_PARTITION", 31, 1, 366),
            ("EXACT_GRAPH_MIN_REMAINING_MS", 25, 1, 1_000),
            ("EXACT_GRAPH_MEMBERSHIP_BATCH_SIZE", 1_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE", 5_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_CANDIDATE_MAX_ROWS", 1_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_ANCHOR_PARTITION_HOURS", 2, 1, 168),
            ("EXACT_GRAPH_TRACE_ANCHOR_MIN_PARTITION_HOURS", 1, 1, 168),
            ("EXACT_GRAPH_TRACE_ANCHOR_MAX_WORKERS", 2, 1, 16),
            ("EXACT_GRAPH_TRACE_ANCHOR_PAGE_SIZE", 50_000, 1, 1_000_000),
            ("EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_DAYS", 30, 1, 3_660),
            ("EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE", 5_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE", 512, 1, 100_000),
            ("EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE", 5_000, 1, 100_000),
            ("EXACT_GRAPH_TRACE_INITIAL_SLICE_SECONDS", 5 * 60, 1, 86_400),
            ("EXACT_GRAPH_TRACE_MIN_SLICE_SECONDS", 30, 1, 86_400),
            (
                "EXACT_GRAPH_TRACE_MAX_SLICE_SECONDS",
                2 * 24 * 60 * 60,
                1,
                31 * 24 * 60 * 60,
            ),
            ("EXACT_GRAPH_TRACE_GROWTH_QUERY_TIME_MS", 2_000, 1, 60_000),
            ("EXACT_GRAPH_SPAN_MAX_PARTITION_HOURS", 24, 1, 31 * 24),
            ("EXACT_GRAPH_SPAN_INITIAL_PARTITION_HOURS", 1, 1, 31 * 24),
            ("EXACT_GRAPH_SPAN_GROW_BELOW_QUERY_MS", 250, 1, 60_000),
            ("EXACT_GRAPH_READ_BLOCK_SIZE", 512, 1, 65_536),
            (
                "EXACT_GRAPH_READ_PREFERRED_BLOCK_BYTES",
                4 * 1024**2,
                64 * 1024,
                64 * 1024**2,
            ),
            (
                "EXACT_GRAPH_READ_EXTERNAL_SPILL_BYTES",
                32 * 1024**2,
                64 * 1024,
                4 * 1024**3,
            ),
            ("EXACT_GRAPH_TRACE_CLASSIFIER_MAX_THREADS", 8, 1, 32),
            (
                "INTERACTIVE_READ_DEFAULT_MAX_RESPONSE_UNITS",
                2 * 1024**2,
                64 * 1024,
                64 * 1024**2,
            ),
            ("DASHBOARD_FILTER_VALUE_WALL_MS", 30_000, 100, 60_000),
            ("DASHBOARD_TRACE_READ_MAX_THREADS", 4, 1, 16),
            (
                "DASHBOARD_TRACE_READ_MAX_BYTES",
                1024**4,
                64 * 1024**2,
                2 * 1024**4,
            ),
            (
                "DASHBOARD_TRACE_READ_MAX_MEMORY_BYTES",
                36 * 1024**3,
                64 * 1024**2,
                128 * 1024**3,
            ),
            ("DASHBOARD_TRACE_READ_MAX_RESULT_ROWS", 250_000, 1, 1_000_000),
            (
                "DASHBOARD_TRACE_READ_MAX_RESULT_BYTES",
                64 * 1024**2,
                64 * 1024,
                512 * 1024**2,
            ),
            ("DASHBOARD_TRACE_MAX_CONCURRENT_METRICS", 2, 1, 8),
            ("DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE", 50, 1, 200),
            ("DASHBOARD_FILTER_VALUE_FINITE_MAX", 5_000, 1, 50_000),
            ("DASHBOARD_FILTER_VALUE_LEGACY_MAX", 500, 1, 5_000),
            (
                "DASHBOARD_FILTER_VALUE_MAX_RESULT_BYTES",
                64 * 1024**2,
                64 * 1024,
                512 * 1024**2,
            ),
            (
                "ATTRIBUTE_READ_MAX_RESULT_BYTES",
                64 * 1024**2,
                16 * 1024**2,
                512 * 1024**2,
            ),
            ("DASHBOARD_ROLLUP_MAX_QUERIES", 2, 1, 16),
            ("DASHBOARD_ROLLUP_MAX_POINTS", 10_000, 100, 100_000),
            (
                "DASHBOARD_ROLLUP_MAX_RESULT_BYTES",
                32 * 1024**2,
                64 * 1024,
                512 * 1024**2,
            ),
            ("DASHBOARD_FILTER_VALUE_SEARCH_PAGE_SIZE", 20, 1, 200),
            ("DASHBOARD_FILTER_VALUE_COMPAT_LOOKBACK_DAYS", 365, 1, 3_660),
            ("DASHBOARD_METRICS_ATTRIBUTE_KEY_LIMIT", 2_000, 1, 100_000),
            ("DASHBOARD_METRICS_ATTRIBUTE_WORKERS", 1, 1, 8),
            ("DASHBOARD_METRICS_CATALOG_DEFAULT_PAGE_SIZE", 50, 1, 500),
            ("DASHBOARD_METRICS_CATALOG_MAX_PAGE_SIZE", 200, 1, 500),
            ("DASHBOARD_METRICS_CATALOG_SEARCH_MAX_CHARS", 256, 1, 4_096),
            ("DASHBOARD_METRICS_EVAL_USAGE_QUERY_TIMEOUT_MS", 5_000, 100, 60_000),
            ("DASHBOARD_METRICS_EVAL_USAGE_LOOKBACK_DAYS", 90, 1, 3_660),
            ("OBSERVABILITY_LIST_MAX_BLOCK_SIZE", 8192, 1, 65_536),
            ("OBSERVABILITY_LIST_MAX_BYTES", 1024**4, 64 * 1024**2, 2 * 1024**4),
            (
                "OBSERVABILITY_LIST_CELL_PREVIEW_MAX_BYTES",
                16 * 1024,
                1_024,
                1024**2,
            ),
            (
                "OBSERVABILITY_LIST_MAX_MEMORY_BYTES",
                36 * 1024**3,
                64 * 1024**2,
                128 * 1024**3,
            ),
            ("OBSERVABILITY_LIST_MAX_RESULT_ROWS", 5_001, 1, 100_000),
            ("CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS", 120_000, 100, 120_000),
            ("MONITOR_GRAPH_CH_TIMEOUT_CAP_MS", 6_000, 100, 60_000),
            ("MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS", 1_000, 100, 10_000),
            ("GRAPH_BACKGROUND_WALL_MS", 120_000, 1_000, 120_000),
            ("GRAPH_EVENT_LIMIT", 2_000, 1, 100_000),
            ("GRAPH_TRACE_DECORATION_CANDIDATE_LIMIT", 40, 1, 4_096),
            ("GRAPH_SPAN_METRIC_BATCH_SIZE", 1_024, 1, 4_096),
            ("FILTER_VALUE_READ_TIMEOUT_MS", 30_000, 100, 60_000),
            ("FILTER_VALUE_CURSOR_MIN_SEGMENT_SECONDS", 5, 1, 300),
            # A five-minute system-value continuation read crossed 1 GiB and
            # the four-second picker wall on a production-scale project. Start
            # at the existing exact five-second floor and grow only after a
            # complete empty/duplicate-only slice proves it is safe.
            ("FILTER_VALUE_CURSOR_INITIAL_SEGMENT_SECONDS", 5, 1, 86_400),
            (
                "FILTER_VALUE_CURSOR_MAX_SEGMENT_SECONDS",
                60 * 24 * 60 * 60,
                60,
                366 * 24 * 60 * 60,
            ),
            ("FILTER_VALUE_CURSOR_MAX_QUERIES", 6, 1, 128),
            ("FILTER_VALUE_CURSOR_SCAN_LIMIT", 201, 2, 10_001),
            ("FILTER_VALUE_READ_MAX_THREADS", 2, 1, 16),
            ("FILTER_SELECTOR_QUERY_TIMEOUT_MS", 2_500, 25, 10_000),
            ("FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS", 3_000, 25, 30_000),
            ("FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS", 30_000, 25, 120_000),
            ("FILTER_SELECTOR_MAX_THREADS", 1, 1, 8),
            ("FILTER_SELECTOR_MAX_NUMBERED_PAGE_WORK_ROWS", 5_000, 1, 100_000),
            ("OBSERVABILITY_NAVIGATION_CANDIDATE_LIMIT", 4_095, 1, 65_535),
            ("OBSERVABILITY_NAVIGATION_SCAN_PAGE_SIZE", 200, 1, 1_000),
            ("OBSERVABILITY_NAVIGATION_MAX_QUERIES", 128, 1, 1_024),
            ("TRACE_LIST_ENRICHMENT_CHUNK_SIZE", 100, 1, 500),
            ("TRACE_LIST_ENRICHMENT_MAX_WORKERS", 2, 1, 16),
            ("TRACE_LIST_ANNOTATION_SCORE_SPAN_LIMIT", 50_000, 1, 1_000_000),
            ("VOICE_CONTENT_MAX_QUERY_ATTEMPTS", 64, 1, 1_024),
            ("VOICE_CONTENT_MAX_BATCH_SIZE", 200, 1, 5_000),
            ("VOICE_CONTENT_MIN_REMAINING_MS", 1_500, 1, 60_000),
            ("VOICE_LIST_DEFAULT_PAGE_SIZE", 10, 1, 5_000),
            ("VOICE_FILTER_CLASSIFY_FALLBACK_BATCH_SIZE", 50, 1, 5_000),
            ("VOICE_FILTER_EXPENSIVE_CLASSIFIER_CHUNKS", 4, 1, 64),
            ("VOICE_FILTER_LIGHT_CLASSIFIER_CHUNKS", 8, 1, 64),
            ("VOICE_FILTER_PUBLIC_MAX_PAGE_SIZE", 512, 1, 5_000),
            ("SESSION_LIST_READ_MAX_THREADS", 2, 1, 16),
            ("SESSION_LIST_MAX_RESULT_BYTES", 32 * 1024**2, 64 * 1024, 512 * 1024**2),
            ("SESSION_LIST_ATTRIBUTE_MAX_RESULT_ROWS", 50_000, 1, 1_000_000),
            ("SESSION_LIST_FILTER_MAX_CANDIDATES", 200, 1, 5_000),
            ("SESSION_LIST_FILTER_MAX_SEED_ATTEMPTS", 24, 1, 512),
            ("SESSION_LIST_FILTER_MAX_QUERIES", 48, 1, 1_024),
            ("ANNOTATION_QUEUE_ADD_ITEMS_SYNC_MAX", 1_000, 1, 10_000),
            ("ANNOTATION_QUEUE_EXPORT_SYNC_MAX_ITEMS", 1_000, 1, 10_000),
            (
                "ANNOTATION_QUEUE_AUTOMATION_MAX_RESPONSE_UNITS",
                1024**2,
                64 * 1024,
                64 * 1024**2,
            ),
            ("ANNOTATION_QUEUE_DEADLINE_CHECK_INTERVAL", 128, 1, 10_000),
            ("ANNOTATION_QUEUE_DEADLINE_BULK_BATCH_SIZE", 500, 1, 10_000),
            ("ANNOTATION_QUEUE_AUTOMATION_DEFAULT_PAGE_SIZE", 25, 1, 500),
            ("ANNOTATION_QUEUE_AUTOMATION_MAX_PAGE_SIZE", 100, 1, 500),
            ("BULK_SELECTION_MAX_CAP", 10_000, 1, 100_000),
            ("BULK_SELECTION_DEADLINE_MS", 15_000, 100, 60_000),
            ("BULK_SELECTION_MAX_SEED_ATTEMPTS", 64, 1, 512),
            ("BULK_SELECTION_MAX_QUERY_COUNT", 128, 1, 1024),
            ("BULK_SELECTION_MAX_CANDIDATES", 200, 1, 5_000),
            ("BULK_SELECTION_CLASSIFY_BATCH_SIZE", 200, 1, 5_000),
            ("BULK_SELECTION_MAX_RAW_PAGE_SIZE", 12_799, 1, 100_000),
            ("BULK_SELECTION_MAX_EXCLUDE_COUNT", 12_797, 0, 100_000),
            ("SMART_FILTER_REQUEST_WALL_MS", 9_000, 100, 60_000),
            ("SMART_FILTER_VALUE_READ_WALL_MS", 4_000, 100, 10_000),
            ("SMART_FILTER_VALUE_LIMIT", 100, 1, 1_000),
            ("SMART_FILTER_SEARCH_MAX_BYTES", 256, 1, 4_096),
            ("SMART_FILTER_PROJECT_SCOPE_LIMIT", 1_000, 1, 10_000),
            ("SMART_FILTER_GROUNDED_VALUE_LIMIT", 20, 1, 200),
            ("EVAL_LOG_MAX_OFFSET", 1_000_000, 1_000, 10_000_000),
            ("EVAL_LOG_DEFAULT_PAGE_SIZE", 10, 1, 500),
            ("EVAL_METRIC_MAX_WINDOW_DAYS", 365, 1, 3660),
            ("EVAL_METRIC_MAX_CHOICE_SCORES", 100, 1, 1_000),
            ("EVAL_METRIC_CHOICE_LABEL_MAX_UTF8_BYTES", 1_024, 1, 16 * 1024),
            ("EVAL_METRIC_NUMERIC_TEXT_MAX_CHARS", 64, 1, 512),
            ("EVAL_METRIC_ABS_SCORE_LIMIT", 1_000_000, 1, 1_000_000_000),
            ("EVAL_METRIC_BUCKET_DEADLINE_CHECK_INTERVAL", 32, 1, 10_000),
            ("EVAL_LOG_MAX_SEARCH_LENGTH", 512, 1, 4_096),
            ("EVAL_LOG_MAX_SEARCH_COLUMNS", 64, 1, 512),
            ("EVAL_LOG_MAX_SORT_COLUMNS", 3, 1, 16),
            ("EVAL_LOG_MAX_COLUMNS", 200, 1, 1_000),
            ("EVAL_LOG_COLUMN_NAME_MAX_CHARS", 2_000, 1, 16 * 1024),
            ("EVAL_LOG_REQUIRED_KEYS_LIMIT", 100, 1, 1_000),
            ("EVAL_LOG_ROW_DEADLINE_CHECK_INTERVAL", 8, 1, 10_000),
            ("EVAL_LOG_COLUMN_DEADLINE_CHECK_INTERVAL", 32, 1, 10_000),
            ("EVAL_TASK_USAGE_DETAIL_TEXT_MAX_CHARS", 8 * 1024, 256, 256 * 1024),
            ("EVAL_TASK_USAGE_JSON_PREVIEW_MAX_CHARS", 2 * 1024, 256, 64 * 1024),
            ("EVAL_TASK_USAGE_MAPPING_PATH_LIMIT", 16, 1, 256),
            ("EVAL_TASK_USAGE_MAPPING_ENTRY_LIMIT", 16, 1, 256),
            ("EVAL_TASK_USAGE_MAPPING_JSON_MAX_CHARS", 8 * 1024, 256, 256 * 1024),
            ("EVAL_TASK_USAGE_OMITTED_FIELDS_LIMIT", 24, 1, 512),
            ("EVAL_TASK_USAGE_AGGREGATION_JSON_MAX_CHARS", 64 * 1024, 1024, 1024**2),
            (
                "EVAL_TASK_USAGE_AGGREGATION_JSON_MAX_UNITS",
                512 * 1024,
                1024,
                8 * 1024**2,
            ),
            ("EVAL_TASK_ERROR_TEXT_MAX_CHARS", 8 * 1024, 256, 256 * 1024),
            ("EVAL_TASK_WARNING_KEY_LIMIT", 32, 1, 512),
            ("EVAL_TASK_WARNING_KEY_MAX_CHARS", 128, 1, 4_096),
            ("EVAL_TASK_WARNING_MESSAGE_MAX_CHARS", 1024, 1, 64 * 1024),
            ("EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT", 1_000, 1, 100_000),
            ("EVAL_TASK_LIST_COMPATIBILITY_RELATION_LIMIT", 5_000, 1, 500_000),
            ("EVAL_TASK_LIST_MAX_OFFSET", 50_000, 1_000, 1_000_000),
            ("EVAL_TASK_LIST_DEFAULT_PAGE_SIZE", 30, 1, 500),
            ("EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE", 10, 1, 500),
            ("EVAL_TASK_USAGE_DEFAULT_PAGE_SIZE", 25, 1, 500),
            ("EVAL_TASK_USAGE_MAX_PAGE_SIZE", 100, 1, 500),
            ("EVAL_TASK_USAGE_MAX_PAGE_NUMBER", 100, 1, 10_000),
            (
                "EVAL_TASK_LIST_COMPATIBILITY_FILTER_UNITS",
                512 * 1024,
                64 * 1024,
                8 * 1024**2,
            ),
            ("EVAL_TASK_ROOT_JSON_PREFLIGHT_UNITS", 1024**2, 64 * 1024, 16 * 1024**2),
            ("EVAL_TASK_USAGE_MAX_CHART_POINTS", 367, 1, 3_660),
            ("EVAL_TASK_USAGE_AGGREGATION_ROW_LIMIT", 5_000, 1, 100_000),
            ("EVAL_TASK_ERROR_GROUPS_LIMIT", 50, 1, 1_000),
            ("EVAL_TASK_WARNING_GROUPS_LIMIT", 20, 1, 1_000),
            ("EVAL_TASK_WARNING_LOG_SCAN_LIMIT", 1_000, 1, 100_000),
            ("PROMPT_METRICS_MAX_EVAL_COLUMNS", 50, 1, 500),
            ("PROMPT_METRICS_MAX_CHOICE_UTF8_BYTES", 512, 1, 16 * 1024),
            ("PROMPT_METRICS_MAX_TOTAL_CHOICE_UTF8_BYTES", 16 * 1024, 1, 1024**2),
            ("PROMPT_METRICS_MAX_OFFSET", 50_000, 1_000, 1_000_000),
            (
                "PROMPT_METRICS_SPAN_PAGE_DB_PAYLOAD_BYTES",
                1_500_000,
                64 * 1024,
                64 * 1024**2,
            ),
            ("SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE", 50, 1, 500),
            ("SIMULATION_PREVIEW_MAX_PAGE_SIZE", 50, 1, 500),
            ("SIMULATION_PREVIEW_CURSOR_MAX_AGE_SECONDS", 60 * 60, 60, 24 * 60 * 60),
        )
    ),
    **_specs(
        (
            ("EXACT_GRAPH_TRACE_ANCHOR_MIN_RETENTION_FRACTION", 0.25, 0.01, 1.0),
            ("REDIS_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS", 1.0, 0.05, 2.0),
            ("REDIS_CACHE_SOCKET_TIMEOUT_SECONDS", 1.0, 0.05, 2.0),
        ),
        value_type=float,
    ),
}

RUNTIME_NUMERIC_SETTING_SPECS = {
    **PROPERTY_CATALOG_RUNTIME_SETTING_SPECS,
    **DATASET_READ_SETTING_SPECS,
    **INTERACTIVE_READ_SETTING_SPECS,
}

if len(RUNTIME_NUMERIC_SETTING_SPECS) != sum(
    map(
        len,
        (
            PROPERTY_CATALOG_RUNTIME_SETTING_SPECS,
            DATASET_READ_SETTING_SPECS,
            INTERACTIVE_READ_SETTING_SPECS,
        ),
    )
):
    raise RuntimeError("runtime numeric setting names must be unique")


def load_numeric_settings(
    specs: Mapping[str, NumericSettingSpec],
    *,
    source: object,
    fallback: object | None = None,
) -> dict[str, Numeric]:
    """Resolve a complete bounded setting group from mappings or objects."""

    return {
        name: spec.parse(name, _read_value(source, fallback, name))
        for name, spec in specs.items()
    }


def _read_value(source: object, fallback: object | None, name: str) -> object:
    value = _lookup(source, name)
    if not _is_missing(value):
        return value
    return _lookup(fallback, name) if fallback is not None else _MISSING


def _lookup(source: object, name: str) -> object:
    if isinstance(source, Mapping):
        return source.get(name, _MISSING)
    return getattr(source, name, _MISSING)


def validate_property_catalog_settings(values: Mapping[str, Numeric]) -> None:
    """Validate relationships that cannot be expressed by one field's bounds."""

    def value(name: str) -> Numeric:
        return values[f"PROPERTY_CATALOG_{name}"]

    _require_at_most(
        value("REVISION_LEASE_SECONDS"),
        value("MAX_REVISION_LEASE_SECONDS"),
        "revision lease cannot exceed maximum revision lease",
    )
    _require_at_most(
        value("DEV_STANDARD_MAX_WALL_MS"),
        value("DEV_INITIAL_BACKFILL_MAX_WALL_MS"),
        "standard DEV wall cannot exceed initial-backfill DEV wall",
    )
    _require_at_most(
        value("RECONCILE_DEFAULT_EXTENDED_WALL_MS"),
        value("DEV_SCHEDULED_RECONCILE_MAX_WALL_MS"),
        "default reconcile wall cannot exceed scheduled reconcile wall",
    )
    _require_at_most(
        value("SOURCE_MAX_PAGE_BYTES"),
        value("SOURCE_MAX_TOTAL_BYTES"),
        "source page bytes cannot exceed source total bytes",
    )
    _require_at_most(
        value("SOURCE_ADAPTER_WALL_SECONDS"),
        value("SCHEDULED_RECONCILE_SOURCE_ADAPTER_WALL_SECONDS"),
        "source adapter wall cannot exceed scheduled reconcile source adapter wall",
    )
    _require_at_most(
        value("SCHEDULED_RECONCILE_SOURCE_ADAPTER_WALL_SECONDS"),
        value("INITIAL_BACKFILL_SOURCE_ADAPTER_WALL_SECONDS"),
        "scheduled reconcile source adapter wall cannot exceed initial backfill source adapter wall",
    )
    if (
        value("POSTGRES_STATEMENT_TIMEOUT_MS")
        >= value("SOURCE_ADAPTER_WALL_SECONDS") * 1_000
    ):
        raise ValueError("PostgreSQL statement timeout must be below the source wall")
    _require_at_most(
        value("POSTGRES_PAGE_ROWS"),
        value("POSTGRES_MAX_TOTAL_ROWS"),
        "PostgreSQL page rows cannot exceed total rows",
    )
    _require_at_most(
        value("PUBLISHER_WALL_MS"),
        value("DEADLINE_MAX_WALL_MS"),
        "deadline wall cannot be below the publisher wall",
    )
    _require_at_most(
        value("DRAIN_POLL_INTERVAL_MS"),
        value("DRAIN_POLL_CAP_MS"),
        "drain poll interval cannot exceed the poll cap",
    )
    _require_at_most(
        value("STATE_STORE_TIMEOUT_MS"),
        value("PUBLISHER_WALL_MS"),
        "state-store timeout cannot exceed the publisher wall",
    )
    _require_at_most(
        value("READ_MAX_THREADS"),
        value("READ_POOL_SIZE"),
        "ClickHouse read threads cannot exceed the read pool size",
    )
    _require_at_most(
        value("READ_MAX_RESULT_BYTES"),
        min(value("READ_MAX_BYTES"), value("READ_MAX_MEMORY_BYTES")),
        "ClickHouse result bytes cannot exceed read or memory bytes",
    )
    _require_at_most(
        value("READ_EXTERNAL_GROUP_BY_BYTES"),
        value("READ_MAX_MEMORY_BYTES"),
        "ClickHouse external group-by threshold cannot exceed read memory",
    )
    _require_at_most(
        value("READ_EXTERNAL_SORT_BYTES"),
        value("READ_MAX_MEMORY_BYTES"),
        "ClickHouse external sort threshold cannot exceed read memory",
    )
    if (
        value("DEV_INITIAL_BACKFILL_MAX_WALL_MS")
        + value("INITIAL_BACKFILL_LEASE_HEADROOM_MS")
        > value("MAX_REVISION_LEASE_SECONDS") * 1_000
    ):
        raise ValueError(
            "initial-backfill wall plus headroom cannot exceed the maximum lease"
        )
    _require_at_most(
        value("CURSOR_MAX_AGE_SECONDS"),
        value("LINEAGE_ANCHOR_MAX_AGE_SECONDS"),
        "cursor lifetime cannot exceed lineage-anchor retention",
    )
    _require_at_most(
        max(
            value("CANONICAL_SPAN_DEFAULT_PAGE_ROWS"),
            value("DEV_CANONICAL_SPAN_PAGE_ROWS"),
            value("INITIAL_BACKFILL_CANONICAL_SPAN_PAGE_ROWS"),
        ),
        value("CANONICAL_SPAN_PAGE_ROWS"),
        "specialized span page size cannot exceed the maximum",
    )
    _require_at_most(
        value("CANONICAL_SPAN_QUERY_TIMEOUT_MS"),
        value("INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS"),
        "standard span timeout cannot exceed initial backfill timeout",
    )
    _require_at_most(
        value("CANONICAL_SPAN_MAX_THREADS"),
        value("READ_MAX_THREADS"),
        "canonical-span threads cannot exceed catalog read threads",
    )
    _require_at_most(
        value("CANONICAL_SPAN_MAX_GROUP_BYTES"),
        min(value("READ_MAX_BYTES"), value("READ_MAX_MEMORY_BYTES")),
        "canonical-span group bytes cannot exceed read or memory bytes",
    )
    _require_at_most(
        value("AUTHORITATIVE_VALUE_BATCH_MAX_ROWS"),
        value("CANONICAL_SPAN_MAX_GROUPS"),
        "authoritative value batch rows cannot exceed canonical span groups",
    )
    _require_at_most(
        value("AUTHORITATIVE_VALUE_BATCH_MAX_BYTES"),
        value("CANONICAL_SPAN_MAX_GROUP_BYTES"),
        "authoritative value batch bytes cannot exceed canonical group bytes",
    )
    _require_at_most(
        value("RECONCILE_DEFAULT_ENVELOPE_ROWS"),
        value("RECONCILE_MAX_ENVELOPE_ROWS"),
        "default envelope rows cannot exceed maximum envelope rows",
    )
    _require_at_most(
        value("RECONCILE_DEFAULT_MAX_ENVELOPE_BYTES"),
        value("RECONCILE_MAX_ENVELOPE_BYTES"),
        "default envelope bytes cannot exceed maximum envelope bytes",
    )


def validate_dataset_read_settings(values: Mapping[str, Numeric]) -> None:
    """Validate dataset limits that must remain internally consistent."""

    _require_at_most(
        values["DATASET_TABLE_EXACT_MAX_COLUMNS"],
        values["DATASET_TABLE_EXACT_MAX_CELLS"],
        "dataset column limit cannot exceed the cell limit",
    )
    _require_at_most(
        values["DATASET_TABLE_EXACT_MAX_CELL_VALUE_BYTES"],
        values["DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES"],
        "single dataset cell bytes cannot exceed the variable-data budget",
    )
    _require_at_most(
        values["DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES"],
        values["DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES"],
        "dataset variable-data budget cannot exceed the serialized budget",
    )
    _require_at_most(
        values["DATASET_TABLE_EXACT_MAX_SCHEMA_BYTES"],
        values["DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES"],
        "dataset schema budget cannot exceed the serialized budget",
    )
    _require_at_most(
        values["DATASET_ROW_ADJACENCY_MAX_ROWS"],
        values["DATASET_INTERACTIVE_MAX_PAGE_SIZE"],
        "dataset adjacency rows cannot exceed the interactive page maximum",
    )


def validate_interactive_read_settings(values: Mapping[str, Numeric]) -> None:
    """Validate cross-field relationships for interactive read controls."""

    _require_at_most(
        values["INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS"],
        values["CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS"],
        "interactive analytics wall cannot exceed the reviewed ClickHouse ceiling",
    )
    _require_at_most(
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        values["CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS"],
        "interactive read wall cannot exceed the reviewed ClickHouse ceiling",
    )
    _require_at_most(
        values["CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS"],
        values["CLICKHOUSE_APPLICATION_READ_MAX_THREADS"],
        "default ClickHouse read threads cannot exceed the application maximum",
    )
    if not (
        values["CLICKHOUSE_READ_ADMISSION_RETRY_FIRST_MS"]
        <= values["CLICKHOUSE_READ_ADMISSION_RETRY_SECOND_MS"]
        <= values["CLICKHOUSE_READ_ADMISSION_RETRY_THIRD_MS"]
    ):
        raise ValueError("ClickHouse read-admission retry delays must be ordered")
    _require_at_most(
        values["EXACT_GRAPH_TRACE_CANDIDATE_MAX_ROWS"],
        values["EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE"],
        "exact-graph candidate rows cannot exceed the selector page size",
    )
    _require_at_most(
        values["EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE"],
        values["EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE"],
        "exact-graph root verification batch cannot exceed the classifier batch",
    )
    _require_at_most(
        values["EXACT_GRAPH_TRACE_ANCHOR_MIN_PARTITION_HOURS"],
        values["EXACT_GRAPH_TRACE_ANCHOR_PARTITION_HOURS"],
        "exact-graph minimum anchor partition cannot exceed its starting width",
    )
    if not (
        values["EXACT_GRAPH_TRACE_MIN_SLICE_SECONDS"]
        <= values["EXACT_GRAPH_TRACE_INITIAL_SLICE_SECONDS"]
        <= values["EXACT_GRAPH_TRACE_MAX_SLICE_SECONDS"]
    ):
        raise ValueError(
            "exact-graph trace slices must satisfy minimum <= initial <= maximum"
        )
    _require_at_most(
        values["EXACT_GRAPH_SPAN_INITIAL_PARTITION_HOURS"],
        values["EXACT_GRAPH_SPAN_MAX_PARTITION_HOURS"],
        "exact-graph initial span partition cannot exceed its maximum width",
    )
    _require_at_most(
        values["EXACT_GRAPH_TRACE_CLASSIFIER_MAX_THREADS"],
        values["CLICKHOUSE_APPLICATION_READ_MAX_THREADS"],
        "exact-graph classifier threads cannot exceed the application maximum",
    )
    _require_at_most(
        values["ANALYTICS_DEFAULT_LOOKBACK_DAYS"],
        values["EVAL_METRIC_MAX_WINDOW_DAYS"],
        "default analytics lookback cannot exceed the eval-metric maximum window",
    )
    _require_at_most(
        values["MONITOR_GRAPH_CH_TIMEOUT_CAP_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "MONITOR_GRAPH_CH_TIMEOUT_CAP_MS cannot exceed INTERACTIVE_READ_DEFAULT_WALL_MS",
    )
    _require_at_most(
        values["MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS cannot exceed INTERACTIVE_READ_DEFAULT_WALL_MS",
    )
    if (
        not values["FILTER_VALUE_CURSOR_MIN_SEGMENT_SECONDS"]
        <= values["FILTER_VALUE_CURSOR_INITIAL_SEGMENT_SECONDS"]
        <= values["FILTER_VALUE_CURSOR_MAX_SEGMENT_SECONDS"]
    ):
        raise ValueError(
            "filter value cursor segment limits must satisfy minimum <= initial <= maximum"
        )
    _require_at_most(
        values["FILTER_SELECTOR_QUERY_TIMEOUT_MS"],
        values["FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS"],
        "FILTER_SELECTOR_QUERY_TIMEOUT_MS cannot exceed FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS",
    )
    _require_at_most(
        values["FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS"],
        values["FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS"],
        "FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS cannot exceed FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS",
    )
    if (
        values["BULK_SELECTION_MAX_EXCLUDE_COUNT"]
        >= values["BULK_SELECTION_MAX_RAW_PAGE_SIZE"]
    ):
        raise ValueError(
            "BULK_SELECTION_MAX_EXCLUDE_COUNT must be less than BULK_SELECTION_MAX_RAW_PAGE_SIZE"
        )
    if (
        values["BULK_SELECTION_MAX_CAP"] + 1
        > values["BULK_SELECTION_MAX_RAW_PAGE_SIZE"]
    ):
        raise ValueError(
            "BULK_SELECTION_MAX_RAW_PAGE_SIZE must fit the capped result plus its overflow sentinel"
        )
    bulk_prefix_rows = values["BULK_SELECTION_MAX_RAW_PAGE_SIZE"] + 1
    bulk_seed_queries = _ceil_div(
        bulk_prefix_rows,
        values["BULK_SELECTION_MAX_CANDIDATES"],
    )
    if bulk_seed_queries > values["BULK_SELECTION_MAX_SEED_ATTEMPTS"]:
        raise ValueError(
            "BULK_SELECTION_MAX_SEED_ATTEMPTS cannot prove the configured raw page"
        )
    if (
        bounded_bulk_worst_case_query_count(
            raw_page_size=int(values["BULK_SELECTION_MAX_RAW_PAGE_SIZE"]),
            max_candidates=int(values["BULK_SELECTION_MAX_CANDIDATES"]),
            classify_batch_size=int(values["BULK_SELECTION_CLASSIFY_BATCH_SIZE"]),
        )
        > values["BULK_SELECTION_MAX_QUERY_COUNT"]
    ):
        raise ValueError(
            "BULK_SELECTION_MAX_QUERY_COUNT cannot prove the configured raw page"
        )
    _require_at_most(
        values["SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE"],
        values["SIMULATION_PREVIEW_MAX_PAGE_SIZE"],
        "SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE cannot exceed SIMULATION_PREVIEW_MAX_PAGE_SIZE",
    )
    _require_at_most(
        values["DASHBOARD_METRICS_EVAL_USAGE_QUERY_TIMEOUT_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "DASHBOARD_METRICS_EVAL_USAGE_QUERY_TIMEOUT_MS cannot exceed INTERACTIVE_READ_DEFAULT_WALL_MS",
    )
    _require_at_most(
        values["DASHBOARD_METRICS_CATALOG_DEFAULT_PAGE_SIZE"],
        values["DASHBOARD_METRICS_CATALOG_MAX_PAGE_SIZE"],
        "dashboard metrics catalog default page cannot exceed its maximum",
    )
    _require_at_most(
        values["DASHBOARD_FILTER_VALUE_WALL_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "filter-value wall cannot exceed the interactive request wall",
    )
    _require_at_most(
        values["FILTER_VALUE_READ_TIMEOUT_MS"],
        values["INTERACTIVE_READ_DEFAULT_WALL_MS"],
        "filter-value read timeout cannot exceed the interactive request wall",
    )
    _require_at_most(
        values["GRAPH_BACKGROUND_WALL_MS"],
        values["CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS"],
        "background graph wall cannot exceed the reviewed ClickHouse ceiling",
    )
    _require_at_most(
        values["DASHBOARD_FILTER_VALUE_SEARCH_PAGE_SIZE"],
        values["DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE"],
        "filter-value search page cannot exceed the page maximum",
    )
    _require_at_most(
        values["DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE"],
        values["DASHBOARD_FILTER_VALUE_FINITE_MAX"],
        "filter-value page maximum cannot exceed the finite-value maximum",
    )
    _require_at_most(
        values["DASHBOARD_FILTER_VALUE_LEGACY_MAX"],
        values["DASHBOARD_FILTER_VALUE_FINITE_MAX"],
        "legacy filter-value maximum cannot exceed the finite-value maximum",
    )
    _require_at_most(
        values["DASHBOARD_TRACE_MAX_CONCURRENT_METRICS"],
        values["CLICKHOUSE_APPLICATION_READ_MAX_THREADS"],
        "dashboard metric concurrency cannot exceed ClickHouse read threads",
    )
    _require_at_most(
        values["SMART_FILTER_VALUE_READ_WALL_MS"],
        values["SMART_FILTER_REQUEST_WALL_MS"],
        "smart-filter value wall cannot exceed the request wall",
    )
    _require_at_most(
        values["BULK_SELECTION_CLASSIFY_BATCH_SIZE"],
        values["BULK_SELECTION_MAX_CANDIDATES"],
        "bulk-selection classify batch cannot exceed the candidate bound",
    )
    _require_at_most(
        values["PROMPT_METRICS_MAX_CHOICE_UTF8_BYTES"],
        values["PROMPT_METRICS_MAX_TOTAL_CHOICE_UTF8_BYTES"],
        "one prompt choice cannot exceed the total choice-byte budget",
    )
    _require_at_most(
        values["REDIS_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS"],
        values["REDIS_CACHE_SOCKET_TIMEOUT_SECONDS"],
        "Redis connect timeout cannot exceed its socket timeout",
    )
    _require_at_most(
        values["ANNOTATION_QUEUE_AUTOMATION_DEFAULT_PAGE_SIZE"],
        values["ANNOTATION_QUEUE_AUTOMATION_MAX_PAGE_SIZE"],
        "annotation automation default page size cannot exceed its maximum",
    )
    _require_at_most(
        values["EVAL_LOG_DEFAULT_PAGE_SIZE"],
        values["INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE"],
        "EVAL_LOG_DEFAULT_PAGE_SIZE cannot exceed INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE",
    )
    _require_at_most(
        values["EVAL_LOG_REQUIRED_KEYS_LIMIT"],
        values["EVAL_LOG_MAX_COLUMNS"],
        "eval-log required key limit cannot exceed its column limit",
    )
    _require_at_most(
        values["EVAL_TASK_LIST_DEFAULT_PAGE_SIZE"],
        values["INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE"],
        "eval-task default page cannot exceed the interactive maximum",
    )
    _require_at_most(
        values["EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE"],
        values["INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE"],
        "eval-task project page cannot exceed the interactive maximum",
    )
    _require_at_most(
        values["EVAL_TASK_USAGE_DEFAULT_PAGE_SIZE"],
        values["EVAL_TASK_USAGE_MAX_PAGE_SIZE"],
        "eval-task usage default page cannot exceed its maximum",
    )
    _require_at_most(
        values["EVAL_TASK_USAGE_MAX_PAGE_SIZE"],
        values["INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE"],
        "eval-task usage page maximum cannot exceed the interactive maximum",
    )
    _require_at_most(
        values["VOICE_FILTER_CLASSIFY_FALLBACK_BATCH_SIZE"],
        values["VOICE_FILTER_PUBLIC_MAX_PAGE_SIZE"],
        "voice classifier fallback batch cannot exceed the public page maximum",
    )
    _require_at_most(
        values["VOICE_LIST_DEFAULT_PAGE_SIZE"],
        values["VOICE_FILTER_PUBLIC_MAX_PAGE_SIZE"],
        "voice default page cannot exceed the public page maximum",
    )
    _require_at_most(
        values["VOICE_CONTENT_MIN_REMAINING_MS"],
        values["INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS"],
        "voice content reserve cannot exceed the interactive analytics wall",
    )


def validate_runtime_numeric_settings(values: Mapping[str, Numeric]) -> None:
    validate_property_catalog_settings(values)
    validate_dataset_read_settings(values)
    validate_interactive_read_settings(values)
    _require_at_most(
        values["PROPERTY_CATALOG_MAX_PAGE_SIZE"],
        values["DASHBOARD_METRICS_CATALOG_MAX_PAGE_SIZE"],
        "property catalog cursor page cannot exceed the dashboard catalog maximum",
    )


def _require_at_most(left: Numeric, right: Numeric, message: str) -> None:
    if left > right:
        raise ValueError(message)


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def bounded_bulk_worst_case_query_count(
    *,
    raw_page_size: int,
    max_candidates: int,
    classify_batch_size: int,
) -> int:
    """Count seed and classifier reads needed to prove a raw result prefix."""

    if raw_page_size < 0 or max_candidates < 1 or classify_batch_size < 1:
        raise ValueError("bulk-selection query-count inputs must be positive")
    prefix_needed = raw_page_size + 1
    full_seed_pages, final_seed_rows = divmod(prefix_needed, max_candidates)
    classifiers_per_full_seed = _ceil_div(max_candidates, classify_batch_size)
    query_count = full_seed_pages * (1 + classifiers_per_full_seed)
    if final_seed_rows:
        query_count += 1 + _ceil_div(final_seed_rows, classify_batch_size)
    return query_count
