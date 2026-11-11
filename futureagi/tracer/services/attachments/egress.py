"""Per-principal playback egress limit, shared by every gateway worker.

A fixed one-second window per viewer in the shared cache (Redis in every
deployment). Bytes are counted before they are sent. A cache failure denies,
because an unenforceable limit must not become an unlimited one.
"""

import time

import structlog
from django.core.cache import cache

from tracer.services.attachments.bootstrap import LIMITS

logger = structlog.get_logger(__name__)

WINDOW_SECONDS = 1


class EgressDenied(Exception):
    def __init__(self, retry_after=WINDOW_SECONDS):
        super().__init__("egress limit")
        self.retry_after = retry_after


def _key(principal, window):
    return f"fi:attachments:egress:{principal}:{window}"


def consume(principal, nbytes, *, limit=None):
    """Charge ``nbytes`` to this second's budget or raise ``EgressDenied``."""
    limit = LIMITS["playback_bytes_per_second"] if limit is None else limit
    if nbytes < 0 or nbytes > limit:
        raise EgressDenied()
    window = int(time.time() // WINDOW_SECONDS)
    key = _key(principal, window)
    try:
        cache.add(key, 0, timeout=WINDOW_SECONDS * 2)
        used = cache.incr(key, nbytes)
    except Exception:
        logger.warning("attachment_egress_limiter_unavailable")
        raise EgressDenied() from None
    if used > limit:
        raise EgressDenied()
    return used


def admit(principal, *, limit=None):
    """Zero-byte admission check before any response headers are sent."""
    limit = LIMITS["playback_bytes_per_second"] if limit is None else limit
    window = int(time.time() // WINDOW_SECONDS)
    try:
        used = cache.get(_key(principal, window), 0)
    except Exception:
        logger.warning("attachment_egress_limiter_unavailable")
        raise EgressDenied() from None
    if not isinstance(used, int) or used >= limit:
        raise EgressDenied()
