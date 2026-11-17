"""Body-only AWS form capabilities, separate from single-use redirect codes."""

import hashlib
import re
import secrets
import time
from contextlib import contextmanager

from django.core.cache import cache

from accounts.services.handoff_exchange import HandoffRefused

ATTEMPT_TTL_SECONDS = 15 * 60
_ATTEMPT_SHAPE = re.compile(r"aws1_[A-Za-z0-9_-]{43}")
_PREFIX = "aws_onboarding_attempt:"


class OnboardingAttemptBusy(Exception):
    """Another submission is still processing this form."""


def issue_attempt(onboarding_token):
    attempt = f"aws1_{secrets.token_urlsafe(32)}"
    record = {
        "onboarding_token": onboarding_token,
        "expires_at": time.time() + ATTEMPT_TTL_SECONDS,
    }
    if not cache.add(_record_key(attempt), record, timeout=ATTEMPT_TTL_SECONDS):
        raise HandoffRefused()
    return attempt


def _record_key(attempt):
    if not isinstance(attempt, str) or not _ATTEMPT_SHAPE.fullmatch(attempt):
        raise HandoffRefused()
    return f"{_PREFIX}{hashlib.sha256(attempt.encode()).hexdigest()}"


def _live_record(key):
    record = cache.get(key)
    if not record or record.get("expires_at", 0) <= time.time():
        raise HandoffRefused()
    return record


@contextmanager
def use_attempt(attempt):
    """Serialize submissions, spend on success, retain on correctable failure.

    The underlying purchase session must still be checked by the caller. Its
    original cache entry is never refreshed, so exchanging cannot extend it.
    The lock outlives the attempt's remaining lifetime; a delayed contender
    rechecks the record under the lock before any business side effects.
    """
    key = _record_key(attempt)
    _live_record(key)
    lock_key = f"{key}:processing"
    if not cache.add(lock_key, True, timeout=ATTEMPT_TTL_SECONDS):
        raise OnboardingAttemptBusy()
    try:
        record = _live_record(key)
        yield record["onboarding_token"]
        cache.delete(key)
    finally:
        cache.delete(lock_key)
