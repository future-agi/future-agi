"""Managed-credit console API, under ``/agentcc/managed-credits/`` (GW-WALLET,
ported from the integration line's lane D4).

One org-scoped API over the managed credit ledger (the money system of record)
and the gateway's hot-path account: the balance, the entries with a CSV
export, the monthly statement, checkout, the low-balance and auto-reload
settings, and the staff grant, adjust and refund.

Who may call what: balances, entries, statements, checkout and settings are an
org Owner's or Admin's (billing-ledger-read-v1 section 2; ``IsOrgGatewayAdmin``
is ``IsOrganizationAdmin`` for the org the request acts in). Grant, adjust and
refund require a staff user's JWT or the gateway admin token as a bearer,
never an org API key or a session cookie.

Each enterprise capability is imported at one boundary: ``_ledger`` (the
ledger service, ``ee.usage``) and ``_checkout`` (cloud billing, BL-STRIPE).
``CHECKOUT_MODULE`` is the one interface checkout reaches cloud billing
through: ``create_session(org, user, amount_usd, success_url, cancel_url)``
returns ``{"url", "session_id"}`` (``checkout_url`` is read too) for a hosted
Checkout session and may raise ``TopupAmountOutOfRange``; ``fulfil(org,
session_id)`` is the landing-page fulfilment (PLAN section 2.2), which
re-fetches the session and credits it once through the ledger's
``fulfil_topup`` with the session's ``payment_intent_id`` (ruling 492: the one
way in for every session, PaymentIntent and invoice credit, so a session and
its PaymentIntent never both credit). A build without the ledger answers every
route 503 ``billing_unavailable``; one without cloud billing answers checkout
503 and reports ``billing_available: false``. No route here writes credit
itself: credit comes from fulfilment and staff only.

The landing page checks two things itself before it runs ``fulfil`` (MUST-FIX
G, ``agentcc.services.checkout_verification``): the session is bound to the
calling org (checkout records the binding when ``create_session`` returns,
before the URL is answered), and Stripe says it is a complete, paid USD
payment in this deployment's mode for at least the bound amount. That read
of the Checkout Session is FA's own, a deliberate exception to the one
boundary above: BL-STRIPE is unreviewed, so delegating the check to it would
prove nothing. It uses the STRIPE_SECRET_KEY setting FA already has (stripe
is a base dependency) and is one function, swappable for a BL-STRIPE read.
A live-mode deployment opens and fulfils no session unless the owner-go
setting BILLING_LEDGER_ACCEPT_LIVE accepts live money.
"""

import csv
import importlib
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from functools import wraps
from types import ModuleType
from typing import Any

import httpx
import structlog
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction
from django.db.models import Count, Q, QuerySet, Sum
from django.http import Http404, StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView, set_rollback

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization import Organization
from agentcc.auto_reload_terms import (
    AutoReloadConsentError,
    require_current_consent_version,
)
from agentcc.contracts.gateway_admin import contract_default
from agentcc.managed_credit_schema import NullableGatewayCreditInspector
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.permissions import IsAdminToken, IsOrgGatewayAdmin, IsStaffOrAdminToken
from agentcc.serializers.managed_credits import (
    CREDIT_KINDS,
    CSV_DESCRIPTION,
    ENTRY_CSV_COLUMNS,
    STATEMENT_CSV_COLUMNS,
    ManagedCreditAdjustRequestSerializer,
    ManagedCreditCheckoutRequestSerializer,
    ManagedCreditCheckoutResponseSerializer,
    ManagedCreditCheckoutSerializer,
    ManagedCreditCheckoutStatusResponseSerializer,
    ManagedCreditCheckoutStatusSerializer,
    ManagedCreditContextErrorResponseSerializer,
    ManagedCreditEntriesPageSerializer,
    ManagedCreditEntriesQuerySerializer,
    ManagedCreditEntriesResponseSerializer,
    ManagedCreditErrorResponseSerializer,
    ManagedCreditGrantRequestSerializer,
    ManagedCreditJsonQuerySerializer,
    ManagedCreditRefundRequestSerializer,
    ManagedCreditSettingsRequestSerializer,
    ManagedCreditSettingsResponseSerializer,
    ManagedCreditSettingsSerializer,
    ManagedCreditStatementQuerySerializer,
    ManagedCreditStatementResponseSerializer,
    ManagedCreditStatementSerializer,
    ManagedCreditSummaryResponseSerializer,
    ManagedCreditSummarySerializer,
    ManagedCreditWriteResponseSerializer,
    ManagedCreditWriteSerializer,
    csv_cell,
    encode_cursor,
    entry_csv_row,
    micros,
    usd,
)
from agentcc.services import checkout_verification
from agentcc.services.auto_reload_consent import (
    record_auto_reload_consent,
    require_consent_actor,
)
from agentcc.services.config_push import _managed_models_enabled
from agentcc.services.gateway_admin import credits as gateway_credits
from agentcc.services.gateway_client import GatewayClientError
from tfc.logging.checkout_format import SESSION_ID as CHECKOUT_SESSION_ID
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

LEDGER_MODULE = "ee.usage.services.managed_credits"
BALANCE_CACHE_MODULE = "ee.usage.services.balance_cache"
CHECKOUT_MODULE = "ee.cloud.billing.managed_credits_checkout"
# The usage event type of a managed call's spend (the ledger's admission check).
MANAGED_SPEND_EVENT_TYPE = "gateway_managed_spend"
CURRENCY = "USD"
# The admin GET's account fields that all read 0 for an org with no account
# hash (the gateway's handleOrgCredits: "an org never pushed reads as zeros").
_UNPUSHED_ACCOUNT_FIELDS = (
    "granted_version",
    "granted_total_micros",
    "spent_micros",
    "held_micros",
)

