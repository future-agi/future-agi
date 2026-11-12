"""Credit ledger port for managed (Future AGI credits) model calls.

Contracts
---------
The assumed ``holdSettle`` contract (assumed until the gateway
publishes its port):

- ``hold(org, workspace, amount_estimate, idempotency_key, purpose)`` runs
  before a managed provider call is dispatched; a refusal dispatches nothing.
- ``settle(hold_id, actual_amount)`` runs once after the call returns, and
  only when the managed transport claimed the hold (payer binding, below). A
  port that declares ``idempotent_settle = True`` (a replay with the same hold
  id and amount is a no-op) gets bounded settle retries; any other port gets
  exactly one attempt.
- ``release(hold_id)`` runs before claim or when SDK-bound evidence proves
  a claimed request was refused before upstream, without any output. Other
  claimed failures with observed output settle actual usage capped at the hold;
  unknown outcomes retain the hold for record-based reconciliation (24h TTL).
  A claimed call without a validated ``x-agentcc-request-id`` RESPONSE header
  is such an unknown outcome even with priced output: a provisional debit that
  no record could ever correct would also escape the TTL release.
- Every claimed Exception is terminal, whether charged or released; only
  pre-claim refusals retain the contract's bounded backoff below.

The gateway's refusal contract: every refusal is keyed on
``error.code``, never ``error.type``:

- ``managed_credits_exhausted``, ``agent_budget_exhausted``,
  ``budget_exceeded`` and ``org_budget_exceeded`` are terminal: the hold is
  released and managed dispatch stops (for the run, and for an org-level
  refusal also for new holds of that organization for a short window).
- ``managed_credits_insufficient`` is retried only when
  ``metadata.limit_source == "agentcc_in_flight"`` and
  ``metadata.remedy_hint == "retry"``, after Retry-After; otherwise terminal.
- ``managed_spend_velocity`` and ``rate_limit_exceeded`` back off per
  Retry-After (bounded).
- ``managed_billing_unavailable`` fails closed (nothing is dispatched) and is
  retryable by the caller. After the transport claimed and sent the call, the
  meter retains the reservation pending reconciliation unless output proves
  usage; unknown outcomes never charge the full hold at call time.
- ``permission_denied`` (provisional: unpriced managed model) is terminal.

Payer binding
-------------
A managed meter places the hold. The managed transport must call
``require_managed_hold(model_name=...)`` right before it dispatches on the
managed credential; that claims the hold and pins the route. A hold that no
transport claimed is released and refused, so credits are charged only for a
call dispatched on the managed credential. Call sites that dispatch directly
with a provider credential (the optimizer generator and trial evaluator)
call ``guard_direct_dispatch``: a Future AGI credits route is refused there
before any model call.

Fail closed
-----------
A failed settlement or an unclaimed managed dispatch suspends new managed
holds on that ledger for ``METER_SUSPEND_SECONDS`` (``managed_billing_unavailable``).
Without a configured ledger every managed hold is refused.

BYOK calls (the organization's own provider key) never touch the ledger: the
meter is inert for them and their behaviour is unchanged.

The real-contract dependencies live in this module only, so the swap is one
place: the ledger (``configure_credit_ledger(port)`` or
``settings.FI_CREDIT_LEDGER_PORT = "module.path:factory"``) and the payer
route (``model_hub.services.model_route.resolve_model_route``, the routing
adapter; ``ROUTE_OPERATIONS`` maps purposes to its operations).

Identity: every managed call carries a ``BillingEnvelope``
(org, workspace, purpose, source, source id, row, call ref, model) whose
``logical_call_id`` is stable across retries of the same logical call. Each
dispatch gets its own ``request_id``; the ledger idempotency key is
``dsx:<request_id>``. Usage event ids are ``uuid5`` of the request id, so a
replayed emit deduplicates.

Single charge: a managed call has exactly one LLM-cost debit,
its own settle. ``settlement_sink`` lets a caller that meters a whole
evaluation mark its usage with the request ids that paid for it;
``legacy_billing_installed`` and ``require_outer_meter`` back the visible cuts
for surfaces that would bill the same call a second time.

``MockCreditLedger`` is the in-memory double for tests only.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import importlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

import structlog
from django.db import connection

from tfc.utils.error_codes import managed_gateway_display

logger = structlog.get_logger(__name__)

MANAGED = "managed"
BYOK = "byok"
NO_ROUTE = "none"
PAYER_CREDITS = "future_agi_credits"
PAYER_OWN_KEY = "own_provider_key"

PURPOSE_DATASET_RUN_PROMPT = "dataset_run_prompt"
PURPOSE_RUN_PROMPT_PREVIEW = "run_prompt_preview"
PURPOSE_PROMPT_PLAYGROUND = "prompt_playground"
PURPOSE_EXPERIMENT_PROMPT = "experiment_prompt"
PURPOSE_OPTIMIZER_TEACHER = "optimizer_teacher"
PURPOSE_OPTIMIZER_PROPOSAL = "optimizer_proposal"
PURPOSE_OPTIMIZER_TRIAL = "optimizer_trial"
PURPOSE_LEGACY_OPTIMIZER = "legacy_optimizer"

# The routing adapter's route operations (model_route.OPERATIONS) per purpose.
# The purpose itself stays the ledger purpose and the billing identity.
ROUTE_OPERATIONS = {
    PURPOSE_DATASET_RUN_PROMPT: "run_prompt",
    PURPOSE_RUN_PROMPT_PREVIEW: "run_prompt",
    PURPOSE_PROMPT_PLAYGROUND: "run_prompt",
    PURPOSE_EXPERIMENT_PROMPT: "experiment_prompt",
    PURPOSE_OPTIMIZER_TEACHER: "run_prompt",
    PURPOSE_OPTIMIZER_PROPOSAL: "run_prompt",
    PURPOSE_OPTIMIZER_TRIAL: "run_prompt",
    PURPOSE_LEGACY_OPTIMIZER: "run_prompt",
}

MODEL_ROUTE_MODULE = "model_hub.services.model_route"
LEDGER_SETTING = "FI_CREDIT_LEDGER_PORT"
BILLING_URL_SETTING = "FI_CREDITS_BILLING_URL"
DEFAULT_BILLING_URL = "/settings/billing"

DEFAULT_COMPLETION_TOKENS = 1024
NON_TEXT_PART_TOKENS = 1000
FALLBACK_HOLD_ESTIMATE = Decimal("0.05")
SETTLE_ATTEMPTS = 3
MANAGED_CALL_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 10
METER_SUSPEND_SECONDS = 120
ORG_REFUSAL_MEMO_SECONDS = 30
RECONCILE_TTL = timedelta(hours=24)
_MICRO = Decimal("0.000001")
_ZERO = Decimal("0")
_MAX_AMOUNT = Decimal("1e9")

# The gateway refusal contract's error.code values.
CODE_CREDITS_EXHAUSTED = "managed_credits_exhausted"
CODE_CREDITS_INSUFFICIENT = "managed_credits_insufficient"
CODE_AGENT_BUDGET_EXHAUSTED = "agent_budget_exhausted"
CODE_BUDGET_EXCEEDED = "budget_exceeded"
CODE_ORG_BUDGET_EXCEEDED = "org_budget_exceeded"
CODE_SPEND_VELOCITY = "managed_spend_velocity"
CODE_RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
CODE_BILLING_UNAVAILABLE = "managed_billing_unavailable"
CODE_PERMISSION_DENIED = "permission_denied"
LIMIT_SOURCE_IN_FLIGHT = "agentcc_in_flight"
LIMIT_SOURCE_CREDITS = "agentcc_credits"
REMEDY_RETRY = "retry"
REMEDY_ADD_CREDITS = "add_credits"
# The superseded assumed-contract refusal code, still accepted from
# a port and normalised to managed_credits_insufficient.
LEGACY_INSUFFICIENT_CREDIT = "INSUFFICIENT_CREDIT"
METADATA_KEYS = (
    "limit_source",
    "remedy_hint",
    "required_usd",
    "available_usd",
    "scope",
    "resets_at",
)

# Backoff sleeps (tests replace these).
_sleep = time.sleep
_asleep = asyncio.sleep


# --- amounts ----------------------------------------------------------------


def _to_decimal(value: Any) -> Decimal | None:
    """A bounded amount, or None. The exponent is checked before any
    context-sensitive arithmetic: ``abs()`` of 1E1000000 raises
    decimal.Overflow under the default context. ``adjusted()`` and
    ``copy_abs()`` never round."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ArithmeticError, ValueError, TypeError):
            return None
    if not amount.is_finite():
        return None
    if amount and not -12 <= amount.adjusted() <= 12:
        return None
    if amount.copy_abs() >= _MAX_AMOUNT:
        return None
    return amount


def _as_amount(value: Any) -> Decimal:
    amount = _to_decimal(value)
    if amount is None or amount < 0:
        raise CreditHoldStateError(f"invalid credit amount {value!r}")
    return amount


def _amount_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    amount = _to_decimal(value)
    if amount is None:
        return None
    try:
        return format(amount.quantize(_MICRO).normalize(), "f")
    except ArithmeticError:
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _org_key(org: Any) -> str:
    return str(getattr(org, "id", org))


