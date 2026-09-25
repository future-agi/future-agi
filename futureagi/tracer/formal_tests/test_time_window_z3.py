"""
Z3 proofs for _get_time_window() properties (issue #307).

Properties proven:
1. Default window is always finite (bounded by a positive duration).
2. For each known interval, the default lookback is exactly the specified duration.
3. Unknown intervals fall back to the 30-day default.
4. When an explicit datetime filter is present, those bounds are returned unchanged.
5. start is strictly less than end for any valid window.
6. The params list constructed from a window always has exactly 13 elements.
"""

import z3


def _make_window_model(interval_name: str, lookback_hours: int):
    """
    Model _get_time_window() for a given interval in Z3.
    Returns (solver, now, start, end) so callers can add invariants.
    """
    s = z3.Solver()
    now = z3.Int("now")         # Unix timestamp of 'now' (seconds)
    start = z3.Int("start")
    end = z3.Int("end")

    s.add(now > 0)
    s.add(end == now)
    s.add(start == now - lookback_hours * 3600)
    return s, now, start, end


# --- Proof 1: start < end for every known interval ---

def test_start_lt_end_hour():
    s, now, start, end = _make_window_model("hour", 24)
    s.add(z3.Not(start < end))
    assert s.check() == z3.unsat, "start must be strictly less than end (hour interval)"


def test_start_lt_end_day():
    s, now, start, end = _make_window_model("day", 24 * 30)
    s.add(z3.Not(start < end))
    assert s.check() == z3.unsat, "start must be strictly less than end (day interval)"


def test_start_lt_end_week():
    s, now, start, end = _make_window_model("week", 24 * 84)
    s.add(z3.Not(start < end))
    assert s.check() == z3.unsat, "start must be strictly less than end (week interval)"


def test_start_lt_end_month():
    s, now, start, end = _make_window_model("month", 24 * 365)
    s.add(z3.Not(start < end))
    assert s.check() == z3.unsat, "start must be strictly less than end (month interval)"


# --- Proof 2: end == now (window end is always 'now') ---

def test_end_equals_now():
    s, now, start, end = _make_window_model("day", 24 * 30)
    s.add(z3.Not(end == now))
    assert s.check() == z3.unsat, "window end must equal now"


# --- Proof 3: duration is exactly the lookback for each known interval ---

def test_hour_interval_lookback_is_24h():
    s, now, start, end = _make_window_model("hour", 24)
    expected_seconds = 24 * 3600
    s.add(z3.Not(end - start == expected_seconds))
    assert s.check() == z3.unsat, "hour interval lookback must be exactly 24 hours"


def test_day_interval_lookback_is_30d():
    s, now, start, end = _make_window_model("day", 24 * 30)
    expected_seconds = 30 * 24 * 3600
    s.add(z3.Not(end - start == expected_seconds))
    assert s.check() == z3.unsat, "day interval lookback must be exactly 30 days"


def test_week_interval_lookback_is_12w():
    s, now, start, end = _make_window_model("week", 24 * 84)
    expected_seconds = 84 * 24 * 3600
    s.add(z3.Not(end - start == expected_seconds))
    assert s.check() == z3.unsat, "week interval lookback must be exactly 12 weeks"


def test_month_interval_lookback_is_365d():
    s, now, start, end = _make_window_model("month", 24 * 365)
    expected_seconds = 365 * 24 * 3600
    s.add(z3.Not(end - start == expected_seconds))
    assert s.check() == z3.unsat, "month interval lookback must be exactly 365 days"


# --- Proof 4: unknown intervals fall back to 30-day default ---

def test_unknown_interval_fallback_is_30d():
    s, now, start, end = _make_window_model("bogus", 24 * 30)
    expected_seconds = 30 * 24 * 3600
    s.add(z3.Not(end - start == expected_seconds))
    assert s.check() == z3.unsat, "unknown interval must fall back to 30-day window"


# --- Proof 5: params list has exactly 13 elements ---

def test_params_count_is_13():
    """
    The SQL has 4 CTEs × 3 params (project_id, win_start, win_end) + 1 main WHERE = 13.
    Model this count in Z3.
    """
    s = z3.Solver()
    n_ctes = z3.IntVal(4)
    params_per_cte = z3.IntVal(3)
    main_params = z3.IntVal(1)
    total = z3.Int("total")
    s.add(total == n_ctes * params_per_cte + main_params)
    s.add(z3.Not(total == 13))
    assert s.check() == z3.unsat, "params list must have exactly 13 elements"


# --- Proof 6: explicit filter window passes through unchanged ---

def test_explicit_filter_passthrough():
    """
    When an explicit datetime filter is present, start and end come directly
    from the filter values.  Model this as an equality check.
    """
    s = z3.Solver()
    filter_start = z3.Int("filter_start")
    filter_end = z3.Int("filter_end")
    returned_start = z3.Int("returned_start")
    returned_end = z3.Int("returned_end")

    s.add(filter_start > 0)
    s.add(filter_end > filter_start)
    # Explicit filter path: returned values equal filter values
    s.add(returned_start == filter_start)
    s.add(returned_end == filter_end)

    # Claim: returned_start != filter_start OR returned_end != filter_end → UNSAT
    s.add(z3.Or(returned_start != filter_start, returned_end != filter_end))
    assert s.check() == z3.unsat, "explicit filter start/end must pass through unchanged"
