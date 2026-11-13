import json
import time
from collections import deque

import structlog
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from sockets.membership import MembershipSocketGuard, user_socket_group
from sockets.org_scope import resolve_socket_organization
from sockets.saml_guard import SamlSocketGuard
from sockets.targets import normalize_socket_target

logger = structlog.get_logger(__name__)

# Subscription frames that arrive while a credential or membership check is
# pending are kept, in order, and answered once a check succeeds.
SUBSCRIPTION_FRAME_TYPES = frozenset({"subscribe", "unsubscribe"})
MAX_PENDING_SUBSCRIPTION_FRAMES = 16


class DataConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_subscription = None
        self.user = None
        self.organization_id = None
        self.user_group_name = None
        self._guard = SamlSocketGuard(self)
        self._membership = MembershipSocketGuard(self)
        self._pending_subscription_frames = deque(
            maxlen=MAX_PENDING_SUBSCRIPTION_FRAMES
        )

    async def connect(self):
        self.user = self.scope["user"]

        if not await self._guard.ensure_live(inbound=True):
            return

        try:
            resolved_at = time.monotonic()
            # Get room group name asynchronously
            self.room_group_name = await self.get_room_group_name()

            if self.room_group_name is None:
                logger.warning(
                    "websocket_connect_failed",
                    reason="user_has_no_organization",
                    user_id=str(self.user.id) if self.user else None,
                )
                await self.close(code=4002)
                return

            # The per-user group lets membership changes close this socket.
            self.user_group_name = user_socket_group(self.user.id)
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
            await self.channel_layer.group_add(self.room_group_name, self.channel_name)
            self._membership.mark_validated(resolved_at)
            await self.accept()

        except Exception as e:
            logger.error(
                "websocket_connect_error",
                reason=str(e),
                error_type=type(e).__name__,
                user_id=str(self.user.id) if self.user else None,
            )
            await self.close(code=4000)

    async def receive(self, text_data=None, bytes_data=None):
        """Guard raw frames so subclasses cannot skip SAML or membership
        revalidation before answering a client request."""

        if not await self._may_answer():
            if not self._socket_closed():
                self._keep_subscription_frame(text_data)
            return
        await self._answer_pending_subscription_frames()
        await super().receive(text_data=text_data, bytes_data=bytes_data)

    async def _may_answer(self) -> bool:
        if self._membership.closed:
            return False
        if not await self._guard.ensure_live(inbound=True):
            return False
        return await self._membership.ensure_member()

    def _socket_closed(self) -> bool:
        return self._guard.revoked or self._guard.closed or self._membership.closed

    def _keep_subscription_frame(self, text_data) -> None:
        """Keep a subscription frame that arrived while a check was pending.

        A later subscription replaces an earlier one, so when the queue is
        full the oldest frame is dropped.
        """

        if not text_data:
            return
        try:
            content = json.loads(text_data)
        except ValueError:
            return
        if not isinstance(content, dict):
            return
        if content.get("type") in SUBSCRIPTION_FRAME_TYPES:
            self._pending_subscription_frames.append(content)

    async def _answer_pending_subscription_frames(self) -> None:
        """Answer kept subscription frames; call only after a check succeeded."""

        while self._pending_subscription_frames:
            content = self._pending_subscription_frames.popleft()
            await self.receive_json(content)

    async def receive_json(self, content):
        """
        Handle subscription/unsubscription requests and other messages
        """
        try:
            message_type = content.get("type")

            if message_type == "subscribe":
                await self.handle_subscribe(content)
            elif message_type == "unsubscribe":
                await self.handle_unsubscribe(content)
            else:
                await self.handle_message(content)

        except Exception as e:
            logger.exception(
                f"websocket: Error handling message from user {self.user.id}: {str(e)}"
            )

            await self.send_json({"type": "error", "message": str(e)})

    async def handle_subscribe(self, content):
        uuid = content.get("uuid")
        if not uuid:
            logger.error(
                f"websocket: User {self.user.id} attempted to subscribe without providing UUID"
            )

            await self.send_json(
                {"type": "error", "message": "UUID is required for subscription"}
            )
            return

        # Automatically unsubscribe from previous subscription
        if self.current_subscription:
            await self.channel_layer.group_discard(
                self.current_subscription, self.channel_name
            )

        # Subscribe to new channel
        channel_name = self._uuid_group_name(uuid)

        await self.channel_layer.group_add(channel_name, self.channel_name)
        self.current_subscription = channel_name

        await self.send_json(
            {
                "type": "subscription_confirmed",
                "uuid": uuid,
                "current_subscription": self.current_subscription,
            }
        )

    async def handle_unsubscribe(self, content):
        uuid = content.get("uuid")
        if not uuid:
            logger.error(
                f"websocket: User {self.user.id} attempted to unsubscribe without providing UUID"
            )

            await self.send_json(
                {"type": "error", "message": "UUID is required for unsubscription"}
            )
            return

        channel_name = self._uuid_group_name(uuid)
        if channel_name == self.current_subscription:
            await self.channel_layer.group_discard(channel_name, self.channel_name)
            self.current_subscription = None

            await self.send_json(
                {
                    "type": "unsubscription_confirmed",
                    "uuid": uuid,
                    "current_subscription": None,
                }
            )

        else:
            await self.send_json(
                {"type": "info", "message": f"Not subscribed to {uuid}"}
            )

    async def handle_message(self, content):
        # Clients only keep the connection alive; stream frames come from the
        # server-side relay, never from other clients.
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})
            return
        await self.send_json({"type": "error", "message": "Unsupported message type."})

    async def disconnect(self, close_code):
        if self.user_group_name:
            await self.channel_layer.group_discard(
                self.user_group_name, self.channel_name
            )

        # Cleanup base group
        if getattr(self, "room_group_name", None):
            await self.channel_layer.group_discard(
                self.room_group_name, self.channel_name
            )

        # Cleanup current subscription
        if self.current_subscription:
            await self.channel_layer.group_discard(
                self.current_subscription, self.channel_name
            )

    async def send_data(self, event):
        """
        Send data to the WebSocket, checking if we're subscribed to the channel
        """
        data = event.get("data", {})
        uuid = data.get("uuid")

        if not await self._guard.ensure_live(inbound=False):
            return
        if self._pending_subscription_frames:
            # A kept subscription may be for this frame's target.
            if not await self._membership.ensure_member():
                return
            await self._answer_pending_subscription_frames()
        if uuid and self._uuid_group_name(uuid) != self.current_subscription:
            return
        if not await self._membership.ensure_member():
            return
        try:
            await self.send_json(data)
        except Exception:
            logger.exception("websocket_send_failed")

    async def membership_changed(self, event):
        """Revalidate now; one of this user's memberships has changed."""

        organization_id = event.get("organization_id")
        if organization_id is not None and organization_id != self.organization_id:
            return
        # A SAML socket must not reuse its cached credential check either.
        self._guard.expire()
        await self._membership.ensure_member(force=True)

    def _uuid_group_name(self, uuid):
        try:
            uuid = normalize_socket_target(uuid)
        except ValueError:
            # Preserve non-UUID subscriptions used by older socket clients.
            pass
        auth_scope = self.scope.get("auth_scope")
        if auth_scope is not None:
            return f"uuid_org_{auth_scope.org_id}_{uuid}"
        # Non-SAML sessions use the same resolved tenant as their broadcast room.
        return f"uuid_{self.room_group_name}_{uuid}"

    async def get_room_group_name(self):
        org_id = await resolve_socket_organization(self.scope, self.user)
        if org_id is None:
            return None
        self.organization_id = str(org_id)
        return f"org_{org_id}"
