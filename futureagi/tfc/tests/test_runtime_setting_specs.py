from types import SimpleNamespace

import pytest

from tfc.settings.runtime_setting_specs import (
    DATASET_READ_SETTING_SPECS,
    INTERACTIVE_READ_SETTING_SPECS,
    PROPERTY_CATALOG_RUNTIME_SETTING_SPECS,
    RUNTIME_NUMERIC_SETTING_SPECS,
    bounded_bulk_worst_case_query_count,
    load_numeric_settings,
    validate_dataset_read_settings,
    validate_interactive_read_settings,
    validate_property_catalog_settings,
    validate_runtime_numeric_settings,
)


def test_all_runtime_numeric_defaults_are_valid():
    values = load_numeric_settings(RUNTIME_NUMERIC_SETTING_SPECS, source={})

    validate_runtime_numeric_settings(values)
    assert values.keys() == RUNTIME_NUMERIC_SETTING_SPECS.keys()


def test_cross_domain_catalog_page_sizes_are_validated_together():
    values = load_numeric_settings(RUNTIME_NUMERIC_SETTING_SPECS, source={})
    values["PROPERTY_CATALOG_MAX_PAGE_SIZE"] = (
        values["DASHBOARD_METRICS_CATALOG_MAX_PAGE_SIZE"] + 1
    )

    with pytest.raises(ValueError, match="catalog cursor page"):
        validate_runtime_numeric_settings(values)


def test_numeric_settings_accept_mapping_and_object_overrides():
    values = load_numeric_settings(
        INTERACTIVE_READ_SETTING_SPECS,
        source={"DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE": "25"},
        fallback=SimpleNamespace(DASHBOARD_FILTER_VALUE_FINITE_MAX=250),
    )

    assert values["DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE"] == 25
    assert values["DASHBOARD_FILTER_VALUE_FINITE_MAX"] == 250
    assert values["DASHBOARD_FILTER_VALUE_LEGACY_MAX"] == 500


def test_interactive_read_profile_accepts_a_thirty_second_filter_value_wall():
    values = load_numeric_settings(
        INTERACTIVE_READ_SETTING_SPECS,
        source={
            "INTERACTIVE_READ_DEFAULT_WALL_MS": "30000",
            "INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS": "30000",
            "DASHBOARD_FILTER_VALUE_WALL_MS": "30000",
            "FILTER_VALUE_READ_TIMEOUT_MS": "30000",
        },
    )

    validate_interactive_read_settings(values)
    assert values["DASHBOARD_FILTER_VALUE_WALL_MS"] == 30_000
    assert values["FILTER_VALUE_READ_TIMEOUT_MS"] == 30_000


def test_large_tenant_reads_scan_widely_without_unbounding_memory_or_results():
    values = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})

    assert values["DASHBOARD_TRACE_READ_MAX_BYTES"] == 1024**4
    assert values["OBSERVABILITY_LIST_MAX_BYTES"] == 1024**4
    assert values["CLICKHOUSE_APPLICATION_READ_MAX_BYTES"] == 1024**4
    assert values["DASHBOARD_TRACE_READ_MAX_MEMORY_BYTES"] == 36 * 1024**3
    assert values["OBSERVABILITY_LIST_MAX_MEMORY_BYTES"] == 36 * 1024**3
    assert values["OBSERVABILITY_LIST_CELL_PREVIEW_MAX_BYTES"] == 16 * 1024
    assert values["DASHBOARD_TRACE_READ_MAX_RESULT_BYTES"] == 64 * 1024**2
    assert values["DASHBOARD_FILTER_VALUE_MAX_RESULT_BYTES"] == 64 * 1024**2
    assert values["ATTRIBUTE_READ_MAX_RESULT_BYTES"] == 64 * 1024**2


