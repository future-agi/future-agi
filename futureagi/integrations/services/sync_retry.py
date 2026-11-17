"""When a connection in Error is synced again, and since when it has been in Error.

A connection whose sync failed stays scheduled: the scheduler tries it again
after its sync interval, then after twice that for each further failure in a
row, at most an hour apart, until a sync succeeds and it is active again.
"Since when" is the first failure in that row, so the email alert for a
connection in Error for over an hour fires however many retries ran.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from integrations.models import SyncLog, SyncStatus

RETRY_MAX_DELAY = timedelta(hours=1)
# The rows a streak is read from: enough for the longest delay to be reached.
_STREAK_WINDOW = 50


@dataclass(frozen=True)
class FailureStreak:
    """The failed syncs since the connection's last sync that did not fail."""

    count: int
    first_at: datetime | None
    last_at: datetime | None


def failure_streak(connection) -> FailureStreak:
    rows = (
        SyncLog.objects.filter(connection=connection, completed_at__isnull=False)
        .order_by("-started_at")
        .values_list("status", "started_at", "completed_at")[:_STREAK_WINDOW]
    )
    count, first_at, last_at = 0, None, None
    for status, started_at, completed_at in rows:
        if status != SyncStatus.FAILED:
            break
        count += 1
        last_at = last_at or completed_at
        first_at = started_at
    return FailureStreak(count, first_at, last_at)


def retry_delay(connection, failures: int) -> timedelta:
    """The wait after `failures` failed syncs in a row."""
    base = max(int(connection.sync_interval_seconds or 0), 60)
    doublings = min(max(failures - 1, 0), 16)
    return min(timedelta(seconds=base * 2**doublings), RETRY_MAX_DELAY)


def retry_due(connection, now: datetime) -> bool:
    """Whether the scheduler syncs this connection in Error again now."""
    streak = failure_streak(connection)
    since = streak.last_at or connection.updated_at
    return now - since >= retry_delay(connection, streak.count)


def in_error_since(connection) -> datetime:
    """When the connection's current run of failed syncs began."""
    return failure_streak(connection).first_at or connection.updated_at
