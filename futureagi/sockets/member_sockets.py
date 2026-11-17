"""Close a member's open sockets for an organization when it removes them.

A consumer that serves one organization joins that member's group for it at
connect (``member_{organization_id}_{user_id}``, built like the
``org_{organization_id}`` groups) and leaves it on disconnect. Removing the
member sends one message to that group once the removal commits, and every
socket in it closes with ``WS_CLOSE_SESSION_ENDED``.

Joining also names the organization to the socket's ``SessionGuard``, whose
periodic check then also requires the user to still act in it. If the removal
message is lost (a channel-layer failure), the same sockets close within one
check interval.
"""

import structlog

from tfc.middleware.ws_session_guard import WS_CLOSE_SESSION_ENDED

logger = structlog.get_logger(__name__)

MEMBER_REMOVED = "member.removed"


def member_group_name(organization_id, user_id):
    """The group of one member's sockets for one organization."""
    return f"member_{organization_id}_{user_id}"


def close_member_sockets(organization_id, user_id):
    """Close every open socket of this member for this organization.

    Runs after the removal has committed, so it never raises: a channel-layer
    failure is logged and the session guard's next check closes the sockets.
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            member_group_name(organization_id, user_id), {"type": MEMBER_REMOVED}
        )
    except Exception as error:  # noqa: BLE001 - the removal stands regardless.
        logger.warning("member_sockets_close_failed", error_type=type(error).__name__)


class MemberSocketMixin:
    """Closes a consumer serving one organization when its member is removed."""

    member_group = None

    async def join_member_group(self, organization_id):
        """Call at connect, once the consumer knows the organization it serves."""
        user = self.scope.get("user")
        if organization_id is None or user is None:
            return
        guard = self.scope.get("session_guard")
        if guard is not None:
            guard.serve_organization(organization_id)
        layer = getattr(self, "channel_layer", None)
        channel = getattr(self, "channel_name", None)
        if layer is None or channel is None:
            return
        group = member_group_name(organization_id, user.id)
        await layer.group_add(group, channel)
        self.member_group = group

    async def leave_member_group(self):
        group, self.member_group = self.member_group, None
        if group is not None:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def member_removed(self, event):
        await self.close(code=WS_CLOSE_SESSION_ENDED)