def test_exact_graph_workers_have_a_larger_bounded_wall_than_http_reads():
    values = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})

    assert values["INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS"] == 30_000
    assert values["VOICE_CONTENT_MIN_REMAINING_MS"] == 30_000
    assert values["GRAPH_BACKGROUND_WALL_MS"] == 180_000
    assert values["CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS"] == 180_000
    validate_interactive_read_settings(values)


def test_blank_source_value_uses_fallback_before_default():
    values = load_numeric_settings(
        INTERACTIVE_READ_SETTING_SPECS,
        source={"DASHBOARD_FILTER_VALUE_FINITE_MAX": ""},
        fallback={"DASHBOARD_FILTER_VALUE_FINITE_MAX": 250},
    )

    assert values["DASHBOARD_FILTER_VALUE_FINITE_MAX"] == 250


@pytest.mark.parametrize("raw_value", (True, 1.5, "not-a-number", 0))
def test_numeric_settings_reject_invalid_values(raw_value):
    with pytest.raises(ValueError):
        load_numeric_settings(
            PROPERTY_CATALOG_RUNTIME_SETTING_SPECS,
            source={"PROPERTY_CATALOG_MAX_PROJECTS": raw_value},
        )


def test_property_catalog_cross_field_limits_are_validated():
    values = load_numeric_settings(PROPERTY_CATALOG_RUNTIME_SETTING_SPECS, source={})
    values["PROPERTY_CATALOG_SOURCE_MAX_PAGE_BYTES"] = (
        values["PROPERTY_CATALOG_SOURCE_MAX_TOTAL_BYTES"] + 1
    )

    with pytest.raises(ValueError, match="source page bytes"):
        validate_property_catalog_settings(values)


@pytest.mark.parametrize(
    ("lower_name", "upper_name"),
    (
        (
            "PROPERTY_CATALOG_REVISION_LEASE_SECONDS",
            "PROPERTY_CATALOG_MAX_REVISION_LEASE_SECONDS",
        ),
        (
            "PROPERTY_CATALOG_DEV_STANDARD_MAX_WALL_MS",
            "PROPERTY_CATALOG_DEV_INITIAL_BACKFILL_MAX_WALL_MS",
        ),
        (
            "PROPERTY_CATALOG_RECONCILE_DEFAULT_EXTENDED_WALL_MS",
            "PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_MAX_WALL_MS",
        ),
        (
            "PROPERTY_CATALOG_SOURCE_ADAPTER_WALL_SECONDS",
            "PROPERTY_CATALOG_SCHEDULED_RECONCILE_SOURCE_ADAPTER_WALL_SECONDS",
        ),
        (
            "PROPERTY_CATALOG_SCHEDULED_RECONCILE_SOURCE_ADAPTER_WALL_SECONDS",
            "PROPERTY_CATALOG_INITIAL_BACKFILL_SOURCE_ADAPTER_WALL_SECONDS",
        ),
        (
            "PROPERTY_CATALOG_POSTGRES_PAGE_ROWS",
            "PROPERTY_CATALOG_POSTGRES_MAX_TOTAL_ROWS",
        ),
        ("PROPERTY_CATALOG_PUBLISHER_WALL_MS", "PROPERTY_CATALOG_DEADLINE_MAX_WALL_MS"),
        (
            "PROPERTY_CATALOG_DRAIN_POLL_INTERVAL_MS",
            "PROPERTY_CATALOG_DRAIN_POLL_CAP_MS",
        ),
        (
            "PROPERTY_CATALOG_STATE_STORE_TIMEOUT_MS",
            "PROPERTY_CATALOG_PUBLISHER_WALL_MS",
        ),
        (
            "PROPERTY_CATALOG_READ_MAX_THREADS",
            "PROPERTY_CATALOG_READ_POOL_SIZE",
        ),
        (
            "PROPERTY_CATALOG_READ_MAX_RESULT_BYTES",
            "PROPERTY_CATALOG_READ_MAX_BYTES",
        ),
        (
            "PROPERTY_CATALOG_READ_EXTERNAL_GROUP_BY_BYTES",
            "PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES",
        ),
        (
            "PROPERTY_CATALOG_READ_EXTERNAL_SORT_BYTES",
            "PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES",
        ),
        (
            "PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS",
            "PROPERTY_CATALOG_LINEAGE_ANCHOR_MAX_AGE_SECONDS",
        ),
        (
            "PROPERTY_CATALOG_CANONICAL_SPAN_QUERY_TIMEOUT_MS",
            "PROPERTY_CATALOG_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS",
        ),
        (
            "PROPERTY_CATALOG_CANONICAL_SPAN_MAX_THREADS",
            "PROPERTY_CATALOG_READ_MAX_THREADS",
        ),
        (
            "PROPERTY_CATALOG_AUTHORITATIVE_VALUE_BATCH_MAX_ROWS",
            "PROPERTY_CATALOG_CANONICAL_SPAN_MAX_GROUPS",
        ),
        (
            "PROPERTY_CATALOG_AUTHORITATIVE_VALUE_BATCH_MAX_BYTES",
            "PROPERTY_CATALOG_CANONICAL_SPAN_MAX_GROUP_BYTES",
        ),
        (
            "PROPERTY_CATALOG_RECONCILE_DEFAULT_ENVELOPE_ROWS",
            "PROPERTY_CATALOG_RECONCILE_MAX_ENVELOPE_ROWS",
        ),
        (
            "PROPERTY_CATALOG_RECONCILE_DEFAULT_MAX_ENVELOPE_BYTES",
            "PROPERTY_CATALOG_RECONCILE_MAX_ENVELOPE_BYTES",
        ),
    ),
)
def test_property_catalog_ordered_limits_reject_inverted_values(lower_name, upper_name):
    values = load_numeric_settings(PROPERTY_CATALOG_RUNTIME_SETTING_SPECS, source={})
    values[lower_name] = values[upper_name] + 1

    with pytest.raises(ValueError):
        validate_property_catalog_settings(values)


