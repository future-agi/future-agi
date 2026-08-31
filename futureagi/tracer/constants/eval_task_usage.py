from datetime import timedelta
from enum import Enum


class UsagePeriod(str, Enum):
    """Time windows understood by the eval-task usage endpoint.

    ``CUSTOM`` and ``ALL`` are never accepted as the ``period`` query param —
    they are only reported back through ``period_requested`` / ``period_used``.
    """

    THIRTY_MINUTES = "30m"
    SIX_HOURS = "6h"
    ONE_DAY = "1d"
    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"
    NINETY_DAYS = "90d"
    ONE_EIGHTY_DAYS = "180d"
    ONE_YEAR = "365d"
    CUSTOM = "custom"
    ALL = "all"

    @classmethod
    def selectable(cls):
        """Periods a client may request, i.e. everything with a fixed length."""
        return tuple(period for period in cls if period in USAGE_PERIOD_DELTAS)


USAGE_PERIOD_DELTAS = {
    UsagePeriod.THIRTY_MINUTES: timedelta(minutes=30),
    UsagePeriod.SIX_HOURS: timedelta(hours=6),
    UsagePeriod.ONE_DAY: timedelta(days=1),
    UsagePeriod.SEVEN_DAYS: timedelta(days=7),
    UsagePeriod.THIRTY_DAYS: timedelta(days=30),
    UsagePeriod.NINETY_DAYS: timedelta(days=90),
    UsagePeriod.ONE_EIGHTY_DAYS: timedelta(days=180),
    UsagePeriod.ONE_YEAR: timedelta(days=365),
}

DEFAULT_USAGE_PERIOD = UsagePeriod.THIRTY_DAYS

# Chart bucket width, chosen from the resolved window length rather than the
# requested period: a custom range or the all-time fallback has no period to
# key off, and reusing the requested one would bucket a year into 5-minute
# slots.
USAGE_BUCKET_THRESHOLDS = (
    (timedelta(minutes=30), 5),
    (timedelta(hours=6), 30),
    (timedelta(days=1), 60),
    (timedelta(days=7), 360),
)
DEFAULT_USAGE_BUCKET_MINUTES = 1440

# Upper bound on zero-filled chart points. A wide window with a narrow bucket
# would otherwise serialize tens of thousands of entries.
MAX_USAGE_CHART_BUCKETS = 1500
