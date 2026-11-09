from urllib.parse import parse_qs

import structlog
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection

from accounts.authentication import decode_token
from tfc.middleware.ws_session_guard import SessionGuard, session_token_id

logger = structlog.get_logger(__name__)

User = get_user_model()


class JWTAuthMiddleware:
    """Middleware to authenticate WebSocket connections using JWT from query parameters."""

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
        query_string = parse_qs(scope["query_string"].decode())
        token = query_string.get("token", [None])[0]

        if token:
            auth_result = await self.get_user_from_token(token)
            if auth_result is None:
                await self.close_connection(send, 4003, "Invalid token provided")
                return

            user, auth_scope, _token = auth_result
            scope["user"] = user
            scope["auth_scope"] = auth_scope
            scope["auth_token_id"] = getattr(auth_scope, "token_id", None)
        else:
            await self.close_connection(send, 4003, "Invalid token provided")
            return

        # READINESS lane 3.22: the token is checked at connect only, so the socket gets a guard
        # that ends it when the session ends (user deactivated or token revoked) while it is open.
        token_id = session_token_id(token)
        if token_id is None:
            await self.close_connection(send, 4003, "Invalid token provided")
            return
        return await SessionGuard(token_id, user.id).run(
            self.inner, scope, receive, send
        )

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
