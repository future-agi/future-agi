"""Throttle and lockout helpers for MCP public authentication endpoints."""

import hashlib
import ipaddress
import math
import time
import uuid
from collections.abc import Mapping
from functools import lru_cache
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
    DEFAULT_OAUTH_REGISTER_IP_THROTTLE_RATE,
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


@lru_cache(maxsize=8)
def _parse_trusted_proxies(
    entries: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for entry in entries:
        try:
            network = ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError:
            logger.warning("mcp_oauth_trusted_proxy_entry_ignored", entry=entry)
            continue
        # Compare IPv4-mapped IPv6 entries in their IPv4 form, like addresses.
        mapped = isinstance(network, ipaddress.IPv6Network) and (
            network.network_address.ipv4_mapped
        )
        if mapped and network.prefixlen >= 96:
            network = ipaddress.ip_network((mapped, network.prefixlen - 96))
        networks.append(network)
    return tuple(networks)


def _trusted_proxies() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    entries = _setting("MCP_OAUTH_TRUSTED_PROXIES", ())
    if isinstance(entries, str):
        entries = entries.split(",")
    return _parse_trusted_proxies(tuple(e.strip() for e in entries if e.strip()))


def _trusted_proxy_hops() -> int:
    """How many X-Forwarded-For entries trusted proxies add (0: no limit)."""
    raw = _setting("MCP_OAUTH_TRUSTED_PROXY_HOPS", 0)
    try:
        hops = int(raw or 0)
    except (TypeError, ValueError):
        logger.warning("mcp_oauth_trusted_proxy_hops_ignored", value=raw)
        return 0
    return max(hops, 0)


def _is_trusted_proxy(
    address: str, networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in network for network in networks)


def _forwarded_address(entry: str) -> str | None:
    """The address of one X-Forwarded-For entry, without a port.

    Accepts ``IP``, ``IPv4:port``, ``[IPv6]`` and ``[IPv6]:port``. Anything
    else (``unknown``, a hostname, a malformed entry) is ``None``.
    """
    host = entry
    if entry.startswith("["):
        closing = entry.find("]")
        port = entry[closing + 1 :] if closing > 0 else ""
        if closing < 0 or (port and not (port[0] == ":" and port[1:].isdigit())):
            return None
        host = entry[1:closing]
    elif entry.count(":") == 1:
        host, port = entry.split(":")
        if not port.isdigit():
            return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if entry.startswith("[") and not isinstance(address, ipaddress.IPv6Address):
        return None
    return str(address)


def client_ident(request: Request) -> str:
    """Return a consistent client IP identifier across throttles, lockouts, and logs."""
    # DRF trusts the full X-Forwarded-For header when NUM_PROXIES is unset.
    # Use forwarded addresses only when deployment config explicitly declares
    # the trusted proxy depth or the trusted proxy addresses.
    if api_settings.NUM_PROXIES is not None:
        return _base_throttle.get_ident(request) or "unknown"
    remote_addr = request.META.get("REMOTE_ADDR", "") or ""
    networks = _trusted_proxies()
    if networks and _is_trusted_proxy(remote_addr, networks):
        # MCP_OAUTH_TRUSTED_PROXIES lists the ingress and proxy addresses.
        # Each proxy appends the address it received the request from, so the
        # nearest entry that is not a trusted proxy is the client. With
        # MCP_OAUTH_TRUSTED_PROXY_HOPS, only the entries those proxies added
        # are read: anything the client sent before them is not.
        hops = [
            hop.strip()
            for hop in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
            if hop.strip()
        ]
        limit = _trusted_proxy_hops()
        if limit:
            hops = hops[-limit:]
        addresses = []
        for hop in reversed(hops):
            # Controllers may write "address:port"; the port is not part of
            # the client. An entry that is not an address is not trusted to
            # name the client, so the request counts by its peer address.
            address = _forwarded_address(hop)
            if address is None:
                return remote_addr or "unknown"
            if not _is_trusted_proxy(address, networks):
                return address
            addresses.append(address)
        if addresses:
            return addresses[-1]
    return remote_addr or "unknown"


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
    # Count attempts per client and address across grant types.
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

# Prune expired reservations, read the recorded invalid attempts, and add
# this reservation only if both together leave room for it, in a single step.
# A request that finishes meanwhile is then counted exactly once: either as
# in flight or as recorded. Every reservation is its own sorted-set member
# scored by when it was taken, so later requests never extend an older
# reservation's lifetime. The key TTL only garbage-collects the set once its
# newest member has expired. KEYS[2] is the Django cache key of the counter.
_RESERVE_IN_FLIGHT_LUA = """
local key         = KEYS[1]
local attempt_key = KEYS[2]
local now    = tonumber(ARGV[1])
local ttl    = tonumber(ARGV[2])
local member = ARGV[3]
local limit  = tonumber(ARGV[4])

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - ttl)
local recorded = tonumber(redis.call('GET', attempt_key) or '0') or 0
if recorded + redis.call('ZCARD', key) + 1 > limit then
  return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return 1
"""

_RELEASE_IN_FLIGHT_LUA = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""

# Count one invalid attempt and drop the request's reservation in a single
# step, so admission never sees the attempt both recorded and in flight.
# Matches cache.add(key, 1, timeout) followed by cache.incr(key).
_RECORD_INVALID_ATTEMPT_LUA = """
local attempt_key = KEYS[1]
local key         = KEYS[2]
local window = tonumber(ARGV[1])
local member = ARGV[2]

local attempts = redis.call('INCR', attempt_key)
if attempts == 1 then
  redis.call('EXPIRE', attempt_key, window)
end
if member ~= '' then
  redis.call('ZREM', key, member)
end
return attempts
"""


def _live_reservations(key: str, now: float) -> dict[str, float]:
    """Return the unexpired reservations held in the non-Redis fallback."""
    stored = cache.get(key) or {}
    cutoff = now - _RESERVATION_TIMEOUT_SECONDS
    return {member: taken for member, taken in stored.items() if taken > cutoff}


def _add_reservation(
    key: str, attempt_key: str, member: str, now: float, limit: int
) -> bool:
    """Add one reservation if recorded attempts plus live reservations allow it."""
    script = rate_limiter._redis_script(_RESERVE_IN_FLIGHT_LUA)
    if script is not None:
        return bool(
            int(
                script(
                    keys=[key, cache.make_key(attempt_key)],
                    args=[now, _RESERVATION_TIMEOUT_SECONDS, member, limit],
                )
            )
        )
    # Only reached when the explicit test fallback is enabled; production
    # without Redis already failed closed in _redis_script().
    with rate_limiter._fallback_lock:
        live = _live_reservations(key, now)
        recorded = int(cache.get(attempt_key) or 0)
        if recorded + len(live) + 1 > limit:
            return False
        live[member] = now
        cache.set(key, live, timeout=_RESERVATION_TIMEOUT_SECONDS)
        return True


def _discard_reservation(key: str, member: str) -> None:
    """Drop one fallback reservation. The caller holds the fallback lock."""
    live = _live_reservations(key, time.time())
    live.pop(member, None)
    if live:
        cache.set(key, live, timeout=_RESERVATION_TIMEOUT_SECONDS)
    else:
        cache.delete(key)


def _remove_reservation(key: str, member: str) -> None:
    script = rate_limiter._redis_script(_RELEASE_IN_FLIGHT_LUA)
    if script is not None:
        script(keys=[key], args=[member])
        return
    with rate_limiter._fallback_lock:
        _discard_reservation(key, member)


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

    Recorded invalid attempts and in-flight requests share the configured
    limit. Both are read in the same step that adds the reservation, and a
    refused request never takes a slot. An admitted request keeps its slot until
    record_invalid_token_attempt() or release_token_attempt_reservation()
    gives it back, or until the reservation expires
    _RESERVATION_TIMEOUT_SECONDS after it was taken.

    A request that reaches the limit sets the lockout and then resets the
    counter; MCPOAuthTokenLockoutThrottle catches that by re-checking the
    lockout after reserving.
    """
    key = _in_flight_key(request)
    member = uuid.uuid4().hex
    # Attach the reservation first: if Redis applies the script but the reply
    # is lost, the cleanup below still removes the member.
    setattr(request, _RESERVATION_ATTR, (key, member))
    try:
        admitted = _add_reservation(
            key,
            _invalid_attempt_key(request),
            member,
            time.time(),
            _invalid_attempt_limit(),
        )
    except (ConnectionInterrupted, RedisError) as exc:
        logger.error("mcp_oauth_token_throttle_cache_unavailable", exc_info=True)
        release_token_attempt_reservation(request)
        raise RateLimitBackendUnavailableError() from exc

    if not admitted:
        setattr(request, _RESERVATION_ATTR, None)
    return admitted


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


def _increment_invalid_attempts(attempt_key: str, window_seconds: int) -> int:
    if cache.add(attempt_key, 1, timeout=window_seconds):
        return 1
    try:
        return cache.incr(attempt_key)
    except ValueError:
        # Recover if the window expired between add() and incr().
        cache.set(attempt_key, 1, timeout=window_seconds)
        return 1


def _count_invalid_attempt(
    request: Request, attempt_key: str, window_seconds: int
) -> int:
    """Count one invalid attempt and give back the request's reservation.

    Both happen in one step, so admission never counts the attempt twice:
    once as recorded and once as still in flight.
    """
    reservation = getattr(request, _RESERVATION_ATTR, None)
    script = rate_limiter._redis_script(_RECORD_INVALID_ATTEMPT_LUA)
    if script is not None:
        key, member = reservation or (_in_flight_key(request), "")
        attempts = int(
            script(
                keys=[cache.make_key(attempt_key), key],
                args=[window_seconds, member],
            )
        )
    elif reservation is None:
        # Nothing in flight to give back, so no step to share with admission.
        return _increment_invalid_attempts(attempt_key, window_seconds)
    else:
        key, member = reservation
        with rate_limiter._fallback_lock:
            attempts = _increment_invalid_attempts(attempt_key, window_seconds)
            _discard_reservation(key, member)
    # Cleared only once the reservation is gone, so a failure above still
    # leaves it for release_token_attempt_reservation().
    setattr(request, _RESERVATION_ATTR, None)
    return attempts


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
        attempts = _count_invalid_attempt(request, attempt_key, window_seconds)
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

    Also reserves an in-flight slot so concurrent token requests count toward
    the configured attempt limit. The view must call
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


class MCPOAuthRegisterIPThrottle(AtomicRateThrottle):
    """Rate-limit dynamic client registrations from the same client address."""

    scope = "mcp_oauth_register_ip"
    rate_setting = "MCP_OAUTH_REGISTER_IP_THROTTLE_RATE"
    default_rate = DEFAULT_OAUTH_REGISTER_IP_THROTTLE_RATE

    def get_raw_ident(self, request: Request) -> str:
        return client_ident(request)