def _retry_seconds(value: Any) -> int | float | None:
    """Seconds from a Retry-After value (delta-seconds only; dates are ignored)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds != seconds or seconds < 0:  # NaN or negative
        return None
    return int(seconds) if seconds.is_integer() else seconds


def _copy(key: str | None, fallback: str) -> str:
    """User copy from tfc.utils.error_codes (one source of wording)."""
    if not key:
        return fallback
    try:
        from tfc.utils.error_codes import err_dict
    except Exception:
        return fallback
    value = err_dict.get(key)
    return str(value[0]) if value else fallback


def billing_url_default() -> str:
    try:
        from django.conf import settings

        return getattr(settings, BILLING_URL_SETTING, None) or DEFAULT_BILLING_URL
    except Exception:
        return DEFAULT_BILLING_URL


# --- refusals and errors ----------------------------------------------------


class ManagedCallRefusal(Exception):
    """A managed (Future AGI credits) call that was refused or stopped.

    Keyed on ``code`` (the gateway's ``error.code`` or a meter code). The
    message is always this class's own copy: text from a port, the gateway or
    a provider is never carried. Never rerouted to another credential and
    never answered by an unmetered fallback.
    """

    is_managed_refusal = True
    code = "MANAGED_CALL_REFUSED"
    error_type = "payment_required"
    http_status = 402
    retryable = False
    backoff = False
    default_retry_after: int | None = None
    message_key: str | None = None
    default_message = "This managed model call was not started."
    # True only for a refusal raised after the managed transport sent the call
    # (its reservation was settled); its copy never says "not run".
    dispatched = False
    charged = False

    def __init__(
        self,
        message: str | None = None,
        *,
        metadata: dict | None = None,
        required: Any = None,
        available: Any = None,
        retry_after: Any = None,
        billing_url: str | None = None,
    ):
        meta = metadata if isinstance(metadata, dict) else {}
        self.metadata = {
            key: meta[key] for key in METADATA_KEYS if meta.get(key) is not None
        }
        self.required = _to_decimal(
            required if required is not None else meta.get("required_usd")
        )
        self.available = _to_decimal(
            available if available is not None else meta.get("available_usd")
        )
        seconds = _retry_seconds(
            retry_after if retry_after is not None else meta.get("retry_after")
        )
        self.retry_after = seconds if seconds is not None else self.default_retry_after
        self.billing_url = billing_url or billing_url_default()
        self.message = message or _copy(self.message_key, self.default_message)
        super().__init__(self.message)

    def as_response_body(self) -> dict[str, Any]:
        """The typed refusal body for API responses and cells; no provider text."""
        metadata = dict(self.metadata)
        for key in ("required_usd", "available_usd"):
            if key in metadata:
                metadata[key] = _amount_text(_to_decimal(metadata[key]))
        body = {
            "code": self.code,
            "type": self.error_type,
            "message": self.message,
            "retryable": self.retryable,
            "retry_after": self.retry_after,
            "required": _amount_text(self.required),
            "available": _amount_text(self.available),
            "billing_url": self.billing_url,
            "metadata": metadata,
            "status": self.http_status,
            "category": getattr(self, "category", "generic"),
            "display_wait_seconds": None,
        }
        # Only SDK-bound display attachments replace presentation. A local
        # ledger refusal keeps its own copy and retry contract. No exception
        # attribute or money decision is changed by serialization.
        display = (
            managed_gateway_display(self)
            if isinstance(getattr(self, "gateway_display", None), dict)
            else None
        )
        outcome = self.code in {
            "MANAGED_OUTCOME_PENDING_RECONCILE",
            "CREDIT_SETTLEMENT_FAILED",
            "MANAGED_CALL_ALREADY_COMPLETED",
        }
        if display is not None:
            if outcome:
                body["original_refusal"] = dict(
                    display, retryable=False, retry_after=None
                )
            else:
                for key in (
                    "status",
                    "code",
                    "type",
                    "category",
                    "message",
                    "metadata",
                    "param",
                    "remedy",
                    "display_wait_seconds",
                ):
                    body[key] = display[key]
                body["required"] = display["metadata"].get("required_usd")
                body["available"] = display["metadata"].get("available_usd")
                body["retryable"] = bool(self.retryable and display["retryable"])
                body["retry_after"] = (
                    display["retry_after"] if body["retryable"] else None
                )
                # Only terminal in-flight copy suppresses checked retry advice.
                if display["display_classification"] == "in_flight" and (
                    not body["retryable"] or body["retry_after"] is None
                ):
                    body["metadata"] = {
                        key: value
                        for key, value in body["metadata"].items()
                        if not (key == "remedy_hint" and value == REMEDY_RETRY)
                    }
                    if body["remedy"] == REMEDY_RETRY:
                        body["remedy"] = None
                if self.charged is True:
                    body["message"] = self.message
        return body

    def as_cell_info(self) -> dict[str, Any]:
        body = self.as_response_body()
        return {"reason": body["message"], "error": body}

    def after_dispatch(self, *, charged: bool = True) -> ManagedCallRefusal:
        """Terminal form for a claimed call, based on its settlement state.

        Classification and display never decide billing: only the call's SDK
        evidence does. Every charged refusal discloses the charge, including
        credit, budget and permission failures that were already terminal.
        """
        return ManagedRefusedAfterDispatchError.of(self, charged=charged)


class ManagedCreditRefusal(ManagedCallRefusal, ValueError):
    """Terminal: release the hold and stop managed dispatch; never retried.

    A ``ValueError``, so experiment rows persist it without a Temporal retry
    (experiment_runner re-raises only non-ValueError errors).
    """


class ManagedCallAlreadyCompletedError(ManagedCreditRefusal):
    """A reused identity cannot authorize a second provider dispatch.

    This is not a new release or a claim about the first call's charge. The
    authoritative hold is left untouched, including pending reconciliation.
    """

    code = "MANAGED_CALL_ALREADY_COMPLETED"
    error_type = "invalid_request_error"
    http_status = 409
    message_key = "MANAGED_CALL_ALREADY_COMPLETED"
    default_message = "This call already ran or was refused and will not be sent again."
    dispatched = None
    charged = None


class InsufficientCreditError(ManagedCreditRefusal):
    """402 managed_credits_insufficient that is not an in-flight retry."""

    code = CODE_CREDITS_INSUFFICIENT
    message_key = "MANAGED_CREDITS_INSUFFICIENT"
    default_message = (
        "Not enough Future AGI credits for this request. Add credits on the "
        "Billing page, or use your own provider keys."
    )


class CreditsExhaustedError(InsufficientCreditError):
    code = CODE_CREDITS_EXHAUSTED
    message_key = "MANAGED_CREDITS_EXHAUSTED"
    default_message = (
        "Your organization is out of Future AGI credits. Add credits on the "
        "Billing page, or use your own provider keys."
    )


class AgentBudgetExhaustedError(ManagedCreditRefusal):
    code = CODE_AGENT_BUDGET_EXHAUSTED
    message_key = "MANAGED_AGENT_BUDGET_EXHAUSTED"
    default_message = "This run has used its budget. Raise the run budget to continue."


class BudgetExceededError(ManagedCreditRefusal):
    """429 budget refusal: a budget, never a retryable rate limit."""

    code = CODE_BUDGET_EXCEEDED
    error_type = "rate_limit_error"
    http_status = 429
    message_key = "MANAGED_BUDGET_EXCEEDED"
    default_message = (
        "A spending budget for this request has been reached. Raise or remove "
        "the budget to continue."
    )


class OrgBudgetExceededError(BudgetExceededError):
    code = CODE_ORG_BUDGET_EXCEEDED
    message_key = "MANAGED_ORG_BUDGET_EXCEEDED"
    default_message = (
        "Your organization's spending budget has been reached. An admin can "
        "raise or remove it in budget settings."
    )


class ManagedPermissionDeniedError(ManagedCreditRefusal):
    """403 permission_denied for a managed model (provisional gateway code)."""

    code = CODE_PERMISSION_DENIED
    error_type = "permission_error"
    http_status = 403
    message_key = "MANAGED_MODEL_NOT_AVAILABLE"
    default_message = "This model is not available with Future AGI credits."


class CreditHoldMissingError(ManagedCreditRefusal):
    code = "CREDIT_HOLD_MISSING"
    error_type = "invalid_request_error"
    http_status = 409
    default_message = (
        "A managed model call needs a credit hold before it starts; it was not started."
    )


class ManagedRouteUnavailableError(ManagedCreditRefusal):
    code = "MODEL_ROUTE_UNAVAILABLE"
    error_type = "server_error"
    http_status = 503
    default_message = (
        "This model is not available with Future AGI credits or a provider key "
        "for this workspace."
    )


class ManagedDispatchUnavailableError(ManagedCreditRefusal):
    """A Future AGI credits route at a call site with no managed transport."""

    code = "MANAGED_DISPATCH_UNAVAILABLE"
    error_type = "invalid_request_error"
    http_status = 409
    default_message = (
        "Future AGI credits cannot pay for this step yet, so it was not run. "
        "Add your own provider key for this model to run it."
    )


class CreditSettlementError(ManagedCreditRefusal):
    """A finished managed call could not be settled; its hold is left for reconciliation."""

    code = "CREDIT_SETTLEMENT_FAILED"
    error_type = "server_error"
    http_status = 502
    dispatched = None
    charged = None
    message_key = "MANAGED_SETTLEMENT_UNKNOWN"
    default_message = (
        "The model call finished, but its Future AGI credit charge is not yet confirmed. "
        "The charge is being reconciled; do not automatically retry this request."
    )


class ManagedOutcomePendingReconcileError(ManagedCreditRefusal):
    """Claimed request with unknown execution/usage: hold open, no redispatch."""

    code = "MANAGED_OUTCOME_PENDING_RECONCILE"
    error_type = "server_error"
    http_status = 503
    dispatched = None
    charged = None
    message_key = "MANAGED_OUTCOME_PENDING_RECONCILE"
    default_message = (
        "This request's outcome is being reconciled. Its credit hold remains open; "
        "any charge will match the provider's usage record, up to the held amount. "
        "Do not automatically retry this request."
    )


class ManagedCallDeferred(ManagedCallRefusal, RuntimeError):
    """Retryable: nothing was dispatched (or the dispatch was refused), try later.

    Not a ``ValueError``, so experiment rows re-raise it and Temporal retries.
    """

    retryable = True

    def after_dispatch(self, *, charged: bool = True) -> ManagedCallRefusal:
        """This refusal for a call the managed transport
        had already claimed. Charged (settled once) or released (refused
        before upstream, main 11-06), it is terminal: an automatic retry by
        ManagedAttempts or by Temporal for this RuntimeError would place
        another hold for one logical call."""
        return ManagedRefusedAfterDispatchError.of(self, charged=charged)


class CreditsInFlightError(ManagedCallDeferred):
    """402 managed_credits_insufficient with limit_source agentcc_in_flight + retry."""

    code = CODE_CREDITS_INSUFFICIENT
    backoff = True
    default_retry_after = 2
    message_key = "MANAGED_CREDITS_IN_FLIGHT"
    default_message = (
        "Other requests are holding your Future AGI credits right now. "
        "Try again in a few seconds."
    )


class SpendVelocityError(ManagedCallDeferred):
    code = CODE_SPEND_VELOCITY
    error_type = "rate_limit_error"
    http_status = 429
    backoff = True
    default_retry_after = 5
    message_key = "MANAGED_SPEND_VELOCITY"
    default_message = (
        "Future AGI credits are being spent too quickly. Slow down and try "
        "again shortly."
    )


class ManagedRateLimitError(ManagedCallDeferred):
    code = CODE_RATE_LIMIT_EXCEEDED
    error_type = "rate_limit_error"
    http_status = 429
    backoff = True
    default_retry_after = 5
    message_key = "RATE_LIMIT_REACHED"
    default_message = "Rate limit reached. Please try again later."


class CreditLedgerUnavailableError(ManagedCallDeferred):
    """503 managed_billing_unavailable: fail closed, retryable by the caller."""

    code = CODE_BILLING_UNAVAILABLE
    error_type = "server_error"
    http_status = 503
    message_key = "MANAGED_BILLING_UNAVAILABLE"
    default_message = (
        "Billing is temporarily unavailable, so this request was not run. "
        "Try again shortly."
    )

    def after_dispatch(self, *, charged: bool = True) -> ManagedCallRefusal:
        return ManagedRefusedAfterDispatchError.of(self, charged=charged)


class CreditLedgerUnavailableAfterDispatchError(ManagedCreditRefusal):
    """managed_billing_unavailable for a call the managed transport had already
    sent. The meter settled its reservation because
    nothing proves the provider did no work, so the copy says it may have run
    and was charged. Terminal: an automatic retry would charge a second call
    for work that may already be done."""

    code = CODE_BILLING_UNAVAILABLE
    error_type = "server_error"
    http_status = 503
    dispatched = True
    charged = True
    message_key = "MANAGED_BILLING_UNAVAILABLE_AFTER_DISPATCH"
    default_message = (
        "Billing became unavailable after this request was sent, so it may have "
        "run and its Future AGI credits were charged. Check the result before "
        "you try again."
    )


class ManagedRefusedAfterDispatchError(ManagedCreditRefusal):
    """Any typed refusal for a call the managed transport had already
    claimed. Terminal either way: a ``ValueError`` that
    ManagedAttempts never retries and Temporal does not re-run, so no second
    hold follows. Keeps the gateway's code, type and status; carries no
    Retry-After and no retry remedy hint.

    ``charged``: the meter settled the reservation once (output was observed,
    the gateway attested dispatch, or nothing proves the refusal came before
    upstream); its copy says it may have run and was charged. Otherwise the
    hold was released, nothing was charged (the rule: a refused call
    is never charged), ``dispatched`` is False and the refusal keeps its own
    copy."""

    dispatched = True
    charged = True
    message_key = "MANAGED_REFUSED_AFTER_DISPATCH"
    default_message = (
        "This request was refused after it was sent, so it may have run and "
        "its Future AGI credits were charged. Check the result before you try "
        "again."
    )

    @classmethod
    def of(
        cls, refusal: ManagedCallRefusal, *, charged: bool = True
    ) -> ManagedCreditRefusal:
        metadata = {
            key: value
            for key, value in refusal.metadata.items()
            if not (key == "remedy_hint" and value == REMEDY_RETRY)
        }
        terminal_cls = cls
        if charged and refusal.code == CODE_BILLING_UNAVAILABLE:
            terminal_cls = CreditLedgerUnavailableAfterDispatchError
        elif not charged and isinstance(refusal, ManagedCreditRefusal):
            # Preserve an already-terminal refusal's own class and copy without
            # mutating an object a caller or the safety memo may still hold.
            terminal_cls = type(refusal)
        terminal = terminal_cls(
            None if charged else refusal.message,
            metadata=metadata,
            required=refusal.required,
            available=refusal.available,
            billing_url=refusal.billing_url,
        )
        terminal.display_classification = (
            "in_flight"
            if isinstance(refusal, CreditsInFlightError)
            else getattr(refusal, "display_classification", None)
        )
        terminal.code = refusal.code
        terminal.error_type = refusal.error_type
        terminal.http_status = refusal.http_status
        if hasattr(refusal, "remedy"):
            terminal.remedy = None if refusal.remedy == REMEDY_RETRY else refusal.remedy
        terminal.charged = bool(charged)
        terminal.dispatched = bool(charged)
        terminal.retryable = False
        terminal.backoff = False
        terminal.retry_after = None
        return terminal


class ManagedCallFailedAfterDispatchError(ManagedCreditRefusal):
    """An unclassified claimed failure, settled once and never retried."""

    code = "MANAGED_CALL_FAILED_AFTER_DISPATCH"
    error_type = "server_error"
    http_status = 502
    dispatched = True
    charged = True
    message_key = "MANAGED_FAILED_AFTER_DISPATCH"
    default_message = (
        "This request failed after it was sent, so it may have run and its "
        "Future AGI credits were charged. Check the result before you try again."
    )


class ManagedCallRefusedError(ManagedCreditRefusal):
    """An unclassified SDK-proven refusal: released, terminal, no upstream text."""

    code = "MANAGED_CALL_REFUSED"
    message_key = "MANAGED_REFUSED_NOT_CHARGED"
    default_message = "The gateway refused this request; it was not run or charged."


class CreditTenancyError(PermissionError):
    """The call's workspace is not in its organization; raised before any route or credit operation."""

    code = "CREDIT_TENANCY_MISMATCH"
    retryable = False


class CreditHoldStateError(RuntimeError):
    """Ledger protocol misuse: conflicting settle, settle after release, key reuse."""

    code = "CREDIT_HOLD_STATE"


class LegacyOptimizerRetiredError(ValueError):
    """A legacy optimizer route that cannot run metered on Future AGI credits."""

    code = "LEGACY_OPTIMIZER_RETIRED"
    retryable = False


_REFUSAL_BY_CODE: dict[str, type[ManagedCallRefusal]] = {
    CODE_CREDITS_EXHAUSTED: CreditsExhaustedError,
    CODE_AGENT_BUDGET_EXHAUSTED: AgentBudgetExhaustedError,
    CODE_BUDGET_EXCEEDED: BudgetExceededError,
    CODE_ORG_BUDGET_EXCEEDED: OrgBudgetExceededError,
    CODE_SPEND_VELOCITY: SpendVelocityError,
    CODE_RATE_LIMIT_EXCEEDED: ManagedRateLimitError,
    CODE_BILLING_UNAVAILABLE: CreditLedgerUnavailableError,
    CODE_PERMISSION_DENIED: ManagedPermissionDeniedError,
    LEGACY_INSUFFICIENT_CREDIT: InsufficientCreditError,
}
MANAGED_REFUSAL_CODES = frozenset(_REFUSAL_BY_CODE) | {CODE_CREDITS_INSUFFICIENT}
# Pinned gateway error-contract release tuples. A known 4xx
# code on an unrelated 5xx status is UNKNOWN, not a refund authorization.
REFUSED_BEFORE_UPSTREAM_STATUS_CODES = frozenset(
    {
        (400, "deferred_run_unavailable"),
        (400, "extra_body_conflict"),
        (400, "invalid_extra_body"),
        (400, "invalid_provider_preference"),
        (400, "invalid_routing_field"),
        (400, "model_ambiguous"),
        (400, "no_auto_candidate"),
        (400, "omega_default_unroutable"),
        (400, "provider_preference_unavailable"),
        (400, "route_hint_unsupported"),
        (400, "routing_field_not_yet_supported"),
        (400, "unsupported_field_for_managed"),
        (400, "unsupported_routing_field"),
        (400, "usage_unavailable"),
        (401, "invalid_api_key"),
        (402, "agent_budget_exhausted"),
        (402, "agent_call_cap_exceeded"),
        (402, "agent_key_budget_exhausted"),
        (402, "agent_run_stopped"),
        (402, "call_cap_unenforceable"),
        (402, "managed_credits_exhausted"),
        (402, "managed_credits_insufficient"),
        (402, "managed_spend_paused"),
        (402, "payment_required"),
        (403, "future_agi_credits_off"),
        (403, "key_scope_denied"),
        (403, "managed_router_unbounded"),
        (403, "model_not_permitted"),
        (403, "permission_denied"),
        (404, "managed_models_disabled"),
        (404, "model_not_found"),
        (404, "no_provider_matches"),
        (404, "provider_not_found"),
        (409, "idempotency_in_progress"),
        (410, "model_expired"),
        (422, "idempotency_key_reused"),
        (429, "budget_exceeded"),
        (429, "budget_throttled"),
        (429, "managed_spend_velocity"),
        (429, "org_budget_exceeded"),
        (429, "quota_exceeded"),
        (429, "rate_limit_exceeded"),
        (429, "token_rate_limit_exceeded"),
        (429, "usage_paused"),
        (503, "budget_state_unavailable"),
        (503, "key_list_not_loaded"),
        (503, "provider_busy"),
    }
)
REFUSED_BEFORE_UPSTREAM_CODES = frozenset(
    code for _, code in REFUSED_BEFORE_UPSTREAM_STATUS_CODES
)


def is_in_flight_retry(metadata: Any) -> bool:
    """The contract's only retryable insufficient-credit case."""
    meta = metadata if isinstance(metadata, dict) else {}
    return (
        meta.get("limit_source") == LIMIT_SOURCE_IN_FLIGHT
        and meta.get("remedy_hint") == REMEDY_RETRY
    )


def refusal_for_code(
    code: Any,
    *,
    metadata: dict | None = None,
    retry_after: Any = None,
    required: Any = None,
    available: Any = None,
    billing_url: str | None = None,
) -> ManagedCallRefusal | None:
    """The typed refusal for a contract ``error.code``; ``None`` for any other code."""
    code = code.strip() if isinstance(code, str) else ""
    if code == CODE_CREDITS_INSUFFICIENT:
        cls = (
            CreditsInFlightError
            if is_in_flight_retry(metadata)
            else InsufficientCreditError
        )
    else:
        cls = _REFUSAL_BY_CODE.get(code)
    if cls is None:
        return None
    return cls(
        metadata=metadata,
        retry_after=retry_after,
        required=required,
        available=available,
        billing_url=billing_url,
    )


# --- reading a refusal out of a port result or a gateway error ----------------

_CODE_FIELD = re.compile(r"""['"]code['"]\s*:\s*['"]([A-Za-z_]+)['"]""")
_META_FIELDS = {
    key: re.compile(rf"""['"]{key}['"]\s*:\s*['"]?([A-Za-z0-9_.:+-]+)""")
    for key in METADATA_KEYS
}


def _chain(exc: BaseException | None):
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _safe_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    try:
        return str(item)
    except Exception:
        return ""


def _metadata_from_text(text: str) -> dict:
    metadata = {}
    for key, pattern in _META_FIELDS.items():
        match = pattern.search(text)
        if match and match.group(1) not in ("None", "null"):
            metadata[key] = match.group(1)
    return metadata


def _retry_after_of(item: Any) -> int | float | None:
    for holder in (getattr(item, "response", None), item):
        headers = getattr(holder, "headers", None)
        if headers is None or isinstance(item, (dict, str)):
            continue
        try:
            value = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:
            continue
        seconds = _retry_seconds(value)
        if seconds is not None:
            return seconds
    return None


def _gateway_fields(item: Any) -> tuple[str | None, dict]:
    """(code, metadata) of a contract refusal carried by ``item``, keyed on error.code."""
    code, metadata = None, {}
    body = item if isinstance(item, dict) else getattr(item, "body", None)
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        if (
            isinstance(inner.get("code"), str)
            and inner["code"] in MANAGED_REFUSAL_CODES
        ):
            code = inner["code"]
            if isinstance(inner.get("metadata"), dict):
                metadata = dict(inner["metadata"])
    if code is None and not isinstance(item, (dict, str)):
        attr = getattr(item, "code", None)
        if isinstance(attr, str) and attr in MANAGED_REFUSAL_CODES:
            code = attr
            attr_meta = getattr(item, "metadata", None)
            if attr_meta is None:
                from model_hub.services.model_route import ModelRouteError

                if isinstance(item, ModelRouteError):
                    attr_meta = getattr(item, "details", None)
            # Normalized transport details are for display/classification only.
            metadata = dict(attr_meta) if isinstance(attr_meta, dict) else {}
            for name, key in (
                ("required", "required_usd"),
                ("available", "available_usd"),
            ):
                value = getattr(item, name, None)
                if value is not None and key not in metadata:
                    metadata[key] = value
    if code is None and not isinstance(item, dict):
        text = _safe_text(item)
        match = _CODE_FIELD.search(text)
        if match and match.group(1) in MANAGED_REFUSAL_CODES:
            code = match.group(1)
            metadata = _metadata_from_text(text)
    return code, metadata


def _gateway_header(headers: Any, name: str) -> tuple[bool, Any]:
    """Presence is distinct from validity: malformed headers do not enable fallback."""
    if headers is not None:
        try:
            for key, value in headers.items():
                if isinstance(key, str) and key.lower() == name:
                    return True, value
        except Exception:
            pass
    return False, None


def _gateway_response_id(headers: Any) -> str | None:
    _, value = _gateway_header(headers, "x-agentcc-request-id")
    return _checked_gateway_id(value)


def _checked_gateway_id(value: Any) -> str | None:
    # Gateway ULIDs are short tokens. Also accepts opaque test/legacy IDs;
    # never log arbitrary response/header prose or use the client ID as fallback.
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        return value
    return None


def note_gateway_response(call: ManagedCall, response: Any) -> None:
    """Capture only HTTP response headers at the trusted raw SDK boundary.

    Every present dispatch header adds true, false or malformed to a bounded
    grow-only set. Only the singleton true/false sets attest a decision; mixed
    or malformed evidence is permanently UNKNOWN, regardless of later headers.
    Headerless responses and failures add nothing and cannot erase evidence.
    """
    headers = getattr(response, "headers", None)
    request_header_present, _ = _gateway_header(headers, "x-agentcc-request-id")
    if request_header_present:
        # Invalid/empty headers still forbid body fallback on later stream errors.
        call.gateway_request_id_header_seen = True
    request_id = _gateway_response_id(headers)
    if request_id is not None:
        call.gateway_request_id = request_id
    present, value = _gateway_header(headers, "x-agentcc-dispatched")
    if present:
        observation = (
            value
            if isinstance(value, str) and value in ("true", "false")
            else "malformed"
        )
        call._gateway_dispatch_attestations.add(observation)
        call.refused_before_upstream = call.gateway_dispatched is False
        if call.gateway_dispatch_conflict:
            call.note_dispatch_contradiction()


def note_gateway_failure(call: ManagedCall, sdk_error: BaseException) -> None:
    """Record evidence from the raw SDK error, never text or a normalized wrapper.

    Body metadata.dispatched is untrusted upstream data, even when boolean.
    Only x-agentcc-dispatched on the HTTP response attests dispatch. Without
    that header, row 4 additionally requires an exact contract status/code.
    """
    response = getattr(sdk_error, "response", None)
    note_gateway_response(call, response)
    status = getattr(sdk_error, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool):
        status = getattr(response, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool):
        status = None
    body = getattr(sdk_error, "body", None)
    inner = {}
    if isinstance(body, dict):
        # OpenAI's APIStatusError.body is the unwrapped error object; other
        # SDKs retain the {"error": ...} envelope. Neither attributes nor prose
        # are accepted as the structured error.code.
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
    code = inner.get("code")
    if not isinstance(code, str):
        code = None
    if call.gateway_dispatched is True:
        call.refused_before_upstream = False
    elif call.gateway_dispatched is False:
        call.refused_before_upstream = True
    else:
        # A present but invalid attestation is UNKNOWN, not permission to fall
        # back to a body code. The set contains only current-contract 4xx and
        # the three approved 503 codes, never legacy aliases or bare statuses.
        call.refused_before_upstream = (
            not call.gateway_dispatch_header_seen
            and (status, code) in REFUSED_BEFORE_UPSTREAM_STATUS_CODES
        )


def _in_chain(target: BaseException, exc: BaseException) -> bool:
    return any(item is target for item in _chain(exc))


def managed_refusal_from(error: Any) -> ManagedCallRefusal | None:
    """The typed refusal a managed call failed with, keyed on ``error.code``.

    Use only for a call known to be managed (inside a managed meter or from
    the ledger port): it reads the gateway's code from the error, its body or
    its text. Returns ``None`` for any other error. This is classification
    only: a typed refusal is never proof authorizing a refund.
    """
    items = list(_chain(error)) if isinstance(error, BaseException) else [error]
    for item in items:
        if isinstance(item, ManagedCallRefusal):
            return item
    found = [
        fields for fields in (_gateway_fields(item) for item in items) if fields[0]
    ]
    if not found:
        return None
    # A real contract code anywhere in the chain wins over the superseded
    # assumed-contract INSUFFICIENT_CREDIT a wrapper may carry.
    code, metadata = next(
        (fields for fields in found if fields[0] != LEGACY_INSUFFICIENT_CREDIT),
        found[0],
    )
    retry_after = next(
        (s for s in (_retry_after_of(x) for x in items) if s is not None), None
    )
    return refusal_for_code(code, metadata=metadata, retry_after=retry_after)


# --- the port ----------------------------------------------------------------


@runtime_checkable
class CreditLedgerPort(Protocol):
    """The assumed holdSettle contract. The real port is the
    one-wallet atomic ledger or the gateway holds. Set
    ``idempotent_settle = True`` only when a settle replay with the same hold
    id and amount is a guaranteed no-op."""

    def hold(
        self,
        *,
        org: Any,
        workspace: Any,
        amount_estimate: Decimal,
        idempotency_key: str,
        purpose: str,
        metadata: dict | None = None,
    ) -> Any: ...

    def claim_dispatch_once(self, idempotency_key: str) -> bool:
        """Atomically consume an explicit identity after its hold has committed.

        The hold port must commit before returning when not enclosed by an
        outer transaction. Invoke this primitive outside every Django atomic()
        block; implementations must refuse inside one, never defer consumption
        while permitting transport. The meter checks the ledger thread's actual
        connection after hold() and before invoking this primitive.

        Return True exactly once per key, False on every subsequent call,
        including concurrent calls from other processes and after restarts.
        Commit consumption before returning True; a lost acknowledgement must
        never make the key reusable. Consumption is independent of pending
        markers and survives settle/release. A port implementing this method
        must preserve consumed identities on upgrade. When no callable method
        is available, the meter imports the OSS agentcc.services.dispatch_claims
        primitive in the call path instead. Unavailable OSS support, a port
        lookup/invocation error, or any result other than literal True forbids
        sending; a failed port claim never falls back to another primitive.
        """
        ...

    def settle(
        self,
        hold_id: str,
        actual_amount: Decimal,
        *,
        source: str = "settle_record",
        gateway_request_id: str | None = None,
        excess: Decimal = _ZERO,
    ) -> bool:
        """Atomically debit, persist provenance and record the incident on the hold.

        source is provider_usage (PROVISIONAL) or settle_record (final).
        Provider usage persists pending_reconcile=True and the validated response
        key in the SAME transaction as the debit. Reserve amount_estimate minus
        actual_amount until adjust_settled finalizes; final settlement clears the
        pending marker/reserve. A provider_usage settlement without a validated
        response key must be refused BEFORE any debit.

        excess is the nonnegative amount above the cap, never a debit. Positive
        excess requires actual_amount == hold and atomically creates the first
        metadata.credit_hold_excess = {amount, org, request_id, model, recorded_at}
        with decimal/UTC timestamp strings, plus hold_excess_recorded=True. Both
        sources use this field. No excess means no field. Reports/alerts read the
        field; it IS the durable incident, not an outbox or delivery obligation.

        Return literal True only for the winning initial settlement, False for a
        same-amount replay. A replay never overwrites the field, provenance, key or
        completed correction. The acknowledged winner may emit a best-effort log;
        loss of that log/ack never changes the incident. Old ports must reject
        unsupported keywords, never silently discard them.
        """
        ...

    def adjust_settled(
        self, hold_id: str, corrected_amount: Decimal, *, excess: Decimal = _ZERO
    ) -> bool:
        """Finalize a provisional debit exactly once, bounded to [0, hold].

        Atomically lock/CAS the provisional hold, apply prior minus corrected
        amount to the wallet, persist settlement_source=settle_record and
        settlement_corrected=True, and clear pending/residual reservation. In the
        SAME write, positive excess creates credit_hold_excess (settle's schema)
        only when absent; a pre-existing field is immutable. Positive excess
        requires corrected_amount == hold; no excess creates no field.

        Return literal True only for the winning transition; identical replay
        returns False; conflicting, held, released or final non-provisional
        transitions raise. No second settle, overdraft or redispatch. Persist
        money AND the field across workers/restarts and lost acknowledgements.
        Only an acknowledged winner that newly recorded excess may log it once,
        best-effort; reports/alerts read the hold field, never log delivery state.
        """
        ...

    def release(self, hold_id: str) -> Any: ...

    def mark_pending_hold(
        self, hold_id: str, *, gateway_request_id: str | None
    ) -> None:
        """Durably mark UNKNOWN (or settle-ack failure), without debiting/releasing.
        The initial hold metadata must already contain organization_id,
        request_id, model, opened_at; this binds the response reconcile key.
        Terminal holds remain unchanged. Must be safe to replay.
        """
        ...

    def list_pending_holds(self, older_than: datetime) -> list[dict]:
        """Pending snapshots opened <= older_than, including provisional debits.

        Include settled rows with settlement_source=provider_usage, even after
        24h; retain their residual reservation until record correction. Also
        include abandoned open holds >=24h old when marker writes failed. Final
        incident-bearing rows are NOT pending: reports/alerts read the immutable
        credit_hold_excess field directly, without delivery or acknowledgement.
        Rows carry hold_id, state, pending_reconcile, amount_estimate,
        settled_amount, settlement_source, settlement_corrected,
        hold_excess_recorded, credit_hold_excess when present and all hold metadata.
        Paginate internally.
        """
        ...

    def get_hold(self, hold_id: str) -> dict:
        """Authoritative detached snapshot including terminal/provisional state.

        settle/release atomically arbitrate the initial terminal transition.
        Only adjust_settled may change a settled provisional debit. Snapshots
        include settlement_source, settled_amount, settlement_corrected,
        pending_reconcile, hold_excess_recorded, credit_hold_excess when present
        and the response reconcile key. Copy the nested incident field too: a
        snapshot consumer must not be able to mutate the stored incident.
        """
        ...


@dataclass
class MockHold:
    hold_id: str
    org_id: str
    workspace_id: str | None
    amount: Decimal
    idempotency_key: str
    purpose: str
    state: str = "held"
    settled_amount: Decimal | None = None
    debits: int = 0
    metadata: dict = field(default_factory=dict)
    pending_reconcile: bool = False


class MockCreditLedger:
    """In-memory ``CreditLedgerPort`` double. Tests only; never configured in production.

    ``available = balance - open holds``. A hold is refused with the real
    contract shape: ``managed_credits_exhausted`` when nothing is available,
    ``managed_credits_insufficient`` (limit_source agentcc_credits) when the
    estimate is larger than what is available. A key maps to one hold;
    ``settle`` is exactly once per hold (a replay with the same amount is a
    no-op, hence ``idempotent_settle``); ``release`` frees an open hold and
    leaves a settled one alone. ``calls`` records every operation in order;
    ``fail_next_settles`` makes the next N settles raise a transport error;
    ``refuse_next_holds`` raises (or returns) the queued refusals first.
    ``claim_dispatch_once`` atomically consumes a key under the same lock;
    only this test double uses an in-memory set (real ports must be durable).
    """

    idempotent_settle = True

    def __init__(
        self,
        *,
        balances: dict[Any, Any] | None = None,
        default_balance: Any = _ZERO,
        billing_url: str | None = None,
    ):
        self._lock = threading.RLock()
        self._balances = {str(k): _as_amount(v) for k, v in (balances or {}).items()}
        self._default = _as_amount(default_balance)
        self.billing_url = billing_url
        self.holds: dict[str, MockHold] = {}
        self._keys: dict[str, str] = {}
        self._consumed_dispatch_keys: set[str] = set()
        self.calls: list[tuple] = []
        self.fail_next_settles = 0
        self.refuse_next_holds: list = []

    def _balance(self, org_id: str) -> Decimal:
        return self._balances.setdefault(org_id, self._default)

    def _held(self, org_id: str) -> Decimal:
        return sum(
            (
                h.amount if h.state == "held" else h.amount - h.settled_amount
                for h in self.holds.values()
                if h.org_id == org_id
                and (
                    h.state == "held"
                    or (
                        h.state == "settled"
                        and h.metadata.get("settlement_source") == "provider_usage"
                    )
                )
            ),
            _ZERO,
        )

    def balance_of(self, org: Any) -> Decimal:
        with self._lock:
            return self._balance(_org_key(org))

    def held_of(self, org: Any) -> Decimal:
        with self._lock:
            return self._held(_org_key(org))

    def available_of(self, org: Any) -> Decimal:
        with self._lock:
            key = _org_key(org)
            return self._balance(key) - self._held(key)

    def hold(
        self,
        *,
        org,
        workspace,
        amount_estimate,
        idempotency_key,
        purpose,
        metadata=None,
    ):
        org_id = _org_key(org)
        amount = _as_amount(amount_estimate)
        workspace_id = (
            _text(getattr(workspace, "id", None)) if workspace is not None else None
        )
        with self._lock:
            self.calls.append(("hold", idempotency_key, amount))
            if self.refuse_next_holds:
                queued = self.refuse_next_holds.pop(0)
                if isinstance(queued, BaseException):
                    raise queued
                return queued
            existing = self._keys.get(idempotency_key)
            if existing is not None:
                held = self.holds[existing]
                if (
                    held.org_id != org_id
                    or held.purpose != purpose
                    or held.amount != amount
                ):
                    raise CreditHoldStateError(
                        "idempotency key reused for a different hold"
                    )
                return held.hold_id
            available = self._balance(org_id) - self._held(org_id)
            if available <= 0 or amount > available:
                refusal = (
                    CreditsExhaustedError if available <= 0 else InsufficientCreditError
                )
                raise refusal(
                    metadata={
                        "limit_source": LIMIT_SOURCE_CREDITS,
                        "remedy_hint": REMEDY_ADD_CREDITS,
                        "required_usd": amount,
                        "available_usd": max(available, _ZERO),
                        "scope": "org",
                    },
                    billing_url=self.billing_url,
                )
            hold_id = f"mockhold-{uuid.uuid4()}"
            self.holds[hold_id] = MockHold(
                hold_id=hold_id,
                org_id=org_id,
                workspace_id=workspace_id,
                amount=amount,
                idempotency_key=idempotency_key,
                purpose=purpose,
                metadata={
                    **(metadata or {}),
                    "organization_id": org_id,
                    "opened_at": (metadata or {}).get("opened_at", utc_now()),
                },
            )
            self._keys[idempotency_key] = hold_id
            return hold_id

    def claim_dispatch_once(self, idempotency_key: str) -> bool:
        # Match the real dispatch-claims primitive, including in test doubles.
        # Checking before the set mutation leaves a refused key unconsumed.
        if connection.in_atomic_block:
            raise CreditHoldStateError(
                "dispatch claim requires a committed hold outside atomic()"
            )
        with self._lock:
            if idempotency_key in self._consumed_dispatch_keys:
                return False
            self._consumed_dispatch_keys.add(idempotency_key)
            return True

    def settle(
        self,
        hold_id,
        actual_amount,
        *,
        source="settle_record",
        gateway_request_id=None,
        excess=_ZERO,
    ):
        amount, excess = _as_amount(actual_amount), _as_amount(excess)
        if source not in ("provider_usage", "settle_record"):
            raise CreditHoldStateError("invalid settlement source")
        if (
            gateway_request_id is not None
            and _checked_gateway_id(gateway_request_id) is None
        ):
            raise CreditHoldStateError("invalid gateway response identity")
        if source == "provider_usage" and gateway_request_id is None:
            # A provisional debit with no response key could never be
            # corrected by a record nor released at the TTL.
            raise CreditHoldStateError(
                "provisional settlement requires the response key"
            )
        with self._lock:
            self.calls.append(("settle", hold_id, amount))
            if self.fail_next_settles > 0:
                self.fail_next_settles -= 1
                raise ConnectionError("mock ledger unavailable")
            held = self.holds.get(hold_id)
            if held is None:
                raise CreditHoldStateError("unknown hold")
            if held.state == "settled":
                if held.settled_amount != amount:
                    raise CreditHoldStateError(
                        "hold already settled with a different amount"
                    )
                return False
            if held.state != "held":
                raise CreditHoldStateError(f"cannot settle a {held.state} hold")
            if source == "provider_usage" and amount > held.amount:
                raise CreditHoldStateError("provisional amount exceeds hold")
            if excess > _ZERO and amount != held.amount:
                raise CreditHoldStateError(
                    "excess requires a settlement capped at the hold"
                )
            # Construct before touching money; one locked write owns both outcomes.
            incident = self._excess_metadata(held, excess)
            self._balances[held.org_id] = self._balance(held.org_id) - amount
            held.state = "settled"
            held.pending_reconcile = source == "provider_usage"
            held.settled_amount = amount
            held.metadata.update(
                settlement_source=source,
                gateway_request_id=gateway_request_id,
                hold_excess_recorded="credit_hold_excess" in held.metadata,
                settlement_corrected=False,
            )
            held.metadata.update(incident)
            held.debits += 1
            return True

    def adjust_settled(self, hold_id, corrected_amount, *, excess=_ZERO):
        amount, excess = _as_amount(corrected_amount), _as_amount(excess)
        with self._lock:
            held = self.holds.get(hold_id)
            if held is None or amount > held.amount:
                raise CreditHoldStateError(
                    "correction requires a known hold and bounded amount"
                )
            if held.state != "settled":
                raise CreditHoldStateError(
                    "only a settled provisional hold can be corrected"
                )
            if held.metadata.get("settlement_corrected") is True:
                if held.settled_amount != amount:
                    raise CreditHoldStateError(
                        "hold already corrected with a different amount"
                    )
                return False
            if (
                held.metadata.get("settlement_source") != "provider_usage"
                or not held.pending_reconcile
            ):
                raise CreditHoldStateError("hold has no provisional settlement")
            if excess > _ZERO and amount != held.amount:
                raise CreditHoldStateError(
                    "excess requires a correction capped at the hold"
                )
            balance = (
                self._balance(held.org_id) + _as_amount(held.settled_amount) - amount
            )
            if balance < 0:
                raise CreditHoldStateError("correction cannot create debt")
            incident = self._excess_metadata(held, excess)
            self._balances[held.org_id] = balance
            held.settled_amount = amount
            held.pending_reconcile = False
            held.metadata.update(
                settlement_source="settle_record", settlement_corrected=True
            )
            held.metadata.update(incident)
            self.calls.append(("adjust_settled", hold_id, amount))
            return True

    def release(self, hold_id):
        with self._lock:
            self.calls.append(("release", hold_id))
            held = self.holds.get(hold_id)
            if held is None:
                raise CreditHoldStateError("unknown hold")
            if held.state == "held":
                held.state = "released"
                held.pending_reconcile = False
            return None

    def mark_pending_hold(self, hold_id, *, gateway_request_id):
        with self._lock:
            held = self.holds.get(hold_id)
            if held is None:
                raise CreditHoldStateError("unknown hold")
            if held.state == "held":
                held.pending_reconcile = True
                held.metadata["gateway_request_id"] = gateway_request_id

    def _excess_metadata(self, held, excess):
        """Build the immutable field under the money-write lock, before mutation."""
        if excess <= _ZERO or "credit_hold_excess" in held.metadata:
            return {}
        return {
            "hold_excess_recorded": True,
            "credit_hold_excess": {
                "amount": str(excess),
                "org": held.org_id,
                "request_id": held.metadata.get("request_id"),
                "model": held.metadata.get("model"),
                "recorded_at": utc_now().isoformat(),
            },
        }

    def get_hold(self, hold_id):
        with self._lock:
            held = self.holds.get(hold_id)
            if held is None:
                raise CreditHoldStateError("unknown hold")
            row = {
                **held.metadata,
                "hold_id": held.hold_id,
                "state": held.state,
                "organization_id": held.org_id,
                "amount_estimate": held.amount,
                "pending_reconcile": held.pending_reconcile,
                "settled_amount": held.settled_amount,
            }
            if "credit_hold_excess" in row:
                row["credit_hold_excess"] = dict(row["credit_hold_excess"])
            return row

    def list_pending_holds(self, older_than):
        with self._lock:
            abandoned_before = utc_now() - RECONCILE_TTL
            return [
                self.get_hold(held.hold_id)
                for held in self.holds.values()
                if held.metadata["opened_at"] <= older_than
                and (
                    (
                        held.state == "settled"
                        and held.metadata.get("settlement_source") == "provider_usage"
                    )
                    or (
                        held.state == "held"
                        and (
                            held.pending_reconcile
                            or held.metadata["opened_at"] <= abandoned_before
                        )
                    )
                )
            ]


class UnconfiguredCreditLedger:
    """The ledger when no real port is installed: every managed hold is refused."""

    def hold(self, **_kwargs):
        raise CreditLedgerUnavailableError()

    def claim_dispatch_once(self, idempotency_key: str) -> bool:
        raise CreditHoldStateError("no credit ledger is configured")

    def settle(
        self,
        hold_id,
        actual_amount,
        *,
        source="settle_record",
        gateway_request_id=None,
        excess=_ZERO,
    ):
        raise CreditHoldStateError("no credit ledger is configured")

    def adjust_settled(self, hold_id, corrected_amount, *, excess=_ZERO):
        raise CreditHoldStateError("no credit ledger is configured")

    def release(self, hold_id):
        raise CreditHoldStateError("no credit ledger is configured")

    def mark_pending_hold(self, hold_id, *, gateway_request_id):
        raise CreditHoldStateError("no credit ledger is configured")

    def get_hold(self, hold_id):
        raise CreditHoldStateError("no credit ledger is configured")

    def list_pending_holds(self, older_than):
        raise CreditLedgerUnavailableError()


_UNCONFIGURED = UnconfiguredCreditLedger()
_registry_lock = threading.Lock()
_ledger_override: Any = None
_loaded_ledger: tuple[str, Any] | None = None


def configure_credit_ledger(port: Any) -> None:
    """Install the real ledger port (the one swap point); ``None`` clears it."""
    global _ledger_override
    with _registry_lock:
        _ledger_override = port


@contextlib.contextmanager
def use_credit_ledger(port: Any):
    """Install ``port`` for the duration of the block (tests)."""
    global _ledger_override
    with _registry_lock:
        previous = _ledger_override
        _ledger_override = port
    try:
        yield port
    finally:
        with _registry_lock:
            _ledger_override = previous


def get_credit_ledger() -> Any:
    port = _ledger_override
    if port is not None:
        return port
    return _ledger_from_settings()


def _ledger_from_settings() -> Any:
    global _loaded_ledger
    try:
        from django.conf import settings

        path = getattr(settings, LEDGER_SETTING, None)
    except Exception:
        path = None
    if not path:
        return _UNCONFIGURED
    cached = _loaded_ledger
    if cached is not None and cached[0] == path:
        return cached[1]
    module_name, _, attr = str(path).partition(":")
    try:
        target = getattr(importlib.import_module(module_name), attr)
        is_port = all(hasattr(target, name) for name in ("hold", "settle", "release"))
        port = target if is_port and not isinstance(target, type) else target()
    except Exception as exc:
        logger.error(
            "credit_ledger_port_load_failed",
            setting=LEDGER_SETTING,
            error_type=type(exc).__name__,
        )
        return _UNCONFIGURED
    _loaded_ledger = (path, port)
    return port


def _with_hold_display(refusal: ManagedCallRefusal, source: Any) -> ManagedCallRefusal:
    """Attach presentation to an existing SDK-shaped hold refusal, never classify it.

    The port also accepts local code-only objects/dicts. Those retain their
    established local display; only an SDK envelope/status receives this
    projection. A broken optional descriptor cannot replace the refusal.
    """
    try:
        sdk_shaped = (
            isinstance(source, dict) and ("status" in source or "status_code" in source)
        ) or (
            not isinstance(source, (dict, str))
            and any(
                hasattr(source, name) for name in ("status_code", "body", "response")
            )
        )
        if sdk_shaped:
            refusal.gateway_display = managed_gateway_display(source)
    except Exception:
        pass
    return refusal


def _coerce_hold_id(result: Any) -> str:
    """Accept ``hold_id | refusal`` from the port in any of its likely shapes.

    A refusal is typed from its contract code with this module's copy; the
    port's own message text is never used. An unknown code fails closed.
    """
    if isinstance(result, ManagedCallRefusal):
        raise result
    if isinstance(result, BaseException):
        raise _with_hold_display(
            managed_refusal_from(result) or CreditLedgerUnavailableError(), result
        ) from result
    if isinstance(result, dict):
        inner = result.get("error") if isinstance(result.get("error"), dict) else result
        if inner.get("code"):
            code, metadata = _gateway_fields(result)
            refusal = refusal_for_code(
                code,
                metadata=metadata,
                required=inner.get("required"),
                available=inner.get("available"),
                billing_url=inner.get("billing_url"),
            )
            raise _with_hold_display(refusal or CreditLedgerUnavailableError(), result)
        result = result.get("hold_id")
    code = getattr(result, "code", None)
    if isinstance(code, str) and code:
        known, metadata = _gateway_fields(result)
        refusal = refusal_for_code(
            known,
            metadata=metadata,
            billing_url=getattr(result, "billing_url", None),
        )
        raise _with_hold_display(refusal or CreditLedgerUnavailableError(), result)
    hold_id = getattr(result, "hold_id", result)
    if hold_id is None or not str(hold_id).strip():
        raise CreditLedgerUnavailableError()
    return str(hold_id)


# --- meter safety: suspension after a settle failure, org-level stops -------


def _is_org_stop(refusal: ManagedCallRefusal) -> bool:
    if refusal.code in (CODE_CREDITS_EXHAUSTED, CODE_ORG_BUDGET_EXCEEDED):
        return True
    return (
        refusal.code == CODE_BUDGET_EXCEEDED and refusal.metadata.get("scope") == "org"
    )


_ALL_ORGS = "*"


class _MeterSafety:
    """Process-local fail-closed state, keyed by ledger port identity.

    - After a failed settlement, every new managed hold on that port is
      refused for ``METER_SUSPEND_SECONDS``; after an unclaimed managed
      dispatch, every new managed hold of that organization is.
    - After an org-level terminal refusal (credits exhausted, org budget), new
      managed holds of that organization are refused for
      ``ORG_REFUSAL_MEMO_SECONDS`` without calling the ledger or dispatching.

    Entries hold a reference to their port, so an id is never reused while
    an entry for it exists.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._suspended: dict[tuple[int, str], tuple[Any, float, str]] = {}
        self._org_stops: dict[tuple[int, str], tuple[Any, type, dict, float]] = {}

    def suspend(self, port: Any, reason: str, *, org_id: str | None = None) -> None:
        scope = org_id or _ALL_ORGS
        with self._lock:
            self._suspended[(id(port), scope)] = (
                port,
                time.monotonic() + METER_SUSPEND_SECONDS,
                reason,
            )
        logger.error(
            "credit_meter_suspended",
            reason=reason,
            scope="organization" if org_id else "ledger",
            organization_id=org_id,
            seconds=METER_SUSPEND_SECONDS,
        )

    def _live(self, table: dict, key: tuple, port: Any, now: float, until_index: int):
        entry = table.get(key)
        if entry is not None and (entry[0] is not port or entry[until_index] <= now):
            table.pop(key, None)
            return None
        return entry

    def note_refusal(
        self, port: Any, org_id: str | None, refusal: ManagedCallRefusal
    ) -> None:
        if not org_id or not _is_org_stop(refusal):
            return
        with self._lock:
            self._org_stops[(id(port), org_id)] = (
                port,
                type(refusal),
                dict(refusal.metadata),
                time.monotonic() + ORG_REFUSAL_MEMO_SECONDS,
            )

    def check(self, port: Any, org_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            suspended = self._live(
                self._suspended, (id(port), _ALL_ORGS), port, now, 1
            ) or self._live(self._suspended, (id(port), org_id), port, now, 1)
            stop = self._live(self._org_stops, (id(port), org_id), port, now, 3)
        if suspended is not None:
            raise CreditLedgerUnavailableError()
        if stop is not None:
            raise stop[1](metadata=stop[2])

    def reset(self) -> None:
        with self._lock:
            self._suspended.clear()
            self._org_stops.clear()


_SAFETY = _MeterSafety()


def reset_meter_safety() -> None:
    """Clear the suspension and the org-level stops (tests, operator recovery)."""
    _SAFETY.reset()


# --- payer route (routing adapter seam) ---------------------------------------


def _field(value: Any, name: str) -> Any:
    found = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return getattr(found, "value", found)


@dataclass(frozen=True)
class PayerRoute:
    """Who pays for a call. ``route`` keeps the adapter's ModelRoute, opaque and never rendered."""

    mode: str
    payer: str | None
    provider: str | None = None
    route: Any = field(default=None, repr=False, compare=False)

    @property
    def is_managed(self) -> bool:
        return self.mode == MANAGED

    @classmethod
    def managed(cls, provider: str | None = None, route: Any = None) -> PayerRoute:
        return cls(MANAGED, PAYER_CREDITS, provider, route)

    @classmethod
    def byok(cls, provider: str | None = None, route: Any = None) -> PayerRoute:
        return cls(BYOK, PAYER_OWN_KEY, provider, route)

    @classmethod
    def coerce(cls, value: Any) -> PayerRoute:
        if isinstance(value, PayerRoute):
            return value
        mode = _field(value, "mode")
        provider = _field(value, "provider")
        payer = _field(value, "payer")
        if mode == MANAGED:
            return cls(MANAGED, payer or PAYER_CREDITS, provider, value)
        if mode == BYOK:
            return cls(BYOK, payer or PAYER_OWN_KEY, provider, value)
        raise ManagedRouteUnavailableError()


_STORED_KEY_ROUTE = PayerRoute.byok()
# No route at all (the adapter's "no stored key and no managed route" refusal):
# only the legacy dispatch exists, and it never spends Future AGI credits.
_NO_ROUTE = PayerRoute(NO_ROUTE, None)
_route_resolver_override: Any = None


def route_operation(purpose: str) -> str:
    """The adapter operation a purpose is resolved for."""
    return ROUTE_OPERATIONS.get(purpose, purpose)


@contextlib.contextmanager
def use_route_resolver(resolver: Any):
    """Install a payer-route resolver for the block (tests: a routing double)."""
    global _route_resolver_override
    with _registry_lock:
        previous = _route_resolver_override
        _route_resolver_override = resolver
    try:
        yield resolver
    finally:
        with _registry_lock:
            _route_resolver_override = previous


def _adapter_route(*, organization, workspace, model_name, operation):
    try:
        adapter = importlib.import_module(MODEL_ROUTE_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == MODEL_ROUTE_MODULE:
            # No one-key adapter: only stored-key dispatch exists, nothing to meter.
            return _STORED_KEY_ROUTE
        raise
    return adapter.resolve_model_route(
        organization=organization,
        workspace=workspace,
        model_name=model_name,
        operation=operation,
    )


def resolve_payer_route(
    *, organization, workspace, model_name, operation
) -> PayerRoute:
    """The adapter's route, normalised; callers bind tenancy first (``check_tenancy``)."""
    resolver = _route_resolver_override or _adapter_route
    return PayerRoute.coerce(
        resolver(
            organization=organization,
            workspace=workspace,
            model_name=model_name,
            operation=operation,
        )
    )


# --- tenancy and identity ----------------------------------------------------


def check_tenancy(organization: Any, workspace: Any) -> None:
    """Refuse a workspace outside its organization, for every payer mode.

    Runs before any payer route (which can carry an opaque credential) is
    resolved. A call without a workspace is unaffected.
    """
    if workspace is None:
        return
    org_id = getattr(organization, "id", None) if organization is not None else None
    workspace_org = getattr(workspace, "organization_id", None)
    if workspace_org is None:
        workspace_org = getattr(getattr(workspace, "organization", None), "id", None)
    if org_id is None or workspace_org is None or str(workspace_org) != str(org_id):
        raise CreditTenancyError("The workspace does not belong to this organization.")


def bind_tenancy(organization: Any, workspace: Any) -> tuple[str, str | None]:
    """The org and workspace ids of a managed call. Raises before any credit operation."""
    org_id = getattr(organization, "id", None) if organization is not None else None
    if org_id is None:
        raise CreditTenancyError("A managed model call needs an organization.")
    if workspace is None:
        return str(org_id), None
    check_tenancy(organization, workspace)
    workspace_id = getattr(workspace, "id", None)
    if workspace_id is None:
        raise CreditTenancyError("The workspace does not belong to this organization.")
    return str(org_id), str(workspace_id)


@dataclass(frozen=True)
class BillingEnvelope:
    """The billing identity of one logical managed call."""

    organization_id: str
    workspace_id: str | None
    purpose: str
    source: str
    source_id: str
    model: str
    row_id: str | None = None
    call_ref: str | None = None

    @property
    def logical_call_id(self) -> str:
        canonical = json.dumps(
            [
                self.organization_id,
                self.workspace_id,
                self.purpose,
                self.source,
                self.source_id,
                self.row_id,
                self.call_ref,
                self.model,
            ],
            separators=(",", ":"),
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "fi:dsx:call:" + canonical))

    def properties(self) -> dict[str, str]:
        props = {
            "source": self.source,
            "source_id": self.source_id,
            "purpose": self.purpose,
            "model": self.model,
            "workspace_id": self.workspace_id,
            "row_id": self.row_id,
            "call_ref": self.call_ref,
            "logical_call_id": self.logical_call_id,
        }
        return {k: v for k, v in props.items() if v}


def build_envelope(
    *,
    organization: Any,
    workspace: Any,
    purpose: str,
    source: str,
    source_id: Any,
    model: str | None,
    row_id: Any = None,
    call_ref: Any = None,
) -> BillingEnvelope:
    org_id = getattr(organization, "id", None) if organization is not None else None
    workspace_id = getattr(workspace, "id", None) if workspace is not None else None
    return BillingEnvelope(
        organization_id=_text(org_id) or "",
        workspace_id=_text(workspace_id),
        purpose=purpose,
        source=source,
        source_id=_text(source_id) or "",
        model=model or "",
        row_id=_text(row_id),
        call_ref=_text(call_ref),
    )


# --- costs ---------------------------------------------------------------------


def _estimate_prompt_tokens(messages: Any) -> int:
    chars = 0
    parts = 0
    for message in messages or []:
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    chars += len(part)
                elif isinstance(part, dict) and isinstance(part.get("text"), str):
                    chars += len(part["text"])
                else:
                    parts += 1
    return max(1, chars // 4 + parts * NON_TEXT_PART_TOKENS)


def estimate_call_cost(
    model_name: str | None, messages: Any = None, max_tokens: Any = None
) -> Decimal:
    """The hold estimate: catalog price of the prompt plus ``max_tokens`` of output."""
    try:
        completion = int(max_tokens) if max_tokens else 0
    except (TypeError, ValueError):
        completion = 0
    if completion <= 0:
        completion = DEFAULT_COMPLETION_TOKENS
    usage = {
        "prompt_tokens": _estimate_prompt_tokens(messages),
        "completion_tokens": completion,
    }
    try:
        from agentic_eval.core_evals.fi_utils.token_count_helper import (
            calculate_total_cost,
        )

        amount = _to_decimal(
            calculate_total_cost(model_name or "", usage).get("total_cost")
        )
    except Exception:
        amount = None
    if amount is None or amount <= 0:
        return FALLBACK_HOLD_ESTIMATE
    return amount.quantize(_MICRO, rounding=ROUND_CEILING)


def cost_from_value_info(value_info: Any) -> Decimal | None:
    """``metadata.cost.total_cost`` of a RunPrompt ``value_info``.

    A cost priced at a custom model's saved price (pricing_source
    "custom_model") is a display price the organization set for its own
    model; it is never a credits amount, so it is not read here.
    """
    if not isinstance(value_info, dict):
        return None
    metadata = value_info.get("metadata")
    if not isinstance(metadata, dict):
        metadata = value_info
    cost = metadata.get("cost")
    if isinstance(cost, dict):
        if cost.get("pricing_source") == "custom_model":
            return None
        cost = cost.get("total_cost")
    amount = _to_decimal(cost)
    return amount if amount is not None and amount >= 0 else None


def _usage_dict(usage: Any) -> dict | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return dict(usage)
    for name in ("model_dump", "dict"):
        dump = getattr(usage, name, None)
        if callable(dump):
            try:
                data = dump()
            except Exception:
                continue
            if isinstance(data, dict):
                return data
    try:
        return dict(usage)
    except Exception:
        return {
            k: getattr(usage, k)
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            if hasattr(usage, k)
        }


def cost_from_response(model_name: str | None, response: Any) -> Decimal | None:
    """Catalog cost of a litellm response's usage (the pricing source)."""
    usage = _usage_dict(getattr(response, "usage", None))
    if usage:
        try:
            from agentic_eval.core_evals.fi_utils.token_count_helper import (
                calculate_total_cost,
            )

            priced_model = getattr(response, "model", None) or model_name or ""
            amount = _to_decimal(
                calculate_total_cost(priced_model, usage).get("total_cost")
            )
            if amount is not None and amount >= 0:
                return amount
        except Exception:
            logger.warning("credit_cost_from_usage_failed", model=model_name)
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        amount = _to_decimal(hidden.get("response_cost"))
        if amount is not None and amount >= 0:
            return amount
    return None


# --- one managed call -----------------------------------------------------------


def record_hold_excess_incident(*, organization_id, request_id, model, excess) -> None:
    """Best-effort value-free hint AFTER the winning write, not the incident.

    The durable incident is credit_hold_excess on the hold. Reports/alerts read
    that field; a missing/failed log never needs an acknowledgement or retry.
    """
    try:
        logger.warning(
            "credit_hold_excess",
            organization_id=organization_id,
            request_id=request_id,
            model=model,
            excess=str(excess),
        )
    except Exception:
        pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def _record_actual(settle_record, *, organization_id, gateway_request_id) -> Decimal:
    """A settle record is scoped to the response ID, never the client ID."""
    if (
        not gateway_request_id
        or _field(settle_record, "organization_id") != str(organization_id)
        or _field(settle_record, "gateway_request_id") != gateway_request_id
    ):
        raise CreditHoldStateError(
            "settle record does not match the hold's reconcile key"
        )
    return _as_amount(_field(settle_record, "actual_amount"))


def reconcile_pending_hold(
    *,
    organization_id,
    gateway_request_id,
    hold_id,
    amount_estimate,
    opened_at,
    settle_record=None,
    now=None,
):
    """Reconcile open holds and provisional provider-usage debits, without dispatch.

    Final/released holds are no-ops. A record corrects a provisional debit
    exactly once through the atomic port primitive, never through settle again.
    Missing usage still releases at 24h; priced provisional usage and its
    residual reservation remain until a record arrives, not a TTL refund.
    Excess is a first-wins hold field in that SAME write; an acknowledged
    winner may log once, best-effort. Reports/alerts read the field even if the
    write acknowledgement was lost. No separate delivery or recovery exists.
    """
    ledger = get_credit_ledger()
    held = ledger.get_hold(hold_id)
    if held["organization_id"] != str(organization_id):
        raise CreditHoldStateError("hold organization mismatch")
    provisional = (
        held["state"] == "settled" and held.get("settlement_source") == "provider_usage"
    )
    if held["state"] in ("settled", "released") and not provisional:
        return held["state"]
    if (
        (held["state"] != "held" and not provisional)
        or held.get("gateway_request_id") != gateway_request_id
        or held["opened_at"] != opened_at
        or _as_amount(held["amount_estimate"]) != _as_amount(amount_estimate)
    ):
        raise CreditHoldStateError("hold metadata mismatch")
    hold_amount = _as_amount(held["amount_estimate"])
    if settle_record is not None:
        actual = _record_actual(
            settle_record,
            organization_id=organization_id,
            gateway_request_id=gateway_request_id,
        )
        corrected = min(actual, hold_amount)
        excess = max(actual - hold_amount, _ZERO)
        if provisional:
            try:
                adjust = getattr(ledger, "adjust_settled", None)
                if not callable(adjust):
                    raise CreditHoldStateError(
                        "settlement correction primitive unavailable"
                    )
                changed = (
                    adjust(hold_id, corrected, excess=excess)
                    if excess > _ZERO
                    else adjust(hold_id, corrected)
                )
                if changed is not True and changed is not False:
                    raise CreditHoldStateError(
                        "settlement correction was not confirmed"
                    )
            except Exception as exc:
                # No settle/release fallback: the prior provisional debit is
                # authoritative, including when an adjustment acknowledgement
                # was lost. A later snapshot decides whether to retry.
                try:
                    logger.error(
                        "credit_settlement_correction_failed",
                        organization_id=str(organization_id),
                        request_id=held.get("request_id"),
                        error_type=type(exc).__name__,
                    )
                except Exception:
                    pass
                raise CreditHoldStateError(
                    "provisional settlement needs reconciliation"
                ) from None
        else:
            # Never retry an uncertain non-idempotent debit here.
            changed = ledger.settle(
                hold_id,
                corrected,
                source="settle_record",
                gateway_request_id=gateway_request_id,
                excess=excess,
            )
            if changed is not True and changed is not False:
                raise CreditHoldStateError("record settlement was not confirmed")
            latest = ledger.get_hold(hold_id)
            if latest.get("settlement_source") == "provider_usage":
                # A simultaneous initial provisional settle with the same
                # amount can win. Finalize it through the correction CAS.
                return reconcile_pending_hold(
                    organization_id=organization_id,
                    gateway_request_id=gateway_request_id,
                    hold_id=hold_id,
                    amount_estimate=amount_estimate,
                    opened_at=opened_at,
                    settle_record=settle_record,
                    now=now,
                )
        if changed is True and excess > _ZERO and "credit_hold_excess" not in held:
            record_hold_excess_incident(
                organization_id=str(organization_id),
                request_id=held.get("request_id"),
                model=held.get("model"),
                excess=excess,
            )
        return "settled"
    if provisional:
        return "pending_reconcile"
    if (now if now is not None else utc_now()) - held["opened_at"] >= RECONCILE_TTL:
        ledger.release(hold_id)
        # A concurrent record settlement can win the terminal transition.
        return ledger.get_hold(hold_id)["state"]
    return "pending_reconcile"


class ManagedCall:
    """One managed dispatch: its hold, its request id, its claim and its settlement."""

    def __init__(
        self, envelope: BillingEnvelope, route: PayerRoute, *, dispatch_id: Any = None
    ):
        self.envelope = envelope
        self.route = route
        self.dispatch_id = (
            str(dispatch_id) if dispatch_id is not None else str(uuid.uuid4())
        )
        self.request_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"fi:dsx:dispatch:{envelope.logical_call_id}:{self.dispatch_id}",
            )
        )
        self.idempotency_key = f"dsx:{self.request_id}"
        self.hold_id: str | None = None
        self.amount_estimate: Decimal | None = None
        self.actual_amount: Decimal | None = None
        self.cost_source: str | None = None
        self.status = "prepared"
        self.dispatched = False
        self.dispatch_count = 0
        self._observed: Decimal | None = None
        # Any response, chunk, usage or cost observed for this call: the
        # provider produced output; a False attestation becomes a contradiction.
        self.output_seen = False
        # Only trusted raw SDK boundaries add true, false or malformed. The
        # decision is a projection of this monotonic set, never a latest value.
        self._gateway_dispatch_attestations: set[str] = set()
        self.refused_before_upstream = False
        self._hold_excess_amount = _ZERO
        self._dispatch_contradiction_recorded = False
        self.gateway_request_id: str | None = None
        self.gateway_request_id_header_seen = False
        self.opened_at = utc_now()
        self.settle_record: Any = None

    @property
    def gateway_dispatched(self) -> bool | None:
        if self._gateway_dispatch_attestations == {"true"}:
            return True
        if self._gateway_dispatch_attestations == {"false"}:
            return False
        return None

    @property
    def gateway_dispatch_header_seen(self) -> bool:
        return bool(self._gateway_dispatch_attestations)

    @property
    def gateway_dispatch_conflict(self) -> bool:
        """Mixed or malformed evidence is sticky UNKNOWN, even without output."""
        return (
            len(self._gateway_dispatch_attestations) > 1
            or "malformed" in self._gateway_dispatch_attestations
        )

    def observe(
        self, *, value_info: Any = None, response: Any = None, cost: Any = None
    ):
        """Record the provider-reported cost of this call (settled on exit)."""
        if value_info is not None or response is not None or cost is not None:
            self.output_seen = True
        amount = _to_decimal(cost) if cost is not None else None
        if amount is None and value_info is not None:
            amount = cost_from_value_info(value_info)
        if amount is None and response is not None:
            amount = cost_from_response(self.envelope.model, response)
        # A zero cost is also the RunPrompt pricing-failure sentinel. Until
        # the port supplies a priced-zero proof, retain the reservation.
        if amount is not None and amount > 0:
            self._observed = amount
        return self._observed

    def settlement_amount(self) -> tuple[Decimal | None, str | None]:
        hold = (
            self.amount_estimate
            if self.amount_estimate is not None
            else FALLBACK_HOLD_ESTIMATE
        )
        observed, source = self._observed, "provider_usage"
        if self.settle_record is not None:
            observed = _record_actual(
                self.settle_record,
                organization_id=self.envelope.organization_id,
                gateway_request_id=self.gateway_request_id,
            )
            source = "settle_record"
        # Calculation is side-effect free with respect to money/incidents. The
        # port records this amount WITH the winning debit; only then may we log.
        self._hold_excess_amount = _ZERO
        if observed is not None:
            if observed > hold:
                self._hold_excess_amount = observed - hold
                return hold, source + "_capped"
            return observed, source
        return None, None

    def note_dispatch_contradiction(self) -> None:
        contradiction = self.gateway_dispatch_conflict or (
            self.output_seen and self.gateway_dispatched is False
        )
        if contradiction and not self._dispatch_contradiction_recorded:
            self._dispatch_contradiction_recorded = True
            try:
                logger.warning(
                    "credit_dispatch_contradiction",
                    organization_id=self.envelope.organization_id,
                    request_id=self.request_id,
                    gateway_request_id=self.gateway_request_id,
                )
            except Exception:
                pass

    def event_id(self, meter: str) -> str:
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"fi:dsx:event:{meter}:{self.request_id}")
        )

    def summary(self) -> dict[str, Any]:
        return {
            "payer": (self.route.payer if self.route else None) or PAYER_CREDITS,
            "purpose": self.envelope.purpose,
            "logical_call_id": self.envelope.logical_call_id,
            "request_id": self.request_id,
            "gateway_request_id": self.gateway_request_id,
            "estimated": _amount_text(self.amount_estimate),
            "settled": _amount_text(self.actual_amount),
            "cost_source": self.cost_source,
            "status": self.status,
        }

    def __repr__(self) -> str:
        return (
            f"ManagedCall(purpose={self.envelope.purpose!r}, "
            f"request_id={self.request_id!r}, status={self.status!r})"
        )


