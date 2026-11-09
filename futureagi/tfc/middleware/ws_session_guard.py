"""End an open WebSocket when its session ends (READINESS lane 3.22).

``JWTAuthMiddleware`` checks the token once, at connect. A socket opened before its user was
deactivated (or its access token revoked) kept serving: no consumer checks the user again.
This guard wraps every authenticated socket, whichever consumer serves it, and re-checks the
session on a fixed interval with one indexed read (the access token row, still active, of a
still-active user). When the check fails it closes the socket with ``WS_CLOSE_SESSION_ENDED``
and hands the consumer a disconnect, so its group subscriptions are dropped as on any close.

The check reads the database, not the token cache, so a bulk deactivation (no signal, no cache
eviction) still ends the socket within one interval. A check that cannot reach the database
keeps the socket open and tries again next interval: an outage must not drop every socket.

Granian, the server the API runs on, holds a socket's close back while a ``receive()`` is
pending (measured on granian 2.8.3: the close went out only once the pending read was
cancelled), and the guard always has a read pending. So every close, the guard's or the
consumer's own, first cancels the pending read, then is sent, and the consumer is handed the
disconnect the server would have delivered.
"""

import asyncio

import structlog
from channels.db import database_sync_to_async
from django.conf import settings

logger = structlog.get_logger(__name__)

WS_CLOSE_SESSION_ENDED = 4401
DEFAULT_RECHECK_SECONDS = 30


def recheck_seconds():
    value = getattr(settings, "WS_SESSION_RECHECK_SECONDS", DEFAULT_RECHECK_SECONDS)
    return (
        value
        if isinstance(value, (int, float)) and value > 0
        else DEFAULT_RECHECK_SECONDS
    )


def session_token_id(token):
    """The access token row id inside an encrypted access token, or None."""
    from accounts.authentication import decrypt_message

    try:
        return decrypt_message(token).get("id")
    except Exception:
        return None


def session_is_live(token_id, user_id):
    """True while the access token row is active and its user is active.

    Called through ``database_sync_to_async``, which closes stale connections around it.
    """
    from accounts.models.auth_token import AuthToken, AuthTokenType

    return AuthToken.objects.filter(
        id=token_id,
        user_id=user_id,
        auth_type=AuthTokenType.ACCESS.value,
        is_active=True,
        user__is_active=True,
    ).exists()


class SessionGuard:
    """Runs one socket's app with a periodic session check beside it."""

    def __init__(self, token_id, user_id, interval=None, is_live=None):
        self.token_id = token_id
        self.user_id = user_id
        self.interval = interval if interval is not None else recheck_seconds()
        self.is_live = is_live or database_sync_to_async(session_is_live)
        self.closed = False
        self.ended = False

    async def run(self, app, scope, receive, send):
        inbox = asyncio.Queue()
        reader = {}
        disconnected = asyncio.Event()

        async def pump():
            while True:
                event = await receive()
                if event.get("type") == "websocket.disconnect":
                    disconnected.set()
                await inbox.put(event)
                if disconnected.is_set():
                    return

        async def close(message):
            """Send a close (the guard's or the consumer's) so that it reaches the client."""
            self.closed = True
            pending = reader.get("task")
            stopped = False
            if not disconnected.is_set() and pending is not None and not pending.done():
                pending.cancel()
                await asyncio.wait({pending}, timeout=1)
                stopped = not disconnected.is_set()
            await send(message)
            if stopped:
                # The cancelled read would have delivered this once the client answered the close.
                await inbox.put(
                    {"type": "websocket.disconnect", "code": message.get("code", 1000)}
                )

        async def guarded_send(message):
            if self.closed:
                return  # The socket is closed; the consumer's late frames go nowhere.
            if message.get("type") == "websocket.close":
                await close(message)
                return
            await send(message)

        async def watch():
            while not self.closed:
                await asyncio.sleep(self.interval)
                if self.closed:
                    return
                try:
                    live = await self.is_live(self.token_id, self.user_id)
                except Exception as error:  # noqa: BLE001 - an outage keeps the socket.
                    logger.warning(
                        "ws_session_recheck_failed", error_type=type(error).__name__
                    )
                    continue
                if live:
                    continue
                self.ended = True
                logger.info("ws_session_ended", user_id=str(self.user_id))
                await guarded_send(
                    {"type": "websocket.close", "code": WS_CLOSE_SESSION_ENDED}
                )
                return

        reader["task"] = asyncio.ensure_future(pump())
        tasks = [reader["task"], asyncio.ensure_future(watch())]
        try:
            return await app(scope, inbox.get, guarded_send)
        finally:
            for task in tasks:
                task.cancel()
            # Bounded: a server's read that ignored the cancel must not keep the app from returning.
            await asyncio.wait(tasks, timeout=1)
