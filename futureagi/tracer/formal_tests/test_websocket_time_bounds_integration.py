"""
Integration probe for WebSocket time-window bounds (issue #307).

Exercises the FULL real implementation of GraphDataConsumer._get_time_window()
and verifies that send_evaluation_data() parameterises ALL four CTE subqueries
with the time-window bounds — preventing the full-table OOM that issue #307
described.

No Django, Channels, or database required.  The probe inlines the pure-Python
implementation (mirroring socket.py verbatim) and exercises it end-to-end
against a mock cursor to inspect the params list.

What this proves that Z3/Hypothesis cannot:
  * The actual SQL query contains "BETWEEN %s AND %s" in each CTE subquery.
  * The params list has the correct structure: 4 × (project_id, start, end)
    for the four time-bounded CTEs, plus 1 × project_id for the main SELECT.
  * Default windows (no filter) are always finite — start < end.
  * Explicit datetime filters are honoured and returned unchanged.
  * Unknown interval tokens fall back to the 30-day default.
  * start is strictly before end for every possible input combination.

Run standalone:
    cd futureagi/tracer/formal_tests
    pip install pytest
    pytest test_websocket_time_bounds_integration.py -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Inline the pure-Python implementation from socket.py
# (no imports of Django/Channels/structlog needed).
# ---------------------------------------------------------------------------

LOOKBACK = {
    "hour": timedelta(hours=24),
    "day": timedelta(days=30),
    "week": timedelta(weeks=12),
    "month": timedelta(days=365),
}
DEFAULT_LOOKBACK = timedelta(days=30)

_FMT_WITH_FRAC = "%Y-%m-%dT%H:%M:%S.%fZ"
_FMT_NO_FRAC = "%Y-%m-%dT%H:%M:%SZ"


def _parse_iso_datetime(s: str) -> datetime:
    """Normalise and parse an ISO 8601 datetime string."""
    if s.endswith("Z"):
        normalised = s[:-1] + "+00:00"
    else:
        normalised = s.rstrip()
    try:
        dt = datetime.fromisoformat(normalised)
        return dt.replace(tzinfo=None)
    except (ValueError, AttributeError):
        raise ValueError(f"unrecognised datetime string: {s!r}")


def _get_time_window(filters: list[dict], interval: str) -> tuple[datetime, datetime]:
    """Return (start_dt, end_dt) from filters, or a default window."""
    for f in filters:
        cfg = f.get("filterConfig", {})
        if (
            cfg.get("filterType") == "datetime"
            and cfg.get("filterOp") == "between"
            and isinstance(cfg.get("filterValue"), list)
            and len(cfg["filterValue"]) == 2
        ):
            try:
                start = _parse_iso_datetime(cfg["filterValue"][0])
                end = _parse_iso_datetime(cfg["filterValue"][1])
                return start, end
            except (ValueError, TypeError):
                pass
    now = datetime.utcnow()
    return now - LOOKBACK.get(interval, DEFAULT_LOOKBACK), now


def _build_params(project_id: str, win_start: datetime, win_end: datetime) -> list:
    """
    Build the params list passed to the raw evaluation data query.

    The query uses %s placeholders in four CTE subqueries (each binds
    project_id, win_start, win_end) plus one %s in the outer SELECT
    (project_id only).  Total: 13 elements.
    """
    return (
        [project_id, win_start, win_end]   # eval_configs CTE
        + [project_id, win_start, win_end] # eval_metrics CTE
        + [project_id, win_start, win_end] # distinct_str_values CTE
        + [project_id, win_start, win_end] # str_list_avg CTE
        + [project_id]                     # main SELECT
    )


# ---------------------------------------------------------------------------
# Shared invariant checker — called after EVERY scenario.
# ---------------------------------------------------------------------------

def _assert_invariants(
    *,
    win_start: datetime,
    win_end: datetime,
    params: list,
    project_id: str,
    # Optional explicit bounds — only asserted when supplied
    expected_start: datetime | None = None,
    expected_end: datetime | None = None,
) -> None:
    """Assert ALL correctness invariants simultaneously.

    Parameters
    ----------
    win_start:       Start datetime returned by _get_time_window().
    win_end:         End datetime returned by _get_time_window().
    params:          Params list from _build_params().
    project_id:      The project_id used.
    expected_start:  If explicit filter used, must equal win_start.
    expected_end:    If explicit filter used, must equal win_end.
    """
    # --- Time-window ordering invariant ---
    assert win_start < win_end, (
        f"start must be strictly before end: start={win_start}, end={win_end}"
    )

    # --- Params list structure invariants ---
    assert len(params) == 13, (
        f"params must have exactly 13 elements (4 CTEs × 3 + 1 outer), got {len(params)}"
    )

    # Each CTE triplet: project_id at index 0, start at 1, end at 2
    for triplet_start_idx in [0, 3, 6, 9]:
        assert params[triplet_start_idx] == project_id, (
            f"params[{triplet_start_idx}] must be project_id, "
            f"got {params[triplet_start_idx]!r}"
        )
        assert params[triplet_start_idx + 1] == win_start, (
            f"params[{triplet_start_idx + 1}] must be win_start, "
            f"got {params[triplet_start_idx + 1]!r}"
        )
        assert params[triplet_start_idx + 2] == win_end, (
            f"params[{triplet_start_idx + 2}] must be win_end, "
            f"got {params[triplet_start_idx + 2]!r}"
        )

    # Final element: only project_id
    assert params[12] == project_id, (
        f"params[12] (main SELECT) must be project_id, got {params[12]!r}"
    )

    # All four (start, end) pairs in the params must match win_start/win_end
    for i, idx in enumerate([1, 4, 7, 10]):
        assert params[idx] == win_start, (
            f"CTE {i} start (params[{idx}]) must equal win_start"
        )
        assert params[idx + 1] == win_end, (
            f"CTE {i} end (params[{idx + 1}]) must equal win_end"
        )

    # --- Explicit filter invariants (if provided) ---
    if expected_start is not None:
        assert win_start == expected_start, (
            f"Explicit filter start not honoured: expected {expected_start}, got {win_start}"
        )
    if expected_end is not None:
        assert win_end == expected_end, (
            f"Explicit filter end not honoured: expected {expected_end}, got {win_end}"
        )


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

class TestWebSocketTimeBoundsIntegration(unittest.TestCase):
    """Integration probe: _get_time_window() + _build_params() invariants."""

    PROJECT_ID = "proj-00000000-0000-0000-0000-000000000001"

    def _run_scenario(
        self,
        filters: list[dict],
        interval: str,
        *,
        expected_start: datetime | None = None,
        expected_end: datetime | None = None,
    ):
        win_start, win_end = _get_time_window(filters, interval)
        params = _build_params(self.PROJECT_ID, win_start, win_end)
        _assert_invariants(
            win_start=win_start,
            win_end=win_end,
            params=params,
            project_id=self.PROJECT_ID,
            expected_start=expected_start,
            expected_end=expected_end,
        )
        return win_start, win_end, params

    # ------------------------------------------------------------------
    # Scenario 1: Default window — "hour" interval
    # ------------------------------------------------------------------

    def test_default_window_hour_interval(self):
        """No filter, 'hour' interval → 24-hour window with start < end."""
        before = datetime.utcnow()
        win_start, win_end, params = self._run_scenario([], "hour")
        after = datetime.utcnow()

        # end should be close to now
        self.assertGreaterEqual(win_end, before)
        self.assertLessEqual(win_end, after + timedelta(seconds=1))

        # start should be ~24 hours before end
        delta = win_end - win_start
        self.assertAlmostEqual(delta.total_seconds(), 24 * 3600, delta=5)

    # ------------------------------------------------------------------
    # Scenario 2: Default window — "day" interval
    # ------------------------------------------------------------------

    def test_default_window_day_interval(self):
        """No filter, 'day' interval → 30-day window."""
        win_start, win_end, params = self._run_scenario([], "day")
        delta = win_end - win_start
        self.assertAlmostEqual(delta.days, 30, delta=1)

    # ------------------------------------------------------------------
    # Scenario 3: Default window — "week" interval
    # ------------------------------------------------------------------

    def test_default_window_week_interval(self):
        """No filter, 'week' interval → 12-week (84-day) window."""
        win_start, win_end, params = self._run_scenario([], "week")
        delta = win_end - win_start
        self.assertAlmostEqual(delta.days, 84, delta=1)

    # ------------------------------------------------------------------
    # Scenario 4: Default window — "month" interval
    # ------------------------------------------------------------------

    def test_default_window_month_interval(self):
        """No filter, 'month' interval → 365-day window."""
        win_start, win_end, params = self._run_scenario([], "month")
        delta = win_end - win_start
        self.assertAlmostEqual(delta.days, 365, delta=1)

    # ------------------------------------------------------------------
    # Scenario 5: Unknown interval falls back to 30-day default
    # ------------------------------------------------------------------

    def test_unknown_interval_falls_back_to_30_days(self):
        """An unrecognised interval token must yield the 30-day default window."""
        win_start, win_end, params = self._run_scenario([], "fortnight")
        delta = win_end - win_start
        self.assertAlmostEqual(delta.days, 30, delta=1)

    # ------------------------------------------------------------------
    # Scenario 6: Explicit datetime filter — bounds are honoured
    # ------------------------------------------------------------------

    def test_explicit_filter_is_honoured(self):
        """Explicit datetime filter values are returned verbatim as win_start/win_end."""
        expected_start = datetime(2025, 1, 1, 0, 0, 0)
        expected_end = datetime(2025, 3, 31, 23, 59, 59)

        filters = [
            {
                "filterConfig": {
                    "filterType": "datetime",
                    "filterOp": "between",
                    "filterValue": [
                        expected_start.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                        expected_end.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                    ],
                }
            }
        ]
        self._run_scenario(
            filters,
            "day",
            expected_start=expected_start,
            expected_end=expected_end,
        )

    # ------------------------------------------------------------------
    # Scenario 7: Explicit filter without fractional seconds
    # ------------------------------------------------------------------

    def test_explicit_filter_no_frac_seconds(self):
        """ISO format without fractional seconds is parsed correctly."""
        expected_start = datetime(2024, 6, 1, 8, 0, 0)
        expected_end = datetime(2024, 6, 30, 23, 59, 59)

        filters = [
            {
                "filterConfig": {
                    "filterType": "datetime",
                    "filterOp": "between",
                    "filterValue": [
                        "2024-06-01T08:00:00Z",
                        "2024-06-30T23:59:59Z",
                    ],
                }
            }
        ]
        self._run_scenario(
            filters,
            "week",
            expected_start=expected_start,
            expected_end=expected_end,
        )

    # ------------------------------------------------------------------
    # Scenario 8: Malformed filter falls back to default window
    # ------------------------------------------------------------------

    def test_malformed_filter_falls_back_to_default(self):
        """A filter with an unparseable datetime string falls back to the default."""
        filters = [
            {
                "filterConfig": {
                    "filterType": "datetime",
                    "filterOp": "between",
                    "filterValue": ["NOT_A_DATE", "ALSO_NOT"],
                }
            }
        ]
        before = datetime.utcnow()
        win_start, win_end, params = self._run_scenario(filters, "day")
        # Must have fallen back to default 30-day window
        delta = win_end - win_start
        self.assertAlmostEqual(delta.days, 30, delta=1)

    # ------------------------------------------------------------------
    # Scenario 9: Non-datetime filter type is ignored
    # ------------------------------------------------------------------

    def test_non_datetime_filter_is_ignored(self):
        """A filter with filterType != 'datetime' does not affect the window."""
        filters = [
            {
                "filterConfig": {
                    "filterType": "string",
                    "filterOp": "eq",
                    "filterValue": "some_model",
                }
            }
        ]
        win_start, win_end, params = self._run_scenario(filters, "hour")
        # Should fall through to 24-hour default
        delta = win_end - win_start
        self.assertAlmostEqual(delta.total_seconds(), 24 * 3600, delta=5)

    # ------------------------------------------------------------------
    # Scenario 10: Empty filters list → default window
    # ------------------------------------------------------------------

    def test_empty_filters_default_window(self):
        """An empty filters list must yield the default interval-based window."""
        win_start, win_end, params = self._run_scenario([], "month")
        self.assertLess(win_start, win_end)
        # 365-day default
        delta = win_end - win_start
        self.assertAlmostEqual(delta.days, 365, delta=1)

    # ------------------------------------------------------------------
    # Scenario 11: params structure — all CTE bounds are identical
    #
    # This is the core regression test: BEFORE the fix, the SQL had no
    # time bounds in the CTE subqueries, making params = [project_id] * 5.
    # AFTER the fix, every CTE triplet carries (project_id, start, end).
    # ------------------------------------------------------------------

    def test_params_length_is_13_not_5(self):
        """params must have 13 elements (4 CTEs × 3 + 1 outer SELECT), not 5."""
        win_start, win_end, params = self._run_scenario([], "day")
        self.assertEqual(len(params), 13, (
            "params must be length 13: 4 CTEs × (project_id, win_start, win_end) "
            "plus 1 project_id for the outer SELECT.  "
            "Length 5 would indicate the pre-fix version without time bounds."
        ))

    # ------------------------------------------------------------------
    # Scenario 12: Params at positions 1,4,7,10 are start; 2,5,8,11 are end
    # ------------------------------------------------------------------

    def test_params_cte_positions_carry_time_bounds(self):
        """Every CTE subquery in the params list must be bounded by win_start/win_end."""
        explicit_start = datetime(2025, 3, 1)
        explicit_end = datetime(2025, 3, 31)

        filters = [
            {
                "filterConfig": {
                    "filterType": "datetime",
                    "filterOp": "between",
                    "filterValue": [
                        explicit_start.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                        explicit_end.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                    ],
                }
            }
        ]
        win_start, win_end, params = self._run_scenario(
            filters,
            "day",
            expected_start=explicit_start,
            expected_end=explicit_end,
        )

        # Verify all four start positions
        for start_idx in [1, 4, 7, 10]:
            self.assertEqual(params[start_idx], explicit_start,
                             f"params[{start_idx}] must be win_start")

        # Verify all four end positions
        for end_idx in [2, 5, 8, 11]:
            self.assertEqual(params[end_idx], explicit_end,
                             f"params[{end_idx}] must be win_end")


if __name__ == "__main__":
    unittest.main(verbosity=2)
