from urllib.parse import parse_qs, unquote

import structlog
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection

from accounts.authentication import decode_token

logger = structlog.get_logger(__name__)

User = get_user_model()

AUTH_PROTOCOL = "fi.auth.v1"
AUTH_PATHS = {"/ws/connect/", "/ws/falcon-ai/"}


class JWTAuthMiddleware:
    """Authenticate WebSocket connections before dispatching to a consumer."""

    def __init__(self, inner):
        self.inner = inner

    async def close_connection(self, send, code, reason):
        """Properly close a WebSocket connection with custom code."""
        # First, send a proper WebSocket accept to establish the connection
        await send(
            {
                "type": "websocket.accept",
            }
        )

        # Then, send a proper close frame with the custom code
        await send(
            {
                "type": "websocket.close",
                "code": code,
                "reason": reason,
            }
        )

    async def __call__(self, scope, receive, send):
        protocols = scope.get("subprotocols", [])
        path = scope["path"].removeprefix(scope.get("root_path", ""))
        reply = send
        if path in AUTH_PATHS and AUTH_PROTOCOL in protocols:
            # Browsers require a selected protocol even to receive a 4003 close.
            async def select_protocol(message):
                if message["type"] == "websocket.accept":
                    message = {**message, "subprotocol": AUTH_PROTOCOL}
                await send(message)

            reply = select_protocol
            token = None
            if (
                len(protocols) == 2
                and protocols[0] == AUTH_PROTOCOL
                and protocols[1].startswith("bearer.")
            ):
                token = unquote(protocols[1].removeprefix("bearer."))
        else:
            # Older clients and routes not yet migrated use the original handshake.
            token = parse_qs(scope["query_string"].decode()).get("token", [""])[0]

        auth_result = await self.get_user_from_token(token) if token else None
        if auth_result is None:
            await self.close_connection(reply, 4003, "Invalid token provided")
            return

        user, auth_scope, _token = auth_result
        scope["user"] = user
        scope["auth_scope"] = auth_scope
        scope["auth_token_id"] = getattr(auth_scope, "token_id", None)
        return await self.inner(scope, receive, reply)

    @database_sync_to_async
    def get_user_from_token(self, token):
        """Validates JWT token and retrieves user"""
        try:
            close_old_connections()
            with connection.cursor():
                try:
                    user, token, auth_scope = decode_token(token, transport="websocket")
                    return user, auth_scope, token
                except Exception as e:
                    logger.warning(
                        "websocket_auth_failed",
                        error_type=type(e).__name__,
                    )
                    return None

        except Exception as e:
            logger.error(
                "websocket_auth_error",
                error_type=type(e).__name__,
            )
            return None
        finally:
            # Always ensure connections are closed
            close_old_connections()
