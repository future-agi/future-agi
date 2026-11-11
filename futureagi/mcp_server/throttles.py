"""Throttle and lockout helpers for MCP public authentication endpoints."""

import hashlib
import math
import time
import uuid
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

from mcp_server import rate_limiter
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
    # Every grant type verifies the same client_secret, so invalid attempts are
    # counted per client and address, not per grant type. Keying on grant_type
    # would give each client one extra lockout budget per grant type.
    client_id = request_data_value(request, "client_id") or "unknown"
    return stable_hash(f"{client_ident(request)}:{client_id}")


def _invalid_attempt_key(request: Request) -> str:
    return f"mcp_oauth_token_invalid:{oauth_token_attempt_ident(request)}"


def _invalid_lockout_key(request: Request) -> str:
    return f"mcp_oauth_token_invalid_lock:{oauth_token_attempt_ident(request)}"


def _in_flight_key(request: Request) -> str:
    return f"mcp_oauth_token_in_flight_v2:{oauth_token_attempt_ident(request)}"


# Request attribute holding (key, member) of the in-flight reservation until
# release.
_RESERVATION_ATTR = "_mcp_oauth_token_attempt_reservation"
# Each reservation expires on its own this long after it was taken, so a slot
# lost to a killed worker or a failed release stops counting after this,
# whatever other traffic does in the meantime.
_RESERVATION_TIMEOUT_SECONDS = 60

# Prune expired reservations and add this one in a single step. Every
# reservation is its own sorted-set member scored by when it was taken, so
# later requests never extend an older reservation's lifetime. The key TTL
# only garbage-collects the set once its newest member has expired.
_RESERVE_IN_FLIGHT_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local ttl    = tonumber(ARGV[2])
local member = ARGV[3]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - ttl)
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return redis.call('ZCARD', key)
"""

_RELEASE_IN_FLIGHT_LUA = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""


def _live_reservations(key: str, now: float) -> dict[str, float]:
    """Return the unexpired reservations held in the non-Redis fallback."""
    stored = cache.get(key) or {}
    cutoff = now - _RESERVATION_TIMEOUT_SECONDS
    return {member: taken for member, taken in stored.items() if taken > cutoff}


def _add_reservation(key: str, member: str, now: float) -> int:
    """Record one reservation and return how many are live, this one included."""
    script = rate_limiter._redis_script(_RESERVE_IN_FLIGHT_LUA)
    if script is not None:
        return int(
            script(
                keys=[key],
                args=[now, _RESERVATION_TIMEOUT_SECONDS, member],
            )
        )
    # Only reached when the explicit test fallback is enabled; production
    # without Redis already failed closed in _redis_script().
    with rate_limiter._fallback_lock:
        live = _live_reservations(key, now)
        live[member] = now
        cache.set(key, live, timeout=_RESERVATION_TIMEOUT_SECONDS)
        return len(live)


def _remove_reservation(key: str, member: str) -> None:
    script = rate_limiter._redis_script(_RELEASE_IN_FLIGHT_LUA)
    if script is not None:
        script(keys=[key], args=[member])
        return
    with rate_limiter._fallback_lock:
        live = _live_reservations(key, time.time())
        live.pop(member, None)
        if live:
            cache.set(key, live, timeout=_RESERVATION_TIMEOUT_SECONDS)
        else:
            cache.delete(key)


def _invalid_attempt_limit() -> int:
    return int(
        _setting(
            "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT",
            DEFAULT_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT,
        )
    )


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


def reserve_token_attempt(request: Request) -> bool:
    """Count this request as in flight; return False if it would exceed the limit.

    The lockout is only written after a failed secret check, so without a
    reservation a concurrent burst passes the lockout check together and gets
    more secret checks than the limit allows. Recorded invalid attempts plus
    requests still being verified must stay within the limit. A refused
    request gives its slot back at once; an admitted one keeps it until
    release_token_attempt_reservation() runs after the response is built, or
    until the reservation expires _RESERVATION_TIMEOUT_SECONDS after it was
    taken.
    """
    key = _in_flight_key(request)
    member = uuid.uuid4().hex
    try:
        in_flight = _add_reservation(key, member, time.time())
        setattr(request, _RESERVATION_ATTR, (key, member))
        # Read the recorded attempts after reserving. A request that finishes
        # in between records its attempt before it releases its slot, so this
        # order can only overcount, never undercount.
        recorded = int(cache.get(_invalid_attempt_key(request)) or 0)
    except (ConnectionInterrupted, RedisError) as exc:
        logger.error("mcp_oauth_token_throttle_cache_unavailable", exc_info=True)
        release_token_attempt_reservation(request)
        raise RateLimitBackendUnavailableError() from exc

    if recorded + in_flight <= _invalid_attempt_limit():
        return True
    release_token_attempt_reservation(request)
    return False


def release_token_attempt_reservation(request: Request | None) -> None:
    reservation = getattr(request, _RESERVATION_ATTR, None)
    if not reservation:
        return
    setattr(request, _RESERVATION_ATTR, None)
    key, member = reservation
    try:
        _remove_reservation(key, member)
    except (ConnectionInterrupted, RedisError, RateLimitBackendUnavailableError):
        # The reservation expires on its own _RESERVATION_TIMEOUT_SECONDS
        # after it was taken, so a missed release cannot linger past that.
        logger.error("mcp_oauth_token_reservation_release_failed", exc_info=True)


def record_invalid_token_attempt(request: Request) -> int:
    limit = _invalid_attempt_limit()
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
    """Reject token requests while an invalid-attempt lockout is active.

    Also reserves an in-flight slot so concurrent requests cannot get more
    secret checks than the invalid-attempt limit. The view must call
    release_token_attempt_reservation() once the request finishes.
    """

    def _raise_lockout(self, request: Request, wait: int) -> None:
        log_token_warning(
            request,
            "mcp_oauth_token_invalid_attempt_lockout",
            retry_after_seconds=wait,
        )
        raise MCPOAuthTokenThrottled(
            wait,
            detail="Too many invalid token attempts",
        )

    def _raise_in_flight_limit(self, request: Request) -> None:
        log_token_warning(
            request,
            "mcp_oauth_token_in_flight_limit",
            retry_after_seconds=1,
        )
        raise MCPOAuthTokenThrottled(
            1,
            detail="Too many concurrent token requests",
        )

    def allow_request(self, request: Request, view: object) -> bool:
        wait = get_invalid_attempt_lockout_wait(request)
        if wait > 0:
            self._raise_lockout(request, wait)

        if not reserve_token_attempt(request):
            # In-flight attempts already fill the remaining budget. Retry once
            # they finish; by then they are recorded or cleared.
            self._raise_in_flight_limit(request)

        # A request that finished between the first check and the reservation
        # may have set the lockout and reset the attempt counter.
        wait = get_invalid_attempt_lockout_wait(request)
        if wait > 0:
            release_token_attempt_reservation(request)
            self._raise_lockout(request, wait)
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
    """Rate-limit token exchange attempts for the same client, across grant types."""

    scope = "mcp_oauth_token_client"
    rate_setting = "MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE"
    default_rate = DEFAULT_OAUTH_TOKEN_CLIENT_THROTTLE_RATE

    def get_raw_ident(self, request: Request) -> str | None:
        # Every grant type verifies the same client_secret, so one window
        # covers all of them.
        return request_data_value(request, "client_id") or None
