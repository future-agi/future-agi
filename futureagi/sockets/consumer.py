import structlog
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from sockets.org_scope import resolve_socket_organization
from sockets.saml_guard import SamlSocketGuard

logger = structlog.get_logger(__name__)


class DataConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_subscription = None
        self.user = None
        self._guard = SamlSocketGuard(self)

    async def connect(self):
        self.user = self.scope["user"]

        if not await self._guard.ensure_live(inbound=True):
            return

        try:
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

            await self.channel_layer.group_add(self.room_group_name, self.channel_name)
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
        """Guard raw frames so subclasses cannot bypass SAML revalidation."""

        if not await self._guard.ensure_live(inbound=True):
            return
        await super().receive(text_data=text_data, bytes_data=bytes_data)

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
        message_type = content.get("type")
        data = content.get("data", {})
        target_uuid = content.get("uuid")

        if message_type == "ping":
            await self.send_json({"type": "pong"})

        if target_uuid:
            channel_name = self._uuid_group_name(target_uuid)
            if channel_name == self.current_subscription:
                await self.channel_layer.group_send(
                    channel_name,
                    {
                        "type": "send_data",
                        "data": {
                            "type": message_type,
                            "data": data,
                            "uuid": target_uuid,
                        },
                    },
                )
            else:
                logger.error(
                    f"websocket: User {self.user.id} attempted to send message to {target_uuid} without being subscribed"
                )

                await self.send_json(
                    {
                        "type": "error",
                        "message": f"Not subscribed to {target_uuid}. Please subscribe first.",
                    }
                )

    async def disconnect(self, close_code):
        # Cleanup base group
        if hasattr(self, "room_group_name"):
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
        if not uuid or self._uuid_group_name(uuid) == self.current_subscription:
            try:
                await self.send_json(data)
            except Exception:
                logger.exception("websocket_send_failed")
        else:
            pass

    def _uuid_group_name(self, uuid):
        auth_scope = self.scope.get("auth_scope")
        if auth_scope is not None:
            return f"uuid_org_{auth_scope.org_id}_{uuid}"
        return f"uuid_{uuid}"

    async def get_room_group_name(self):
        org_id = await resolve_socket_organization(self.scope, self.user)
        if org_id is None:
            return None
        return f"org_{org_id}"
