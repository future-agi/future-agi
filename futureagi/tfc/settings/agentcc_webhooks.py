"""Housekeeping limits for gateway outbound webhook events.

Each is a deployment setting; 0 turns the limit off.
"""

import os

from django.core.exceptions import ImproperlyConfigured


def _integer(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be an integer.") from None
    if value < minimum:
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
