"""A recovered socket must not operate on its previous workspace's chat."""

from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async

from accounts.models.workspace import WorkspaceMembership
from ee.falcon_ai.consumers import FalconAIConsumer
from ee.falcon_ai.models import Conversation, Message
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import (
    assert_no_effects,
    chat_frame,
    successful_chat,
)
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
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db(transaction=True)


def recover_to_default(harness):
    state = harness.state
    WorkspaceMembership._base_manager.filter(pk=harness.grant.pk).delete()
    WorkspaceMembership.no_workspace_objects.create(
        user=state.member,
        workspace=state.default,
        organization_membership=state.membership,
        is_active=True,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
    )
    return Conversation._base_manager.create(
        user=state.member, organization=state.org, workspace=state.default
    )


def test_recovered_chat_refuses_old_and_unknown_targets_before_effects(socket_harness):
    harness = socket_harness
    landing = recover_to_default(harness)
    before = list(Message._base_manager.order_by("pk").values())

    async def scenario():
        outcomes = []
        for target in (str(harness.conversation.pk), str(uuid4()), "malformed"):
            socket, consumer = await harness.connect(harness.state.default.pk)
            try:
                assert await consumer._chat_access_allowed()
                frame = chat_frame(harness.conversation)
                frame.update(conversation_id=target, client_message_id="retry")
                await socket.send_json_to(frame)
                event = await socket.receive_json_from(timeout=5)
                assert event["data"].pop("conversation_id") == target
                outcomes.append(event)
                assert event["data"]["code"] == "workspace_unavailable"
                assert event["data"]["client_message_id"] == "retry"
                assert await socket.receive_output(timeout=5) == {
                    "type": "websocket.close",
                    "code": 4003,
                }
                assert_no_effects(harness, consumer)
            finally:
                await socket.disconnect()
        assert outcomes[0] == outcomes[1] == outcomes[2]
        assert (
            await database_sync_to_async(
                lambda: list(Message._base_manager.order_by("pk").values())
            )()
            == before
        )

        socket, consumer = await harness.connect(harness.state.default.pk)
        try:
            await successful_chat(harness, socket, consumer, landing)
            assert harness.runs[0][0].workspace.pk == landing.workspace_id
            assert harness.runs[0][1] == []  # No old workspace memories.
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert not Message._base_manager.filter(conversation=harness.conversation).exists()
    assert list(
        Message._base_manager.filter(conversation=landing)
        .order_by("created_at")
        .values_list("role", flat=True)
    ) == ["user", "assistant"]


HELPERS = (
    "_save_user_message",
    "_save_assistant_message",
    "_get_conversation_and_history",
    "_update_conversation_tokens",
    "_update_conversation_mode",
    "_owns_conversation",
)


def call_helper(consumer, method, target):
    args = {
        "_save_user_message": ("user text",),
        "_save_assistant_message": ({"id": str(uuid4()), "content": "assistant"},),
        "_get_conversation_and_history": (),
        "_update_conversation_tokens": (11,),
        "_update_conversation_mode": ("chat",),
        "_owns_conversation": (),
    }
    return FalconAIConsumer.__dict__[method].func(consumer, str(target), *args[method])


@pytest.mark.parametrize("method", HELPERS)
@pytest.mark.parametrize("target_kind", ["old", "unknown", "malformed", "empty"])
def test_every_conversation_helper_is_scoped(socket_harness, method, target_kind):
    harness = socket_harness
    recover_to_default(harness)
    Message._base_manager.create(
        conversation=harness.conversation, role="assistant", content="private history"
    )
    consumer = FalconAIConsumer()
    consumer.user = harness.state.member
    consumer.organization = harness.state.org
    consumer.workspace = None if target_kind == "empty" else harness.state.default
    target = {
        "old": harness.conversation.pk,
        "unknown": uuid4(),
        "malformed": "malformed",
        "empty": harness.conversation.pk,
    }[target_kind]
    messages = list(Message._base_manager.order_by("pk").values())
    conversations = list(Conversation._base_manager.order_by("pk").values())
    result = call_helper(consumer, method, target)
    if method == "_get_conversation_and_history":
        assert result == (None, [])
    elif method == "_owns_conversation":
        assert result is False
    else:
        assert result is None
    assert list(Message._base_manager.order_by("pk").values()) == messages
    assert list(Conversation._base_manager.order_by("pk").values()) == conversations


@pytest.mark.parametrize("method", HELPERS)
def test_conversation_helper_revalidates_membership(socket_harness, method):
    harness = socket_harness
    consumer = FalconAIConsumer()
    consumer.user = harness.state.member
    consumer.organization = harness.state.org
    consumer.workspace = harness.state.private
    assert async_to_sync(consumer._chat_access_allowed)()
    WorkspaceMembership._base_manager.filter(pk=harness.grant.pk).delete()
    messages = list(Message._base_manager.order_by("pk").values())
    conversations = list(Conversation._base_manager.order_by("pk").values())
    result = call_helper(consumer, method, harness.conversation.pk)
    assert (
        result == (None, [])
        if method == "_get_conversation_and_history"
        else not result
    )
    assert list(Message._base_manager.order_by("pk").values()) == messages
    assert list(Conversation._base_manager.order_by("pk").values()) == conversations


@pytest.mark.parametrize("method", HELPERS)
def test_conversation_helpers_allow_landing_conversation(socket_harness, method):
    harness = socket_harness
    conversation = recover_to_default(harness)
    consumer = FalconAIConsumer()
    consumer.user = harness.state.member
    consumer.organization = harness.state.org
    consumer.workspace = harness.state.default
    result = call_helper(consumer, method, conversation.pk)
    conversation.refresh_from_db()
    if method in ("_save_user_message", "_save_assistant_message"):
        assert result.conversation_id == conversation.pk
    elif method == "_get_conversation_and_history":
        assert result == (conversation, [])
    elif method == "_update_conversation_tokens":
        assert conversation.total_tokens == 11
    elif method == "_update_conversation_mode":
        assert conversation.mode == "chat"
    else:
        assert result is True
