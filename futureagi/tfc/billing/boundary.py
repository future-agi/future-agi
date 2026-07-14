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
from typing import Any, Literal, Optional


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UsageDecision:
    """Outcome of a usage/entitlement check.

    ``frozen=True`` — this is safe to share as a singleton (see ``_ALLOW``
    below). Without freezing, any caller that mutates the returned instance
    would silently corrupt every subsequent call that got the same object.
    """

    allowed: bool = True
    reason: str = ""
    error_code: str = ""
    upgrade_cta: Optional[dict] = field(default=None)
    retry_after: Optional[int] = None


_ALLOW = UsageDecision(allowed=True)


# ---------------------------------------------------------------------------
# BillingEventType — re-exported from ee when present, stubbed for OSS
# ---------------------------------------------------------------------------

try:
    from ee.usage.schemas.event_types import BillingEventType  # noqa: F401
except ImportError:
    class _BillingEventTypeStub:
        """OSS stub: attribute access returns the real enum's ``.value`` string.

        The real enum is declared ``class BillingEventType(str, Enum)`` with
        lowercase snake_case values, e.g.::

            SYNTHETIC_DATA_GENERATION = "synthetic_data_generation"

        Because it's a ``str, Enum`` mix-in, ``BillingEventType.X`` in EE
        equals its ``.value`` — the lowercase string. This stub must emit
        the SAME lowercase string so an OSS boundary call that happens to
        pass ``event_type`` downstream (e.g. to a log line, a metric, an
        integration webhook) sees the same key as the EE build.

        The returned value is a ``str`` subclass exposing ``.value`` (and
        ``.name``) so call sites written against the EE enum — e.g.
        ``BillingEventType.CODE_EVALUATOR.value`` — work identically in OSS
        instead of raising ``AttributeError`` on a plain str.
        """
        class _StubMember(str):
            @property
            def value(self) -> str:
                return str(self)

            @property
            def name(self) -> str:
                return str(self).upper()

        def __getattr__(self, name: str) -> "_BillingEventTypeStub._StubMember":
            return self._StubMember(name.lower())

    BillingEventType = _BillingEventTypeStub()


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class Billing:
    """Coarse billing operations exposed to OSS code.

    Never instantiate directly — use get_billing().
    """

    is_enabled: bool = True

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

    def log_and_deduct(
        self,
        organization: Any,
        api_call_type: str,
        config: Optional[dict] = None,
        source: Optional[str] = None,
        source_id: Any = None,
        workspace: Any = None,
    ) -> Any:
        """Create an APICallLog row and gate on credits.  No-ops in OSS.

        Explicit signature (mirrors ee's ``log_and_deduct_cost_for_api_request``)
        so a typo'd kwarg raises ``TypeError`` on OSS test runs instead of only
        blowing up on EE/Cloud.
        """
        raise NotImplementedError

    def log_and_deduct_resource(
        self,
        organization: Any,
        api_call_type: str,
        config: Optional[dict] = None,
        workspace: Any = None,
        **extra: Any,
    ) -> Any:
        """Gate resource creation (datasets, rows, KB) and log the attempt.  No-ops in OSS.

        ``**extra`` is forwarded to the EE implementation so callers can pass
        the extra kwargs the underlying ee.usage function accepts (e.g.
        ``sdk_source=True``) without every one of them having to be spelled
        out on the boundary.
        """
        raise NotImplementedError

    def ai_credits(self, cost_usd: float) -> float:
        """Convert a raw LLM cost (USD) to AI credits (fractional).  Returns 0 in OSS."""
        raise NotImplementedError

    def has_feature(self, org_id: str, feature: str) -> bool:
        """Entitlement gate — returns False (fail-CLOSED) in OSS."""
        raise NotImplementedError

    def check_feature_gate(self, org_id: str, feature: str) -> UsageDecision:
        """Feature gate check with denial reason (richer than has_feature).

        Use this when the caller needs the denial reason (e.g. to return it
        in an API response).  Returns UsageDecision(allowed=False) in OSS.
        EE calls Entitlements.check_feature so existing test mocks work.
        """
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

    def count_tiktoken_tokens(
        self,
        text: str,
        image_urls: Optional[list] = None,
    ) -> int:
        """Count tokens for a text (+ optional image URLs) via tiktoken.

        Separate from ``count_tokens`` because callers that previously used
        ``count_tiktoken_tokens`` need the image-aware variant with the same
        tokenizer they had on dev.  Returns 0 in OSS.
        """
        raise NotImplementedError

    def get_tracing_billing_mode(self, org_id: str) -> str:
        """Return ``events`` or ``storage`` for a tracing org.  Defaults to
        ``storage`` in OSS to match ee.usage.services.billing_engine's
        fallback so span-ingest metering stays on the same dimension.
        """
        raise NotImplementedError

    def get_retention_days(self, org_id: str, data_type: str) -> int:
        """Data retention in days.  Returns 0 (no enforcement) in OSS."""
        raise NotImplementedError

    def eval_per_run_fee(self) -> float:
        """Per-run platform fee for evals.  Returns 0.0 in OSS."""
        raise NotImplementedError

    def refund(self, api_call_log_row: Any, **config: Any) -> None:
        """Refund credits for a failed API call.  No-op in OSS."""
        raise NotImplementedError

    def check_rate_limit(
        self, org_id: str, limit_type: Literal["api", "ingestion"]
    ) -> UsageDecision:
        """Rate-limit check.  Returns ALLOW in OSS.

        ``limit_type`` is a *limit type* (``"api"`` or ``"ingestion"``), NOT a
        BillingEventType — EE's RateLimiter silently allows unknown values, so
        passing anything else would disable rate limiting.
        """
        raise NotImplementedError

    def setup_org_subscription(self, organization: Any) -> None:
        """Create default subscription for a new org.  No-op in OSS."""
        raise NotImplementedError

    def get_gateway_client(self, **kw: Any) -> Any:
        """Return sync gateway LLM client.  Raises ImportError in OSS."""
        raise NotImplementedError

    def get_async_gateway_client(self, **kw: Any) -> Any:
        """Return async gateway LLM client.  Raises ImportError in OSS."""
        raise NotImplementedError

    # -- shared guard helpers (same logic for EE and OSS) -------------------

    def deduct_denied(self, call_log_row: Any) -> bool:
        """True when a ``log_and_deduct`` result means the action must be blocked.

        EE ``None`` → billing errored; fail closed (dev raised inside the ee
        function).  OSS ``None`` → allowed (there is no billing).  A non-None
        row denies unless its status is PROCESSING.
        """
        if call_log_row is None:
            return self.is_enabled
        from tfc.constants.api_calls import APICallStatusChoices

        return call_log_row.status != APICallStatusChoices.PROCESSING.value

    def resource_denied(self, call_log_row: Any) -> bool:
        """True when a ``log_and_deduct_resource`` result means the resource limit was hit.

        EE ``None`` → billing errored; fail closed.  OSS ``None`` → allowed.
        A non-None row denies only on RESOURCE_LIMIT (mirrors dev's checks).
        """
        if call_log_row is None:
            return self.is_enabled
        from tfc.constants.api_calls import APICallStatusChoices

        return call_log_row.status == APICallStatusChoices.RESOURCE_LIMIT.value