ERRORS = dict.fromkeys((400, 401, 403, 404, 503), ManagedCreditErrorResponseSerializer)


class ManagedCreditsApiError(Exception):
    """A refusal this API answers with ``status_code`` and a fixed ``code``."""

    def __init__(self, status_code: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.result = {"message": message, **extra} if extra else message


class BillingUnavailable(ManagedCreditsApiError):
    def __init__(self, what: str, message: str | None = None):
        super().__init__(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "billing_unavailable",
            message or f"{what} is not installed in this build.",
        )


# --- enterprise boundaries --------------------------------------------------------


def _ledger() -> ModuleType:
    """The managed credit ledger service (BL-LEDGER owns it)."""
    try:
        return importlib.import_module(LEDGER_MODULE)
    except ImportError:
        raise BillingUnavailable("The managed credit ledger") from None


def _checkout() -> ModuleType:
    """Cloud billing's managed-credit Checkout (BL-STRIPE): CHECKOUT_MODULE."""
    try:
        return importlib.import_module(CHECKOUT_MODULE)
    except ImportError:
        raise BillingUnavailable("Credit purchase") from None


def _balance_exhausted(org: Organization) -> bool:
    """The ledger's own admission answer for managed spend: True once the
    org's balance is at or below zero (``balance_cache.admission_refusal``,
    error code BALANCE_EXHAUSTED). It reads the balance cache, else Postgres."""
    try:
        balance_cache = importlib.import_module(BALANCE_CACHE_MODULE)
    except ImportError as exc:
        raise BillingUnavailable("The managed credit ledger") from exc
    refusal = balance_cache.admission_refusal(str(org.pk), MANAGED_SPEND_EVENT_TYPE)
    return refusal is not None


def _billing_available() -> bool:
    try:
        _checkout()
    except BillingUnavailable:
        return False
    return True


def _checkout_session(session: Any) -> dict:
    """The hosted Checkout session cloud billing opened, as
    ``{checkout_url, session_id}``, or 503: a reply without a URL and an id is
    never passed on as if a session existed. BL-STRIPE's ``create_session``
    names the URL ``url`` (the BL-LEDGER-2A interface's seam); ``checkout_url``
    is read too."""
    if isinstance(session, dict):
        url = session.get("checkout_url") or session.get("url")
        session_id = session.get("session_id")
        if isinstance(url, str) and url and isinstance(session_id, str) and session_id:
            return {"checkout_url": url, "session_id": session_id}
    logger.error(
        "managed_credits_checkout_no_session", reply_type=type(session).__name__
    )
    raise BillingUnavailable(
        "Credit purchase",
        "Credit purchase could not start a checkout session; nothing was charged.",
    )


def _bind_checkout_session(
    org: Organization, session_id: str, amount_usd: Decimal, actor: Any
) -> None:
    """Record which org opened the session before its URL is answered, so no
    one can pay a session the landing page could not bind. A conflict (the id
    is bound to another org, amount or mode) is a 503 without the URL; a
    database error propagates to the view's value-free 503."""
    try:
        checkout_verification.record_binding(
            org, session_id, amount_usd, checkout_verification.stripe_live(), actor
        )
    except checkout_verification.CheckoutBindingConflict:
        logger.error(
            "managed_credits_checkout_binding_conflict",
            org_id=str(org.pk),
            reason="binding_conflict",
        )
        raise BillingUnavailable(
            "Credit purchase",
            "Credit purchase could not start a checkout session; nothing was charged.",
        ) from None


def _landing_verified(org: Organization, session_id: str) -> bool:
    """FA's own landing checks (checkout_verification.landing_refusal). A
    refusal, or any error while verifying, leaves the session pending and is
    logged once with the org and a fixed reason (and the failed read's or the
    error's class name), never the session id, a Stripe body or a traceback."""
    try:
        refusal = checkout_verification.landing_refusal(org, session_id)
    except Exception as exc:
        logger.warning(
            "managed_credits_landing_not_fulfilled",
            org_id=str(org.pk),
            reason="verification_failed",
            exception_type=type(exc).__name__,
        )
        return False
    if refusal is None:
        return True
    fields = {"org_id": str(org.pk), "reason": refusal.reason}
    if refusal.exception_type:
        fields["exception_type"] = refusal.exception_type
    logger.warning("managed_credits_landing_not_fulfilled", **fields)
    return False


def _fulfil_on_landing(org: Organization, session_id: str) -> None:
    """Run cloud billing's landing-page fulfilment for the session, if this
    build has it, once FA has verified the session is bound to this org and
    paid (``_landing_verified``). Fulfilment verifies the session with Stripe
    again and credits it once; any refusal only leaves the session pending."""
    if not _landing_verified(org, session_id):
        return
    try:
        fulfil = getattr(_checkout(), "fulfil", None)
        if fulfil is not None:
            fulfil(org, session_id)
    except BillingUnavailable:
        return
    except Exception as exc:
        # Both the supplied id and the exception/traceback can contain secrets.
        # The org, exception class and a fixed reason suffice for diagnostics.
        logger.warning(
            "managed_credits_landing_fulfil_refused",
            org_id=str(org.pk),
            exception_type=type(exc).__name__,
            reason="fulfilment_refused",
        )


# --- reads (HTTP-free) --------------------------------------------------------------


def _active_config(org: Organization) -> AgentccOrgConfig | None:
    return AgentccOrgConfig.no_workspace_objects.filter(
        organization=org, is_active=True, deleted=False
    ).first()


def _managed_models(config: AgentccOrgConfig | None) -> dict:
    managed = config.managed_models if config is not None else None
    return managed if isinstance(managed, dict) else {}


def _opted_in(config: AgentccOrgConfig | None) -> bool:
    """The org takes managed credit: ``config_push``'s own predicate, so a
    rollout candidate that opts in counts as it does after a push."""
    return config is not None and _managed_models_enabled(config)


def _min_topup_usd(config: AgentccOrgConfig | None) -> Decimal:
    topup = _managed_models(config).get("topup")
    value = topup.get("min_usd") if isinstance(topup, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        value = contract_default("ManagedTopupConfig", "min_usd")
    return Decimal(str(value))


def _gateway_account(org: Organization) -> tuple[dict | None, str | None]:
    """The gateway's account (credits-v1 admin), or None and why it is missing.

    Fails open: the ledger is the record, so a summary without the gateway's
    hot-path figures is still a correct one. The failure is logged.
    """
    try:
        state = gateway_credits.get_credits(str(org.pk))
        return {
            **{name: int(state[name]) for name in _UNPUSHED_ACCOUNT_FIELDS},
            "available_micros": int(state["available_micros"]),
            "markup_percent": state.get("markup_percent"),
        }, None
    except GatewayClientError as exc:
        logger.warning(
            "managed_credits_gateway_read_failed",
            org_id=str(org.pk),
            exception_type=type(exc).__name__,
            reason="gateway_read_failed",
        )
        # 404: the gateway has no managed providers (managed_models_disabled).
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            return None, "managed_models_disabled"
        return None, "unreachable"
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "managed_credits_gateway_read_failed",
            org_id=str(org.pk),
            exception_type=type(exc).__name__,
            reason="gateway_read_failed",
        )
        return None, "unreachable"


def _gateway_lacks_account(state: dict, account: Any) -> bool:
    """Whether the gateway holds no account for an org the ledger knows.

    The admin GET has no "exists" field: an org with no account hash reads as
    zeros at version 0, as a freshly pushed zero account does. Zeros count as
    missing only when the ledger disagrees: the org has no ledger account yet,
    or the ledger has a credit-side version or a settled spend the gateway
    would hold after any push.
    """
    if any(state[name] != 0 for name in _UNPUSHED_ACCOUNT_FIELDS):
        return False
    return account is None or account.version > 0 or account.settled_spend_micros > 0


def _settings(account: Any, min_topup: Decimal) -> dict:
    return {
        "low_balance_threshold_usd": usd(
            account.low_balance_threshold_micros if account else None
        ),
        "auto_reload": {
            "enabled": bool(account and account.autoreload_enabled),
            "threshold_usd": usd(
                account.autoreload_threshold_micros if account else None
            ),
            "amount_usd": usd(account.autoreload_amount_micros if account else None),
            "disabled_reason": account.autoreload_disabled_reason if account else "",
        },
        "min_topup_usd": min_topup,
    }


def credit_summary(ledger: ModuleType, org: Organization) -> dict:
    """The org's balance from the ledger and the gateway, and its settings.

    The ledger figures are BL-LEDGER-2A's ``ledger_balance``: granted is the
    credit total F, settled spend is the gateway's S, and available is the
    balance F - S - P, which the wallet-funded platform spend P lowers too.
    ``balance_exhausted`` is the ledger's admission answer for managed spend,
    and ``freeze_reason`` says why spending is paused while the org is frozen
    (``receivable_open`` or ``dispute_open``, the ledger's ``freeze_reason``),
    so the console names the freeze instead of a generic "add credits".

    Read-only: a missing gateway account for an opted-in org is ``syncing``.
    Config pushes and the settlement drain repair it, never this GET. A missing
    ledger account already reads as zero totals and default settings below.
    """
    config = _active_config(org)
    opted_in = _opted_in(config)
    account = ledger.get_account(org)
    state, warning = _gateway_account(org)
    if state is not None and opted_in and _gateway_lacks_account(state, account):
        # Preserve the warning without writing an account or pushing gateway state.
        state, warning = None, "syncing"

    balance = ledger.ledger_balance(org)
    gateway = pending = None
    if state is not None:
        gateway = {
            "spent_usd": usd(state["spent_micros"]),
            "held_usd": usd(state["held_micros"]),
            "available_usd": usd(state["available_micros"]),
        }
        # Charged at the gateway and not yet drained into the ledger. A gateway
        # behind the ledger is drift, which the reconciler reports, not credit.
        pending = usd(max(state["spent_micros"] - balance.settled_spend_micros, 0))
    settings = _settings(account, _min_topup_usd(config))
    return {
        # The gateway answered its credits route (even if its account is syncing):
        # some provider is managed: true. 404 managed_models_disabled, the
        # shipped default, and an unreachable gateway both read as False.
        "managed_models_available": state is not None or warning == "syncing",
        "enabled": _managed_models(config).get("enabled") is True,
        "granted_usd": usd(balance.credit_total_micros),
        "settled_spend_usd": usd(balance.settled_spend_micros),
        "available_usd": usd(balance.balance_micros),
        "balance_exhausted": _balance_exhausted(org),
        "freeze_reason": ledger.freeze_reason(org),
        "gateway": gateway,
        "gateway_warning": warning,
        "pending_settlement_usd": pending,
        "markup_percent": state["markup_percent"] if state else None,
        "min_topup_usd": settings["min_topup_usd"],
        "low_balance_threshold_usd": settings["low_balance_threshold_usd"],
        "auto_reload": settings["auto_reload"],
        "last_reconciled_at": account.last_reconciled_at if account else None,
        "billing_available": _billing_available(),
        "currency": CURRENCY,
    }


def credit_entries(ledger: ModuleType, org: Organization, query: dict) -> QuerySet:
    """The org's entries matching ``query``, newest first, after its cursor."""
    entries = ledger.ledger_entries(org, start=query.get("from"), end=query.get("to"))
    if query.get("kind"):
        entries = entries.filter(kind__in=query["kind"])
    if query.get("cursor"):
        at, entry_id = query["cursor"]
        entries = entries.filter(
            Q(occurred_at__lt=at) | Q(occurred_at=at, id__lt=entry_id)
        )
    return entries.order_by("-occurred_at", "-id")


def credit_statement(
    ledger: ModuleType, org: Organization, year: int, month: int
) -> dict:
    """The month's statement, from ledger entries only (UTC calendar month)."""
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)
    # ledger_entries orders by time; group and sum over unordered sets.
    before = ledger.ledger_entries(org, end=start).order_by()
    during = ledger.ledger_entries(org, start=start, end=end).order_by()

    opening = before.aggregate(total=Sum("amount_micros"))["total"] or 0
    change = during.aggregate(total=Sum("amount_micros"))["total"] or 0
    credits = dict.fromkeys(CREDIT_KINDS, 0)
    credits.update(
        during.filter(kind__in=CREDIT_KINDS)
        .values_list("kind")
        .annotate(total=Sum("amount_micros"))
    )
    # One line per model and estimate: spend billed on the gateway's own count
    # (usage_estimated, no upstream usage) is its own, marked line.
    spend = (
        during.filter(kind=ledger.Kind.SPEND)
        .values("model", "usage_estimated")
        .annotate(
            count=Count("id"),
            billed=Sum("amount_micros"),
            markup=Sum("markup_micros"),
        )
        # spend is negative: the largest first
        .order_by("billed", "model", "usage_estimated")
    )
    return {
        "month": f"{year:04d}-{month:02d}",
        "currency": CURRENCY,
        "opening_balance": usd(opening),
        "credits_by_kind": {kind: usd(total) for kind, total in credits.items()},
        "spend_by_model": [
            {
                "model": line["model"],
                "count": line["count"],
                "spend_usd": usd(-line["billed"]),
                "markup_usd": usd(line["markup"] or 0),
                "estimated": line["usage_estimated"],
            }
            for line in spend
        ],
        "closing_balance": usd(opening + change),
    }