def test_property_catalog_statement_timeout_requires_source_wall_headroom():
    values = load_numeric_settings(PROPERTY_CATALOG_RUNTIME_SETTING_SPECS, source={})
    values["PROPERTY_CATALOG_POSTGRES_STATEMENT_TIMEOUT_MS"] = int(
        values["PROPERTY_CATALOG_SOURCE_ADAPTER_WALL_SECONDS"] * 1_000
    )

    with pytest.raises(ValueError, match="statement timeout"):
        validate_property_catalog_settings(values)


def test_property_catalog_initial_backfill_wall_requires_lease_headroom():
    values = load_numeric_settings(PROPERTY_CATALOG_RUNTIME_SETTING_SPECS, source={})
    values["PROPERTY_CATALOG_MAX_REVISION_LEASE_SECONDS"] -= 1

    with pytest.raises(ValueError, match="initial-backfill wall plus headroom"):
        validate_property_catalog_settings(values)


def test_property_catalog_specialized_span_pages_fit_the_canonical_page():
    values = load_numeric_settings(PROPERTY_CATALOG_RUNTIME_SETTING_SPECS, source={})
    values["PROPERTY_CATALOG_DEV_CANONICAL_SPAN_PAGE_ROWS"] = (
        values["PROPERTY_CATALOG_CANONICAL_SPAN_PAGE_ROWS"] + 1
    )

    with pytest.raises(ValueError, match="specialized span page"):
        validate_property_catalog_settings(values)