_ACTIVE_CALL: contextvars.ContextVar = contextvars.ContextVar(
    "dsx_managed_call", default=None
)
_ACTIVE_SCOPE: contextvars.ContextVar = contextvars.ContextVar(
    "dsx_billing_scope", default=None
)
_UNMETERED_SURFACE: contextvars.ContextVar = contextvars.ContextVar(
    "dsx_unmetered_surface", default=None
)
_SETTLED_CALLS: contextvars.ContextVar = contextvars.ContextVar(
    "dsx_settled_calls", default=None
)


@contextlib.contextmanager
def settlement_sink():
    """Collect the managed calls settled inside this block (each carries its
    request id and settled amount), so a caller that meters a whole evaluation
    can mark its usage with the settlements that paid for it. Collection only:
    it never changes how a call is held, claimed or settled."""
    sink: list = []
    token = _SETTLED_CALLS.set(sink)
    try:
        yield sink
    finally:
        _SETTLED_CALLS.reset(token)


def current_managed_call() -> ManagedCall | None:
    return _ACTIVE_CALL.get()


def _split_model(name: str | None) -> tuple[str, str]:
    provider, _, base = (name or "").strip().lower().rpartition("/")
    return provider, base


def _same_model(dispatched: str | None, held: str | None) -> bool:
    """Provider-qualified match; an unqualified name matches any provider."""
    if not dispatched or not held:
        return True
    dispatched_provider, dispatched_base = _split_model(dispatched)
    held_provider, held_base = _split_model(held)
    if dispatched_base != held_base:
        return False
    return (
        not dispatched_provider
        or not held_provider
        or dispatched_provider == held_provider
    )