def statement_csv_rows(statement: dict) -> list[list]:
    rows = [["opening_balance", "", "", statement["opening_balance"], "", ""]]
    for kind, amount in statement["credits_by_kind"].items():
        rows.append(["credit", kind, "", amount, "", ""])
    for line in statement["spend_by_model"]:
        rows.append(
            [
                "spend",
                line["model"],
                line["count"],
                line["spend_usd"],
                line["markup_usd"],
                "true" if line["estimated"] else "false",
            ]
        )
    rows.append(["closing_balance", "", "", statement["closing_balance"], "", ""])
    return [[csv_cell(cell) for cell in row] for row in rows]


def checkout_credited(ledger: ModuleType, org: Organization, session_id: str) -> bool:
    """Whether this org's ledger holds the top-up of a Checkout session (E1
    keys it ``stripe:<session id>`` and records the id as ``stripe_ref``)."""
    return (
        ledger.ledger_entries(org)
        .filter(kind=ledger.Kind.TOPUP, stripe_ref=session_id)
        .exists()
    )


# --- writes (HTTP-free) ---------------------------------------------------------------


def save_settings(
    ledger: ModuleType,
    org: Organization,
    data: dict,
    min_topup: Decimal,
    opted_in: bool,
    *,
    actor: Any = None,
) -> Any:
    """Save settings and their authorization atomically, never ledger totals.

    Every enabled PUT re-confirms consent, even if only the low-balance alert
    changed. Disabling needs none and invalidates the active authorization while
    retaining all historical receipts. No legacy row is presumed to have agreed.
    """
    auto = data["auto_reload"]
    if auto["enabled"]:
        try:
            require_current_consent_version(auto.get("consent_version"))
            require_consent_actor(actor)
        except AutoReloadConsentError as exc:
            raise ManagedCreditsApiError(
                status.HTTP_400_BAD_REQUEST, exc.code, str(exc)
            ) from exc
    if auto["enabled"] and auto["amount_usd"] < min_topup:
        raise ManagedCreditsApiError(
            status.HTTP_400_BAD_REQUEST,
            "topup_amount_out_of_range",
            f"An auto-reload amount is at least the ${min_topup} minimum top-up.",
        )
    fields = {
        "low_balance_threshold_micros": micros(data["low_balance_threshold_usd"]),
        "autoreload_enabled": auto["enabled"],
        "autoreload_threshold_micros": micros(auto.get("threshold_usd")),
        "autoreload_amount_micros": micros(auto.get("amount_usd")),
        "updated_at": timezone.now(),
    }
    accounts = ledger.ManagedCreditAccount.objects
    with transaction.atomic():
        _, created = accounts.get_or_create(organization=org)
        # The claim path locks this same row. Never expose enabled settings
        # before their receipt commits, or race a disable with a new claim.
        account = accounts.select_for_update().get(organization=org)
        if auto["enabled"]:
            accepted_at = timezone.now()
            account.autoreload_threshold_micros = fields["autoreload_threshold_micros"]
            account.autoreload_amount_micros = fields["autoreload_amount_micros"]
            record_auto_reload_consent(
                account,
                version=auto["consent_version"],
                actor=actor,
                accepted_at=accepted_at,
            )
            fields.update(
                autoreload_disabled_reason="",
                autoreload_consent_version=auto["consent_version"],
                autoreload_consent_at=accepted_at,
            )
        else:
            fields.update(autoreload_consent_version="", autoreload_consent_at=None)
        accounts.filter(pk=account.pk).update(**fields)
    if created and opted_in:
        # GET / reads a version-0 account with no spend as in step with the
        # gateway's zeros, so it would never push the account made here.
        # BL-LEDGER-2A's request_push: one push in flight per org (a push
        # already running picks this one up), and it never raises.
        ledger.request_push(str(org.pk))
    return ledger.get_account(org)


