"""Per-actor admission for sampling previews.

A preview runs one bounded ClickHouse read of up to 30 seconds. Any project
member may request one, so each actor gets at most one preview in flight and a
modest number per minute. The guard fails closed: if its cache is unavailable
the preview is refused as unavailable rather than admitted unguarded.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache

_PREFIX = "eval_sampling_preview"
_clock = time.time


class PreviewThrottled(Exception):
    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


class PreviewGuardUnavailable(Exception):
    pass


def _limit(name: str, default: int, *, low: int, high: int) -> int:
    value = getattr(settings, name, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}.")
    return value


@contextmanager
def preview_slot(actor: str) -> Iterator[None]:
    """Admit one preview for ``actor`` or raise ``PreviewThrottled``."""

    concurrent = _limit("EVAL_SAMPLING_PREVIEW_CONCURRENCY", 1, low=1, high=10)
    per_minute = _limit("EVAL_SAMPLING_PREVIEW_PER_MINUTE", 12, low=1, high=600)
    # Longer than the 30-second read budget, so a crashed request's slot
    # expires on its own instead of locking the actor out.
    hold = 45
    token = uuid.uuid4().hex
    slot_key = None
    try:
        for index in range(concurrent):
            key = f"{_PREFIX}:active:{actor}:{index}"
            if cache.add(key, token, timeout=hold):
                slot_key = key
                break
    except Exception as exc:
        raise PreviewGuardUnavailable() from exc
    if slot_key is None:
        raise PreviewThrottled(
            "A sampling preview is already running for you. Retry when it finishes.",
            retry_after=hold,
        )
    try:
        now = _clock()
        bucket = int(now // 60)
        rate_key = f"{_PREFIX}:rate:{actor}:{bucket}"
        try:
            cache.add(rate_key, 0, timeout=120)
            count = cache.incr(rate_key)
        except Exception as exc:
            raise PreviewGuardUnavailable() from exc
        if count > per_minute:
            raise PreviewThrottled(
                f"Sampling previews are limited to {per_minute} per minute. Retry shortly.",
                retry_after=math.ceil(60 - now % 60),
            )
        yield
    finally:
        try:
            if cache.get(slot_key) == token:
                cache.delete(slot_key)
        except Exception:
            # The slot expires after ``hold`` seconds on its own.
            pass