@contextlib.contextmanager
def unmetered_surface(surface: str):
    """Mark a surface whose managed dispatch is not metered yet.

    A managed transport that calls ``require_managed_hold`` inside it gets
    ``ManagedDispatchUnavailableError`` (a user-visible disclosure) before it
    dispatches; BYOK calls are unaffected.
    """
    token = _UNMETERED_SURFACE.set(surface)
    try:
        yield
    finally:
        _UNMETERED_SURFACE.reset(token)


def require_managed_hold(*, model_name: str | None = None) -> ManagedCall:
    """For the managed transport, right before a managed dispatch.

    Claims the open hold (only a claimed hold is settled) and returns the
    call: its ``route`` is the pinned payer route and its ``request_id`` is
    the gateway request reference. Refuses a dispatch without an open hold or
    for a different provider-qualified model.
    """
    call = _ACTIVE_CALL.get()
    if call is None or call.status != "held" or not call.hold_id:
        if _UNMETERED_SURFACE.get() is not None:
            raise ManagedDispatchUnavailableError()
        raise CreditHoldMissingError()
    if not _same_model(model_name, call.envelope.model):
        raise CreditHoldMissingError(
            "The credit hold was placed for a different model; the call was not started."
        )
    if call.dispatched:
        raise CreditHoldMissingError(
            "A managed hold can pay for only one provider call."
        )
    call.dispatched = True
    call.dispatch_count += 1
    return call


