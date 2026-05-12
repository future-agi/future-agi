import asyncio
import os

import structlog
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = structlog.get_logger(__name__)

CHANNEL_LAYER_TIMEOUT_SECONDS = float(
    os.getenv("WEBSOCKET_CHANNEL_LAYER_TIMEOUT_SECONDS", "2")
)


class DataConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_subscription = None
        self.user = None
        self._channel_layer_tasks = set()

    async def connect(self):
        self.user = self.scope["user"]

        try:
            try:
                self.room_group_name = await asyncio.wait_for(
                    self.get_room_group_name(),
                    timeout=CHANNEL_LAYER_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                self.room_group_name = None
                logger.warning(
                    "websocket_room_group_lookup_timeout",
                    user_id=str(self.user.id) if self.user else None,
                    timeout_seconds=CHANNEL_LAYER_TIMEOUT_SECONDS,
                )

            if self.room_group_name is None:
                logger.warning(
                    "websocket_connect_failed",
                    reason="user_has_no_organization",
                    user_id=str(self.user.id) if self.user else None,
                )
                await self.close(code=4002)
                return

            await self.accept()
            self._start_channel_layer_task(self._safe_group_add(self.room_group_name))

        except Exception as e:
            logger.error(
                "websocket_connect_error",
                reason=str(e),
                error_type=type(e).__name__,
                user_id=str(self.user.id) if self.user else None,
            )
            await self.close(code=4000)

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
            await self._safe_group_discard(self.current_subscription)

        # Subscribe to new channel
        channel_name = f"uuid_{uuid}"

        if not await self._safe_group_add(channel_name):
            await self.send_json(
                {
                    "type": "error",
                    "message": "Subscription backend is unavailable. Please retry shortly.",
                }
            )
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

        channel_name = f"uuid_{uuid}"
        if channel_name == self.current_subscription:
            await self._safe_group_discard(channel_name)
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
            channel_name = f"uuid_{target_uuid}"
            if channel_name == self.current_subscription:
                if not await self._safe_group_send(
                    channel_name,
                    {
                        "type": "send_data",
                        "data": {
                            "type": message_type,
                            "data": data,
                            "uuid": target_uuid,
                        },
                    },
                ):
                    await self.send_json(
                        {
                            "type": "error",
                            "message": "Message backend is unavailable. Please retry shortly.",
                        }
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
        for task in list(self._channel_layer_tasks):
            task.cancel()
        self._channel_layer_tasks.clear()

        # Cleanup base group
        if hasattr(self, "room_group_name"):
            await self._safe_group_discard(self.room_group_name)

        # Cleanup current subscription
        if self.current_subscription:
            await self._safe_group_discard(self.current_subscription)

    async def send_data(self, event):
        """
        Send data to the WebSocket, checking if we're subscribed to the channel
        """
        data = event.get("data", {})
        uuid = data.get("uuid")

        if not uuid or f"uuid_{uuid}" == self.current_subscription:
            try:
                await self.send_json(data)
            except Exception:
                logger.exception("websocket_send_failed")
        else:
            pass

    def _start_channel_layer_task(self, awaitable):
        task = asyncio.create_task(awaitable)
        self._channel_layer_tasks.add(task)

        def _discard_task(done_task):
            self._channel_layer_tasks.discard(done_task)

        task.add_done_callback(_discard_task)
        return task

    async def _safe_channel_layer_call(self, operation, group_name, awaitable):
        try:
            await asyncio.wait_for(
                awaitable,
                timeout=CHANNEL_LAYER_TIMEOUT_SECONDS,
            )
            return True
        except asyncio.CancelledError:
            logger.debug(
                "websocket_channel_layer_cancelled",
                operation=operation,
                group_name=group_name,
                user_id=str(self.user.id) if self.user else None,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "websocket_channel_layer_timeout",
                operation=operation,
                group_name=group_name,
                timeout_seconds=CHANNEL_LAYER_TIMEOUT_SECONDS,
                user_id=str(self.user.id) if self.user else None,
            )
        except Exception as e:
            logger.warning(
                "websocket_channel_layer_error",
                operation=operation,
                group_name=group_name,
                reason=str(e),
                error_type=type(e).__name__,
                user_id=str(self.user.id) if self.user else None,
            )
        return False

    async def _safe_group_add(self, group_name):
        if not group_name:
            return False
        return await self._safe_channel_layer_call(
            "group_add",
            group_name,
            self.channel_layer.group_add(group_name, self.channel_name),
        )

    async def _safe_group_discard(self, group_name):
        if not group_name:
            return False
        return await self._safe_channel_layer_call(
            "group_discard",
            group_name,
            self.channel_layer.group_discard(group_name, self.channel_name),
        )

    async def _safe_group_send(self, group_name, message):
        if not group_name:
            return False
        return await self._safe_channel_layer_call(
            "group_send",
            group_name,
            self.channel_layer.group_send(group_name, message),
        )

    @database_sync_to_async
    def get_organization_id(self):
        try:
            from accounts.models.organization_membership import OrganizationMembership

            membership = (
                OrganizationMembership.objects.filter(user=self.user, is_active=True)
                .select_related("organization")
                .first()
            )
            if membership:
                return membership.organization.id
            # Fallback to legacy FK
            if getattr(self.user, "organization", None):
                return self.user.organization.id
            return None
        except Exception:
            return None

    async def get_room_group_name(self):
        org_id = await self.get_organization_id()
        if org_id is None:
            return None
        return f"org_{org_id}"