# ---------------------------------------------------------------------------
# _NoopBilling — OSS fallback
# ---------------------------------------------------------------------------

class _NoopBilling(Billing):
    """OSS default: metering is silent, entitlements fail-CLOSED."""

    is_enabled = False

    def record_usage(self, org_id, event_type, *, amount=1.0, **properties):
        pass

    def check_usage(self, org_id, event_type, amount=0):
        return _ALLOW

    def log_and_deduct(
        self,
        organization,
        api_call_type,
        config=None,
        source=None,
        source_id=None,
        workspace=None,
    ):
        return None

    def log_and_deduct_resource(self, organization, api_call_type, config=None, workspace=None, **extra):
        return None

    def ai_credits(self, cost_usd):
        return 0.0

    def has_feature(self, org_id, feature):
        return False  # entitlement: fail-CLOSED — avoids the develop_annotations leak

    def check_feature_gate(self, org_id, feature):
        return UsageDecision(allowed=False, reason="This feature requires an EE or Cloud plan.")

    def can_create(self, org_id, resource, current_count=0):
        return _ALLOW

    def count_tokens(self, text):
        return 0

    def count_tiktoken_tokens(self, text, image_urls=None):
        return 0

    def get_tracing_billing_mode(self, org_id):
        return "storage"

    def get_retention_days(self, org_id, data_type):
        return 0

    def eval_per_run_fee(self) -> float:
        return 0.0

    def refund(self, api_call_log_row, **config):
        pass  # no credits to refund in OSS

    def check_rate_limit(self, org_id, limit_type):
        return _ALLOW  # no rate limiting in OSS

    def setup_org_subscription(self, organization):
        pass  # no subscription management in OSS

    def get_gateway_client(self, **kw):
        raise ImportError("get_gateway_client requires the ee extras")

    def get_async_gateway_client(self, **kw):
        raise ImportError("get_async_gateway_client requires the ee extras")


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

    def log_and_deduct(
        self,
        organization,
        api_call_type,
        config=None,
        source=None,
        source_id=None,
        workspace=None,
    ):
        from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request

        return log_and_deduct_cost_for_api_request(
            organization,
            api_call_type,
            config=config,
            source=source,
            source_id=source_id,
            workspace=workspace,
        )

    def log_and_deduct_resource(self, organization, api_call_type, config=None, workspace=None, **extra):
        from ee.usage.utils.usage_entries import log_and_deduct_cost_for_resource_request

        return log_and_deduct_cost_for_resource_request(
            organization, api_call_type, config=config, workspace=workspace, **extra
        )

    def ai_credits(self, cost_usd):
        from ee.usage.services.config import BillingConfig

        # calculate_ai_credits returns fractional credits — do NOT truncate,
        # billing events are charged with the fractional amount.
        return BillingConfig.get().calculate_ai_credits(cost_usd)

    def has_feature(self, org_id, feature):
        # ee present but entitlements broken → allow by default, mirroring
        # tfc.ee_gating.check_ee_feature's ImportError policy.
        try:
            from ee.usage.services.entitlements import Entitlements
        except ImportError:
            _log_entitlements_import_failure()
            return True

        return Entitlements.has_feature_unified(str(org_id), feature)

    def check_feature_gate(self, org_id, feature):
        try:
            from ee.usage.services.entitlements import Entitlements
        except ImportError:
            _log_entitlements_import_failure()
            return _ALLOW

        result = Entitlements.check_feature(str(org_id), feature)
        return UsageDecision(
            allowed=getattr(result, "allowed", True),
            reason=getattr(result, "reason", ""),
            error_code=getattr(result, "error_code", ""),
            upgrade_cta=_cta_dict(getattr(result, "upgrade_cta", None)),
        )

    def can_create(self, org_id, resource, current_count=0):
        try:
            from ee.usage.services.entitlements import Entitlements
        except ImportError:
            _log_entitlements_import_failure()
            return _ALLOW

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

    def count_tiktoken_tokens(self, text, image_urls=None):
        from ee.usage.utils.usage_entries import count_tiktoken_tokens

        if image_urls is None:
            return count_tiktoken_tokens(str(text))
        return count_tiktoken_tokens(str(text), image_urls)

    def get_tracing_billing_mode(self, org_id):
        from ee.usage.services.emitter import get_redis
        from ee.usage.models.usage import OrganizationSubscription

        org_id_str = str(org_id)
        cache_key = f"tracing_billing_mode:{org_id_str}"
        try:
            cached = get_redis().get(cache_key)
            if cached is not None:
                return cached if isinstance(cached, str) else cached.decode()
        except Exception:
            pass

        mode = (
            OrganizationSubscription.objects.filter(
                organization_id=org_id_str, deleted=False
            )
            .values_list("tracing_billing_mode", flat=True)
            .first()
        ) or "storage"

        try:
            get_redis().setex(cache_key, 300, mode)
        except Exception:
            pass

        return mode

    def get_retention_days(self, org_id, data_type):
        from ee.usage.services.entitlements import Entitlements

        return Entitlements.get_retention_days(str(org_id), data_type)

    def eval_per_run_fee(self) -> float:
        from ee.usage.services.config import BillingConfig

        return BillingConfig.get().get_eval_per_run_fee()

    def refund(self, api_call_log_row, **config):
        from ee.usage.utils.usage_entries import refund_cost_for_api_call

        refund_cost_for_api_call(api_call_log_row, config=config or None)

    def check_rate_limit(self, org_id, limit_type):
        from ee.usage.services.rate_limiter import RateLimiter

        result = RateLimiter.check(org_id, limit_type)
        return UsageDecision(
            allowed=getattr(result, "allowed", True),
            reason=getattr(result, "reason", ""),
            error_code=getattr(result, "error_code", ""),
            retry_after=getattr(result, "retry_after", None),
        )

    def setup_org_subscription(self, organization):
        from ee.usage.utils.usage_entries import create_organization_subscription_if_not_exists

        create_organization_subscription_if_not_exists(organization)

    def get_gateway_client(self, **kw):
        from ee.usage.services.gateway_llm_client import get_gateway_client

        return get_gateway_client(**kw)

    def get_async_gateway_client(self, **kw):
        from ee.usage.services.gateway_llm_client import get_async_gateway_client

        return get_async_gateway_client(**kw)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_entitlements_import_failure() -> None:
    import logging

    # ERROR, not warning: entitlements failing open on a broken EE install
    # means every paid feature is granted to every org — a revenue leak that
    # must be loud enough to trip log-based alerting.
    logging.getLogger(__name__).error(
        "ee.usage.services.entitlements import failed; entitlements are "
        "FAILING OPEN — every feature gate is allowing by default. "
        "Fix the ee install immediately."
    )


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


# ---------------------------------------------------------------------------
# Re-exports: token_usage_properties / llm_usage_properties
# These build the properties dict passed to billing.record_usage(**props).
# Exposed here so call sites don't import from ee.usage directly.
# ---------------------------------------------------------------------------

try:
    from ee.usage.utils.event_properties import (  # noqa: F401
        token_usage_properties,
        llm_usage_properties,
    )
except ImportError:
    def token_usage_properties(*args: Any, **kwargs: Any) -> dict:  # type: ignore[misc]
        return {}

    def llm_usage_properties(*args: Any, **kwargs: Any) -> dict:  # type: ignore[misc]
        return {}


# ---------------------------------------------------------------------------
# Re-export: UsageLimitExceeded exception
# ---------------------------------------------------------------------------

try:
    from ee.usage.exceptions import UsageLimitExceeded  # noqa: F401
except ImportError:
    class UsageLimitExceeded(Exception):  # type: ignore[misc]
        """OSS stub — raised in EE/Cloud when usage limits are exceeded."""