class _HoldSession:
    """The hold / claim / settle / release state machine shared by the sync and async meters."""

    def __init__(self, meter, *, messages, max_tokens, amount_estimate, dispatch_id):
        self.meter = meter
        self.messages = messages
        self.max_tokens = max_tokens
        self.amount_estimate = amount_estimate
        self.dispatch_id = dispatch_id
        self.ledger = None
        self.org_id: str | None = None
        self.call: ManagedCall | None = None

    def open(self) -> ManagedCall:
        meter = self.meter
        org_id, workspace_id = bind_tenancy(meter.organization, meter.workspace)
        envelope = meter.envelope
        if envelope.organization_id != org_id or envelope.workspace_id != workspace_id:
            raise CreditTenancyError(
                "The billing identity does not match the call's organization and workspace."
            )
        self.org_id = org_id
        ledger = get_credit_ledger()
        _SAFETY.check(ledger, org_id)
        explicit_dispatch_id = self.dispatch_id is not None
        claim_dispatch_once = None
        if explicit_dispatch_id:
            try:
                claim_dispatch_once = getattr(ledger, "claim_dispatch_once", None)
            except Exception:
                raise ManagedCallAlreadyCompletedError() from None
            if not callable(claim_dispatch_once):
                # OSS-direct support is only for a port with no callable method.
                # Do not try a second primitive after a port claim fails.
                try:
                    from agentcc.services.dispatch_claims import claim_dispatch_once
                except Exception:
                    raise ManagedCallAlreadyCompletedError() from None
                if not callable(claim_dispatch_once):
                    raise ManagedCallAlreadyCompletedError()
        if not all(
            callable(getattr(ledger, name, None))
            for name in ("mark_pending_hold", "list_pending_holds", "get_hold")
        ):
            # Old real ports cannot create a durable reconcile obligation.
            raise CreditLedgerUnavailableError()
        call = ManagedCall(envelope, meter.route, dispatch_id=self.dispatch_id)
        if self.amount_estimate is not None:
            call.amount_estimate = _as_amount(self.amount_estimate)
        else:
            call.amount_estimate = estimate_call_cost(
                envelope.model, self.messages, self.max_tokens
            )
        try:
            result = ledger.hold(
                org=meter.organization,
                workspace=meter.workspace,
                amount_estimate=call.amount_estimate,
                idempotency_key=call.idempotency_key,
                purpose=envelope.purpose,
                metadata={
                    "organization_id": org_id,
                    "request_id": call.request_id,
                    "gateway_request_id": None,
                    "model": envelope.model,
                    "opened_at": call.opened_at,
                },
            )
            call.hold_id = _coerce_hold_id(result)
            if explicit_dispatch_id:
                # The hold must commit before consuming the dispatch identity.
                # ametered runs this check on the same thread/connection as hold().
                # Never invoke the primitive inside an outer atomic(), and never
                # release a possibly shared hold on this pre-transport refusal.
                # Its existing abandoned-hold TTL still applies if it commits.
                try:
                    if connection.in_atomic_block:
                        raise ManagedCallAlreadyCompletedError()
                    claimed = claim_dispatch_once(call.idempotency_key)
                except Exception:
                    raise ManagedCallAlreadyCompletedError() from None
                if claimed is not True:
                    raise ManagedCallAlreadyCompletedError()
        except ManagedCallRefusal as refusal:
            call.status = "refused"
            _SAFETY.note_refusal(ledger, org_id, refusal)
            logger.info(
                "credit_hold_refused",
                code=refusal.code,
                purpose=envelope.purpose,
                request_id=call.request_id,
                organization_id=org_id,
                workspace_id=workspace_id,
            )
            raise
        except CreditHoldStateError:
            call.status = "refused"
            raise
        except Exception as exc:
            call.status = "refused"
            refusal = managed_refusal_from(exc) or CreditLedgerUnavailableError()
            _with_hold_display(refusal, exc)
            _SAFETY.note_refusal(ledger, org_id, refusal)
            logger.error(
                "credit_hold_failed",
                code=refusal.code,
                purpose=envelope.purpose,
                request_id=call.request_id,
                error_type=type(exc).__name__,
            )
            raise refusal from exc
        call.status = "held"
        self.ledger, self.call = ledger, call
        logger.info(
            "credit_hold_placed",
            purpose=envelope.purpose,
            request_id=call.request_id,
            logical_call_id=envelope.logical_call_id,
            organization_id=org_id,
            workspace_id=workspace_id,
        )
        return call

    def succeed(self) -> ManagedCall:
        call = self.call
        if not call.dispatched:
            # No claim proves only a missing meter binding, not that no
            # upstream work happened. Release this hold and refuse the result.
            self._release(status="unclaimed")
            _SAFETY.suspend(self.ledger, "unclaimed_managed_hold", org_id=self.org_id)
            logger.error(
                "credit_hold_unclaimed",
                purpose=call.envelope.purpose,
                request_id=call.request_id,
            )
            raise CreditHoldMissingError(
                "This managed call was not completed by the credit meter. "
                "Its credit hold was released; upstream billing needs reconciliation."
            )
        if self.refused_before_upstream():
            self.release_refused()
            if call.status == "release_failed":
                raise CreditSettlementError()
            raise ManagedCallRefusedError()
        if (
            call.gateway_dispatch_conflict
            or not call.output_seen
            or call.gateway_dispatched is False
        ):
            self.pending_reconcile()
            raise ManagedOutcomePendingReconcileError()
        if _checked_gateway_id(call.gateway_request_id) is None:
            # Only a validated x-agentcc-request-id RESPONSE header
            # keys a settle record. A provisional debit without it could never
            # be corrected and would skip the 24 h TTL release, so the hold
            # stays open BEFORE any debit (and before any excess
            # incident), also for priced output after an SDK failure whose body
            # carried only a request_id.
            self.pending_reconcile()
            raise ManagedOutcomePendingReconcileError()
        amount, source = call.settlement_amount()
        if amount is None:
            self.pending_reconcile()
            raise ManagedOutcomePendingReconcileError()
        # Replaying settle is safe only on a port that declares it idempotent.
        attempts = (
            SETTLE_ATTEMPTS
            if getattr(self.ledger, "idempotent_settle", False) is True
            else 1
        )
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                # Provenance and the response key must commit WITH the debit,
                # not via a best-effort metadata write after settlement.
                changed = self.ledger.settle(
                    call.hold_id,
                    amount,
                    source="provider_usage"
                    if source.startswith("provider_usage")
                    else "settle_record",
                    gateway_request_id=call.gateway_request_id,
                    excess=call._hold_excess_amount,
                )
                if changed is not True and changed is not False:
                    raise CreditHoldStateError("settlement was not confirmed")
            except CreditHoldStateError as exc:
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "credit_settle_attempt_failed",
                    request_id=call.request_id,
                    attempt=attempt,
                    attempts=attempts,
                    error_type=type(exc).__name__,
                )
                continue
            if changed is True and call._hold_excess_amount > _ZERO:
                record_hold_excess_incident(
                    organization_id=call.envelope.organization_id,
                    request_id=call.request_id,
                    model=call.envelope.model,
                    excess=call._hold_excess_amount,
                )
            call.actual_amount = amount
            call.cost_source = source
            call.status = "settled"
            sink = _SETTLED_CALLS.get()
            if sink is not None:
                sink.append(call)
            logger.info(
                "credit_hold_settled",
                purpose=call.envelope.purpose,
                request_id=call.request_id,
                cost_source=source,
            )
            return call
        call.status = "settle_failed"
        self._persist_pending()
        _SAFETY.suspend(self.ledger, "settlement_failed", org_id=self.org_id)
        logger.error(
            "credit_settle_failed",
            purpose=call.envelope.purpose,
            request_id=call.request_id,
            error_type=type(last_error).__name__ if last_error else None,
        )
        raise CreditSettlementError() from last_error

    def _persist_pending(self) -> None:
        try:
            self.ledger.mark_pending_hold(
                self.call.hold_id, gateway_request_id=self.call.gateway_request_id
            )
        except Exception as exc:
            # The creation-time metadata still makes abandoned holds discoverable
            # at TTL through list_pending_holds, even after a lost marker write.
            _SAFETY.suspend(
                self.ledger, "pending_hold_record_failed", org_id=self.org_id
            )
            logger.error(
                "credit_pending_hold_record_failed",
                request_id=self.call.request_id,
                error_type=type(exc).__name__,
            )

    def pending_reconcile(self) -> None:
        self.call.status = "pending_reconcile"
        self.call.actual_amount = None
        self.call.cost_source = None
        self.call.note_dispatch_contradiction()
        self._persist_pending()

    def fail(self) -> None:
        # Mixed/malformed evidence (with or without output) and output/False
        # contradictions are UNKNOWN, never an estimate-based debit or refund.
        if not self.call.dispatched:
            self._release(status="released")
        elif self.call.gateway_dispatch_conflict:
            self.pending_reconcile()
        elif self.refused_before_upstream():
            self.release_refused()
        elif self.call.output_seen and self.call.gateway_dispatched is not False:
            self.succeed()
        else:
            self.pending_reconcile()

    def _release(self, *, status: str) -> None:
        call = self.call
        try:
            self.ledger.release(call.hold_id)
        except Exception as exc:
            call.status = "release_failed"
            logger.error(
                "credit_release_failed",
                purpose=call.envelope.purpose,
                request_id=call.request_id,
                error_type=type(exc).__name__,
            )
            return
        call.status = status
        logger.info(
            "credit_hold_released",
            purpose=call.envelope.purpose,
            request_id=call.request_id,
            status=status,
        )

    def refused_before_upstream(self) -> bool:
        """Billing depends only on the claimed call's trusted SDK evidence.
        Exception types, text and chains can classify a failure, never refund it.
        """
        call = self.call
        return bool(
            call is not None
            and call.dispatched
            and call.refused_before_upstream
            and not call.gateway_dispatch_conflict
            and not call.output_seen
            and call.gateway_dispatched is not True
        )

    def release_refused(self) -> None:
        """Release a claimed call's hold once: refused before upstream, charge 0."""
        self._release(status="released_refused")
        logger.info(
            "credit_hold_released_refused",
            purpose=self.call.envelope.purpose,
            request_id=self.call.request_id,
        )

    def dispatch_refusal(self, exc: BaseException) -> ManagedCallRefusal | None:
        """The typed refusal a failed managed dispatch carries, keyed on
        error.code; for a call the transport had already sent (claimed), its
        after-dispatch form: charged when ``fail()`` settled it, uncharged
        when ``release_refused()`` released it."""
        if not isinstance(exc, Exception):
            return None
        if self.call is not None and self.call.dispatched:
            if self.call.status == "pending_reconcile":
                return ManagedOutcomePendingReconcileError()
            if self.call.status in ("settle_failed", "release_failed"):
                return CreditSettlementError()
        refusal = managed_refusal_from(exc)
        if refusal is not None:
            _SAFETY.note_refusal(self.ledger, self.org_id, refusal)
        if self.call is not None and self.call.dispatched:
            charged = self.call.status == "settled"
            if refusal is not None:
                return refusal.after_dispatch(charged=charged)
            # Unknown gateway errors and timeouts must not escape as retryable
            # non-ValueErrors to an outer activity/attempt boundary.
            return (
                ManagedCallFailedAfterDispatchError()
                if charged
                else ManagedCallRefusedError()
            )
        return refusal