@pytest.mark.parametrize(
    ("lower_name", "upper_name"),
    (
        (
            "DATASET_TABLE_EXACT_MAX_COLUMNS",
            "DATASET_TABLE_EXACT_MAX_CELLS",
        ),
        (
            "DATASET_TABLE_EXACT_MAX_CELL_VALUE_BYTES",
            "DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES",
        ),
        (
            "DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES",
            "DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES",
        ),
        (
            "DATASET_TABLE_EXACT_MAX_SCHEMA_BYTES",
            "DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES",
        ),
        (
            "DATASET_ROW_ADJACENCY_MAX_ROWS",
            "DATASET_INTERACTIVE_MAX_PAGE_SIZE",
        ),
    ),
)
def test_dataset_ordered_limits_reject_inverted_values(lower_name, upper_name):
    values = load_numeric_settings(DATASET_READ_SETTING_SPECS, source={})
    values[lower_name] = values[upper_name] + 1

    with pytest.raises(ValueError):
        validate_dataset_read_settings(values)


def test_interactive_cross_field_limits_are_validated():
    values = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    values["BULK_SELECTION_MAX_EXCLUDE_COUNT"] = values[
        "BULK_SELECTION_MAX_RAW_PAGE_SIZE"
    ]

    with pytest.raises(ValueError, match="MAX_EXCLUDE_COUNT"):
        validate_interactive_read_settings(values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        (
            {
                "BULK_SELECTION_MAX_RAW_PAGE_SIZE": 10_000,
                "BULK_SELECTION_MAX_EXCLUDE_COUNT": 9_999,
            },
            "overflow sentinel",
        ),
        ({"BULK_SELECTION_MAX_SEED_ATTEMPTS": 63}, "MAX_SEED_ATTEMPTS"),
        ({"BULK_SELECTION_MAX_QUERY_COUNT": 127}, "MAX_QUERY_COUNT"),
    ),
)
def test_bulk_selection_runtime_limits_must_form_a_complete_proof_budget(
    overrides, message
):
    values = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        validate_interactive_read_settings(values)


def test_bulk_selection_query_budget_formula_is_shared_with_the_resolver():
    assert (
        bounded_bulk_worst_case_query_count(
            raw_page_size=12_799,
            max_candidates=200,
            classify_batch_size=200,
        )
        == 128
    )


@pytest.mark.parametrize(
    ("lower_name", "upper_name"),
    (
        (
            "INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS",
            "CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS",
        ),
        (
            "INTERACTIVE_READ_DEFAULT_WALL_MS",
            "CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS",
        ),
        (
            "CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS",
            "CLICKHOUSE_APPLICATION_READ_MAX_THREADS",
        ),
        (
            "EXACT_GRAPH_TRACE_CANDIDATE_MAX_ROWS",
            "EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE",
        ),
        (
            "EXACT_GRAPH_TRACE_ANCHOR_MIN_PARTITION_HOURS",
            "EXACT_GRAPH_TRACE_ANCHOR_PARTITION_HOURS",
        ),
        (
            "EXACT_GRAPH_TRACE_CLASSIFIER_MAX_THREADS",
            "CLICKHOUSE_APPLICATION_READ_MAX_THREADS",
        ),
        ("ANALYTICS_DEFAULT_LOOKBACK_DAYS", "EVAL_METRIC_MAX_WINDOW_DAYS"),
        ("MONITOR_GRAPH_CH_TIMEOUT_CAP_MS", "INTERACTIVE_READ_DEFAULT_WALL_MS"),
        (
            "MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS",
            "INTERACTIVE_READ_DEFAULT_WALL_MS",
        ),
        (
            "FILTER_SELECTOR_QUERY_TIMEOUT_MS",
            "FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS",
        ),
        (
            "FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS",
            "FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS",
        ),
        (
            "DASHBOARD_METRICS_EVAL_USAGE_QUERY_TIMEOUT_MS",
            "INTERACTIVE_READ_DEFAULT_WALL_MS",
        ),
        (
            "DASHBOARD_FILTER_VALUE_WALL_MS",
            "INTERACTIVE_READ_DEFAULT_WALL_MS",
        ),
        (
            "FILTER_VALUE_READ_TIMEOUT_MS",
            "INTERACTIVE_READ_DEFAULT_WALL_MS",
        ),
        (
            "GRAPH_BACKGROUND_WALL_MS",
            "CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS",
        ),
        (
            "DASHBOARD_FILTER_VALUE_SEARCH_PAGE_SIZE",
            "DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE",
        ),
        (
            "DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE",
            "DASHBOARD_FILTER_VALUE_FINITE_MAX",
        ),
        (
            "DASHBOARD_FILTER_VALUE_LEGACY_MAX",
            "DASHBOARD_FILTER_VALUE_FINITE_MAX",
        ),
        (
            "DASHBOARD_TRACE_MAX_CONCURRENT_METRICS",
            "CLICKHOUSE_APPLICATION_READ_MAX_THREADS",
        ),
        (
            "SMART_FILTER_VALUE_READ_WALL_MS",
            "SMART_FILTER_REQUEST_WALL_MS",
        ),
        (
            "BULK_SELECTION_CLASSIFY_BATCH_SIZE",
            "BULK_SELECTION_MAX_CANDIDATES",
        ),
        (
            "PROMPT_METRICS_MAX_CHOICE_UTF8_BYTES",
            "PROMPT_METRICS_MAX_TOTAL_CHOICE_UTF8_BYTES",
        ),
        (
            "REDIS_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS",
            "REDIS_CACHE_SOCKET_TIMEOUT_SECONDS",
        ),
    ),
)
def test_interactive_ordered_limits_reject_inverted_values(lower_name, upper_name):
    values = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    values[lower_name] = values[upper_name] + 1

    with pytest.raises(ValueError):
        validate_interactive_read_settings(values)


