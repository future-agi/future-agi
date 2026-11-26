"""Provenance/negative controls for the historical and pinned-dev SQL fixtures.

No source-authority enrollment is implied. Native execution uses the maintained
CH25 reduced-schema fixtures, never a replacement compiler or fake dependency.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.selectors.filter_seed_width import EMPTY_DENSITY_ESTIMATE
from tracer.selectors.session_candidate_slice import (
    _CANDIDATE_SLICE_TARGET_INDEX_ROWS,
    _SliceReader,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests._base_list_parity import (
    _load_frozen_session_builder,
    assert_sql_and_parameters_equal,
    baseline_fixture,
    baseline_statements,
    filters,
    historical_session_builder,
    native_session_builder,
)


def test_lexer_ignores_layout_but_preserves_quoted_whitespace():
    assert_sql_and_parameters_equal(
        ("SELECT\n 'a  b' AS value", {}), ("SELECT 'a  b' AS value", {})
    )
    for altered in ("SELECT 'a b' AS value", "SELECT 'a  b' AS other"):
        with pytest.raises(AssertionError, match="SQL token drift"):
            assert_sql_and_parameters_equal(
                (altered, {}), ("SELECT 'a  b' AS value", {})
            )


@pytest.mark.parametrize(
    "before,after",
    [
        ("latest_is_deleted = 0", "latest_is_deleted != 0"),
        (" AS total_cost", " AS changed_cost"),
        ("count()", "uniqExact(session_id)"),
        ("min(start_time)", "max(start_time)"),
    ],
)
def test_comparator_rejects_real_operator_alias_and_aggregation_drift(before, after):
    statements = baseline_statements(native_session_builder())
    matching = [row for row in statements.values() if before in row["sql"]]
    assert matching, "negative control must mutate a real compiled statement"
    row = matching[0]
    with pytest.raises(AssertionError, match="SQL token drift"):
        assert_sql_and_parameters_equal(
            (row["sql"].replace(before, after, 1), row["params"]),
            (row["sql"], row["params"]),
        )


def test_legacy_parity_entry_point_uses_explicit_native_dev_contract():
    from tracer.tests._base_list_parity import old_session_builder

    assert baseline_statements(old_session_builder()) == baseline_statements(
        native_session_builder()
    )


def test_comparator_preserves_bound_parameter_types():
    with pytest.raises(AssertionError, match="SQL parameter drift"):
        assert_sql_and_parameters_equal(
            ("SELECT %(x)s", {"x": True}), ("SELECT %(x)s", {"x": 1})
        )


def test_comparator_rejects_parameter_and_placeholder_drift():
    row = baseline_statements(native_session_builder())["build_candidate_page_query"]
    changed = {**row["params"], "project_id": str(UUID(int=99))}
    with pytest.raises(AssertionError, match="SQL parameter drift"):
        assert_sql_and_parameters_equal(
            (row["sql"], changed), (row["sql"], row["params"])
        )
    assert "%(project_id)s" in row["sql"]
    with pytest.raises(AssertionError, match="SQL token drift"):
        assert_sql_and_parameters_equal(
            (
                row["sql"].replace("%(project_id)s", "%(foreign_project)s"),
                row["params"],
            ),
            (row["sql"], row["params"]),
        )


def test_frozen_oracles_do_not_import_installed_usage_helpers(monkeypatch):
    from tracer.services.clickhouse.query_builders import trace_usage

    def reject(*args, **kwargs):
        raise AssertionError("installed helper contaminated historical oracle")

    for name in ("usage_aggregate_sql", "usage_coverage_sql", "source_summary_sql"):
        monkeypatch.setattr(trace_usage, name, reject)
    assert (
        baseline_statements(historical_session_builder())
        == baseline_fixture()["statements"]
    )
    assert baseline_statements(native_session_builder())["build_page_hydration_query"]


def test_historical_helper_drift_is_detected_without_snapshot_refresh():
    fixture = baseline_fixture()
    changed = deepcopy(fixture["historical_closure"])
    helper = "tracer.services.clickhouse.query_builders.trace_usage"
    # Reintroduce the exact class of original defect: old builder + newer helper.
    changed["modules"][helper] = fixture["native_dev_closure"]["modules"][helper]
    actual = baseline_statements(_load_frozen_session_builder(changed))
    expected = fixture["statements"]
    assert (
        actual["build_candidate_page_query"] == expected["build_candidate_page_query"]
    )
    with pytest.raises(AssertionError, match="SQL token drift"):
        assert_sql_and_parameters_equal(
            (
                actual["build_page_hydration_query"]["sql"],
                actual["build_page_hydration_query"]["params"],
            ),
            (
                expected["build_page_hydration_query"]["sql"],
                expected["build_page_hydration_query"]["params"],
            ),
        )


@pytest.mark.parametrize(
    "project,page_size,offset", [(1, 1, 0), (917, 25, 1), (339, 100, 100)]
)
def test_native_dev_parity_is_not_one_self_snapshot(project, page_size, offset):
    start = datetime(2026, 10, 20, 12, 0, 0, 999999, tzinfo=UTC)
    kwargs = {
        "project_id": str(UUID(int=project)),
        "page_size": page_size,
        "page_number": offset,
        "filters": filters(start, start + timedelta(days=2)),
    }
    current, native = (
        SessionListQueryBuilderV2(**kwargs),
        native_session_builder()(**kwargs),
    )
    for method, args, options in (
        ("build_candidate_page_query", (), {}),
        ("build_candidate_count_query", (), {}),
        (
            "build_candidate_cursor_page_query",
            (),
            {
                "before_start_time": start + timedelta(hours=1),
                "before_session_id": str(UUID(int=56)),
            },
        ),
        ("build_page_hydration_query", ([str(UUID(int=101)), str(UUID(int=202))],), {}),
    ):
        assert_sql_and_parameters_equal(
            getattr(current, method)(*args, **options),
            getattr(native, method)(*args, **options),
        )


def _density_reader(builder_type, narrower_result):
    """Drive the installed selector with real compiler code and fixture transport."""
    start = datetime(2026, 10, 20, tzinfo=UTC)
    end = start + timedelta(hours=16)
    builder = builder_type(
        project_id=str(UUID(int=1)), page_size=25, filters=filters(start, end)
    )
    # The installed selector reads the builder's normalized request bounds.
    request_start, scan_end = builder.parse_time_range(builder.filters)
    full_start_us = int(start.timestamp()) * 1_000_000
    calls = []

    def execute(query, params, **options):
        assert "EXPLAIN ESTIMATE" in query
        calls.append(params["candidate_density_start_us"])
        if calls[-1] == full_start_us:
            return SimpleNamespace(
                data=[
                    {
                        "table": builder.TABLE,
                        "rows": _CANDIDATE_SLICE_TARGET_INDEX_ROWS + 1,
                    }
                ],
                columns=["table", "rows"],
            )
        return narrower_result

    reader = _SliceReader(
        builder=builder,
        analytics=SimpleNamespace(execute_ch_query=execute),
        deadline=SimpleNamespace(remaining_ms=lambda timeout: timeout),
        read_settings=lambda max_rows: {"max_result_rows": max_rows},
        query_timeout_ms=1_000,
        page_size=25,
        before_start_time=None,
        before_session_id=None,
        request_start=request_start,
        scan_end=scan_end,
    )
    return reader, builder, calls


@pytest.mark.parametrize(
    "builder_factory",
    [historical_session_builder, native_session_builder],
    ids=["historical", "native-dev"],
)
@pytest.mark.parametrize(
    "rows,columns,expected",
    [
        ([], ["table", "rows"], 0),
        ([], ["unreadable"], None),
        ([{"table": "spans", "rows": 37}], ["table", "rows"], 37),
    ],
    ids=["readable-empty", "unreadable", "nonempty"],
)
def test_frozen_density_results_cross_the_live_selector_boundary(
    builder_factory, rows, columns, expected
):
    reader, builder, calls = _density_reader(
        builder_factory(), SimpleNamespace(data=rows, columns=columns)
    )
    assert (
        builder.candidate_slice_density_estimate([], ["table", "rows"])
        is EMPTY_DENSITY_ESTIMATE
    )
    # Establish the readable full-window result before interpreting a narrower
    # empty estimate as zero, as the real selector's request contract requires.
    assert reader.probe_hours(16) == _CANDIDATE_SLICE_TARGET_INDEX_ROWS + 1
    actual = reader.probe_hours(1)
    if expected is None:
        assert actual is None
    else:
        assert type(actual) is int
        assert actual == expected
    assert len(calls) == 2


@pytest.mark.parametrize(
    "builder_factory",
    [historical_session_builder, native_session_builder],
    ids=["historical", "native-dev"],
)
def test_dense_full_window_with_empty_narrower_slice_keeps_native_width(
    builder_factory,
):
    empty = SimpleNamespace(data=[], columns=["table", "rows"])
    current, _, current_calls = _density_reader(SessionListQueryBuilderV2, empty)
    frozen, _, frozen_calls = _density_reader(builder_factory(), empty)

    expected = current.initial_floor()
    assert expected is not None
    assert frozen.initial_floor() == expected
    assert frozen_calls == current_calls
    assert len(frozen_calls) > 1