class CallMeter:
    """Meters one call site: inert for BYOK, hold / claim / settle / release for managed."""

    def __init__(self, *, organization, workspace, route, envelope):
        self.organization = organization
        self.workspace = workspace
        self.route = PayerRoute.coerce(route)
        self.envelope = envelope

    @property
    def is_managed(self) -> bool:
        return self.route.is_managed

    def _session(
        self, messages, max_tokens, amount_estimate, dispatch_id
    ) -> _HoldSession:
        return _HoldSession(
            self,
            messages=messages,
            max_tokens=max_tokens,
            amount_estimate=amount_estimate,
            dispatch_id=dispatch_id,
        )

    @contextlib.contextmanager
    def metered(
        self, *, messages=None, max_tokens=None, amount_estimate=None, dispatch_id=None
    ):
        """Hold before the body dispatches; settle a claimed call on success;
        release before dispatch, and release a claimed call the gateway
        refused before upstream (main 11-06: a refused call is never charged);
        observed failures settle actual usage; unknowns stay pending. A claimed
        Exception is raised as a terminal ManagedCreditRefusal/ValueError, so
        neither a charged nor a released claim is automatically retried."""
        if not self.is_managed:
            yield None
            return
        session = self._session(messages, max_tokens, amount_estimate, dispatch_id)
        call = session.open()
        token = _ACTIVE_CALL.set(call)
        try:
            yield call
        except BaseException as exc:
            _ACTIVE_CALL.reset(token)
            try:
                if isinstance(exc, Exception) and session.refused_before_upstream():
                    session.release_refused()
                else:
                    session.fail()
            except ManagedCreditRefusal as outcome:
                # Presentation only: preserve the outcome selected by finalization.
                outcome.gateway_display = managed_gateway_display(exc)
                raise
            refusal = session.dispatch_refusal(exc)
            if refusal is None or refusal is exc or _in_chain(refusal, exc):
                raise
            refusal.gateway_display = managed_gateway_display(exc)
            raise refusal from exc
        _ACTIVE_CALL.reset(token)
        session.succeed()

    @contextlib.asynccontextmanager
    async def ametered(
        self, *, messages=None, max_tokens=None, amount_estimate=None, dispatch_id=None
    ):
        """Async ``metered``; ledger operations run off the event loop."""
        if not self.is_managed:
            yield None
            return
        from asgiref.sync import sync_to_async

        session = self._session(messages, max_tokens, amount_estimate, dispatch_id)
        call = await sync_to_async(session.open, thread_sensitive=True)()
        token = _ACTIVE_CALL.set(call)
        try:
            yield call
        except BaseException as exc:
            _ACTIVE_CALL.reset(token)
            try:
                if isinstance(exc, Exception) and session.refused_before_upstream():
                    await sync_to_async(
                        session.release_refused, thread_sensitive=True
                    )()
                else:
                    await sync_to_async(session.fail, thread_sensitive=True)()
            except ManagedCreditRefusal as outcome:
                # Presentation only: preserve the outcome selected by finalization.
                outcome.gateway_display = managed_gateway_display(exc)
                raise
            refusal = session.dispatch_refusal(exc)
            if refusal is None or refusal is exc or _in_chain(refusal, exc):
                raise
            refusal.gateway_display = managed_gateway_display(exc)
            raise refusal from exc
        _ACTIVE_CALL.reset(token)
        await sync_to_async(session.succeed, thread_sensitive=True)()

    def emit_usage_event(self, call, *, event_type, properties=None):
        """Settlement is the debit; do not enqueue a second billable event.

        Accounting remains available in the ledger and credit_settlement
        metadata. The legacy emitter has no proven non-billable event type.
        """
        return None


