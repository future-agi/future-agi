"""Stripe billing settings read from environment variables (MUST-FIX G).

Stdlib and django.core.exceptions only (the layering of tfc/utils/env.py), so
settings.py can resolve them while it is still being imported. A refusal names
the variable, never its value.

WEBHOOK_SECRET_LIVE / WEBHOOK_SECRET_TEST
    The Stripe webhook signing secret for the API key's mode (STRIPE_LIVE
    picks LIVE), so the secret never disagrees with the key the events were
    generated against. It is never empty: with STRIPE_SECRET_KEY set and the
    mode's secret blank the process refuses to start; with no Stripe key it
    is a per-process refusal value nobody holds, so every signature check
    against it fails, an event forged with the empty key included.

BILLING_LEDGER_ACCEPT_LIVE / BILLING_LEDGER_ACCEPT_LIVE_OWNER_GO
    Real-money (live-mode) credit is accepted only on owner go: unset, blank
    or ``false`` refuses it; exactly ``true`` accepts it and then
    BILLING_LEDGER_ACCEPT_LIVE_OWNER_GO must name the owner-go record (a
    reference such as ``owner-go/2026-11-11``, not a secret). Any other value
    refuses to start: it is never read as true or as false.

STRIPE_API_BASE
    Unset in production: every Stripe call goes to the SDK's default
    (https://api.stripe.com). A test stack sets it to a Stripe API mock such
    as stripe-mock, and tfc.stripe_client sends every Stripe call there. Only
    an http(s) URL with a host, no credentials, query, fragment, whitespace
    or control character is accepted.
"""

import re
import secrets
from collections.abc import Mapping
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

WEBHOOK_SECRET_LIVE = "WEBHOOK_SECRET_LIVE"
WEBHOOK_SECRET_TEST = "WEBHOOK_SECRET_TEST"
ACCEPT_LIVE = "BILLING_LEDGER_ACCEPT_LIVE"
ACCEPT_LIVE_OWNER_GO = "BILLING_LEDGER_ACCEPT_LIVE_OWNER_GO"
API_BASE = "STRIPE_API_BASE"

# Marks the refusal value in any diagnostic that shows its shape. The random
# part is 256 bits, drawn per process and never logged.
_REFUSAL_PREFIX = "fi-unset-refuse-"
_OWNER_GO_REFERENCE = re.compile(r"[A-Za-z0-9._:/-]{3,128}")


def resolve_stripe_webhook_secret(
    env: Mapping[str, str], *, stripe_live: bool, stripe_enabled: bool
) -> tuple[str, bool]:
    """``(secret, configured)`` for the webhook signing secret of the key's mode.

    ``configured`` is False only for the refusal value. ``stripe_enabled`` is
    whether STRIPE_SECRET_KEY is set: a deployment that uses Stripe must be
    able to authenticate its events, so a blank secret there refuses to start
    (before rollout, in the deploy job's settings import).
    """
    name = WEBHOOK_SECRET_LIVE if stripe_live else WEBHOOK_SECRET_TEST
    value = (env.get(name) or "").strip()
    if value:
        return value, True
    if stripe_enabled:
        raise ImproperlyConfigured(
            f"{name} must be set when STRIPE_SECRET_KEY is set: Stripe webhook "
            "events cannot be verified without the signing secret for the key's "
            "mode"
        )
    return _REFUSAL_PREFIX + secrets.token_hex(32), False


def resolve_accept_live(env: Mapping[str, str]) -> tuple[bool, str]:
    """``(accept_live, owner_go_reference)``; ``(False, "")`` unless owner go."""
    raw = (env.get(ACCEPT_LIVE) or "").strip().lower()
    if raw in ("", "false"):
        return False, ""
    if raw != "true":
        raise ImproperlyConfigured(f"{ACCEPT_LIVE} must be true or false")
    reference = (env.get(ACCEPT_LIVE_OWNER_GO) or "").strip()
    if not _OWNER_GO_REFERENCE.fullmatch(reference):
        raise ImproperlyConfigured(
            f"{ACCEPT_LIVE_OWNER_GO} must name the owner-go record (3 to 128 "
            f"letters, digits or . _ : / -) when {ACCEPT_LIVE} is true"
        )
    return True, reference


def stripe_key_mode(key: object) -> str:
    """Return live/test/disabled/invalid, never a credential or exception value.

    Stripe's server credentials are standard secret (sk) or restricted (rk)
    keys. Publishable, unknown, truncated and whitespace-padded forms cannot
    silently select test mode. Only absent/empty keys mean Stripe is disabled.
    """
    if key is None or key == "":
        return "disabled"
    if not isinstance(key, str) or any(character.isspace() for character in key):
        return "invalid"
    for mode in ("live", "test"):
        for family in ("sk", "rk"):
            prefix = f"{family}_{mode}_"
            if key.startswith(prefix) and len(key) > len(prefix):
                return mode
    return "invalid"


def resolve_stripe_live(key: object) -> bool:
    """Startup validation; a nonempty unknown credential refuses configuration."""
    mode = stripe_key_mode(key)
    if mode == "invalid":
        raise ImproperlyConfigured(
            "STRIPE_SECRET_KEY must be a supported standard or restricted server credential"
        )
    return mode == "live"


def resolve_stripe_api_base(value: object) -> str:
    """The Stripe API base to call instead of Stripe's, or ``""`` when unset.

    Unset (None or empty) keeps Stripe's own base. Anything else must be an
    http or https URL with a host and no credentials, query, fragment,
    whitespace or control character (urlsplit keeps one inside the host); a
    trailing ``/`` is dropped, since the SDK appends ``/v1/...``.
    A refusal names the variable, never the value.
    """
    if value is None or value == "":
        return ""
    refusal = ImproperlyConfigured(
        f"{API_BASE} must be an http or https URL with a host, and no "
        "credentials, query or fragment"
    )
    if not isinstance(value, str) or any(
        c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value
    ):
        raise refusal
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in ("http", "https")
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and "?" not in value
            and "#" not in value
            # .port raises ValueError for a malformed or out-of-range port.
            and (parts.port is None or parts.port > 0)
        )
    except ValueError:
        valid = False
    if not valid:
        raise refusal
    return value.rstrip("/")


def refuse_api_base_with_live(base: str, live: bool) -> None:
    """A test API base never receives a live key: a deployment that sets
    STRIPE_API_BASE with a live Stripe key is misconfigured, and its landing
    read would send the live key to that host and trust its answer. The
    refusal names the variable, never the value."""
    if base and live:
        raise ImproperlyConfigured(f"{API_BASE} must not be set with a live Stripe key")
