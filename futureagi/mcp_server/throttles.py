"""Throttle and lockout helpers for MCP public authentication endpoints."""

import hashlib
import math
import time
from collections.abc import Mapping
from typing import TypeVar

import structlog
from django.conf import settings
from django.core.cache import cache
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError
from rest_framework.exceptions import APIException, Throttled
from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle

from mcp_server.constants import (
    DEFAULT_OAUTH_TOKEN_CLIENT_THROTTLE_RATE,
    DEFAULT_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT,
    DEFAULT_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS,
    DEFAULT_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS,
    DEFAULT_OAUTH_TOKEN_IP_THROTTLE_RATE,
)
from mcp_server.exceptions import RateLimitBackendUnavailableError
from mcp_server.rate_limiter import check_sliding_window

logger = structlog.get_logger(__name__)

_T = TypeVar("_T")
_base_throttle = BaseThrottle()


def _setting(name: str, default: _T) -> _T:
    return getattr(settings, name, default)


def stable_hash(value: str, length: int | None = None) -> str:
    """Return a deterministic hash for cache keys and non-secret log labels."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:length] if length else digest


def request_data_value(request: Request, key: str) -> str:
    data = getattr(request, "data", None)
    if not isinstance(data, Mapping):
        return ""
    value = data.get(key, "")
    return str(value).strip() if value is not None else ""


def client_ident(request: Request) -> str:
    """Return a consistent client IP identifier across throttles, lockouts, and logs."""
    # DRF trusts the full X-Forwarded-For header when NUM_PROXIES is unset.
    # Use forwarded addresses only when deployment config explicitly declares
    # the trusted proxy depth; otherwise callers could choose their identity.
    if api_settings.NUM_PROXIES is not None:
        return _base_throttle.get_ident(request) or "unknown"
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def oauth_token_context(request: Request) -> dict[str, str | None]:
    """Return non-secret structured log context for an OAuth token request."""
    client_id = request_data_value(request, "client_id")
    ident = client_ident(request)
    return {
        "grant_type": request_data_value(request, "grant_type") or "unknown",
        "client_id_hash": stable_hash(client_id, length=16) if client_id else None,
        "remote_addr_hash": (
            stable_hash(ident, length=16) if ident != "unknown" else None
        ),
    }


def log_token_warning(request: Request, event_name: str, **kwargs: object) -> None:
    logger.warning(event_name, **oauth_token_context(request), **kwargs)


def oauth_token_attempt_ident(request: Request) -> str:
    grant_type = request_data_value(request, "grant_type") or "unknown"
    client_id = request_data_value(request, "client_id") or "unknown"
    return stable_hash(f"{client_ident(request)}:{grant_type}:{client_id}")


def _invalid_attempt_key(request: Request) -> str:
    return f"mcp_oauth_token_invalid:{oauth_token_attempt_ident(request)}"


def _invalid_lockout_key(request: Request) -> str:
    return f"mcp_oauth_token_invalid_lock:{oauth_token_attempt_ident(request)}"


def get_invalid_attempt_lockout_wait(request: Request) -> int:
    try:
        locked_until = cache.get(_invalid_lockout_key(request))
    except (ConnectionInterrupted, RedisError) as exc:
        logger.error("mcp_oauth_token_throttle_cache_unavailable", exc_info=True)
        raise RateLimitBackendUnavailableError() from exc
    if not locked_until:
        return 0

    return max(math.ceil(locked_until - time.time()), 0)


def clear_invalid_token_attempts(request: Request) -> None:
    try:
        cache.delete(_invalid_attempt_key(request))
    except (ConnectionInterrupted, RedisError) as exc:
        logger.error("mcp_oauth_token_throttle_cache_unavailable", exc_info=True)
        raise RateLimitBackendUnavailableError() from exc


def record_invalid_token_attempt(request: Request) -> int:
    limit = int(
        _setting(
            "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT",
            DEFAULT_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT,
        )
    )
    window_seconds = int(
        _setting(
            "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS",
            DEFAULT_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS,
        )
    )
    lockout_seconds = int(
        _setting(
            "MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS",
            DEFAULT_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS,
        )
    )

    attempt_key = _invalid_attempt_key(request)
    try:
        if cache.add(attempt_key, 1, timeout=window_seconds):
            attempts = 1
        else:
            try:
                attempts = cache.incr(attempt_key)
            except ValueError:
                # Recover if the window expired between add() and incr().
                cache.set(attempt_key, 1, timeout=window_seconds)
                attempts = 1
    except (ConnectionInterrupted, RedisError) as exc:
        logger.error("mcp_oauth_token_throttle_cache_unavailable", exc_info=True)
        raise RateLimitBackendUnavailableError() from exc

    if attempts < limit:
        return 0

    locked_until = time.time() + lockout_seconds
    try:
        cache.set(
            _invalid_lockout_key(request),
            locked_until,
            timeout=lockout_seconds,
        )
        cache.delete(attempt_key)
    except (ConnectionInterrupted, RedisError) as exc:
        logger.error("mcp_oauth_token_throttle_cache_unavailable", exc_info=True)
        raise RateLimitBackendUnavailableError() from exc
    return lockout_seconds


class MCPOAuthTokenThrottled(Throttled):
    """OAuth-shaped 429 exception that still receives DRF retry headers."""

    def __init__(
        self,
        wait: float | int | None,
        detail: str = "Too many token requests",
    ) -> None:
        self.wait = wait
        APIException.__init__(
            self,
            {
                "error": "slow_down",
                "error_description": detail,
            },
            "throttled",
        )


class MCPOAuthTokenLockoutThrottle(BaseThrottle):
    """Reject token requests while an invalid-attempt lockout is active."""

    def allow_request(self, request: Request, view: object) -> bool:
        wait = get_invalid_attempt_lockout_wait(request)
        if wait > 0:
            log_token_warning(
                request,
                "mcp_oauth_token_invalid_attempt_lockout",
                retry_after_seconds=wait,
            )
            raise MCPOAuthTokenThrottled(
                wait,
                detail="Too many invalid token attempts",
            )
        return True


class AtomicRateThrottle(SimpleRateThrottle):
    """Count requests with the shared atomic sliding-window limiter."""

    cache_format = "mcp_oauth_token_throttle_v2_%(scope)s_%(ident)s"
    rate_setting: str = ""
    default_rate: str = ""
    _wait: int | None = None

    def get_rate(self) -> str | None:
        return _setting(self.rate_setting, self.default_rate)

    def get_raw_ident(self, request: Request) -> str | None:
        raise NotImplementedError

    def get_cache_key(self, request: Request, view: object) -> str | None:
        raw_ident = self.get_raw_ident(request)
        if not raw_ident:
            return None
        ident = stable_hash(raw_ident)
        return self.cache_format % {"scope": self.scope, "ident": ident}

    def allow_request(self, request: Request, view: object) -> bool:
        if self.rate is None:
            return True

        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True

        self.now = self.timer()
        allowed, oldest = check_sliding_window(
            self.key,
            self.num_requests,
            self.duration,
            now=self.now,
        )
        if allowed:
            self._wait = None
            return True

        self._wait = max(int(self.duration - (self.now - oldest)) + 1, 1)
        return False

    def wait(self) -> int | None:
        return self._wait


class MCPOAuthTokenIPThrottle(AtomicRateThrottle):
    """Rate-limit token exchange attempts from the same client address."""

    scope = "mcp_oauth_token_ip"
    rate_setting = "MCP_OAUTH_TOKEN_IP_THROTTLE_RATE"
    default_rate = DEFAULT_OAUTH_TOKEN_IP_THROTTLE_RATE

    def get_raw_ident(self, request: Request) -> str:
        return client_ident(request)


class MCPOAuthTokenClientThrottle(AtomicRateThrottle):
    """Rate-limit token exchange attempts for the same client and grant type."""

    scope = "mcp_oauth_token_client"
    rate_setting = "MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE"
    default_rate = DEFAULT_OAUTH_TOKEN_CLIENT_THROTTLE_RATE

    def get_raw_ident(self, request: Request) -> str | None:
        client_id = request_data_value(request, "client_id")
        if not client_id:
            return None

        grant_type = request_data_value(request, "grant_type") or "unknown"
        return f"{grant_type}:{client_id}"
