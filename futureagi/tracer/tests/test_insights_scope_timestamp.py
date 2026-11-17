"""A report started from a Traces or Spans view that has a Timestamp filter
reads the rows the grid lists: the view's date window and the Timestamp bound
together.

The scope builder (observeScope.js makeScope/splitWindow) moves only the
view's created_at window into ``scope.window``; a Timestamp bound stays among
``scope.filters``. The server puts the window back beside it, so the preview,
the estimate and the run read where the two overlap. They don't read the
Timestamp bound alone, with the default lookback or now as its other edge.

Every bound below is a fixed instant with an explicit zone, so neither the
clock nor the lookback setting enters a passing run.
"""

from datetime import UTC, datetime, timedelta

import pytest
from rest_framework import serializers

from tracer.services import instant_eval_rows as rows_service
from tracer.views import insights as views

# The window a view hands over (observeScope.js isoWindow), in UTC.
WINDOW = ["2026-10-01T00:00:00.000Z", "2026-10-08T00:00:00.000Z"]
WINDOW_FROM = datetime(2026, 10, 1, tzinfo=UTC)
WINDOW_TO = datetime(2026, 10, 8, tzinfo=UTC)
# A bound inside the window, as the filter panel sends it: the viewer's local
# time with its offset. 10:30 at +05:30 is 05:00 UTC.
INSIDE = "2026-10-03T10:30:00+05:30"
INSIDE_UTC = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)
# "After" excludes the bound itself: start_time is DateTime64(6).
AFTER_INSIDE = INSIDE_UTC + timedelta(microseconds=1)
# A bound before the window opens.
BEFORE = "2026-09-28T09:00:00+05:30"
ERRORED = {
    "column_id": "status",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "ERROR",
    },
}

# The Timestamp operator and value, and the start and end the report reads.
CASES = [
    # Before 10:30 on the 3rd: from the window's start.
    pytest.param("less_than", INSIDE, WINDOW_FROM, INSIDE_UTC, id="less_than"),
    # After 10:30 on the 3rd: up to the window's end.
    pytest.param("greater_than", INSIDE, AFTER_INSIDE, WINDOW_TO, id="greater_than"),
    # A range that opens before the window: the window's start, the range's end.
    pytest.param("between", [BEFORE, INSIDE], WINDOW_FROM, INSIDE_UTC, id="between"),
]


def timestamp(filter_op, filter_value):
    """A Timestamp filter as the Traces list and the Spans view send it."""
    return {
        "column_id": "start_time",
        "property_id": "system_attribute:traces:start_time",
        "display_name": "Timestamp",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def window_item(window):
    """A view's date window among its filters (observeScope.js windowItem)."""
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": window,
        },
    }


def handed_over(*filters):
    """The scope a Traces or Spans view hands to Evaluate > Find patterns."""
    return {"filters": list(filters), "window": WINDOW, "preset": "7D"}


@pytest.mark.parametrize(("filter_op", "value", "start", "end"), CASES)
def test_a_timestamp_filter_keeps_the_views_window_beside_it(
    filter_op, value, start, end
):
    filters = views._scope_filters(handed_over(timestamp(filter_op, value)))

    assert [item["column_id"] for item in filters] == ["start_time", "created_at"]
    kept, window = filters
    assert kept["property_id"] == "system_attribute:traces:start_time"
    assert kept["filter_config"]["filter_op"] == filter_op
    assert kept["filter_config"]["filter_value"] == value
    assert window["filter_config"]["filter_op"] == "between"
    assert window["filter_config"]["filter_value"] == WINDOW


@pytest.mark.parametrize("unit", ["traces", "spans"])
@pytest.mark.parametrize(("filter_op", "value", "start", "end"), CASES)
def test_the_report_reads_the_window_and_the_timestamp_together(
    filter_op, value, start, end, unit
):
    # What the preview and the run read: _frozen_filters(_scope_filters(scope)).
    pinned, since, until = rows_service._frozen_filters(
        views._scope_filters(handed_over(timestamp(filter_op, value))),
        rows_service.ROW_TYPE[unit],
    )

    assert (since, until) == (start, end)
    # The run reads its rows inside that one window, pinned to absolute times.
    assert pinned[-1]["column_id"] == "created_at"
    assert pinned[-1]["filter_config"]["filter_value"] == [
        start.isoformat(),
        end.isoformat(),
    ]


def test_a_window_among_the_filters_stays_the_scopes_window():
    """A created_at window already among the filters (a rerun's, or an older
    report's) is the window; the scope's separate window isn't added too."""
    filters = views._scope_filters(
        {
            "filters": [timestamp("less_than", INSIDE), window_item(WINDOW)],
            "window": ["2026-11-08T00:00:00Z", "2026-11-09T00:00:00Z"],
        }
    )

    assert [item["column_id"] for item in filters] == ["start_time", "created_at"]
    assert filters[1]["filter_config"]["filter_value"] == WINDOW


def test_a_scope_without_a_window_reads_only_with_a_date_of_its_own():
    with pytest.raises(serializers.ValidationError) as refused:
        views._scope_filters({"filters": [ERRORED]})
    assert str(refused.value.detail[0]) == "Choose a date range for the report."

    filters = views._scope_filters({"filters": [timestamp("less_than", INSIDE)]})
    assert [item["column_id"] for item in filters] == ["start_time"]
