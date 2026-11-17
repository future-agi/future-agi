"""FA's own checks before a managed-credit Checkout session is fulfilled
(MUST-FIX G).

The landing route (GET /agentcc/managed-credits/checkout/<id>/) used to run
cloud billing's ``fulfil`` for any well-formed session id. Cloud billing
is not reviewed, so FA now proves two things itself first:

* org binding: POST checkout/ records which org opened a session (a sha256
  digest of its id, the amount and the mode) as soon as cloud billing returns
  it; the landing fulfils only a session bound to the calling org. Another
  org's session reads exactly like an unknown one.
* paid status: ``read_checkout_session`` reads the Checkout Session from
  Stripe (the one Stripe read here, GET /v1/checkout/sessions/<id>, with the
  STRIPE_SECRET_KEY FA already holds) and ``landing_refusal`` requires that
  session, a payment, complete, paid, in this deployment's mode, in USD, for
  at least the bound amount.

Live money needs the owner-go setting BILLING_LEDGER_ACCEPT_LIVE at checkout
and at landing. Anything refused stays pending; the webhook path is
unaffected. Errors carry fixed codes only, never the key, a session id or a
Stripe body, and no exception leaves the frame that holds the Stripe client.
Settings are read at call time, never cached.
"""

import contextlib
import hashlib
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import stripe
from django.conf import settings
from django.db import transaction

from agentcc.models.checkout_binding import AgentccCheckoutBinding
from tfc.logging.stripe_sdk import install_stripe_sdk_log_filter
from tfc.settings.stripe_billing import stripe_key_mode
from tfc.stripe_client import stripe_client

CURRENCY = "usd"
LIVE_PREFIX = "cs_live_"
SESSION_ID = re.compile(r"cs_(?:live|test)_[A-Za-z0-9]{24,}")
MICROS_PER_USD = 1_000_000
MICROS_PER_CENT = 10_000
READ_TIMEOUT_SECONDS = 5


class CheckoutBindingConflict(Exception):
    """The session id is already bound to another org, amount or mode."""


class CheckoutReadRefused(Exception):
    """The Stripe read produced no session. ``code`` is ``stripe_not_configured``
    (no STRIPE_SECRET_KEY: nothing was sent) or ``stripe_read_failed``;
    ``exception_type`` is the failure's class name, nothing more."""

    def __init__(self, code: str, exception_type: str | None = None):
        super().__init__(code)
        self.code = code
        self.exception_type = exception_type


@dataclass(frozen=True)
class CheckoutSessionState:
    """The Checkout Session fields the landing checks. A field Stripe did not
    send with the expected type is None, which no check accepts."""

    id: str | None
    mode: str | None
    status: str | None
    payment_status: str | None
    livemode: bool | None
    currency: str | None
    amount_total: int | None


@dataclass(frozen=True)
class LandingRefusal:
    """Why the landing must not fulfil: a fixed reason code, plus the failed
    read's exception class name when the reason is a Stripe read failure."""

    reason: str
    exception_type: str | None = None


# --- settings (read per call) ---------------------------------------------------


def stripe_live() -> bool:
    """Derive mode from the current credential, not a stale STRIPE_LIVE flag.

    Without a configured credential, preserve the explicit deployment flag
    (used by alternate settings modules); invalid credentials never fall back.
    """
    mode = stripe_key_mode(getattr(settings, "STRIPE_SECRET_KEY", None))
    if mode == "invalid":
        raise CheckoutReadRefused("stripe_not_configured")
    if mode == "disabled":
        return getattr(settings, "STRIPE_LIVE", False) is True
    return mode == "live"


def live_accepted() -> bool:
    """Owner go for live money; a settings module without it refuses."""
    return getattr(settings, "BILLING_LEDGER_ACCEPT_LIVE", False) is True


