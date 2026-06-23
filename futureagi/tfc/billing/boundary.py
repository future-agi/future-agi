"""ee/OSS billing boundary.

This is the ONLY OSS module that may import from ee.usage.  All other OSS
modules must go through get_billing() and never import ee.usage directly.

Design (per TH-5971):
  - Billing Protocol exposes coarse, intention-revealing operations.
  - _NoopBilling  — OSS default.  Metering/ingestion fail-open; entitlement
                    checks fail-CLOSED (has_feature → False).
  - _EeBilling    — EE/Cloud. Delegates to ee.usage at call time via lazy
                    imports so the module is still importable in OSS.
  - get_billing() — process-singleton factory; picks the right impl once.

BillingEventType is re-exported here so call sites only need one import and
the stub works in OSS without any ee install.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass
class UsageDecision:
    """Outcome of a usage/entitlement check."""

    allowed: bool = True
    reason: str = ""
    error_code: str = ""
    upgrade_cta: Optional[dict] = field(default=None)


_ALLOW = UsageDecision(allowed=True)


# ---------------------------------------------------------------------------
# BillingEventType — re-exported from ee when present, stubbed for OSS
# ---------------------------------------------------------------------------

try:
    from ee.usage.schemas.event_types import BillingEventType  # noqa: F401
except ImportError:
    class _BillingEventTypeStub:
        """OSS stub: attribute access returns the attribute name as a string.

        BillingEventType.SYNTHETIC_DATA_GENERATION → "SYNTHETIC_DATA_GENERATION"
        Matches the real enum's .value so boundary calls receive the right key.
        """
        def __getattr__(self, name: str) -> str:
            return name

    BillingEventType = _BillingEventTypeStub()


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class Billing:
    """Coarse billing operations exposed to OSS code.

    Never instantiate directly — use get_billing().
    """

    def record_usage(
        self,
        org_id: str,
        event_type: str,
        *,
        amount: float = 1.0,
        **properties: Any,
    ) -> None:
        """Emit a metering event (fire-and-forget).  Always succeeds in OSS."""
        raise NotImplementedError

    def check_usage(
        self,
        org_id: str,
        event_type: str,
        amount: float = 0,
    ) -> UsageDecision:
        """Pre-check before a billable action.  Returns ALLOW in OSS."""
        raise NotImplementedError

    def log_and_deduct(self, **kw: Any) -> Any:
        """Create an APICallLog row and gate on credits.  No-ops in OSS."""
        raise NotImplementedError

    def log_and_deduct_resource(
        self,
        organization: Any,
        api_call_type: str,
        config: Optional[dict] = None,
        workspace: Any = None,
    ) -> Any:
        """Gate resource creation (datasets, rows, KB) and log the attempt.  No-ops in OSS."""
        raise NotImplementedError

    def ai_credits(self, cost_usd: float) -> int:
        """Convert a raw LLM cost (USD) to AI credits.  Returns 0 in OSS."""
        raise NotImplementedError

    def has_feature(self, org_id: str, feature: str) -> bool:
        """Entitlement gate — returns False (fail-CLOSED) in OSS."""
        raise NotImplementedError

    def can_create(
        self,
        org_id: str,
        resource: str,
        current_count: int = 0,
    ) -> UsageDecision:
        """Resource-limit gate.  Returns ALLOW in OSS."""
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        """Count tokens via tiktoken.  Returns 0 in OSS."""
        raise NotImplementedError

    def get_retention_days(self, org_id: str, data_type: str) -> int:
        """Data retention in days.  Returns 0 (no enforcement) in OSS."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# _NoopBilling — OSS fallback
# ---------------------------------------------------------------------------

class _NoopBilling(Billing):
    """OSS default: metering is silent, entitlements fail-CLOSED."""

    def record_usage(self, org_id, event_type, *, amount=1.0, **properties):
        pass

    def check_usage(self, org_id, event_type, amount=0):
        return _ALLOW

    def log_and_deduct(self, **kw):
        return None

    def log_and_deduct_resource(self, organization, api_call_type, config=None, workspace=None):
        return None

    def ai_credits(self, cost_usd):
        return 0

    def has_feature(self, org_id, feature):
        return False  # entitlement: fail-CLOSED — avoids the develop_annotations leak

    def can_create(self, org_id, resource, current_count=0):
        return _ALLOW

    def count_tokens(self, text):
        return 0

    def get_retention_days(self, org_id, data_type):
        return 0


# ---------------------------------------------------------------------------
# _EeBilling — EE/Cloud implementation
# ---------------------------------------------------------------------------

class _EeBilling(Billing):
    """Delegates all operations to ee.usage via lazy imports."""

    def record_usage(self, org_id, event_type, *, amount=1.0, **properties):
        from ee.usage.services.emitter import emit
        from ee.usage.schemas.events import UsageEvent

        emit(UsageEvent(
            org_id=str(org_id),
            event_type=event_type,
            amount=amount,
            properties=properties,
        ))

    def check_usage(self, org_id, event_type, amount=0):
        from ee.usage.services.metering import check_usage as _check

        result = _check(str(org_id), event_type, amount)
        return UsageDecision(
            allowed=getattr(result, "allowed", True),
            reason=getattr(result, "reason", ""),
            error_code=getattr(result, "error_code", ""),
            upgrade_cta=_cta_dict(getattr(result, "upgrade_cta", None)),
        )

    def log_and_deduct(self, **kw):
        from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request

        return log_and_deduct_cost_for_api_request(**kw)

    def log_and_deduct_resource(self, organization, api_call_type, config=None, workspace=None):
        from ee.usage.utils.usage_entries import log_and_deduct_cost_for_resource_request

        return log_and_deduct_cost_for_resource_request(
            organization, api_call_type, config=config, workspace=workspace
        )

    def ai_credits(self, cost_usd):
        from ee.usage.services.config import BillingConfig

        return int(BillingConfig.get().calculate_ai_credits(cost_usd))

    def has_feature(self, org_id, feature):
        from ee.usage.services.entitlements import Entitlements

        return Entitlements.has_feature_unified(str(org_id), feature)

    def can_create(self, org_id, resource, current_count=0):
        from ee.usage.services.entitlements import Entitlements

        result = Entitlements.can_create(str(org_id), resource, current_count)
        return UsageDecision(
            allowed=getattr(result, "allowed", True),
            reason=getattr(result, "reason", ""),
            error_code=getattr(result, "error_code", ""),
            upgrade_cta=_cta_dict(getattr(result, "upgrade_cta", None)),
        )

    def count_tokens(self, text):
        from ee.usage.utils.usage_entries import count_text_tokens

        return count_text_tokens(str(text))

    def get_retention_days(self, org_id, data_type):
        from ee.usage.services.entitlements import Entitlements

        return Entitlements.get_retention_days(str(org_id), data_type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cta_dict(cta: Any) -> Optional[dict]:
    if cta is None:
        return None
    if isinstance(cta, dict):
        return cta
    if hasattr(cta, "model_dump"):
        return cta.model_dump()
    return dict(cta)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_instance: Optional[Billing] = None


def get_billing() -> Billing:
    """Return the process-singleton Billing implementation.

    _EeBilling if ee.usage is importable, _NoopBilling otherwise.
    Cached for the process lifetime — never changes after first call.
    """
    global _instance
    if _instance is None:
        try:
            import ee.usage  # noqa: F401
            _instance = _EeBilling()
        except ImportError:
            _instance = _NoopBilling()
    return _instance


def _reset_for_testing() -> None:
    """Force re-detection on next get_billing() call.  Test use only."""
    global _instance
    _instance = None
