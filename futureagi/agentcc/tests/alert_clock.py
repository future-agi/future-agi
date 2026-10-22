"""A clock the email alert tests move.

budget.exceeded keys are pruned against timezone.now() once their period has
ended (plus a day of grace), so a dedupe test with fixed dates goes stale on a
later day. These tests instead take timezone.now() at their start, build their
dates from it, and move time themselves.
"""

from contextlib import contextmanager
from datetime import UTC, timedelta
from unittest.mock import patch

from django.utils import timezone


class MovingClock:
    """timezone.now() for one test.

    It starts where the clock stands when the test begins (the real one, or an
    instant a caller patched in), moves only when the test calls ``to``, and
    steps one microsecond per read so rows created "at one instant" still sort
    by created_at in the order they were made.
    """

    def __init__(self, start):
        self.at = start

    def __call__(self):
        self.at += timedelta(microseconds=1)
        return self.at

    def to(self, instant):
        self.at = instant


@contextmanager
def moving_clock():
    clock = MovingClock(timezone.now().astimezone(UTC))
    with patch.object(timezone, "now", new=clock):
        yield clock


def utc_text(instant):
    """The gateway's budget-period form of an instant: 2026-10-01T00:00:00Z."""
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_start(instant):
    return instant.astimezone(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def next_month_start(instant):
    return (month_start(instant) + timedelta(days=32)).replace(day=1)


def day_start(instant):
    return instant.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
