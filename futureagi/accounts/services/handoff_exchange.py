"""One-time codes that carry a freshly minted session from a redirect to the app.

CR-07: a bearer,
access or refresh token must never travel in a URL, where browser history,
proxy and CDN access logs and every page-URL analytics or error SDK would keep
it. Every login handoff (OAuth SSO, SAML, SOS and AWS Marketplace)
redirects with an opaque code instead. The app redeems the code once, by a
JSON POST from its own origin, and receives the tokens only in that response
body.

The record lives in the Django cache under a digest of the code, never the
code itself, and is bound to the handoff kind it was issued for. Single use
requires an atomic delete that reports the winner (Redis DEL, Memcached
DELETE, or LocMemCache under its lock). A prior read never authorizes a
redeem: only the caller that actually deletes the live record may return it.
Backends without an unambiguous delete result fail closed. Every refusal is
the same ``HandoffRefused``: a caller cannot tell a spent code from an expired,
unknown or wrong-kind one. The one exception to single use is an onboarding
code whose sign-up was refused for a reason the buyer can correct: its
redeemer may put it back inside its original deadline (``restore``).
"""

import hashlib
import math
import re
import secrets
import time
from typing import TypedDict
from uuid import UUID

import structlog
from django.core.cache import cache

logger = structlog.get_logger(__name__)

# At most 60 seconds; this also bounds a pasted SOS copy-link.
HANDOFF_CODE_TTL_SECONDS = 60
# 32 bytes from the OS CSPRNG (256 bits); the security floor is 128.
HANDOFF_CODE_BYTES = 32

KIND_SSO = "sso"
KIND_SAML = "saml"
KIND_SOS = "sos"
KIND_AWS_MARKETPLACE = "aws_marketplace"
KIND_AWS_ONBOARDING = "aws_marketplace_onboarding"
KIND_WS = "ws"
LOGIN_HANDOFF_KINDS = frozenset({KIND_SSO, KIND_SAML, KIND_SOS, KIND_AWS_MARKETPLACE})
HANDOFF_KINDS = LOGIN_HANDOFF_KINDS | {KIND_WS, KIND_AWS_ONBOARDING}
# An onboarding code mints no session: its consumer still has to finish
# sign-up, so a refusal the buyer can correct may hand it back (``restore``).
RESTORABLE_KINDS = frozenset({KIND_AWS_ONBOARDING})
WS_QUERY_FIELDS = {
    "/ws/connect/": frozenset(),
    "/ws/simulation-updates/": frozenset({"test_id"}),
    "/ws/prompt-stream/": frozenset({"workspace_id"}),
    "/ws/falcon-ai/": frozenset({"workspace_id"}),
}

_CODE_SHAPE = re.compile(r"[A-Za-z0-9_-]{43}")
_RECORD_PREFIX = "handoff_exchange:record:"


class HandoffPayload(TypedDict):
    access: str
    refresh: str | None


class WebSocketPayload(TypedDict):
    user_id: str
    session_id: str
    path: str
    query: dict[str, str]


class OnboardingPayload(TypedDict):
    onboarding_token: str


ExchangePayload = HandoffPayload | WebSocketPayload | OnboardingPayload


class HandoffRefused(Exception):
    """The one categorical refusal for any code that cannot be redeemed."""


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _bound_payload(kind: str, payload: ExchangePayload) -> ExchangePayload:
    if kind not in HANDOFF_KINDS or not isinstance(payload, dict):
        raise ValueError("Invalid handoff payload.")
    if kind == KIND_WS:
        # No bearer is stored, even as an optional/extra field. Only the live
        # session identity is carried; the socket revalidates its database row.
        if set(payload) != {"user_id", "session_id", "path", "query"}:
            raise ValueError("Invalid socket ticket payload.")
        user_id = str(UUID(payload["user_id"]))
        session_id = str(UUID(payload["session_id"]))
        path, query = payload["path"], payload["query"]
        if not isinstance(path, str) or path not in WS_QUERY_FIELDS:
            raise ValueError("Invalid socket path.")
        if not isinstance(query, dict) or set(query) - WS_QUERY_FIELDS[path]:
            raise ValueError("Invalid socket parameters.")
        if any(
            not isinstance(value, str) or not value or len(value) > 256
            for value in query.values()
        ):
            raise ValueError("Invalid socket parameters.")
        return {
            "user_id": user_id,
            "session_id": session_id,
            "path": path,
            "query": dict(query),
        }
    if kind == KIND_AWS_ONBOARDING:
        token = payload.get("onboarding_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Invalid onboarding handoff.")
        return {"onboarding_token": token}
    access, refresh = payload.get("access"), payload.get("refresh")
    if not isinstance(access, str) or not access:
        raise ValueError("A handoff must carry a minted session.")
    if refresh is not None and (not isinstance(refresh, str) or not refresh):
        raise ValueError("A handoff refresh token must be a non-empty string.")
    return {"access": access, "refresh": refresh}