def live_checkout_refused() -> bool:
    """Unknown credentials or unaccepted live mode cannot open checkout."""
    if stripe_key_mode(getattr(settings, "STRIPE_SECRET_KEY", None)) == "invalid":
        return True
    return stripe_live() and not live_accepted()


# --- binding ----------------------------------------------------------------------


def session_digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def amount_micros(amount_usd: Any) -> int:
    """A positive USD amount as exact integer micros."""
    value = Decimal(str(amount_usd)) * MICROS_PER_USD
    if value <= 0 or value != value.to_integral_value():
        raise ValueError("a checkout amount is a positive amount of whole micros")
    return int(value)


def _actor(actor: Any) -> Any:
    if getattr(actor, "is_authenticated", False) and getattr(actor, "pk", None):
        return actor
    return None


def record_binding(
    org: Any, session_id: str, amount_usd: Any, livemode: bool, actor: Any
) -> AgentccCheckoutBinding:
    """Bind ``session_id`` to ``org`` for ``amount_usd`` in ``livemode``.

    Idempotent for the same org, amount and mode; the same id for another org,
    amount or mode raises CheckoutBindingConflict and changes nothing. A
    database error propagates (the caller answers 503 and returns no URL).
    """
    live = livemode is True
    if (
        not isinstance(session_id, str)
        or SESSION_ID.fullmatch(session_id) is None
        or session_id.startswith(LIVE_PREFIX) != live
    ):
        raise CheckoutBindingConflict("checkout session mode mismatch")
    digest = session_digest(session_id)
    micros = amount_micros(amount_usd)
    initiating_actor = _actor(actor)
    with transaction.atomic():
        binding, created = AgentccCheckoutBinding.objects.get_or_create(
            session_digest=digest,
            defaults={
                "organization": org,
                "amount_micros": micros,
                "currency": CURRENCY,
                "livemode": live,
                "created_by": initiating_actor,
                "actor_identifier": initiating_actor.pk if initiating_actor else None,
            },
        )
    if not created and (
        binding.organization_id != org.pk
        or binding.amount_micros != micros
        or binding.currency != CURRENCY
        or binding.livemode is not live
    ):
        raise CheckoutBindingConflict("checkout session binding conflict")
    return binding


def bound_session(org: Any, session_id: str) -> AgentccCheckoutBinding | None:
    """``org``'s binding of ``session_id``, or None (unknown or another org's)."""
    return AgentccCheckoutBinding.objects.filter(
        organization=org, session_digest=session_digest(session_id)
    ).first()


# --- the Stripe read ------------------------------------------------------------------


def _stripe_key_configured() -> bool:
    # Returns a bool only: the key never sits in a frame that raises.
    return stripe_key_mode(getattr(settings, "STRIPE_SECRET_KEY", None)) in {
        "live",
        "test",
    }


def _http_client() -> stripe.HTTPClient:
    return stripe.RequestsClient(timeout=READ_TIMEOUT_SECONDS)


def _text(session: Any, name: str) -> str | None:
    value = getattr(session, name, None)
    return value if isinstance(value, str) else None


def _state(session: Any) -> CheckoutSessionState:
    livemode = getattr(session, "livemode", None)
    amount = getattr(session, "amount_total", None)
    return CheckoutSessionState(
        id=_text(session, "id"),
        mode=_text(session, "mode"),
        status=_text(session, "status"),
        payment_status=_text(session, "payment_status"),
        livemode=livemode if isinstance(livemode, bool) else None,
        currency=_text(session, "currency"),
        amount_total=(
            amount if isinstance(amount, int) and not isinstance(amount, bool) else None
        ),
    )


def _retrieve(session_id: str) -> tuple[CheckoutSessionState | None, str | None]:
    """``(state, None)``, or ``(None, failure class name)``. Never raises, so
    no exception, traceback or frame holding the client leaves this frame."""
    http_client = None
    try:
        install_stripe_sdk_log_filter()
        http_client = _http_client()
        client = stripe_client(
            settings.STRIPE_SECRET_KEY,
            max_network_retries=0,
            http_client=http_client,
        )
        return _state(client.v1.checkout.sessions.retrieve(session_id)), None
    except Exception as exc:
        return None, type(exc).__name__
    finally:
        if http_client is not None:
            with contextlib.suppress(Exception):
                http_client.close()


