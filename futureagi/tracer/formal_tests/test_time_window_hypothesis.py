"""
Hypothesis property-based tests for _get_time_window() (issue #307).

These tests exercise a pure-Python model of the function against arbitrary
inputs to prove the invariants that Z3 proves symbolically.
"""

from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Pure Python model of _get_time_window() — mirrors the production code.
# ---------------------------------------------------------------------------

LOOKBACK = {
    "hour": timedelta(hours=24),
    "day": timedelta(days=30),
    "week": timedelta(weeks=12),
    "month": timedelta(days=365),
}

DEFAULT_LOOKBACK = timedelta(days=30)
FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _get_time_window(filters, interval):
    for f in filters:
        cfg = f.get("filterConfig", {})
        if (
            cfg.get("filterType") == "datetime"
            and cfg.get("filterOp") == "between"
            and isinstance(cfg.get("filterValue"), list)
            and len(cfg["filterValue"]) == 2
        ):
            try:
                start = datetime.strptime(cfg["filterValue"][0], FMT)
                end = datetime.strptime(cfg["filterValue"][1], FMT)
                return start, end
            except (ValueError, TypeError):
                pass
    now = datetime.utcnow()
    return now - LOOKBACK.get(interval, DEFAULT_LOOKBACK), now


def _build_params(project_id, win_start, win_end):
    return (
        [project_id, win_start, win_end]  # eval_configs
        + [project_id, win_start, win_end]  # eval_metrics
        + [project_id, win_start, win_end]  # distinct_str_values
        + [project_id, win_start, win_end]  # str_list_avg
        + [project_id]  # main SELECT
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_intervals = st.sampled_from(["hour", "day", "week", "month"])
any_interval = st.one_of(valid_intervals, st.text(min_size=1, max_size=20))

valid_dt_str = st.datetimes(
    min_value=datetime(2000, 1, 1), max_value=datetime(2050, 1, 1)
).map(lambda d: d.strftime(FMT))


def datetime_filter(start_str, end_str):
    return {"filterConfig": {"filterType": "datetime", "filterOp": "between", "filterValue": [start_str, end_str]}}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@given(any_interval)
def test_start_lt_end_no_filter(interval):
    start, end = _get_time_window([], interval)
    assert start < end, f"start must be < end for interval={interval!r}"


@given(valid_intervals)
def test_default_lookback_matches_table(interval):
    before = datetime.utcnow()
    start, end = _get_time_window([], interval)
    after = datetime.utcnow()

    expected_delta = LOOKBACK[interval]
    actual_delta = end - start

    # Allow 1-second clock drift from utcnow() calls
    assert abs(actual_delta - expected_delta) < timedelta(seconds=1), (
        f"lookback for {interval} must be {expected_delta}, got {actual_delta}"
    )
    assert before <= end <= after, "end must be approximately now"


@given(any_interval)
def test_unknown_interval_uses_30d_fallback(interval):
    if interval in LOOKBACK:
        return  # skip known intervals — this property is only for unknowns
    start, end = _get_time_window([], interval)
    actual_delta = end - start
    assert abs(actual_delta - DEFAULT_LOOKBACK) < timedelta(seconds=1), (
        f"unknown interval {interval!r} must use 30-day fallback"
    )


@given(
    st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2049, 12, 31)),
    st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2049, 12, 31)),
    any_interval,
)
def test_explicit_filter_passthrough(start_dt, end_dt, interval):
    if start_dt >= end_dt:
        start_dt, end_dt = min(start_dt, end_dt), max(start_dt, end_dt)
        if start_dt == end_dt:
            end_dt = end_dt + timedelta(seconds=1)

    start_str = start_dt.strftime(FMT)
    end_str = end_dt.strftime(FMT)

    filters = [datetime_filter(start_str, end_str)]
    returned_start, returned_end = _get_time_window(filters, interval)

    assert returned_start == datetime.strptime(start_str, FMT)
    assert returned_end == datetime.strptime(end_str, FMT)


@given(
    st.text(min_size=1),
    st.text(min_size=1),
    any_interval,
)
def test_malformed_filter_falls_back_to_default(bad_start, bad_end, interval):
    """If filter values can't be parsed, fall back to the default window."""
    # Ensure neither string accidentally parses as a valid datetime
    filters = [datetime_filter("not-a-date-" + bad_start, "not-a-date-" + bad_end)]
    start, end = _get_time_window(filters, interval)
    # Should have fallen back: end ≈ now, delta matches lookback table
    now = datetime.utcnow()
    assert abs((now - end).total_seconds()) < 2, "fallback end must be ≈ now"
    assert start < end


@given(
    st.integers(min_value=1),
    any_interval,
)
def test_params_list_always_13_elements(project_id, interval):
    start, end = _get_time_window([], interval)
    params = _build_params(project_id, start, end)
    assert len(params) == 13, f"params must have 13 elements, got {len(params)}"


@given(
    st.integers(min_value=1),
    any_interval,
)
def test_params_project_id_appears_5_times(project_id, interval):
    start, end = _get_time_window([], interval)
    params = _build_params(project_id, start, end)
    assert params.count(project_id) == 5, "project_id must appear exactly 5 times"


@given(
    st.integers(min_value=1),
    any_interval,
)
def test_params_win_start_appears_4_times(project_id, interval):
    start, end = _get_time_window([], interval)
    params = _build_params(project_id, start, end)
    # win_start at positions 1, 4, 7, 10 (0-indexed)
    assert params[1] == params[4] == params[7] == params[10] == start
    assert params.count(start) == 4


@given(
    st.integers(min_value=1),
    any_interval,
)
def test_params_win_end_appears_4_times(project_id, interval):
    start, end = _get_time_window([], interval)
    params = _build_params(project_id, start, end)
    # win_end at positions 2, 5, 8, 11 (0-indexed)
    assert params[2] == params[5] == params[8] == params[11] == end
    assert params.count(end) == 4


@given(any_interval)
@settings(max_examples=200)
def test_window_always_positive_duration(interval):
    start, end = _get_time_window([], interval)
    assert (end - start).total_seconds() > 0