def call_meter(
    *,
    organization,
    workspace,
    model_name,
    purpose,
    source,
    source_id,
    row_id=None,
    call_ref=None,
    operation=None,
) -> CallMeter:
    """Bind tenancy, resolve who pays for this call and build its billing identity."""
    check_tenancy(organization, workspace)
    route = resolve_payer_route(
        organization=organization,
        workspace=workspace,
        model_name=model_name,
        operation=operation or route_operation(purpose),
    )
    if workspace is None and route.is_managed:
        resolved_workspace = getattr(route.route, "workspace_id", None)
        if resolved_workspace is not None:
            from accounts.models.workspace import Workspace

            workspace = Workspace.objects.get(
                pk=resolved_workspace, organization_id=organization.id
            )
    envelope = build_envelope(
        organization=organization,
        workspace=workspace,
        purpose=purpose,
        source=source,
        source_id=source_id,
        model=model_name,
        row_id=row_id,
        call_ref=call_ref,
    )
    return CallMeter(
        organization=organization, workspace=workspace, route=route, envelope=envelope
    )


# --- bounded attempts for the contract's backoff refusals -----------------------


def backoff_seconds(refusal: ManagedCallRefusal) -> int | float:
    seconds = refusal.retry_after
    if seconds is None:
        seconds = refusal.default_retry_after or 1
    return min(max(seconds, 0), MAX_BACKOFF_SECONDS)


def _backoff_refusal(exc: BaseException) -> ManagedCallRefusal | None:
    for item in _chain(exc):
        if isinstance(item, ManagedCallRefusal):
            return item if item.backoff else None
    return None


class _ManagedAttempt:
    def __init__(self, owner: ManagedAttempts):
        self.owner = owner
        self.number = owner.number

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        delay = self.owner._delay_for(exc_type, exc)
        if delay is None:
            return False
        _sleep(delay)
        return True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        delay = self.owner._delay_for(exc_type, exc)
        if delay is None:
            return False
        await _asleep(delay)
        return True


class ManagedAttempts:
    """Bounded attempts of one managed call.

    Only a backoff refusal (in-flight insufficient credit with the retry
    hint, spend velocity, rate limit) raised before the call was claimed is
    retried, after its Retry-After capped at ``MAX_BACKOFF_SECONDS``, at most
    ``MANAGED_CALL_ATTEMPTS`` times in all. A claimed call's backoff refusal
    arrives in its terminal after-dispatch form (settled once or released on
    trusted evidence), so it is not retried.
    Every other error, and every error of a BYOK meter, propagates unchanged
    from the first attempt::

        for attempt in managed_attempts(meter):
            with attempt:
                with meter.metered(...) as managed_call:
                    ...
    """

    def __init__(self, managed: bool, *, attempts: int = MANAGED_CALL_ATTEMPTS):
        self.managed = bool(managed)
        self.limit = max(1, int(attempts)) if self.managed else 1
        self.number = 0
        self.done = False
        self.delays: list = []

    def __iter__(self):
        return self

    def __next__(self) -> _ManagedAttempt:
        if self.done:
            raise StopIteration
        self.number += 1
        return _ManagedAttempt(self)

    def __aiter__(self):
        return self

    async def __anext__(self) -> _ManagedAttempt:
        if self.done:
            raise StopAsyncIteration
        self.number += 1
        return _ManagedAttempt(self)

    def _delay_for(self, exc_type, exc) -> int | float | None:
        if exc_type is None:
            self.done = True
            return None
        refusal = _backoff_refusal(exc) if isinstance(exc, Exception) else None
        if not self.managed or refusal is None or self.number >= self.limit:
            self.done = True
            return None
        delay = backoff_seconds(refusal)
        self.delays.append(delay)
        logger.info(
            "managed_call_backoff", code=refusal.code, attempt=self.number, delay=delay
        )
        return delay


def managed_attempts(meter: Any) -> ManagedAttempts:
    return ManagedAttempts(bool(getattr(meter, "is_managed", False)))


def observe_value_info(call: ManagedCall, result: Any) -> None:
    """``observe`` hook for RunPrompt's ``(response, value_info)`` result."""
    call.observe(value_info=result[1])


