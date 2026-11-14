"""Housekeeping limits for gateway outbound webhook events.

Each is a deployment setting; 0 turns the limit off.
"""

import os

from django.core.exceptions import ImproperlyConfigured


def _integer(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be an integer.") from None
    if value < minimum or (maximum is not None and value > maximum):
        raise ImproperlyConfigured(f"{name} is outside its supported range.")
    return value


# Pending events older than this many hours expire. 0: never.
AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS = _integer(
    "AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS", 24, 0
)
# Pending events one webhook may hold; past it the oldest are dropped. 0: no cap.
AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK = _integer(
    "AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK", 10000, 0
)
# Consecutive failed deliveries that disable a webhook. 0: never.
AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES = _integer(
    "AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES", 100, 0
)
# ...and only once its failures have gone on this many minutes, so a short
# receiver outage never disables it. 0: the count alone decides.
AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES = _integer(
    "AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES", 60, 0
)
# Webhook delivery runs in flight at once, across all webhooks. They share the
# worker's default queue with evals, experiments and alert emails, so a few
# slow receivers must not take all of its slots. 0: no limit.
AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES = _integer(
    "AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES", 3, 0
)
# ...and of one organization's webhooks, so one tenant's slow or backlogged
# receivers cannot hold every slot of the install. 0: no per-org limit.
AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG = _integer(
    "AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_ORG", 1, 0
)
# Seconds one delivery run may keep starting POSTs before it yields its slot.
# A POST in flight then ends within 13 s of its start once the receiver has
# sent its headers: its body is read under that total deadline, not only a
# per-read timeout. At most 90, so a run ends inside its 120 s activity limit
# and lease.
AGENTCC_WEBHOOK_RUN_BUDGET_SECONDS = _integer(
    "AGENTCC_WEBHOOK_RUN_BUDGET_SECONDS", 60, 1, 90
)
