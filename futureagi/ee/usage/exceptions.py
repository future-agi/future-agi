"""Custom exceptions for the usage/billing system."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ee.usage.schemas.events import CheckResult


class UsageLimitExceeded(Exception):
    """Raised when a usage pre-check fails (free tier limit, budget pause, etc.).

    Carries the full CheckResult so views can return structured error responses
    instead of losing the error_code, upgrade_cta, and usage stats.

    Usage:
        from ee.usage.exceptions import UsageLimitExceeded

        check = check_usage(org_id, event_type)
        if not check.allowed:
            raise UsageLimitExceeded(check)
    """

    def __init__(self, check_result: CheckResult):
        self.check_result = check_result
        super().__init__(check_result.reason or "Usage limit exceeded")


class NoDefaultPaymentMethod(Exception):
    """Raised when a plan change needs a card on file and none is set.

    Views map this to 402 so the frontend can open the card-capture flow
    instead of showing a generic failure.
    """
