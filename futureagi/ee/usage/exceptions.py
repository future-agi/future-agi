"""Custom exceptions for the usage/billing system."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tfc.utils.evaluation_denials import EvaluationDenied

if TYPE_CHECKING:
    from ee.usage.schemas.events import CheckResult


class UsageLimitExceeded(EvaluationDenied):
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
        self.code = check_result.error_code
        super().__init__(check_result.reason or "Usage limit exceeded")

    def as_dict(self):
        return {
            **super().as_dict(),
            **self.check_result.model_dump(exclude={"allowed"}),
        }

    def _response(self, general_methods):
        if self.code == "RATE_LIMITED":
            return general_methods.too_many_requests(str(self))
        return general_methods.usage_limit_response(self.check_result)


class NoDefaultPaymentMethod(Exception):
    """Raised when a plan change needs a card on file and none is set.

    Views map this to 402 so the frontend can open the card-capture flow
    instead of showing a generic failure.
    """
