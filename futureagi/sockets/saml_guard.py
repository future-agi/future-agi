"""Database-authoritative liveness checks for SAML WebSocket sessions."""

from __future__ import annotations

import time

import structlog
from channels.db import database_sync_to_async
from django.conf import settings
from django.utils import timezone

from accounts.models.auth_token import AuthToken
from accounts.saml_credential import SamlCredentialInvalid, validate_saml_credential

security_logger = structlog.get_logger("saml2_auth.security")


class SamlSocketGuard:
    """Revalidate SAML credentials before inbound frames and periodic pushes."""

    def __init__(self, consumer):
        self.consumer = consumer
        self.revoked = False
        self._last_validated_at = 0.0

    async def ensure_live(self, *, inbound: bool) -> bool:
        """Return whether the socket remains usable, closing revoked sessions."""

        auth_scope = self.consumer.scope.get("auth_scope")
        if auth_scope is None:
            return True
        if self.revoked:
            return False

        now_monotonic = time.monotonic()
        interval = getattr(settings, "SAML_SOCKET_REVALIDATE_SECONDS", 30)
        if not inbound and now_monotonic - self._last_validated_at < interval:
            return True

        try:
            await self._validate()
        except SamlCredentialInvalid as exc:
            await self._revoke(exc.reason)
            return False
        except Exception as exc:  # Database failure must not keep SAML data live.
            await self._revoke(type(exc).__name__)
            return False

        self._last_validated_at = now_monotonic
        return True

    @database_sync_to_async
    def _validate(self):
        token_id = self.consumer.scope.get("auth_token_id")
        token_row = (
            AuthToken.objects.filter(id=token_id)
            .values(
                "id",
                "user_id",
                "auth_type",
                "is_active",
                "last_used_at",
                "scoped_organization_id",
                "origin_idp_id",
                "origin_idp_generation",
            )
            .first()
        )
        validate_saml_credential(
            token_row,
            str(self.consumer.scope["user"].id),
            now=timezone.now(),
            transport="websocket",
        )

    async def _revoke(self, reason: str) -> None:
        if self.revoked:
            return
        self.revoked = True
        security_logger.warning(
            "saml_socket_revoked",
            reason=reason,
            transport="websocket",
        )
        await self.consumer.close(code=4001)