def _ledger_refusal(ledger: ModuleType, exc: Exception) -> ManagedCreditsApiError:
    if isinstance(exc, ledger.IdempotencyConflict):
        # Dependency exception messages are never part of the public contract.
        return ManagedCreditsApiError(
            status.HTTP_409_CONFLICT,
            "ledger_key_conflict",
            "That reference already belongs to another ledger entry.",
        )
    return ManagedCreditsApiError(
        status.HTTP_400_BAD_REQUEST,
        "ledger_write_refused",
        "The managed credit ledger refused this operation.",
    )


def _replay_must_match(entry: Any, created: bool, amount: int) -> None:
    if not created and entry.amount_micros != amount:
        raise ManagedCreditsApiError(
            status.HTTP_409_CONFLICT,
            "idempotency_key_reused",
            "This idempotency key was already used for another amount.",
            amount_usd=usd(entry.amount_micros),
        )


def spend_entry(ledger: ModuleType, org: Organization, request_id: str) -> Any:
    """The org's spend entry whose own request_id is ``request_id``. A managed
    capability attempt settles as ``<request_id>.<n>``: a bare request id
    names no entry of its own."""
    return (
        ledger.ledger_entries(org)
        .filter(kind=ledger.Kind.SPEND, request_id=request_id)
        .first()
    )


