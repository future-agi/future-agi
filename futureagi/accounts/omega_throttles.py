"""Rate limits on the two Omega credential endpoints (CP P5, READINESS L03, SB W2-KEYS).

Every limit here is an atomic sliding window in the default cache's Redis (``omega_rate_window``):
it holds under a concurrent burst and across backend replicas, and refuses with 503 when Redis is
down. Rates are DRF-style strings (``120/min``) read from the environment on each request:

| Throttle | Counted | Default | Variable |
|---|---|---|---|
| Browser exchange | per Future AGI user, after sign-in | 120/min | ``OMEGA_SESSION_EXCHANGE_THROTTLE_RATE`` |
| API-key exchange | per key from one client source, before the secret is checked | 1,800/min | ``OMEGA_SESSION_EXCHANGE_KEY_THROTTLE_RATE`` |
| API-key exchange | per key across every source, before the secret is checked | 18,000/min | ``OMEGA_SESSION_EXCHANGE_KEY_CEILING_RATE`` |
| API-key exchange | per key owner, after a successful sign-in | 1,800/min | ``OMEGA_SESSION_EXCHANGE_KEY_OWNER_THROTTLE_RATE`` |
| Observe-target lookup | per key from one client source, before the secret is checked | 60/min | ``OMEGA_OBSERVE_TARGET_THROTTLE_RATE`` |
| Observe-target lookup | per key across every source, before the secret is checked | 600/min | ``OMEGA_OBSERVE_TARGET_KEY_CEILING_RATE`` |

Why a key has two windows: anyone who holds only a key's public id (an org admin, a log reader)
can send wrong secrets for it. Counted per key alone, those spend the budget the owner's right
secret needs, and the owner is locked out for as long as the flood lasts. Counted per key and
source, one flooding client meets its own limit and the owner, from anywhere else, does not; the
per-key ceiling still bounds guessing from many sources, and a flood must come from at least
ceiling / per-source sources (10 at the defaults) to reach the owner. Who the source is:
``omega_client_source``.
"""

import hashlib
import os

from accounts.omega_client_source import client_source
from accounts.omega_rate_window import (
    AtomicRateThrottle,
    Window,
    parse_rate,
    window_key,
)

SESSION_EXCHANGE_RATE = "120/min"
OBSERVE_TARGET_RATE = "60/min"
OBSERVE_TARGET_CEILING_RATE = "600/min"
# Provisional (READINESS L03): the gateway's read class, 30 a second, as a per-minute budget;
# the ceiling is ten sources' worth.
API_KEY_EXCHANGE_RATE = "1800/min"
API_KEY_EXCHANGE_CEILING_RATE = "18000/min"


def _rate(variable, default):
    return parse_rate(os.environ.get(variable) or default)


def key_digest(api_key: str) -> str:
    """The public key id as the store sees it: hashed, never as given."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:32]


class OmegaSessionExchangeThrottle(AtomicRateThrottle):
    """Browser exchanges, per Future AGI user (the view runs it after authentication)."""

    scope = "omega_session_exchange"

    def windows(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        limit, seconds = _rate(
            "OMEGA_SESSION_EXCHANGE_THROTTLE_RATE", SESSION_EXCHANGE_RATE
        )
        return [Window(window_key(self.scope, f"user:{user.pk}"), limit, seconds)]


class OmegaApiKeyOwnerThrottle(AtomicRateThrottle):
    """Successful key exchanges, per key owner across all of that person's keys."""

    scope = "omega_session_exchange_api_key_owner"

    def windows(self, request, view):
        if getattr(request, "omega_auth_method", None) != "api_key":
            return None
        limit, seconds = _rate(
            "OMEGA_SESSION_EXCHANGE_KEY_OWNER_THROTTLE_RATE", API_KEY_EXCHANGE_RATE
        )
        return [
            Window(window_key(self.scope, f"user:{request.user.pk}"), limit, seconds)
        ]


class ApiKeyAttemptThrottle(AtomicRateThrottle):
    """Every attempt that names a key, before its secret is checked: per (key, source) and per key.

    ``refused_by`` says which window refused: 0 the caller's source, 1 the key's ceiling.
    """

    scope = ""
    source_rate = ("", "")
    ceiling_rate = ("", "")

    def windows(self, request, view):
        api_key = request.headers.get("X-Api-Key", "")
        if not api_key:
            return None
        tag = "{k:%s}" % key_digest(api_key)  # one Redis Cluster slot for both windows
        limit, seconds = _rate(*self.source_rate)
        ceiling, ceiling_seconds = _rate(*self.ceiling_rate)
        return [
            Window(
                window_key(self.scope, tag, "src", client_source(request)),
                limit,
                seconds,
            ),
            Window(window_key(self.scope, tag, "key"), ceiling, ceiling_seconds),
        ]


class OmegaApiKeyExchangeThrottle(ApiKeyAttemptThrottle):
    scope = "omega_session_exchange_api_key"
    source_rate = ("OMEGA_SESSION_EXCHANGE_KEY_THROTTLE_RATE", API_KEY_EXCHANGE_RATE)
    ceiling_rate = (
        "OMEGA_SESSION_EXCHANGE_KEY_CEILING_RATE",
        API_KEY_EXCHANGE_CEILING_RATE,
    )


class OmegaObserveTargetThrottle(ApiKeyAttemptThrottle):
    scope = "omega_observe_target"
    source_rate = ("OMEGA_OBSERVE_TARGET_THROTTLE_RATE", OBSERVE_TARGET_RATE)
    ceiling_rate = (
        "OMEGA_OBSERVE_TARGET_KEY_CEILING_RATE",
        OBSERVE_TARGET_CEILING_RATE,
    )
