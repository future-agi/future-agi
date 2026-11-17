"""Tell fi-collector a key was revoked, so it drops the key at once (READINESS L03).

fi-collector caches a verified key for its warm TTL (1 h). Every path that
disables or deletes an ``OrgApiKey`` publishes here, after saving: the Keys page
(disable and delete, ``accounts/views/keys.py``) and the AI tool
``revoke_api_key``. The payload names the key by its public id, never by its
secret (``accounts.api_secrets.revocation_payloads``).
"""

from __future__ import annotations

import redis
import structlog
from django.conf import settings

from accounts.api_key_lookup import key_revocation_payloads

logger = structlog.get_logger(__name__)

FI_AUTH_REVOKE_CHANNEL = "fi:auth:revoke"


def publish_key_revocation(org_key, *, strict=False) -> None:
    """Publish invalidation; deployment replay can require confirmed delivery."""
    try:
        client = redis.Redis.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        for payload in key_revocation_payloads(org_key):
            client.publish(FI_AUTH_REVOKE_CHANNEL, payload)
    except Exception:
        logger.warning("fi_auth_revoke_publish_failed", exc_info=True)
        if strict:
            raise
