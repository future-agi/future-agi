"""Atomic sliding-window limits for Omega's credential endpoints (READINESS L03, SB W2-KEYS).

DRF's ``SimpleRateThrottle`` reads a request history from the cache, appends to it and writes it
back. Concurrent requests overwrite each other's appends, so the limit loses counts under a burst:
a 20-way wrong-secret flood passed about 4,300 attempts a minute through a 1,800-a-minute limit on
the stack's Redis (verifier, round 3). Here one Redis script prunes, decides and records every
window a request draws on, in one step no other request can interleave with:

- **Sliding window.** At most ``limit`` attempts in any ``seconds``-long span, as the attempt times
  Redis itself reads (``TIME``), so every Django replica shares one clock.
- **All or none.** A request that one window refuses is recorded in none, so a refused attempt
  never spends another window's budget (a flooded source never fills its key's ceiling).
- **Refusals are not recorded.** A flood past the limit costs nothing more, and the window clears
  ``seconds`` after its oldest admitted attempt, which is what ``Retry-After`` says.
- **No Redis, no admission.** If the shared store fails, the request is refused 503 with
  ``Retry-After`` (``RateLimitUnavailable``), never let through unlimited (OD-07 for writes).

When the default cache is not Redis (tests, a locmem development stack), a process lock serialises
the same decision over the cache, which is exact within one process.
"""

from __future__ import annotations

import math
import re
import threading
import time
import uuid
from dataclasses import dataclass

import structlog
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.throttling import BaseThrottle

logger = structlog.get_logger(__name__)

KEY_PREFIX = "futureagi:omega-rl:v1:"
# What a refused request tells the caller to wait while the store is down: about a Redis
# reconnect plus one command deadline, the gateway's own figure (ratelimit.mjs).
STORE_OUTAGE_RETRY_SECONDS = 2
_PERIODS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_RATE = re.compile(r"\s*([1-9][0-9]{0,8})\s*/\s*([smhd])[a-z]*\s*\Z")

# KEYS: one sorted set per window. ARGV[1]: this attempt's member; then limit and window (ms)
# for each key. Returns {1, 0, 0} when admitted, else {0, wait_ms, index of the first refusal}.
_WINDOW_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local wait, refused = 0, 0
for i, key in ipairs(KEYS) do
  local limit, window = tonumber(ARGV[2 * i]), tonumber(ARGV[2 * i + 1])
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
  if redis.call('ZCARD', key) >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local left = window
    if oldest[2] then left = tonumber(oldest[2]) + window - now end
    if left > wait then wait = left end
    if refused == 0 then refused = i end
  end
end
if refused > 0 then return {0, wait, refused} end
for i, key in ipairs(KEYS) do
  redis.call('ZADD', key, now, ARGV[1])
  redis.call('PEXPIRE', key, tonumber(ARGV[2 * i + 1]))
end
return {1, 0, 0}
"""


class RateLimitUnavailable(APIException):
    """The shared limit store failed, so the attempt is refused rather than admitted unlimited."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Rate limiting is temporarily unavailable. Try again shortly."
    default_code = "service_unavailable"
    # DRF's exception handler turns `wait` into a Retry-After header.
    wait = STORE_OUTAGE_RETRY_SECONDS


@dataclass(frozen=True)
class Window:
    """``limit`` attempts in any ``seconds`` span, counted in the sorted set named ``key``."""

    key: str
    limit: int
    seconds: int


@dataclass(frozen=True)
class Decision:
    allowed: bool
    wait_seconds: int = 0
    refused_by: int | None = None  # index into the windows passed to `take`


def parse_rate(rate: str) -> tuple[int, int]:
    """``"1800/min"`` -> ``(1800, 60)``; a malformed rate is a configuration error, never "no limit"."""
    match = _RATE.match(rate or "")
    if not match:
        raise ImproperlyConfigured(
            f"Invalid Omega rate {rate!r}; expected e.g. 120/min"
        )
    return int(match.group(1)), _PERIODS[match.group(2)]


def window_key(*parts: str) -> str:
    """A store key. Callers pass hashes, never a key id, secret or address as given."""
    return KEY_PREFIX + ":".join(parts)


def _redis_client():
    """The default cache's Redis client, or None when the default cache is not django_redis."""
    cache = caches["default"]
    if not type(cache).__module__.startswith("django_redis"):
        return None
    from django_redis import get_redis_connection

    return get_redis_connection("default")


def _take_redis(client, windows) -> Decision:
    args = [uuid.uuid4().hex]
    for window in windows:
        args += [window.limit, window.seconds * 1000]
    try:
        script = client.register_script(_WINDOW_LUA)
        allowed, wait_ms, refused = script(keys=[w.key for w in windows], args=args)
    except Exception as exc:  # any Redis failure: refuse, never admit unlimited
        logger.warning("omega_rate_window_store_unavailable", error=type(exc).__name__)
        raise RateLimitUnavailable() from exc
    if int(allowed) == 1:
        return Decision(True)
    return Decision(False, max(1, math.ceil(int(wait_ms) / 1000)), int(refused) - 1)


_fallback_lock = threading.Lock()


def _take_locked(windows) -> Decision:
    """The same decision over a non-Redis cache, serialised within this process."""
    cache = caches["default"]
    now = time.time()
    with _fallback_lock:
        kept = []
        wait, refused = 0.0, None
        for index, window in enumerate(windows):
            times = [
                t for t in (cache.get(window.key) or []) if t > now - window.seconds
            ]
            kept.append(times)
            if len(times) >= window.limit:
                wait = max(wait, min(times) + window.seconds - now)
                if refused is None:
                    refused = index
        if refused is not None:
            return Decision(False, max(1, math.ceil(wait)), refused)
        for window, times in zip(windows, kept):
            cache.set(window.key, [*times, now], timeout=window.seconds)
        return Decision(True)


def take(windows) -> Decision:
    """Admit one attempt against every window in ``windows``, or refuse it and record nothing."""
    windows = list(windows)
    if not windows:
        return Decision(True)
    client = _redis_client()
    if client is None:
        return _take_locked(windows)
    return _take_redis(client, windows)


class AtomicRateThrottle(BaseThrottle):
    """A DRF throttle over `take`. Subclasses return the windows a request draws on, or None."""

    def __init__(self):
        self._wait = None
        self.refused_by = None

    def windows(self, request, view):
        raise NotImplementedError

    def allow_request(self, request, view):
        windows = self.windows(request, view)
        if not windows:
            return True
        decision = take(windows)
        if decision.allowed:
            return True
        self._wait = decision.wait_seconds
        self.refused_by = decision.refused_by
        return False

    def wait(self):
        return self._wait