def test_clickhouse_admission_retry_delays_must_be_ordered():
    values = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    values["CLICKHOUSE_READ_ADMISSION_RETRY_SECOND_MS"] = (
        values["CLICKHOUSE_READ_ADMISSION_RETRY_FIRST_MS"] - 1
    )

    with pytest.raises(ValueError, match="retry delays"):
        validate_interactive_read_settings(values)


@pytest.mark.parametrize(
    ("minimum_name", "initial_name", "maximum_name"),
    (
        (
            "EXACT_GRAPH_TRACE_MIN_SLICE_SECONDS",
            "EXACT_GRAPH_TRACE_INITIAL_SLICE_SECONDS",
            "EXACT_GRAPH_TRACE_MAX_SLICE_SECONDS",
        ),
        (
            "FILTER_VALUE_CURSOR_MIN_SEGMENT_SECONDS",
            "FILTER_VALUE_CURSOR_INITIAL_SEGMENT_SECONDS",
            "FILTER_VALUE_CURSOR_MAX_SEGMENT_SECONDS",
        ),
    ),
)
def test_interactive_three_part_ranges_reject_an_initial_value_below_minimum(
    minimum_name, initial_name, maximum_name
):
    values = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    values[initial_name] = values[minimum_name] - 1
    assert values[maximum_name] >= values[minimum_name]

    with pytest.raises(ValueError):
        validate_interactive_read_settings(values)


def test_simulation_preview_default_cannot_exceed_configured_maximum():
    values = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    values["SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE"] = 51
    values["SIMULATION_PREVIEW_MAX_PAGE_SIZE"] = 50

    with pytest.raises(ValueError, match="DEFAULT_PAGE_SIZE"):
        validate_interactive_read_settings(values)


@pytest.mark.parametrize(
    ("default_name", "maximum_name"),
    (
        (
            "ANNOTATION_QUEUE_AUTOMATION_DEFAULT_PAGE_SIZE",
            "ANNOTATION_QUEUE_AUTOMATION_MAX_PAGE_SIZE",
        ),
        ("EVAL_LOG_DEFAULT_PAGE_SIZE", "INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE"),
    ),
)
def test_interactive_default_page_sizes_cannot_exceed_their_maximum(
    default_name, maximum_name
):
    values = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    values[default_name] = values[maximum_name] + 1

    with pytest.raises(ValueError):
        validate_interactive_read_settings(values)
