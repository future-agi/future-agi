import asyncio
import json
import time

import structlog
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from sockets.membership import MembershipSocketGuard, user_socket_group
from sockets.org_scope import resolve_socket_organization
from sockets.saml_guard import SamlSocketGuard
from sockets.targets import normalize_socket_target

logger = structlog.get_logger(__name__)

# Subscription frames that arrive while a credential or membership check is
# pending are kept, in order, and answered once a check succeeds. They are
# coalesced to what can still change the socket's one subscription (see
# DataConsumer._keep_subscription_frame), and at most this many are kept.
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
        self._pending_subscription_frames: list[dict] = []
        # Unsubscribe frames left unanswered because the queue was full.
        self._unanswered_subscription_frames = 0
        # Kept frames are answered from receive(), send_data() and the SAML
        # window check; one at a time, so they are answered in order.
        self._answer_lock = asyncio.Lock()
        # The kept frame being answered now; it is no longer in the queue.
        self._answering_frame: dict | None = None

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
        if self._pending_subscription_frames:
            # A new check started while kept frames were answered; keep this
            # one behind them so frames are still answered in order.
            if not self._socket_closed():
                self._keep_subscription_frame(text_data)
            return
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

        A socket has one subscription, and a subscribe replaces it, so a
        subscribe makes every frame kept before it irrelevant and replaces
        them. Repeated frames for one target keep only the latest. What is
        kept is therefore the latest subscribe plus unsubscribes of distinct
        targets. If those exceed the bound, the oldest unsubscribe of a target
        the socket would not be subscribed to is dropped (its only answer
        would be "Not subscribed"), and the client is told how many went
        unanswered.
        """

        if not text_data:
            return
        if type(self).receive_json is not DataConsumer.receive_json:
            # A subclass answers frames its own way; keep none for it.
            return
        try:
            content = json.loads(text_data)
        except ValueError:
            return
        if not isinstance(content, dict):
            return
        frame_type = content.get("type")
        if not (isinstance(frame_type, str) and frame_type in SUBSCRIPTION_FRAME_TYPES):
            return
        kept = self._pending_subscription_frames
        if frame_type == "subscribe" and content.get("uuid"):
            kept.clear()
            self._unanswered_subscription_frames = 0
        else:
            # Spellings of one target (for example in another letter case)
            # name the same group, so they count as one target.
            key = self._subscription_frame_key(content)
            kept[:] = [
                frame for frame in kept if self._subscription_frame_key(frame) != key
            ]
        kept.append(content)
        while len(kept) > MAX_PENDING_SUBSCRIPTION_FRAMES:
            self._drop_kept_frame()

    def _subscription_frame_key(self, frame: dict) -> tuple:
        target = frame.get("uuid")
        return (frame.get("type"), self._uuid_group_name(target) if target else None)

    def _drop_kept_frame(self) -> None:
        """Drop one kept frame, preferring one whose answer changes nothing."""

        kept = self._pending_subscription_frames
        subscribe = next(
            (frame for frame in kept if frame.get("type") == "subscribe"), None
        )
        if subscribe is not None and subscribe.get("uuid"):
            wanted = {self._uuid_group_name(subscribe["uuid"])}
        else:
            wanted = {self.current_subscription}
            # A subscribe being answered now may not have replaced the
            # current subscription yet; its target is wanted too.
            answering = self._answering_frame
            if answering and answering.get("type") == "subscribe":
                if answering.get("uuid"):
                    wanted.add(self._uuid_group_name(answering["uuid"]))
        unsubscribes = [
            index
            for index, frame in enumerate(kept)
            if frame.get("type") == "unsubscribe" and frame.get("uuid")
        ]
        unneeded = [
            index
            for index in unsubscribes
            if self._subscription_frame_key(kept[index])[1] not in wanted
        ]
        # Coalescing leaves at most one frame per target, so an unneeded
        # unsubscribe always exists once the bound is exceeded; the fallbacks
        # only keep the bound if that ever stops being true.
        candidates = (
            unneeded
            or unsubscribes
            or [index for index, frame in enumerate(kept) if frame is not subscribe]
        )
        del kept[candidates[0]]
        self._unanswered_subscription_frames += 1

    async def _answer_pending_subscription_frames(self) -> None:
        """Answer kept subscription frames; call only after a check succeeded."""

        async with self._answer_lock:
            checks = self._check_generations()
            while self._pending_subscription_frames:
                if self._answering_must_stop(checks):
                    # The rest stays kept until the new check succeeds.
                    return
                content = self._pending_subscription_frames.pop(0)
                self._answering_frame = content
                try:
                    await self.receive_json(content)
                finally:
                    self._answering_frame = None
            unanswered = self._unanswered_subscription_frames
            if unanswered and not self._answering_must_stop(checks):
                self._unanswered_subscription_frames = 0
                await self.send_json(
                    {
                        "type": "info",
                        "message": (
                            f"{unanswered} unsubscribe requests for targets this "
                            "socket was not subscribed to were not answered"
                        ),
                    }
                )

    async def _on_saml_check_recovered(self) -> None:
        """The SAML window check succeeded with no frame to carry the replay:
        answer the kept subscription frames now, so a stream sent to a target
        subscribed while pending is delivered."""

        if self._socket_closed() or not self._pending_subscription_frames:
            return
        if not await self._membership.ensure_member():
            return
        await self._answer_pending_subscription_frames()

    def _check_generations(self) -> tuple[int, int]:
        return (self._guard.generation, self._membership.generation)

    def _answering_must_stop(self, checks: tuple[int, int]) -> bool:
        """Whether kept frames may no longer be answered: the socket closed,
        or a check started after answering began and has not succeeded."""

        return (
            self._socket_closed()
            or self._guard.pending
            or self._membership.revalidation_pending
            or self._check_generations() != checks
        )

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

        # Kept frames may be answered outside the consumer's own message
        # handling (the SAML window check), so the socket may have
        # disconnected meanwhile; it must not join a group after that.
        if self._socket_closed():
            return
        try:
            await self.channel_layer.group_add(channel_name, self.channel_name)
        finally:
            if self._socket_closed():
                await self._leave_group(channel_name)
        if self._socket_closed():
            return
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
        self._guard.stop()
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

    async def _leave_group(self, group_name: str) -> None:
        """Leave a group joined while the socket was disconnecting."""

        try:
            await self.channel_layer.group_discard(group_name, self.channel_name)
        except Exception:
            logger.exception("websocket_group_discard_failed")

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
        if not await self._membership.ensure_member(force=True):
            return
        if self._pending_subscription_frames and await self._guard.ensure_live(
            inbound=False
        ):
            # Answering kept frames stops when a check starts; this one
            # succeeded, so answer the rest now rather than at the next frame.
            await self._answer_pending_subscription_frames()

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