def refund_spend(
    ledger: ModuleType, org: Organization, data: dict, actor: Any
) -> tuple[Any, bool]:
    """Refund one spend entry through E1, telling a missing entry, an amount
    over its spend and a repeat for another amount apart."""
    request_id, asked = data["request_id"], micros(data.get("amount_usd"))
    try:
        entry, created = ledger.refund(
            org, request_id, asked, reason=data["reason"], actor=actor
        )
    except ledger.RefundExceedsSpend:
        spend = spend_entry(ledger, org, request_id)
        if spend is None:
            raise ManagedCreditsApiError(
                status.HTTP_404_NOT_FOUND,
                "spend_not_found",
                "This org has no spend entry with that request_id.",
            ) from None
        raise ManagedCreditsApiError(
            status.HTTP_400_BAD_REQUEST,
            "refund_exceeds_spend",
            "A refund cannot exceed the entry's spend.",
            spent_usd=usd(-spend.amount_micros),
        ) from None
    except ledger.ManagedCreditError as exc:
        raise _ledger_refusal(ledger, exc) from None
    if not created:
        # E1 returns the request's one refund whatever was asked this time.
        if asked is None:
            spend = spend_entry(ledger, org, request_id)
            asked = -spend.amount_micros if spend is not None else None
        if entry.amount_micros != asked:
            raise ManagedCreditsApiError(
                status.HTTP_409_CONFLICT,
                "already_refunded",
                "This request was already refunded, for another amount.",
                refunded_usd=usd(entry.amount_micros),
            )
    return entry, created


# --- HTTP ---------------------------------------------------------------------------


class CsvRenderer(BaseRenderer):
    """Lets ``?format=csv`` through DRF's content negotiation, which reads the
    ``format`` query parameter (URL_FORMAT_OVERRIDE) and 404s when no renderer
    claims it. The views write the CSV body themselves."""

    media_type = "text/csv"
    format = "csv"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class _Echo:
    def write(self, value):
        return value


