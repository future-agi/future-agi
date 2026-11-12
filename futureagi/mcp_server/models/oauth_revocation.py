"""Durable revocations for self-contained MCP OAuth access tokens."""

import hashlib
from datetime import datetime

from django.db import DEFAULT_DB_ALIAS, models
from django.utils import timezone


class MCPOAuthAccessRevocation(models.Model):
    """Token-scoped authority, independent of cache and workspace context.

    Never store the bearer itself. Rows may be pruned only after expires_at.
    Reads use the primary so replica lag cannot re-admit a revoked bearer.
    """

    token_digest = models.CharField(max_length=64, primary_key=True, serialize=False)
    expires_at = models.DateTimeField(db_index=True)

    @classmethod
    def revoke(cls, token: str) -> None:
        from mcp_server.oauth_utils import decrypt_oauth_token

        payload = decrypt_oauth_token(token)
        if not payload or payload.get("type") != "mcp_oauth":
            # Invalid/expired grants cannot authenticate and need no tombstone.
            return
        cls.objects.using(DEFAULT_DB_ALIAS).get_or_create(
            token_digest=hashlib.sha256(token.encode()).hexdigest(),
            # The SDK metadata rounds to seconds; preserve the encrypted expiry
            # so revocation also covers the final fractional second of validity.
            defaults={"expires_at": datetime.fromisoformat(payload["expires_at"])},
        )

    @classmethod
    def is_revoked(cls, token: str) -> bool:
        # Do not cache negative answers or suppress database errors: neither a
        # stale cache nor an unavailable authority may authorize this bearer.
        return cls.objects.using(DEFAULT_DB_ALIAS).filter(
            token_digest=hashlib.sha256(token.encode()).hexdigest(),
            expires_at__gte=timezone.now(),
        ).exists()
