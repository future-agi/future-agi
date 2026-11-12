"""The runs statement's time bounds (learning-service P1-09): no database, no ClickHouse.

The statement filters ``start_time`` before ``FINAL`` replaces a span's older versions. The
spans sort key holds ``toStartOfHour(start_time)``, not ``start_time``
(tracer/services/clickhouse/v2/schema/002_spans_v2.sql), so that filter gives the same answer
for every version of a span only when each bound is on a UTC hour. ``statement_params``
refuses any other bound rather than let a sub-hour one silently break replacement.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tracer.services.omega_agents.curves import runs

pytestmark = pytest.mark.unit

HOUR_US = 3_600_000_000
MIDNIGHT = datetime(2026, 10, 20, tzinfo=UTC)
BOUND_KEYS = ("start_us", "end_us", "lookback_us", "child_end_us")


def _params(start, end):
    return runs.statement_params(
        project_id="6f0c9c6e-6f4c-4c65-9d3e-0c8b7c0b1a11",
        agent_id="workforce_agent_1",
        start=start,
        end=end,
    )


def test_the_views_midnight_window_puts_every_bound_on_the_hour():
    params = _params(MIDNIGHT, MIDNIGHT + timedelta(days=2))

    assert all(params[key] % HOUR_US == 0 for key in BOUND_KEYS)
    assert params["lookback_us"] == params["start_us"] - 24 * HOUR_US
    assert params["child_end_us"] == params["end_us"] + 30 * 24 * HOUR_US


def test_a_bound_on_any_utc_hour_is_accepted():
    start = MIDNIGHT + timedelta(hours=9)

    params = _params(start, start + timedelta(hours=5))

    assert all(params[key] % HOUR_US == 0 for key in BOUND_KEYS)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (MIDNIGHT + timedelta(minutes=30), MIDNIGHT + timedelta(days=1)),
        (MIDNIGHT, MIDNIGHT + timedelta(days=1, microseconds=1)),
        # Midnight in India is 18:30 UTC.
        (
            datetime(2026, 10, 20, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            MIDNIGHT + timedelta(days=1),
        ),
    ],
    ids=["start-half-past", "end-one-microsecond-past", "start-midnight-ist"],
)
def test_a_bound_off_the_utc_hour_is_refused(start, end):
    with pytest.raises(ValueError, match="on the hour"):
        _params(start, end)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ROOT_LOOKBACK", timedelta(days=1, minutes=30)),
        ("CHILD_GRACE", timedelta(days=30, seconds=1)),
    ],
)
def test_a_derived_bound_off_the_hour_is_refused(monkeypatch, name, value):
    monkeypatch.setattr(runs, name, value)

    with pytest.raises(ValueError, match="on the hour"):
        _params(MIDNIGHT, MIDNIGHT + timedelta(days=1))