def read_checkout_session(session_id: str) -> CheckoutSessionState:
    """Stripe's Checkout Session ``session_id`` (one GET, a 5 s timeout, no
    retries). Raises CheckoutReadRefused: ``stripe_not_configured`` without a
    STRIPE_SECRET_KEY (nothing is sent), ``stripe_read_failed`` for any SDK,
    HTTP or parse failure. The landing calls it through this module, so it is
    the one function to swap for a cloud-billing read."""
    if os.environ.get("STRIPE_LOG") or getattr(stripe, "log", None):
        # The SDK also has a raw stderr channel outside stdlib logging. Never
        # call it when enabled; changing a process-global debug flag per call
        # would race concurrent requests. Fail closed with a fixed code.
        raise CheckoutReadRefused("stripe_raw_logging_enabled")
    if not _stripe_key_configured():
        raise CheckoutReadRefused("stripe_not_configured")
    state, failure = _retrieve(session_id)
    if state is None:
        raise CheckoutReadRefused("stripe_read_failed", failure)
    return state


# --- the landing decision ---------------------------------------------------------


def _minimum_cents(binding: AgentccCheckoutBinding) -> int:
    # Ceiling: a session is never accepted for less than the bound request.
    return -(-binding.amount_micros // MICROS_PER_CENT)


def _session_refusal(
    state: CheckoutSessionState,
    session_id: str,
    binding: AgentccCheckoutBinding,
    live: bool,
) -> str | None:
    if state.id != session_id:
        return "session_mismatch"
    if state.mode != "payment":
        return "not_payment"
    if state.status != "complete":
        return "not_complete"
    if state.payment_status != "paid":
        # no_payment_required and unpaid never credit.
        return "not_paid"
    if state.livemode is not live:
        return "mode_mismatch"
    if (state.currency or "").lower() != CURRENCY or binding.currency != CURRENCY:
        return "currency_mismatch"
    if state.amount_total is None or state.amount_total < _minimum_cents(binding):
        return "amount_below_request"
    return None


def landing_refusal(org: Any, session_id: str) -> LandingRefusal | None:
    """Why the landing must not fulfil ``session_id`` for ``org``, or None.

    The order is fixed and each refusal stops the rest:

    1. the id's mode prefix is this deployment's (cs_live_ iff STRIPE_LIVE);
       no database read, no Stripe call;
    2. a live session needs BILLING_LEDGER_ACCEPT_LIVE (owner go);
    3. the session is bound to ``org`` (looked up in a savepoint, so a database
       error cannot poison the request's later reads); another org's session
       is ``unbound``, with no Stripe call;
    4. the binding's mode is this deployment's;
    5. Stripe's Checkout Session is read (CheckoutReadRefused gives its code);
    6. it is the same session, a payment, complete, paid, in this mode, in USD,
       for at least the bound amount.

    Exceptions other than a refused read propagate: the view treats them as a
    refusal too (``verification_failed``) and never fulfils.
    """
    live = stripe_live()
    if session_id.startswith(LIVE_PREFIX) != live:
        return LandingRefusal("mode_mismatch")
    if live and not live_accepted():
        return LandingRefusal("live_not_accepted")
    with transaction.atomic():
        binding = bound_session(org, session_id)
    if binding is None:
        return LandingRefusal("unbound")
    if binding.livemode is not live:
        return LandingRefusal("mode_mismatch")
    try:
        state = read_checkout_session(session_id)
    except CheckoutReadRefused as refused:
        return LandingRefusal(refused.code, refused.exception_type)
    reason = _session_refusal(state, session_id, binding, live)
    return LandingRefusal(reason) if reason is not None else None
