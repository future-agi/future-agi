"""Throttle helpers for MCP public authentication endpoints."""

import hashlib
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import SimpleRateThrottle


DEFAULT_OAUTH_TOKEN_IP_RATE = "30/min"
DEFAULT_OAUTH_TOKEN_CLIENT_RATE = "10/min"
DEFAULT_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT = 5
DEFAULT_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS = 300
DEFAULT_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS = 300


def stable_hash(value: str, length: int | None = None) -> str:
    """Return a deterministic hash for cache keys and non-secret log labels."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:length] if length else digest


def request_data_value(request, key: str) -> str:
    value = request.data.get(key, "")
    return str(value).strip() if value is not None else ""


def oauth_token_attempt_ident(request) -> str:
    grant_type = request_data_value(request, "grant_type") or "unknown"
    client_id = request_data_value(request, "client_id") or "unknown"
    remote_addr = request.META.get("REMOTE_ADDR", "") or "unknown"
    return stable_hash(f"{remote_addr}:{grant_type}:{client_id}")


def _invalid_attempt_key(request) -> str:
    return f"mcp_oauth_token_invalid:{oauth_token_attempt_ident(request)}"


def _invalid_lockout_key(request) -> str:
    return f"mcp_oauth_token_invalid_lock:{oauth_token_attempt_ident(request)}"


def get_invalid_attempt_lockout_wait(request) -> int:
    locked_until = cache.get(_invalid_lockout_key(request))
    if not locked_until:
        return 0

    wait = int(locked_until - time.time()) + 1
    return max(wait, 0)


def record_invalid_token_attempt(request) -> int:
    now = time.time()
    limit = int(
        getattr(
            settings,
            "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT",
            DEFAULT_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT,
        )
    )
    window_seconds = int(
        getattr(
            settings,
            "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS",
            DEFAULT_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS,
        )
    )
    lockout_seconds = int(
        getattr(
            settings,
            "MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS",
            DEFAULT_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS,
        )
    )

    attempt_key = _invalid_attempt_key(request)
    attempts = cache.get(attempt_key, []) or []
    cutoff = now - window_seconds
    attempts = [ts for ts in attempts if ts > cutoff]
    attempts.append(now)
    cache.set(attempt_key, attempts, timeout=window_seconds)

    if len(attempts) < limit:
        return 0

    locked_until = now + lockout_seconds
    cache.set(
        _invalid_lockout_key(request),
        locked_until,
        timeout=lockout_seconds,
    )
    return lockout_seconds


class MCPOAuthTokenIPThrottle(SimpleRateThrottle):
    """Rate-limit token exchange attempts from the same remote address."""

    scope = "mcp_oauth_token_ip"

    def get_rate(self):
        return getattr(
            settings,
            "MCP_OAUTH_TOKEN_IP_THROTTLE_RATE",
            DEFAULT_OAUTH_TOKEN_IP_RATE,
        )

    def get_cache_key(self, request, view):
        ident = stable_hash(self.get_ident(request))
        return self.cache_format % {"scope": self.scope, "ident": ident}


class MCPOAuthTokenClientThrottle(SimpleRateThrottle):
    """Rate-limit token exchange attempts for the same client and grant type."""

    scope = "mcp_oauth_token_client"

    def get_rate(self):
        return getattr(
            settings,
            "MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE",
            DEFAULT_OAUTH_TOKEN_CLIENT_RATE,
        )

    def get_cache_key(self, request, view):
        client_id = request_data_value(request, "client_id")
        if not client_id:
            return None

        grant_type = request_data_value(request, "grant_type") or "unknown"
        ident = stable_hash(f"{grant_type}:{client_id}")
        return self.cache_format % {"scope": self.scope, "ident": ident}
