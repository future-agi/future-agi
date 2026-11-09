"""Invalidate OrgApiKey snapshots without coupling model saves to API views."""

import hashlib

import redis
import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)
FI_AUTH_REVOKE_CHANNEL = "fi:auth:revoke"


def publish_key_revocation(api_key: str, secret_key: str) -> None:
    """Evict this credential pair from collector authentication caches."""
    try:
        cache_key = hashlib.sha256(f"{api_key}:{secret_key}".encode()).hexdigest()
        with redis.Redis.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        ) as client:
            client.publish(FI_AUTH_REVOKE_CHANNEL, cache_key)
    except Exception:
        logger.warning("fi_auth_revoke_publish_failed", exc_info=True)
