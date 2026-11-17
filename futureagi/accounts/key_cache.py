"""Invalidate OrgApiKey snapshots without coupling model saves to API views."""

import hashlib
from typing import Any

import redis
import structlog
from django.conf import settings
from redis.backoff import NoBackoff
from redis.retry import Retry

logger = structlog.get_logger(__name__)
FI_AUTH_REVOKE_CHANNEL = "fi:auth:revoke"


def publish_key_revocation(api_key: str, secret_key: str) -> None:
    """Evict this credential pair from collector authentication caches.

    Best effort, and called synchronously after key saves commit (key creation,
    signup, expiry edits, disable and delete), so one attempt bounded by the
    same socket timeouts as the Django Redis cache: an unresponsive Redis
    delays the caller by at most those timeouts instead of indefinitely.
    """
    try:
        cache_key = hashlib.sha256(f"{api_key}:{secret_key}".encode()).hexdigest()
        limits: dict[str, Any] = {
            "socket_connect_timeout": getattr(
                settings, "REDIS_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS", 1.0
            ),
            "socket_timeout": getattr(
                settings, "REDIS_CACHE_SOCKET_TIMEOUT_SECONDS", 1.0
            ),
            "retry": Retry(NoBackoff(), 0),
        }
        client = redis.Redis.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            **limits,
        )
        # Options in the URL query (?socket_timeout=...) replace keyword
        # arguments when redis-py parses the URL; apply the limits after that.
        client.connection_pool.connection_kwargs.update(limits)
        with client as conn:
            conn.publish(FI_AUTH_REVOKE_CHANNEL, cache_key)
    except Exception:
        logger.warning("fi_auth_revoke_publish_failed", exc_info=True)