def issue(kind: str, payload: ExchangePayload) -> str:
    """Store a kind-bound login, onboarding capability or socket identity once."""
    bound_payload = _bound_payload(kind, payload)
    code = secrets.token_urlsafe(HANDOFF_CODE_BYTES)
    record = {
        "kind": kind,
        "payload": bound_payload,
        "expires_at": time.time() + HANDOFF_CODE_TTL_SECONDS,
    }
    if not cache.add(
        f"{_RECORD_PREFIX}{_digest(code)}", record, timeout=HANDOFF_CODE_TTL_SECONDS
    ):
        # A 256-bit collision is not a real event; refuse rather than overwrite.
        raise RuntimeError("Handoff code could not be stored.")
    logger.info("handoff_code_issued", kind=kind)
    return code


def redeem(code: object, kind: object) -> ExchangePayload:
    """Return the payload behind ``code`` exactly once, or raise HandoffRefused.

    A code is spent by the first redeem that reaches it, whatever the kind
    named, so a wrong-kind attempt cannot be retried as the right one.
    """
    return redeem_with_deadline(code, kind)[0]


def redeem_with_deadline(code: object, kind: object) -> tuple[ExchangePayload, float]:
    """``redeem``, also returning the code's original deadline for ``restore``."""
    if not isinstance(code, str) or not _CODE_SHAPE.fullmatch(code):
        raise HandoffRefused()
    if not isinstance(kind, str) or kind not in HANDOFF_KINDS:
        raise HandoffRefused()

    digest = _digest(code)
    record_key = f"{_RECORD_PREFIX}{digest}"
    record = cache.get(record_key)
    if record is None:
        raise HandoffRefused()
    # The delete, not the earlier read, selects exactly one winner. Accept
    # only a true boolean or the integer count 1, never a truthy sentinel.
    deleted = cache.delete(record_key)
    if not (deleted is True or (type(deleted) is int and deleted == 1)):
        raise HandoffRefused()
    # LocMemCache can delete an expired-but-not-yet-purged entry. Also refuse
    # a reader paused past expiry even if its delete won. Old envelopes with
    # no deadline fail closed rather than bypassing this check.
    expires_at = record.get("expires_at", 0)
    if expires_at <= time.time():
        raise HandoffRefused()

    if record.get("kind") != kind:
        logger.warning("handoff_code_kind_mismatch", kind=kind)
        raise HandoffRefused()
    try:
        payload = _bound_payload(kind, record["payload"])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HandoffRefused() from exc
    logger.info("handoff_code_redeemed", kind=kind)
    return payload, expires_at


def restore(code: str, kind: str, payload: ExchangePayload, expires_at: float) -> bool:
    """Put back an onboarding code its redeemer refused for a fixable reason.

    Only for the caller that won ``redeem_with_deadline`` and then refused
    before creating a user, for example because the email it was given
    already has an account. The record keeps its original deadline, so this
    never extends a code's life, and ``cache.add`` never overwrites a live
    record. Login and socket codes stay strictly single use. Returns whether
    the code can be redeemed again.
    """
    if kind not in RESTORABLE_KINDS:
        raise ValueError("Only an onboarding code can be restored.")
    if not isinstance(code, str) or not _CODE_SHAPE.fullmatch(code):
        return False
    remaining = expires_at - time.time()
    if remaining <= 0:
        return False
    record = {
        "kind": kind,
        "payload": _bound_payload(kind, payload),
        "expires_at": expires_at,
    }
    restored = bool(
        cache.add(
            f"{_RECORD_PREFIX}{_digest(code)}",
            record,
            timeout=math.ceil(remaining),
        )
    )
    if restored:
        logger.info("handoff_code_restored", kind=kind)
    return restored
