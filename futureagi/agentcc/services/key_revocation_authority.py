"""Fail-closed reads of the gateway's durable credential and legacy-ID sets."""

import os
import re
from contextlib import contextmanager

import redis
from django.conf import settings

from agentcc.hosted import validate_billing_redis_database

LEGACY_REVOCATIONS = "agentcc:key:revoked"
CREDENTIAL_REVOCATIONS = "agentcc:key:revoked:sha256:v1"
REVOCATION_CHANNEL = "agentcc:key:revoke"
UNREACHABLE = (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)


class RevocationAuthorityUnavailable(RuntimeError):
    """Fail closed. The message may name the failed check and an exception
    class; it never carries a URL, host, password, member or hash."""

    def __init__(self, reason=None):
        message = "historical revocation authority unavailable"
        super().__init__(f"{message}: {reason}" if reason else message)


def failure_reason(step, exc):
    """A fixed step text and the exception's class, never its message."""
    kind = type(exc)
    return f"{step} ({kind.__module__}.{kind.__qualname__})"


@contextmanager
def revocation_authority():
    """Use the same primary/database as gateway Redis, never a cache replica.

    The existing hosted DB declaration is validated when present. The deployment
    must bind REDIS_URL to the gateway authority, not a different Django cache.
    There is deliberately no empty-URL/local fallback and no allow-on-error.
    """
    client = None
    step = "REDIS_URL database must match AGENTCC_REDIS_DB"
    try:
        validate_billing_redis_database(os.environ)
        url = getattr(settings, "REDIS_URL", "")
        if not url:
            raise RevocationAuthorityUnavailable("REDIS_URL is not set")
        step = "Redis client setup failed"
        client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            retry_on_timeout=False,
        )
        step = "a Redis or database call failed"
        yield client
    except RevocationAuthorityUnavailable:
        raise
    except Exception as exc:
        # Redis exceptions may contain connection credentials or members: keep
        # only the step and the exception class.
        if isinstance(exc, UNREACHABLE):
            step = "the gateway's Redis could not be reached"
        raise RevocationAuthorityUnavailable(failure_reason(step, exc)) from None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def credential_is_revoked(key_id, key_hash):
    """Legacy negatives remain authoritative even if a hash cannot be recovered."""
    if not key_id:
        raise RevocationAuthorityUnavailable()
    with revocation_authority() as client:
        if client.sismember(LEGACY_REVOCATIONS, key_id):
            return True
        if not key_hash:
            return False
        if not re.fullmatch(r"[0-9a-fA-F]{64}", key_hash):
            raise RevocationAuthorityUnavailable()
        return bool(client.sismember(CREDENTIAL_REVOCATIONS, key_hash.lower()))
