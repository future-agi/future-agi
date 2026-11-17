"""Budget periods, shared by the spend summary and Request Logs retention."""

from datetime import UTC, datetime, timedelta

BUDGET_PERIODS = ("daily", "weekly", "monthly", "total")
# The gateway's own default (agentcc-gateway/internal/plugins/budget/seed.go).
DEFAULT_BUDGET_PERIOD = "monthly"


def period_start(period, now=None):
    """The start of the current budget period, in UTC."""
    now = now or datetime.now(UTC)
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "total":
        return datetime.min.replace(tzinfo=UTC)
    # monthly, and the default for anything else
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
