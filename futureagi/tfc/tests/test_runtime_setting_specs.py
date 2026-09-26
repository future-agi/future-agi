import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from tfc.settings import runtime_setting_specs
from tfc.settings.runtime_setting_specs import (
    DATASET_READ_SETTING_SPECS,
    INTERACTIVE_READ_SETTING_SPECS,
    LONGEST_RUNNING_ENTRY_SECONDS,
    PROPERTY_CATALOG_RUNTIME_SETTING_SPECS,
    RUNTIME_NUMERIC_SETTING_SPECS,
    bounded_bulk_worst_case_query_count,
    load_numeric_settings,
    validate_dataset_read_settings,
    validate_eval_execution_settings,
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


def test_population_worker_budget_is_separate_and_can_be_lowered():
    defaults = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    assert defaults["FILTER_SELECTOR_MAX_THREADS"] == 1
    assert defaults["FILTER_SELECTOR_POPULATION_MAX_THREADS"] == 2
    reduced = load_numeric_settings(
        INTERACTIVE_READ_SETTING_SPECS,
        source={"FILTER_SELECTOR_POPULATION_MAX_THREADS": "1"},
    )
    validate_interactive_read_settings(reduced)
    assert reduced["FILTER_SELECTOR_POPULATION_MAX_THREADS"] == 1


def test_wide_seed_worker_budget_defaults_to_four_and_is_bounded():
    defaults = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    assert defaults["FILTER_SELECTOR_WIDE_SEED_MAX_THREADS"] == 4
    # Separate from the narrow-seed worker and the population proof's budget.
    assert defaults["FILTER_SELECTOR_MAX_THREADS"] == 1
    assert defaults["FILTER_SELECTOR_POPULATION_MAX_THREADS"] == 2
    lowered = load_numeric_settings(
        INTERACTIVE_READ_SETTING_SPECS,
        source={"FILTER_SELECTOR_WIDE_SEED_MAX_THREADS": "1"},
    )
    validate_interactive_read_settings(lowered)
    assert lowered["FILTER_SELECTOR_WIDE_SEED_MAX_THREADS"] == 1
    with pytest.raises(ValueError):
        load_numeric_settings(
            INTERACTIVE_READ_SETTING_SPECS,
            source={"FILTER_SELECTOR_WIDE_SEED_MAX_THREADS": "9"},
        )


def test_text_seed_row_budget_is_operator_tunable_within_a_measured_range():
    """The short exact-string seed is sized by rows read, not by slice hours."""

    defaults = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    assert defaults["FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS"] == 2_000_000

    lowered = load_numeric_settings(
        INTERACTIVE_READ_SETTING_SPECS,
        source={"FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS": "100000"},
    )
    validate_interactive_read_settings(lowered)
    assert lowered["FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS"] == 100_000


@pytest.mark.parametrize("value", [0, -1, 99_999, 50_000_001, True, "unlimited"])
def test_text_seed_row_budget_rejects_values_outside_the_measured_range(value):
    with pytest.raises(ValueError):
        load_numeric_settings(
            INTERACTIVE_READ_SETTING_SPECS,
            source={"FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS": value},
        )


def test_the_text_seed_witness_slack_defaults_to_one_hour_with_zero_as_the_hatch():
    """The bounded witness is the default contract; zero is the way back.

    One hour is the owner-decided default: on the measured cohort it omitted
    nothing (largest child-witness lag 468 s; the root carried the value itself
    in 165 of 165 matching traces) while reading 28.7x fewer bytes. ZERO
    remains a settable value and emits no envelope at all, so an install whose
    spans really do arrive more than an hour after their root can be put back
    on the unbounded contract without a deploy.
    """

    defaults = load_numeric_settings(INTERACTIVE_READ_SETTING_SPECS, source={})
    assert defaults["FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS"] == 1

    for raw, expected in (("0", 0), (0, 0), ("1", 1), (24, 24), ("168", 168)):
        enabled = load_numeric_settings(
            INTERACTIVE_READ_SETTING_SPECS,
            source={"FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS": raw},
        )
        validate_interactive_read_settings(enabled)
        assert enabled["FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS"] == expected


@pytest.mark.parametrize("value", [-1, 169, 1.5, True, "off"])
def test_the_text_seed_witness_slack_rejects_values_outside_one_week(value):
    with pytest.raises(ValueError):
        load_numeric_settings(
            INTERACTIVE_READ_SETTING_SPECS,
            source={"FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS": value},
        )


@pytest.mark.parametrize("value", [0, -1, 5, True, "unlimited"])
def test_population_worker_budget_rejects_unbounded_or_invalid_values(value):
    with pytest.raises(ValueError):
        load_numeric_settings(
            INTERACTIVE_READ_SETTING_SPECS,
            source={"FILTER_SELECTOR_POPULATION_MAX_THREADS": value},
        )


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
            source={"PROPERTY_CATALOG_MAX_PAGE_SIZE": raw_value},
        )


