"""Behavioral regression test for #307 / PR #335.

The WebSocket eval CTE query ran unbounded over a project's full history, which OOM'd /
timed out on large installations, and its datetime parsing accepted only the fractional-
seconds ISO form ("%Y-%m-%dT%H:%M:%S.%fZ") -- a plain "...T00:00:00Z" crashed. The fix
adds _parse_iso_datetime (accepts ISO 8601 with/without fractional seconds and offsets)
and GraphDataConsumer._get_time_window(), which returns the client's explicit datetime
between-filter when present and otherwise a bounded default lookback per interval, so a
full-table scan cannot be constructed from an unfiltered request.

Exercises the REAL _parse_iso_datetime and the REAL GraphDataConsumer method (the
instance is created with __new__ -- the method reads only self.filters/self.interval,
so no WebSocket machinery is required). The no-fractional-seconds case fails on the
pre-fix parser; the bounded-default cases fail if the lookback window is unwired.
"""
from datetime import datetime, timedelta

import pytest

from tracer.socket import GraphDataConsumer, _parse_iso_datetime


def _consumer(filters, interval="hour"):
    c = GraphDataConsumer.__new__(GraphDataConsumer)
    c.filters = filters
    c.interval = interval
    return c


def _between_filter(start, end):
    return {
        "filterConfig": {
            "filterType": "datetime",
            "filterOp": "between",
            "filterValue": [start, end],
        }
    }


@pytest.mark.unit
def test_parse_iso_datetime_accepts_both_fractional_and_plain_forms():
    # The pre-fix strptime("%Y-%m-%dT%H:%M:%S.%fZ") crashed on the plain form.
    assert _parse_iso_datetime("2026-01-02T03:04:05Z") == datetime(2026, 1, 2, 3, 4, 5)
    assert _parse_iso_datetime("2026-01-02T03:04:05.250000Z") == datetime(
        2026, 1, 2, 3, 4, 5, 250000
    )
    with pytest.raises(ValueError):
        _parse_iso_datetime("not-a-datetime")


@pytest.mark.unit
def test_explicit_between_filter_is_honored_exactly():
    c = _consumer([_between_filter("2026-03-01T00:00:00Z", "2026-03-08T00:00:00Z")])
    start, end = c._get_time_window()
    assert start == datetime(2026, 3, 1)
    assert end == datetime(2026, 3, 8)


@pytest.mark.unit
@pytest.mark.parametrize(
    "interval,expected_lookback",
    [
        ("hour", timedelta(hours=24)),
        ("day", timedelta(days=30)),
        ("week", timedelta(weeks=12)),
        ("month", timedelta(days=365)),
        ("bogus-interval", timedelta(days=30)),  # unknown -> the default lookback
    ],
)
def test_no_filter_yields_bounded_default_window(interval, expected_lookback):
    # The core of #307: an unfiltered request must still produce a BOUNDED window,
    # so the CTE cannot scan a project's entire history.
    before = datetime.utcnow()
    start, end = _consumer([], interval=interval)._get_time_window()
    after = datetime.utcnow()

    assert before <= end <= after
    assert (end - start) == expected_lookback


@pytest.mark.unit
def test_malformed_filter_falls_back_to_bounded_default():
    # A between-filter with an unparseable value must not crash NOR escape the bound.
    c = _consumer([_between_filter("garbage", "2026-03-08T00:00:00Z")], interval="day")
    start, end = c._get_time_window()
    assert (end - start) == timedelta(days=30)