def _csv_response(
    filename: str, header: Iterable[str], rows: Iterable[list]
) -> StreamingHttpResponse:
    writer = csv.writer(_Echo())

    def lines():
        yield writer.writerow(header)
        written = 0
        try:
            for row in rows:
                yield writer.writerow(row)
                written += 1
        except DatabaseError as exc:
            # The headers are sent: say so in the file instead of ending it
            # short, as if it held every entry. Do not log SQL/error values.
            logger.error(
                "managed_credits_csv_failed",
                rows_written=written,
                exception_type=type(exc).__name__,
                reason="export_failed",
            )
            yield writer.writerow(
                [f"# export failed after {written} rows; download it again"]
            )

    response = StreamingHttpResponse(lines(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _request_org(request) -> Organization:
    organization = getattr(request, "organization", None)
    if organization is None:
        raise ManagedCreditsApiError(
            status.HTTP_400_BAD_REQUEST,
            "organization_required",
            "This request acts in no organization.",
        )
    return organization


def _dependency_errors(operation):
    """Keep dependency refusals separate from framework request validation.

    Apply inside ``validated_request``: authentication, permissions and input
    validation have already run. A framework-shaped exception raised by an
    operation is not trusted public error detail merely because of its class.
    """

    @wraps(operation)
    def wrapped(self, request, *args, **kwargs):
        try:
            return operation(self, request, *args, **kwargs)
        except (APIException, Http404, PermissionDenied) as exc:
            return self._operation_failed(exc)

    return wrapped


class _ManagedCreditsView(APIView):
    # Balances and entries are an org Owner's or Admin's (billing-ledger-read-v1).
    permission_classes = [IsAuthenticated, IsOrgGatewayAdmin]
    _gm = GeneralMethods()

    def handle_exception(self, exc):
        if isinstance(exc, ManagedCreditsApiError):
            return self._gm.custom_error_response(
                exc.status_code, exc.result, code=exc.code
            )
        if isinstance(exc, (APIException, Http404, PermissionDenied)):
            return super().handle_exception(exc)
        return self._operation_failed(exc)

    def _operation_failed(self, exc):
        # Unexpected dependency errors can embed supplied identifiers too.
        # Preserve request-transaction rollback without passing their traceback
        # to Django's 500 handler or their text to the API response.
        set_rollback()
        org = getattr(self.request, "organization", None)
        logger.error(
            "managed_credits_operation_failed",
            org_id=str(org.pk) if org is not None else None,
            exception_type=type(exc).__name__,
            reason="operation_failed",
        )
        return self._gm.custom_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The managed credit operation could not be completed.",
            code="billing_unavailable",
        )

    def _written(self, entry, created: bool, **extra) -> Response:
        result = ManagedCreditWriteSerializer(
            {"entry": entry, "created": created, **extra}
        ).data
        return self._gm.success_response(
            result, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class _CsvExportView(_ManagedCreditsView):
    renderer_classes = [JSONRenderer, CsvRenderer]

    def finalize_response(self, request, response, *args, **kwargs):
        # Only the CSV body is CSV: an error under ?format=csv is still JSON.
        if isinstance(response, Response) and isinstance(
            getattr(request, "accepted_renderer", None), CsvRenderer
        ):
            request.accepted_renderer = JSONRenderer()
            request.accepted_media_type = JSONRenderer.media_type
        return super().finalize_response(request, response, *args, **kwargs)


class ManagedCreditSummaryView(_ManagedCreditsView):
    @validated_request(
        query_serializer=ManagedCreditJsonQuerySerializer,
        reject_unknown_fields=True,
        responses={200: ManagedCreditSummaryResponseSerializer, **ERRORS},
        field_inspectors=[NullableGatewayCreditInspector],
    )
    @_dependency_errors
    def get(self, request):
        """The org's managed credit: ledger totals, the gateway's live account,
        settings, and whether credits can be bought in this build."""
        org = _request_org(request)
        summary = credit_summary(_ledger(), org)
        return self._gm.success_response(ManagedCreditSummarySerializer(summary).data)


class ManagedCreditEntriesView(_CsvExportView):
    @validated_request(
        query_serializer=ManagedCreditEntriesQuerySerializer,
        responses={200: ManagedCreditEntriesResponseSerializer, **ERRORS},
        reject_unknown_fields=True,
        produces=["application/json", "text/csv"],
        operation_description=(
            "Ledger entries, newest first, cursor-paginated. " + CSV_DESCRIPTION
        ),
    )
    @_dependency_errors
    def get(self, request):
        """The org's ledger entries, newest first."""
        org = _request_org(request)
        query = request.validated_query_data
        entries = credit_entries(_ledger(), org, query)
        if query["format"] == "csv":
            rows = (entry_csv_row(e) for e in entries.iterator(chunk_size=500))
            return _csv_response("managed-credit-entries.csv", ENTRY_CSV_COLUMNS, rows)
        limit = query["limit"]
        page = list(entries[: limit + 1])
        last = page[limit - 1] if len(page) > limit else None
        result = {
            "entries": page[:limit],
            "next_cursor": encode_cursor(last.occurred_at, last.id) if last else None,
        }
        return self._gm.success_response(
            ManagedCreditEntriesPageSerializer(result).data
        )


class ManagedCreditStatementView(_CsvExportView):
    @validated_request(
        query_serializer=ManagedCreditStatementQuerySerializer,
        responses={200: ManagedCreditStatementResponseSerializer, **ERRORS},
        reject_unknown_fields=True,
        produces=["application/json", "text/csv"],
        operation_description=(
            "The month's opening balance, credits by kind, spend by model and "
            "closing balance, rebuilt from ledger entries only. " + CSV_DESCRIPTION
        ),
    )
    @_dependency_errors
    def get(self, request):
        """The org's monthly statement."""
        org = _request_org(request)
        query = request.validated_query_data
        statement = credit_statement(_ledger(), org, *query["month"])
        if query["format"] == "csv":
            return _csv_response(
                f"managed-credit-statement-{statement['month']}.csv",
                STATEMENT_CSV_COLUMNS,
                statement_csv_rows(statement),
            )
        return self._gm.success_response(
            ManagedCreditStatementSerializer(statement).data
        )


class ManagedCreditCheckoutView(_ManagedCreditsView):
    @validated_request(
        request_serializer=ManagedCreditCheckoutRequestSerializer,
        responses={200: ManagedCreditCheckoutResponseSerializer, **ERRORS},
        reject_unknown_fields=True,
    )
    @_dependency_errors
    def post(self, request):
        """Start a Checkout session that tops up the org's managed credit.
        Owner or Admin only; 503 billing_unavailable without cloud billing.
        Starting a session credits nothing: fulfilment does, once paid."""
        # MUST-FIX G (kept out of the docstring, which is the published
        # OpenAPI description): a live-mode deployment without the owner-go
        # setting opens no session it may not credit (503 billing_unavailable),
        # and the session is bound to the org before its URL is answered.
        org = _request_org(request)
        if checkout_verification.live_checkout_refused():
            logger.warning("managed_credits_checkout_not_enabled", org_id=str(org.pk))
            raise BillingUnavailable(
                "Credit purchase",
                "Credit purchase is not enabled for this deployment credential; "
                "nothing was charged.",
            )
        checkout = _checkout()
        out_of_range = getattr(checkout, "TopupAmountOutOfRange", ())
        data = request.validated_data
        try:
            session = checkout.create_session(
                org,
                request.user,
                data["amount_usd"],
                data["success_url"],
                data["cancel_url"],
            )
        except out_of_range:
            raise ManagedCreditsApiError(
                status.HTTP_400_BAD_REQUEST,
                "topup_amount_out_of_range",
                "The requested top-up amount is outside the allowed range.",
            ) from None
        opened = _checkout_session(session)
        _bind_checkout_session(
            org, opened["session_id"], data["amount_usd"], request.user
        )
        return self._gm.success_response(ManagedCreditCheckoutSerializer(opened).data)


class ManagedCreditCheckoutStatusView(_ManagedCreditsView):
    def initial(self, request, *args, **kwargs):
        # Only a Stripe Checkout Session identifier names a checkout session.
        # Any other value is answered 404 before authentication, permission
        # checks, the handler or its logging run.
        session_id = kwargs.get("session_id")
        if type(session_id) is not str or not CHECKOUT_SESSION_ID.fullmatch(session_id):
            raise ManagedCreditsApiError(
                status.HTTP_404_NOT_FOUND,
                "checkout_session_not_found",
                "No checkout session has this identifier.",
            )
        super().initial(request, *args, **kwargs)

    @validated_request(
        query_serializer=ManagedCreditJsonQuerySerializer,
        reject_unknown_fields=True,
        responses={200: ManagedCreditCheckoutStatusResponseSerializer, **ERRORS},
    )
    @_dependency_errors
    def get(self, request, session_id):
        """Whether a Checkout session's top-up reached this org's ledger, after
        running cloud billing's landing-page fulfilment for it."""
        org = _request_org(request)
        ledger = _ledger()
        if not checkout_credited(ledger, org, session_id):
            _fulfil_on_landing(org, session_id)
        credited = checkout_credited(ledger, org, session_id)
        result = {
            # A pending id is unverified input, not a session the ledger knows.
            "session_id": session_id if credited else None,
            "status": "complete" if credited else "pending",
            "credited": credited,
        }
        return self._gm.success_response(
            ManagedCreditCheckoutStatusSerializer(result).data
        )


def _settings_validation_error(errors):
    """Preserve the normal error envelope, with typed consent refusals."""
    auto_errors = errors.get("auto_reload", {})
    if isinstance(auto_errors, dict):
        for error in auto_errors.get("consent_version", []):
            code = getattr(error, "code", None)
            if code in {"auto_reload_consent_required", "auto_reload_consent_outdated"}:
                return GeneralMethods().custom_error_response(
                    status.HTTP_400_BAD_REQUEST, str(error), code=code
                )
    return GeneralMethods().bad_request(errors)


class ManagedCreditSettingsView(_ManagedCreditsView):
    @validated_request(
        query_serializer=ManagedCreditJsonQuerySerializer,
        reject_unknown_fields=True,
        responses={200: ManagedCreditSettingsResponseSerializer, **ERRORS},
    )
    @_dependency_errors
    def get(self, request):
        """The low-balance threshold and auto-reload preference."""
        org = _request_org(request)
        account = _ledger().get_account(org)
        settings = _settings(account, _min_topup_usd(_active_config(org)))
        return self._gm.success_response(ManagedCreditSettingsSerializer(settings).data)

    @validated_request(
        request_serializer=ManagedCreditSettingsRequestSerializer,
        responses={200: ManagedCreditSettingsResponseSerializer, **ERRORS},
        reject_unknown_fields=True,
        validation_error_response=_settings_validation_error,
    )
    @_dependency_errors
    def put(self, request):
        """Replace the low-balance threshold and auto-reload preference.
        Owner or Admin only."""
        org = _request_org(request)
        ledger = _ledger()
        config = _active_config(org)
        min_topup = _min_topup_usd(config)
        account = save_settings(
            ledger,
            org,
            request.validated_data,
            min_topup,
            _opted_in(config),
            actor=request.user,
        )
        return self._gm.success_response(
            ManagedCreditSettingsSerializer(_settings(account, min_topup)).data
        )


# --- staff ---------------------------------------------------------------------------


class _AdminTokenAuthentication(BaseAuthentication):
    """Admits the gateway admin token as a bearer, with no user, so
    ``IsStaffOrAdminToken`` can let it through. Any other bearer goes on to
    ``APIKeyAuthentication``, which reads it as a user token."""

    def authenticate(self, request):
        if IsAdminToken().has_permission(request, None):
            return AnonymousUser(), None
        return None

    def authenticate_header(self, request):
        # DRF uses the first authenticator's challenge even when a later one
        # refuses credentials. Keep authentication failure distinct from policy.
        return APIKeyAuthentication().authenticate_header(request)


class _StaffBearerAuthentication(APIKeyAuthentication):
    """Accept user JWTs without ever resolving an org key into a staff user.

    Org keys cannot authorize money writes, including system keys whose org
    has no user. Refuse them before the shared authenticator dereferences the
    missing user and turns a permission refusal into billing_unavailable.
    The admin bearer is handled by the preceding authenticator as before.
    """

    def authenticate(self, request):
        auth_token = (
            request.META.get("HTTP_AUTHORIZATION", "")
            .replace("Bearer", "")
            .replace(" ", "")
        )
        if not auth_token:
            return None
        return super().authenticate(request)


class _StaffSessionOrAdminToken(IsStaffOrAdminToken):
    """Money writes require a staff session or the internal admin token.

    Personal API keys, including those owned by staff, cannot perform these
    writes. Retired key types are refused before this permission is evaluated.
    """

    def has_permission(self, request, view):
        if getattr(request, "org_api_key", None) is not None:
            return False
        return super().has_permission(request, view)


class _StaffView(_ManagedCreditsView):
    authentication_classes = [_AdminTokenAuthentication, _StaffBearerAuthentication]
    permission_classes = [_StaffSessionOrAdminToken]

    def _target(self, org_id) -> Organization:
        """The org a staff write names, else the one the request acts in. The
        admin token acts in none, so it must name one."""
        if org_id is None:
            return _request_org(self.request)
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            raise ManagedCreditsApiError(
                status.HTTP_404_NOT_FOUND, "organization_not_found", "No such org."
            )
        return org

    def _actor(self):
        user = self.request.user
        return user if user is not None and user.is_authenticated else None


STAFF_WRITE_RESPONSES = {
    200: ManagedCreditWriteResponseSerializer,
    201: ManagedCreditWriteResponseSerializer,
    409: ManagedCreditErrorResponseSerializer,
    **ERRORS,
}


class ManagedCreditGrantView(_StaffView):
    @validated_request(
        request_serializer=ManagedCreditGrantRequestSerializer,
        security=[{"GatewayAdminBearer": []}, {"StaffBearer": []}],
        responses={
            **STAFF_WRITE_RESPONSES,
            409: ManagedCreditContextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @_dependency_errors
    def post(self, request):
        """Grant credit using an authorized staff session or gateway administrator credential.

        Returns 201 when written, or 200 when the idempotency key repeats an
        earlier grant of the same amount. Debt is repaid first, then any
        remainder becomes promotional credit; debt_repaid_usd reports the
        repayment recorded on this entry (also on an idempotent replay).
        """
        data = request.validated_data
        org = self._target(data.get("org_id"))
        ledger = _ledger()
        amount = micros(data["amount_usd"])
        try:
            entry, created = ledger.grant(
                org,
                amount,
                idempotency_key=data["idempotency_key"],
                actor=self._actor(),
                note=data["note"],
                staff_authorized=True,
            )
        except ledger.ManagedCreditError as exc:
            raise _ledger_refusal(ledger, exc) from None
        _replay_must_match(entry, created, amount)
        return self._written(
            entry,
            created,
            idempotency_key=data["idempotency_key"],
            debt_repaid_usd=usd(entry.funded_debt_micros),
        )


class ManagedCreditAdjustView(_StaffView):
    @validated_request(
        request_serializer=ManagedCreditAdjustRequestSerializer,
        security=[{"GatewayAdminBearer": []}, {"StaffBearer": []}],
        responses={
            **STAFF_WRITE_RESPONSES,
            409: ManagedCreditContextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @_dependency_errors
    def post(self, request):
        """Adjust credit using an authorized staff session or gateway administrator credential."""
        data = request.validated_data
        org = self._target(data.get("org_id"))
        ledger = _ledger()
        amount = micros(data["amount_usd"])
        try:
            entry, created = ledger.adjust(
                org,
                amount,
                idempotency_key=data["idempotency_key"],
                reason=data["reason"],
                actor=self._actor(),
            )
        except ledger.ManagedCreditError as exc:
            raise _ledger_refusal(ledger, exc) from None
        _replay_must_match(entry, created, amount)
        return self._written(entry, created, idempotency_key=data["idempotency_key"])


class ManagedCreditRefundView(_StaffView):
    @validated_request(
        request_serializer=ManagedCreditRefundRequestSerializer,
        security=[{"GatewayAdminBearer": []}, {"StaffBearer": []}],
        responses={
            **STAFF_WRITE_RESPONSES,
            400: ManagedCreditContextErrorResponseSerializer,
            409: ManagedCreditContextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @_dependency_errors
    def post(self, request):
        """Refund one spend entry, by its own request_id (staff or the admin
        token). A request is refunded once; asking again for another amount
        is 409 already_refunded."""
        data = request.validated_data
        org = self._target(data.get("org_id"))
        entry, created = refund_spend(_ledger(), org, data, self._actor())
        return self._written(entry, created)