@pytest.mark.parametrize(
    ("lower_name", "upper_name"),
    (
        ("PROPERTY_CATALOG_READ_MAX_THREADS", "PROPERTY_CATALOG_READ_POOL_SIZE"),
        ("PROPERTY_CATALOG_READ_MAX_RESULT_BYTES", "PROPERTY_CATALOG_READ_MAX_BYTES"),
        (
            "PROPERTY_CATALOG_READ_EXTERNAL_GROUP_BY_BYTES",
            "PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES",
        ),
        (
            "PROPERTY_CATALOG_READ_EXTERNAL_SORT_BYTES",
            "PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES",
        ),
    ),
)
def test_property_catalog_read_limits_reject_inverted_values(lower_name, upper_name):
    values = load_numeric_settings(PROPERTY_CATALOG_RUNTIME_SETTING_SPECS, source={})
    values[lower_name] = values[upper_name] + 1

    with pytest.raises(ValueError):
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
        (
            "USER_LIST_WALK_INITIAL_SLICE_SECONDS",
            "USER_LIST_WALK_MAX_SLICE_SECONDS",
        ),
        (
            "USER_LIST_WALK_MIN_SLICE_SECONDS",
            "USER_LIST_WALK_INITIAL_SLICE_SECONDS",
        ),
        (
            "USER_LIST_PAGE_WALL_MS",
            "INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS",
        ),
        (
            "USER_LIST_WALK_PROBE_WALL_MS",
            "USER_LIST_PAGE_WALL_MS",
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


def _declared_spec_names_by_collection():
    """Read the spec SOURCE and return every declared name per collection.

    The built dictionaries cannot answer this question. ``_specs`` is a dict
    comprehension and a collection is a ``{**_specs(...), **_specs(...)}``
    merge, so a name declared twice inside one collection is silently
    last-wins: both copies collapse to a single key and, when the tuples
    agree, to an identical spec. Only the source text still carries the
    second declaration, so only the source can prove there is one.

    A collection that merges other collections rather than declaring rows of
    its own is not returned: it holds no ``_specs`` call, and cross-collection
    collisions are already refused at import by ``runtime_setting_specs``
    itself.
    """

    tree = ast.parse(Path(runtime_setting_specs.__file__).read_text())
    collections = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not targets or not targets[0].endswith("_SETTING_SPECS"):
            continue
        names = []
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            # ``key is None`` is a ``**`` spread; anything else is an explicit
            # entry and carries no rows.
            if key is not None or not isinstance(value, ast.Call):
                continue
            if not isinstance(value.func, ast.Name) or value.func.id != "_specs":
                continue
            prefix = next(
                (
                    keyword.value.value
                    for keyword in value.keywords
                    if keyword.arg == "prefix"
                    and isinstance(keyword.value, ast.Constant)
                ),
                "",
            )
            for row in value.args[0].elts:
                names.append(f"{prefix}{row.elts[0].value}")
        if names:
            collections[targets[0]] = names
    return collections


def test_the_source_reader_sees_the_rows_it_is_meant_to_guard():
    """A scan that found nothing must not read as a clean scan."""

    collections = _declared_spec_names_by_collection()

    assert set(collections) == {
        "PROPERTY_CATALOG_RUNTIME_SETTING_SPECS",
        "DATASET_READ_SETTING_SPECS",
        "EVAL_EXECUTION_SETTING_SPECS",
        "INTERACTIVE_READ_SETTING_SPECS",
    }
    interactive = collections["INTERACTIVE_READ_SETTING_SPECS"]
    assert "INTERACTIVE_READ_DEFAULT_WALL_MS" in interactive
    assert "USER_LIST_PAGE_WALL_MS" in interactive
    # The prefixed collections are reconstructed, not read verbatim.
    assert (
        "PROPERTY_CATALOG_QUERY_WALL_MS"
        in collections["PROPERTY_CATALOG_RUNTIME_SETTING_SPECS"]
    )
    # Every declared name resolves to a real spec, so the reader is reading
    # the same rows the runtime loads.
    for collection, names in collections.items():
        built = getattr(runtime_setting_specs, collection)
        assert set(names) == set(built)


def test_no_setting_name_is_declared_twice_inside_one_spec_collection():
    """A second declaration of the same name is a silent last-wins edit.

    Two PRs adding the same wall to the same collection produce no error,
    no warning and no behaviour change while their tuples happen to agree;
    the day one of them moves its bounds, the winner is whichever row is
    written later in the file.
    """

    duplicates = {
        collection: sorted({name for name in names if names.count(name) > 1})
        for collection, names in _declared_spec_names_by_collection().items()
        if len(set(names)) != len(names)
    }

    assert not duplicates, (
        "these runtime setting names are declared more than once inside a "
        "single spec collection, where the dict comprehension silently keeps "
        f"the last row: {duplicates}"
    )


def test_env_example_declares_each_setting_once():
    """The documented default must not disagree with itself either."""

    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    keys = [
        line.split("=", 1)[0]
        for line in env_example.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    ]

    assert keys, f"{env_example} parsed to no assignments at all"
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, (
        f"{env_example.name} assigns these keys more than once: {duplicates}"
    )


# Runtime settings that have no line in ``.env.example`` today. This list is a
# record of existing debt, not a licence: it is frozen so the gap cannot grow,
# and the only correct way to change it is to DELETE a name after documenting
# that setting. It was 130 names when this train started; two of them belonged
# to the train itself and were documented rather than listed here.
_UNDOCUMENTED_RUNTIME_SETTINGS = frozenset(
    (
        "DASHBOARD_FILTER_VALUE_COMPAT_LOOKBACK_DAYS",
        "DASHBOARD_FILTER_VALUE_FINITE_MAX",
        "DASHBOARD_FILTER_VALUE_LEGACY_MAX",
        "DASHBOARD_FILTER_VALUE_SEARCH_PAGE_SIZE",
        "DASHBOARD_METRICS_ATTRIBUTE_KEY_LIMIT",
        "DASHBOARD_METRICS_ATTRIBUTE_WORKERS",
        "DASHBOARD_ROLLUP_MAX_POINTS",
        "DASHBOARD_ROLLUP_MAX_QUERIES",
        "DASHBOARD_ROLLUP_MAX_RESULT_BYTES",
        "DATASET_INTERACTIVE_MAX_OFFSET_ROWS",
        "DATASET_INTERACTIVE_MAX_PAGE_SIZE",
        "DATASET_TABLE_CURSOR_MAX_AGE_SECONDS",
        "DATASET_TABLE_EXACT_MAX_CELLS",
        "DATASET_TABLE_EXACT_MAX_CELL_VALUE_BYTES",
        "DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES",
        "DATASET_TABLE_EXACT_MAX_SCHEMA_BYTES",
        "DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES",
        "DATASET_TABLE_SERVER_WALL_SECONDS",
        "EVAL_LOG_COLUMN_DEADLINE_CHECK_INTERVAL",
        "EVAL_LOG_MAX_COLUMNS",
        "EVAL_LOG_MAX_OFFSET",
        "EVAL_LOG_MAX_SEARCH_COLUMNS",
        "EVAL_LOG_MAX_SEARCH_LENGTH",
        "EVAL_LOG_MAX_SORT_COLUMNS",
        "EVAL_LOG_ROW_DEADLINE_CHECK_INTERVAL",
        "EVAL_TASK_ERROR_GROUPS_LIMIT",
        "EVAL_TASK_ERROR_TEXT_MAX_CHARS",
        "EVAL_TASK_LIST_COMPATIBILITY_FILTER_UNITS",
        "EVAL_TASK_LIST_COMPATIBILITY_RELATION_LIMIT",
        "EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT",
        "EVAL_TASK_LIST_MAX_OFFSET",
        "EVAL_TASK_ROOT_JSON_PREFLIGHT_UNITS",
        "EVAL_TASK_USAGE_AGGREGATION_JSON_MAX_CHARS",
        "EVAL_TASK_USAGE_AGGREGATION_JSON_MAX_UNITS",
        "EVAL_TASK_USAGE_AGGREGATION_ROW_LIMIT",
        "EVAL_TASK_USAGE_DETAIL_TEXT_MAX_CHARS",
        "EVAL_TASK_USAGE_JSON_PREVIEW_MAX_CHARS",
        "EVAL_TASK_USAGE_MAPPING_ENTRY_LIMIT",
        "EVAL_TASK_USAGE_MAPPING_JSON_MAX_CHARS",
        "EVAL_TASK_USAGE_MAPPING_PATH_LIMIT",
        "EVAL_TASK_USAGE_MAX_CHART_POINTS",
        "EVAL_TASK_USAGE_OMITTED_FIELDS_LIMIT",
        "EVAL_TASK_WARNING_GROUPS_LIMIT",
        "EVAL_TASK_WARNING_KEY_LIMIT",
        "EVAL_TASK_WARNING_KEY_MAX_CHARS",
        "EVAL_TASK_WARNING_LOG_SCAN_LIMIT",
        "EVAL_TASK_WARNING_MESSAGE_MAX_CHARS",
        "FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS",
        "FILTER_SELECTOR_MAX_NUMBERED_PAGE_WORK_ROWS",
        "FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS",
        "FILTER_SELECTOR_NUMERIC_LONG_TEXT_SEED_WITNESS_SLACK_HOURS",
        "FILTER_SELECTOR_POPULATION_MAX_THREADS",
        "FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS",
        "FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS",
        "FILTER_VALUE_CURSOR_INITIAL_SEGMENT_SECONDS",
        "FILTER_VALUE_CURSOR_MAX_SEGMENT_SECONDS",
        "FILTER_VALUE_CURSOR_MIN_SEGMENT_SECONDS",
        "FILTER_VALUE_CURSOR_SCAN_LIMIT",
        "FILTER_VALUE_READ_MAX_THREADS",
        "GRAPH_SPAN_METRIC_BATCH_SIZE",
        "GRAPH_TRACE_DECORATION_CANDIDATE_LIMIT",
        "INTERACTIVE_READ_DEFAULT_MAX_RESPONSE_UNITS",
        "OBSERVABILITY_LIST_MAX_BLOCK_SIZE",
        "OBSERVABILITY_LIST_MAX_RESULT_ROWS",
        "OBSERVABILITY_NAVIGATION_CANDIDATE_LIMIT",
        "OBSERVABILITY_NAVIGATION_MAX_QUERIES",
        "OBSERVABILITY_NAVIGATION_SCAN_PAGE_SIZE",
        "PROMPT_METRICS_MAX_CHOICE_UTF8_BYTES",
        "PROMPT_METRICS_MAX_EVAL_COLUMNS",
        "PROMPT_METRICS_MAX_OFFSET",
        "PROMPT_METRICS_MAX_TOTAL_CHOICE_UTF8_BYTES",
        "PROMPT_METRICS_SPAN_PAGE_DB_PAYLOAD_BYTES",
        "PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS",
        "PROPERTY_CATALOG_QUERY_WALL_MS",
        "PROPERTY_CATALOG_READ_EXTERNAL_GROUP_BY_BYTES",
        "PROPERTY_CATALOG_READ_EXTERNAL_SORT_BYTES",
        "PROPERTY_CATALOG_READ_MAX_BYTES",
        "PROPERTY_CATALOG_READ_MAX_CONCURRENT_QUERIES_PER_USER",
        "PROPERTY_CATALOG_READ_MAX_MEMORY_BYTES",
        "PROPERTY_CATALOG_READ_MAX_RESULT_BYTES",
        "PROPERTY_CATALOG_READ_MAX_THREADS",
        "PROPERTY_CATALOG_READ_POOL_SIZE",
        "PROPERTY_CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS",
        "SESSION_LIST_FILTER_MAX_CANDIDATES",
        "SESSION_LIST_FILTER_MAX_QUERIES",
        "SESSION_LIST_FILTER_MAX_SEED_ATTEMPTS",
        "SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS",
        "SESSION_LIST_MAX_RESULT_BYTES",
        "SESSION_LIST_READ_MAX_THREADS",
        "SIMULATION_PREVIEW_CURSOR_MAX_AGE_SECONDS",
        "SMART_FILTER_GROUNDED_VALUE_LIMIT",
        "SMART_FILTER_PROJECT_SCOPE_LIMIT",
        "SMART_FILTER_REQUEST_WALL_MS",
        "SMART_FILTER_SEARCH_MAX_BYTES",
        "SMART_FILTER_VALUE_LIMIT",
        "SMART_FILTER_VALUE_READ_WALL_MS",
        "TRACE_LIST_ANNOTATION_SCORE_SPAN_LIMIT",
        "TRACE_LIST_ENRICHMENT_CHUNK_SIZE",
        "TRACE_LIST_ENRICHMENT_MAX_WORKERS",
        "VOICE_CONTENT_MAX_QUERY_ATTEMPTS",
        "VOICE_FILTER_TEXT_SEED_WITNESS_SLACK_HOURS",
    )
)


def _env_example_assignments():
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    return {
        line.split("=", 1)[0].strip()
        for line in env_example.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }


def test_every_new_runtime_setting_is_documented_in_env_example():
    """An operator cannot tune a bound they cannot see.

    Every bound in this file is meant to be operator-tunable, which means it
    needs a line in ``.env.example``. Most already have one; the rest are
    named in ``_UNDOCUMENTED_RUNTIME_SETTINGS`` as existing debt. What this
    guard exists for is the NEXT setting: one added without a line fails
    here, because it is in the specs, absent from the example, and not on the
    frozen list.
    """

    documented = _env_example_assignments()
    undocumented = {
        name for name in RUNTIME_NUMERIC_SETTING_SPECS if name not in documented
    }
    assert undocumented, "the reader found nothing at all; check the parser"

    new_and_undocumented = sorted(undocumented - _UNDOCUMENTED_RUNTIME_SETTINGS)
    assert not new_and_undocumented, (
        "these runtime settings have no line in .env.example and are not on "
        "the frozen debt list, so an operator has no way to see or tune "
        f"them: {new_and_undocumented}"
    )


def test_the_undocumented_list_does_not_name_a_setting_that_is_documented():
    """The debt list must shrink honestly, never drift.

    A name that has since been documented has to leave the list, otherwise
    the list stops being a record of what is missing and starts hiding the
    fact that the gap closed.
    """

    documented = _env_example_assignments()
    stale = sorted(_UNDOCUMENTED_RUNTIME_SETTINGS & documented)
    assert not stale, (
        f"these are documented in .env.example and must be removed from "
        f"_UNDOCUMENTED_RUNTIME_SETTINGS: {stale}"
    )

    unknown = sorted(
        _UNDOCUMENTED_RUNTIME_SETTINGS - set(RUNTIME_NUMERIC_SETTING_SPECS)
    )
    assert not unknown, (
        f"these are on the debt list but are no longer settings at all: {unknown}"
    )


def test_eval_execution_settings_are_validated_as_a_group():
    """Every other spec group declares a validator; the eval-execution group
    was added without one, so nothing re-checked its relations against the
    workflow ceilings they are derived from. The defaults must pass."""
    values = load_numeric_settings(RUNTIME_NUMERIC_SETTING_SPECS, source={})

    validate_eval_execution_settings(values)


def test_a_stale_threshold_that_can_race_a_live_worker_is_rejected():
    """Known positive for the sweep's stale threshold: at or below one running
    entry's longest legitimate life the sweep requeues entries a worker is
    still evaluating. Called through the whole validator with the resolved
    mapping, so it fires on a value the spec bounds alone would miss if
    someone widened them."""
    values = load_numeric_settings(RUNTIME_NUMERIC_SETTING_SPECS, source={})
    values["EVAL_TASK_SWEEP_STALE_RUNNING_SECONDS"] = LONGEST_RUNNING_ENTRY_SECONDS

    with pytest.raises(ValueError, match="longest legitimate life"):
        validate_runtime_numeric_settings(values)


def test_the_declared_stale_threshold_range_cannot_race_a_live_worker():
    """The bound an operator can actually reach. The old minimum was 600 s,
    nine times below the floor, so a deployment configured anywhere in
    600-5399 passed parsing and raced live workers while the invariant test
    -- which read the running value -- still passed on the default."""
    spec = RUNTIME_NUMERIC_SETTING_SPECS["EVAL_TASK_SWEEP_STALE_RUNNING_SECONDS"]

    assert spec.minimum > LONGEST_RUNNING_ENTRY_SECONDS
    with pytest.raises(ValueError, match="must be between"):
        spec.parse(
            "EVAL_TASK_SWEEP_STALE_RUNNING_SECONDS", LONGEST_RUNNING_ENTRY_SECONDS
        )