def run_metered(meter, dispatch, *, messages=None, max_tokens=None, observe=None):
    """Dispatch one call through ``meter`` with the contract's bounded backoff.

    Returns ``(result, managed_call)``; ``managed_call`` is ``None`` for BYOK,
    whose dispatch runs exactly once and whose errors are unchanged.
    """
    result = managed_call = None
    for attempt in managed_attempts(meter):
        with attempt:
            with meter.metered(
                messages=messages, max_tokens=max_tokens
            ) as managed_call:
                result = dispatch()
                if managed_call is not None and observe is not None:
                    observe(managed_call, result)
    return result, managed_call


async def arun_metered(
    meter, dispatch, *, messages=None, max_tokens=None, observe=None
):
    """Async ``run_metered``; ``dispatch`` returns an awaitable."""
    result = managed_call = None
    async for attempt in managed_attempts(meter):
        async with attempt:
            async with meter.ametered(
                messages=messages, max_tokens=max_tokens
            ) as managed_call:
                result = await dispatch()
                if managed_call is not None and observe is not None:
                    observe(managed_call, result)
    return result, managed_call


# --- run-level scope (optimizer) ------------------------------------------------


class BillingScope:
    """The billing context of a long run (dataset optimization).

    Binds tenancy at construction, caches payer routes per (model, purpose)
    and makes the first refusal sticky: every later guarded call in the run
    raises it without resolving a route or dispatching.
    """

    def __init__(self, *, organization, workspace, source, source_id):
        check_tenancy(organization, workspace)
        self.organization = organization
        self.workspace = workspace
        self.source = source
        self.source_id = _text(source_id) or ""
        self._lock = threading.Lock()
        self._routes: dict[tuple, PayerRoute] = {}
        self._refusal: BaseException | None = None

    def has_route(self, model_name: str, purpose: str) -> bool:
        with self._lock:
            return (model_name, purpose) in self._routes

    def route_for(self, model_name: str, purpose: str) -> PayerRoute:
        key = (model_name, purpose)
        with self._lock:
            cached = self._routes.get(key)
        if cached is not None:
            return cached
        try:
            route = resolve_payer_route(
                organization=self.organization,
                workspace=self.workspace,
                model_name=model_name,
                operation=route_operation(purpose),
            )
        except (ManagedCallRefusal, CreditTenancyError):
            raise
        except ValueError as exc:
            from model_hub.services.model_route import ModelRouteUnavailable

            if not isinstance(exc, ModelRouteUnavailable):
                raise ManagedRouteUnavailableError() from None
            # The adapter's own refusal (no stored key and no managed route):
            # no credits route exists for this model.
            logger.info(
                "credit_route_unresolved",
                source=self.source,
                purpose=purpose,
                error_type=type(exc).__name__,
            )
            route = _NO_ROUTE
        with self._lock:
            return self._routes.setdefault(key, route)

    @property
    def refusal(self) -> BaseException | None:
        return self._refusal

    def record_refusal(self, exc: BaseException) -> None:
        with self._lock:
            if self._refusal is None:
                self._refusal = exc
        logger.warning(
            "credit_scope_refused",
            source=self.source,
            source_id=self.source_id,
            code=refusal_code(exc),
        )

    def raise_if_refused(self) -> None:
        refusal = self._refusal
        if refusal is not None:
            raise refusal


@contextlib.contextmanager
def billing_scope(*, organization, workspace, source, source_id):
    scope = BillingScope(
        organization=organization,
        workspace=workspace,
        source=source,
        source_id=source_id,
    )
    token = _ACTIVE_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _ACTIVE_SCOPE.reset(token)


def current_billing_scope() -> BillingScope | None:
    return _ACTIVE_SCOPE.get()


def record_refusal(scope: BillingScope | None, exc: BaseException) -> None:
    if scope is not None and is_terminal_refusal(exc):
        scope.record_refusal(exc)


def raise_if_refused(scope: BillingScope | None) -> None:
    if scope is not None:
        scope.raise_if_refused()


def prime_direct_route(
    scope: BillingScope | None, *, purpose: str, model_name: str
) -> None:
    """Resolve and cache a direct site's route in sync context; never raises."""
    if scope is None:
        return None
    try:
        scope.route_for(model_name, purpose)
    except Exception as exc:
        logger.info(
            "credit_route_prime_failed", purpose=purpose, error_type=type(exc).__name__
        )
    return None


def guard_direct_dispatch(
    scope: BillingScope | None, *, purpose: str, model_name: str
) -> None:
    """For a call site that dispatches with a provider credential directly (the
    caller's own key or the deployment's), never through the managed transport.

    A Future AGI credits route is refused before any model call (it would
    otherwise run unmetered or charge credits for a call the org's own key
    paid). The refusal is sticky for the run. Outside a run scope, and for a
    BYOK route, nothing changes.
    """
    if scope is None:
        return None
    scope.raise_if_refused()
    try:
        route = scope.route_for(model_name, purpose)
    except (ManagedCallRefusal, CreditTenancyError) as refusal:
        scope.record_refusal(refusal)
        raise
    except Exception as exc:
        refusal = ManagedRouteUnavailableError()
        scope.record_refusal(refusal)
        raise refusal from exc
    if route.is_managed:
        refusal = ManagedDispatchUnavailableError()
        scope.record_refusal(refusal)
        logger.warning(
            "credit_direct_dispatch_refused",
            source=scope.source,
            source_id=scope.source_id,
            purpose=purpose,
        )
        raise refusal
    return None


async def aguard_direct_dispatch(
    scope: BillingScope | None, *, purpose: str, model_name: str
) -> None:
    """Async ``guard_direct_dispatch``; an uncached route is resolved off the event loop."""
    if scope is None:
        return None
    scope.raise_if_refused()
    if scope.has_route(model_name, purpose):
        return guard_direct_dispatch(scope, purpose=purpose, model_name=model_name)
    from asgiref.sync import sync_to_async

    return await sync_to_async(guard_direct_dispatch, thread_sensitive=True)(
        scope, purpose=purpose, model_name=model_name
    )


# --- refusal classification ------------------------------------------------------

_USAGE_STATUS_CODES = {
    "rate_limited": "RATE_LIMITED",
    "insufficient_credits": "INSUFFICIENT_CREDITS",
    "resource_limit": "RESOURCE_LIMIT",
}
_USAGE_ERROR_CODE = re.compile(r"""error_code=['"]?([A-Z_]+)""")


def _is_usage_limit_exceeded(exc: BaseException) -> bool:
    try:
        from ee.usage.exceptions import UsageLimitExceeded
    except ImportError:
        UsageLimitExceeded = None
    if UsageLimitExceeded is not None and isinstance(exc, UsageLimitExceeded):
        return True
    return type(exc).__name__ == "UsageLimitExceeded"


def _usage_refusal_code(exc: BaseException) -> str | None:
    """The code of a usage refusal from run_eval_func, in every raise form.

    model_hub/views/utils/evals.py raises UsageLimitExceeded (ee), or
    ValueError(str(check_result)) ("allowed=False ..."), or
    ValueError("API call not allowed : Error validating the api call."), or
    ValueError("API call not allowed : ", <status>) for a usage row that was
    not admitted (rate_limited, insufficient_credits, resource_limit, ...).
    """
    if _is_usage_limit_exceeded(exc):
        check = getattr(exc, "check_result", None)
        return getattr(check, "error_code", None) or "USAGE_LIMIT_EXCEEDED"
    if not isinstance(exc, ValueError) or isinstance(exc, ManagedCallRefusal):
        return None
    first = exc.args[0] if exc.args else ""
    if isinstance(first, str) and first.startswith("API call not allowed"):
        status = exc.args[1] if len(exc.args) > 1 else None
        status = getattr(status, "value", status)
        if status is None:
            return "API_CALL_NOT_ALLOWED"
        return _USAGE_STATUS_CODES.get(
            str(status).strip().lower(), "API_CALL_NOT_ALLOWED"
        )
    if isinstance(first, str) and first.startswith("allowed=False"):
        match = _USAGE_ERROR_CODE.search(first)
        return match.group(1) if match else "USAGE_LIMIT_EXCEEDED"
    return None


def _single_refusal_code(item: BaseException) -> str | None:
    if isinstance(
        item, (ManagedCallRefusal, CreditTenancyError, LegacyOptimizerRetiredError)
    ):
        return item.code
    return _usage_refusal_code(item)


def _first_refusal(exc: BaseException | None) -> BaseException | None:
    for item in _chain(exc):
        if _single_refusal_code(item):
            return item
    return None


def is_managed_refusal(exc: BaseException) -> bool:
    """A typed managed refusal (or tenancy refusal) anywhere in ``exc``'s chain."""
    return any(
        isinstance(item, (ManagedCallRefusal, CreditTenancyError))
        for item in _chain(exc)
    )


def is_terminal_refusal(exc: BaseException | None) -> bool:
    """A credit, usage, payment, entitlement or tenancy refusal, possibly wrapped.

    The caller stops: it never falls back to an unmetered path, never
    reroutes, and records it on the run scope. Gateway text outside a managed
    meter is not read here (BYOK provider errors keep their behaviour).
    """
    return _first_refusal(exc) is not None


def is_retryable_refusal(exc: BaseException | None) -> bool:
    """A refusal that may succeed when the whole call or run is retried later."""
    item = _first_refusal(exc)
    return isinstance(item, ManagedCallRefusal) and item.retryable is True


def refusal_code(exc: BaseException | None) -> str | None:
    item = _first_refusal(exc)
    return _single_refusal_code(item) if item is not None else None


def refusal_message(exc: BaseException) -> str:
    """User-facing text of a refusal: typed copy, never a tuple repr or provider text."""
    item = _first_refusal(exc)
    if isinstance(item, ManagedCallRefusal):
        return item.message
    if isinstance(item, (CreditTenancyError, LegacyOptimizerRetiredError)):
        return str(item)
    if item is None:
        return str(exc)
    code = _single_refusal_code(item)
    try:
        from tfc.utils.error_codes import err_dict, get_specific_error_message
    except Exception:
        return str(item)
    if code == "RESOURCE_LIMIT":
        code = "ENTITLEMENT_LIMIT"
    if code in err_dict and code not in ("RATE_LIMITED", "INSUFFICIENT_CREDITS"):
        # UsageLimitExceeded / "allowed=False" codes and resource limits use
        # the billing copy (FREE_TIER_LIMIT, ENTITLEMENT_LIMIT, ...).
        return str(err_dict[code][0])
    return get_specific_error_message(item)


def refusal_activity_error(exc: BaseException) -> dict[str, Any]:
    """Arguments for the Temporal ApplicationError that ends a run on a refusal."""
    return {
        "message": refusal_message(exc),
        "type": refusal_code(exc) or type(exc).__name__,
        "non_retryable": not is_retryable_refusal(exc),
    }


def refusal_cell_info(exc: BaseException) -> dict[str, Any]:
    """Cell ``value_infos`` for a refused managed call: typed, no provider text."""
    for item in _chain(exc):
        if isinstance(item, ManagedCallRefusal):
            return item.as_cell_info()
        if isinstance(item, CreditTenancyError):
            return {
                "reason": str(item),
                "error": {"code": item.code, "message": str(item)},
            }
    return {"reason": str(exc)}


# --- legacy optimizer routes ------------------------------------------------------


def refuse_legacy_optimizer(surface: str):
    """Legacy optimizer paths whose PromptOptimizer construction cannot run."""
    raise LegacyOptimizerRetiredError(
        f"This legacy optimizer ({surface}) is no longer available. "
        "Use Dataset Optimization instead."
    )


def refuse_legacy_managed_optimizer(
    *, organization, workspace, model_name, surface
) -> None:
    """Refuse a legacy optimizer run whose model would be paid with Future AGI credits.

    The legacy optimizer's calls cannot be held and settled per call, so a
    managed route is refused before any model call; BYOK runs, and models
    with no route at all, are unchanged. Tenancy binds first.
    """
    check_tenancy(organization, workspace)
    try:
        route = resolve_payer_route(
            organization=organization,
            workspace=workspace,
            model_name=model_name,
            operation=route_operation(PURPOSE_LEGACY_OPTIMIZER),
        )
    except (ManagedCallRefusal, CreditTenancyError):
        raise
    except ValueError as exc:
        from model_hub.services.model_route import ModelRouteUnavailable

        if not isinstance(exc, ModelRouteUnavailable):
            raise ManagedRouteUnavailableError() from None
        logger.info(
            "credit_route_unresolved",
            purpose=PURPOSE_LEGACY_OPTIMIZER,
            surface=surface,
            error_type=type(exc).__name__,
        )
        return None
    if route.is_managed:
        raise LegacyOptimizerRetiredError(
            f"This legacy optimizer ({surface}) cannot run on Future AGI credits. "
            "Use Dataset Optimization, or add your own provider key."
        )
    return None


def require_single_billing_path(meter, legacy_billing):
    """Keep legacy billing checks intact instead of silently skipping them."""
    if meter.is_managed and legacy_billing is not None:
        raise ManagedDispatchUnavailableError(
            "Future AGI credits cannot run this surface while legacy billing is active. "
            "Add your own provider key, or use a single-meter managed surface."
        )


# --- single-charge cuts ---------------------------------------------------------

MANAGED_JUDGE_CUT_MESSAGE = (
    "Future AGI credits cannot pay for this evaluation's judge model here yet, "
    "so it was not run. Add your own provider key for this model to run it."
)
MANAGED_PROMPT_CUT_MESSAGE = (
    "Future AGI credits cannot run this prompt here yet, so it was not run. "
    "Add your own provider key for this model to run it."
)
LEGACY_BILLING_MODULE = "ee.usage.utils.usage_entries"


def legacy_billing_installed() -> bool:
    """Whether the deployment's legacy combined billing is installed: the
    ``log_and_deduct_cost_for_api_request`` debit that Dataset Run Prompt, the
    async workbench and EvaluationRunner make besides a managed call's own
    settlement. A failed import other than a missing module counts as
    installed (fail closed)."""
    try:
        module = importlib.import_module(LEGACY_BILLING_MODULE)
    except ImportError:
        return False
    except Exception:
        return True
    return getattr(module, "log_and_deduct_cost_for_api_request", None) is not None


def require_outer_meter(message: str) -> ManagedCall:
    """For a managed dispatch whose caller bills the call again unless an
    outer meter holds it: the active managed call, else the visible cut
    refusal (``message``) before anything is admitted or sent."""
    call = _ACTIVE_CALL.get()
    if call is None:
        raise ManagedDispatchUnavailableError(message)
    return call
