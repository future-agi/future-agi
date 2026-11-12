from datetime import timedelta
from urllib.parse import parse_qs, urlencode

import structlog
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenOrigin,
    AuthTokenType,
)
from accounts.saml_credential import validate_saml_credential
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.utils import timezone
from tfc.middleware.ws_session_guard import SessionGuard

from accounts.services.handoff_exchange import KIND_WS, HandoffRefused, redeem

logger = structlog.get_logger(__name__)
User = get_user_model()


class JWTAuthMiddleware:
    """Authenticate sockets with a one-time, session-bound ticket, never a bearer URL."""

    def __init__(self, inner):
        self.inner = inner

    async def close_connection(self, send, code, reason):
        # Keep the existing protocol close code for clients handling auth errors.
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": code, "reason": reason})

    async def __call__(self, scope, receive, send):
        try:
            query = parse_qs(
                scope.get("query_string", b"").decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=16,
            )
            # Includes blank, duplicated and percent-encoded legacy bearer keys.
            # No header/cookie fallback: a connect without exactly one ticket fails.
            if (
                set(query) != {"ticket"}
                or len(query["ticket"]) != 1
                or not query["ticket"][0]
            ):
                raise ValueError("Invalid socket query.")
        except (UnicodeError, ValueError):
            await self.close_connection(send, 4003, "Invalid ticket provided")
            return

        result = await self.get_user_from_ticket(query["ticket"][0], scope.get("path"))
        if result is None:
            await self.close_connection(send, 4003, "Invalid ticket provided")
            return
        user, auth_scope, session_id, parameters = result
        scope = dict(scope)
        scope["user"] = user
        scope["auth_scope"] = auth_scope
        scope["auth_token_id"] = getattr(auth_scope, "token_id", None)
        # Consumers keep their existing workspace/test query contract, but these
        # parameters came from the authenticated ticket POST, not the socket URL.
        scope["query_string"] = urlencode(parameters).encode("utf-8")
        return await SessionGuard(session_id, user.id).run(
            self.inner, scope, receive, send
        )

    @database_sync_to_async
    def get_user_from_ticket(self, ticket, path):
        try:
            close_old_connections()
            payload = redeem(ticket, KIND_WS)
            if payload["path"] != path:
                raise HandoffRefused()
            session_id, user_id = payload["session_id"], payload["user_id"]
            # Authoritative rows, not a token-cache hit: a ticket cannot revive a
            # revoked session or a deactivated user during its 60-second lifetime.
            row = (
                AuthToken.objects.filter(id=session_id)
                .values(
                    "id",
                    "user_id",
                    "auth_type",
                    "is_active",
                    "last_used_at",
                    "auth_origin",
                    "scoped_organization_id",
                    "origin_idp_id",
                    "origin_idp_generation",
                )
                .first()
            )
            now = timezone.now()
            threshold = now - timedelta(minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES)
            if (
                row is None
                or str(row["user_id"]) != user_id
                or row["auth_type"] != AuthTokenType.ACCESS.value
                or not row["is_active"]
                or row["last_used_at"] is None
                or row["last_used_at"] < threshold
            ):
                raise HandoffRefused()
            auth_scope = None
            if row["auth_origin"] == AuthTokenOrigin.SAML:
                auth_scope = validate_saml_credential(
                    row, user_id, now=now, transport="websocket"
                )
            user = User.objects.select_related("organization").get(
                id=user_id, is_active=True
            )
            if (
                AuthToken.objects.filter(
                    id=session_id,
                    user_id=user_id,
                    user__is_active=True,
                    is_active=True,
                    auth_type=AuthTokenType.ACCESS.value,
                    last_used_at__gte=threshold,
                ).update(last_used_at=now)
                != 1
            ):
                raise HandoffRefused()
            return user, auth_scope, session_id, payload["query"]
        except Exception as exc:
            logger.warning("websocket_auth_failed", error_type=type(exc).__name__)
            return None
        finally:
            close_old_connections()
