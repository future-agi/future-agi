"""Feedback and buffered replay reauthorize the cached socket before any effect."""

from unittest.mock import AsyncMock, Mock

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from ee.falcon_ai import consumers
from ee.falcon_ai.models import Message
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import (
    licensed_features as licensed_features,
)
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import (
    scope_no_egress as scope_no_egress,
)
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import (
    scope_state as scope_state,
)
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import (
    socket_harness as socket_harness,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("message_type", ["feedback", "reconnect"])
@pytest.mark.parametrize(
    "authority", ["active", "inactive-user", "removed-org", "removed-workspace"]
)
def test_feedback_and_replay_require_live_access(
    socket_harness, monkeypatch, message_type, authority
):
    harness = socket_harness
    message = Message._base_manager.create(
        conversation=harness.conversation, role="assistant", content="private replay"
    )
    before = list(Message._base_manager.order_by("pk").values())
    replay = {"type": "text_delta", "data": {"delta": "private replay"}}
    buffer = Mock(
        get_status=Mock(return_value="streaming"),
        get_all_events=Mock(return_value=[replay]),
    )
    factory = Mock(return_value=buffer)
    monkeypatch.setattr(consumers, "StreamBuffer", factory)

    def revoke():
        if authority == "inactive-user":
            User._base_manager.filter(pk=harness.state.member.pk).update(
                is_active=False
            )
        elif authority == "removed-org":
            OrganizationMembership._base_manager.filter(
                pk=harness.state.membership.pk
            ).delete()
        elif authority == "removed-workspace":
            WorkspaceMembership._base_manager.filter(pk=harness.grant.pk).delete()

    async def scenario():
        socket, consumer = await harness.connect()
        consumer._update_message_feedback = AsyncMock(
            wraps=consumer._update_message_feedback
        )
        consumer._owns_conversation = AsyncMock(wraps=consumer._owns_conversation)
        try:
            await database_sync_to_async(revoke)()
            assert consumer.user.is_active  # Deliberately cached before deactivation.
            frame = {
                "type": message_type,
                "conversation_id": str(harness.conversation.pk),
                "message_id": str(message.pk),
                "feedback": "up",
            }
            await socket.send_json_to(frame)
            if authority != "active":
                event = await socket.receive_json_from(timeout=5)
                assert event["type"] == "error", event
                assert event["data"]["code"] == "workspace_unavailable", event
                assert event["data"]["conversation_id"] == str(harness.conversation.pk)
                assert await socket.receive_output(timeout=5) == {
                    "type": "websocket.close",
                    "code": 4003,
                }
                consumer._update_message_feedback.assert_not_awaited()
                consumer._owns_conversation.assert_not_awaited()
                factory.assert_not_called()
            elif message_type == "feedback":
                event = await socket.receive_json_from(timeout=5)
                assert event["type"] == "feedback_updated", event
                consumer._update_message_feedback.assert_awaited_once()
                factory.assert_not_called()
            else:
                event = await socket.receive_json_from(timeout=5)
                assert event == {
                    "type": "reconnect_status",
                    "data": {"status": "streaming", "event_count": 1},
                }
                assert await socket.receive_json_from(timeout=5) == replay
                consumer._owns_conversation.assert_awaited_once()
                factory.assert_called_once_with(str(harness.conversation.pk))
                buffer.get_all_events.assert_called_once()
            assert consumer._agent_task is None
            harness.billed.assert_not_called()
            harness.quota.assert_not_called()
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    if authority == "active" and message_type == "feedback":
        message.refresh_from_db()
        assert message.feedback == "up"
    else:
        assert list(Message._base_manager.order_by("pk").values()) == before
