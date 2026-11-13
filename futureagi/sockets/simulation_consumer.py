import asyncio
import json
import time
from urllib.parse import parse_qs

import redis.asyncio as aioredis
import structlog
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from sockets.membership import MembershipSocketGuard, user_socket_group
from sockets.org_scope import resolve_socket_organization
from sockets.saml_guard import SamlSocketGuard

logger = structlog.get_logger(__name__)


class SimulationUpdateConsumer(AsyncJsonWebsocketConsumer):
    """
    Dedicated WebSocket consumer for simulation grid updates.

    Frontend connects with test_id query param. The consumer verifies the
    user's organization, subscribes to the org-scoped Redis pub/sub channel,
    and forwards messages so the simulation runs grid refreshes in real-time.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._subscriber_task = None
        self._redis = None
        self._pubsub = None
        self.test_id = None
        self.user = None
        self.organization_id = None
        self.user_group_name = None
        self._guard = SamlSocketGuard(self)
        self._membership = MembershipSocketGuard(self)

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return
        if not await self._guard.ensure_live(inbound=True):
            return
        self.user = user

        # Verify user has an organization (prevents cross-org data leaks)
        resolved_at = time.monotonic()
        org_id = await self._get_organization_id()
        if org_id is None:
            await self.close(code=4002)
            return
        self.organization_id = str(org_id)

        # Parse query string using parse_qs for proper URL-decoding
        params = parse_qs(self.scope.get("query_string", b"").decode())
        self.test_id = params.get("test_id", [None])[0]

        if not self.test_id:
            await self.close(code=4003)
            return

        # The per-user group lets membership changes close this socket.
        self.user_group_name = user_socket_group(user.id)
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        self._membership.mark_validated(resolved_at)
        await self.accept()
        logger.info(
            "simulation_ws_connected",
            test_id=self.test_id,
            organization_id=self.organization_id,
        )
        self._subscriber_task = asyncio.create_task(self._subscribe_redis())

    async def _get_organization_id(self):
        return await resolve_socket_organization(self.scope, self.scope.get("user"))

    async def membership_changed(self, event):
        """Revalidate now; one of this user's memberships has changed."""

        organization_id = event.get("organization_id")
        if organization_id is not None and organization_id != self.organization_id:
            return
        self._guard.expire()
        if not await self._membership.ensure_member(force=True):
            if self._membership.closed:
                await self._stop_subscriber()

    async def disconnect(self, close_code):
        self._guard.stop()
        logger.info(
            "simulation_ws_disconnected",
            test_id=self.test_id,
            close_code=close_code,
        )
        try:
            if self.user_group_name:
                await self.channel_layer.group_discard(
                    self.user_group_name, self.channel_name
                )
        finally:
            await self._stop_subscriber()
            if self._pubsub:
                await self._pubsub.unsubscribe()
                await self._pubsub.close()
            if self._redis:
                await self._redis.close()

    async def _stop_subscriber(self):
        """Stop forwarding simulation updates to this socket."""

        task = self._subscriber_task
        if task is None:
            return
        self._subscriber_task = None
        if task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _subscribe_redis(self):
        channel = f"simulation_updates:{self.organization_id}:{self.test_id}"
        try:
            self._redis = aioredis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(channel)
            logger.info("simulation_ws_subscribed", channel=channel)

            async for message in self._pubsub.listen():
                if message["type"] == "message":
                    if not await self._guard.ensure_live(inbound=False):
                        if self._guard.revoked or self._guard.closed:
                            return
                        # Deliver nothing until a credential check succeeds.
                        continue
                    if not await self._membership.ensure_member():
                        if self._membership.closed:
                            return
                        # Deliver nothing until a membership check succeeds.
                        continue
                    try:
                        data = json.loads(message["data"])
                        await self.send_json(data)
                    except Exception:
                        logger.exception("Error forwarding Redis message to WebSocket")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Redis subscription error", channel=channel)
            try:
                await self.send_json(
                    {"type": "error", "message": "Subscription failed"}
                )
                await self.close(code=4500)
            except Exception:
                pass
