"""Socket reads may land, but an explicit unavailable write target must refuse."""

from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace, WorkspaceMembership
from ee.falcon_ai.consumers import FalconAIConsumer
from ee.falcon_ai.models import Conversation, FalconMemory, Message
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

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["missing", "inactive", "foreign", "unjoined"])
@pytest.mark.parametrize("message_type", ["chat", "feedback"])
def test_unavailable_socket_write_has_no_effects(socket_harness, kind, message_type):
    harness = socket_harness
    state = harness.state
    target = str(state.private.pk)
    if kind == "missing":
        target = str(uuid4())
    elif kind == "inactive":
        Workspace._base_manager.filter(pk=state.private.pk).update(is_active=False)
    elif kind == "foreign":
        org = Organization.objects.create(name="Unavailable socket organization")
        Workspace._base_manager.filter(pk=state.private.pk).update(organization=org)
    else:
        WorkspaceMembership._base_manager.filter(user=state.member).delete()
    message = Message._base_manager.create(
        conversation=harness.conversation, role="assistant", content="unchanged"
    )
    models = (Conversation, Message, FalconMemory)
    before = {
        model: list(model._base_manager.order_by("pk").values()) for model in models
    }

    async def scenario():
        socket, consumer = await harness.connect(target)
        try:
            # Reads/connect retain the current landing policy; writes must not.
            frame = chat_frame(harness.conversation)
            if message_type == "feedback":
                frame = {
                    "type": "feedback",
                    "message_id": str(message.pk),
                    "feedback": "up",
                }
            frame["conversation_id"] = str(harness.conversation.pk)
            frame["client_message_id"] = "assistant-local-turn"
            await socket.send_json_to(frame)
            event = await socket.receive_json_from(timeout=5)
            assert event["type"] == "error", event
            assert event["data"]["code"] == "workspace_unavailable", event
            assert event["data"]["conversation_id"] == str(harness.conversation.pk)
            assert event["data"]["client_message_id"] == "assistant-local-turn"
            assert await socket.receive_output(timeout=5) == {
                "type": "websocket.close",
                "code": 4003,
            }
            assert_no_effects(harness, consumer)
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert {
        model: list(model._base_manager.order_by("pk").values()) for model in models
    } == before


@pytest.mark.parametrize("selector", ["absent", "valid"])
def test_available_socket_write_keeps_destination(socket_harness, selector):
    harness = socket_harness

    async def scenario():
        socket, consumer = await harness.connect(
            None if selector == "absent" else harness.state.private.pk
        )
        try:
            assert consumer.workspace.pk == harness.state.private.pk
            await successful_chat(harness, socket, consumer, harness.conversation)
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert list(
        Message._base_manager.filter(conversation=harness.conversation)
        .order_by("created_at")
        .values_list("role", flat=True)
    ) == ["user", "assistant"]
    assert harness.runs[0][0].workspace.pk == harness.state.private.pk


def test_missing_and_foreign_workspace_use_same_authorized_query(socket_harness):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    state = socket_harness.state
    foreign_org = Organization.objects.create(name="Foreign query-path org")
    foreign = Workspace.objects.create(
        name="Foreign", organization=foreign_org, created_by=state.member
    )
    consumer = FalconAIConsumer()
    consumer.user = state.member
    lookup = FalconAIConsumer.__dict__["_get_organization_from_workspace"].func
    paths = []
    for target in (uuid4(), foreign.pk):
        with CaptureQueriesContext(connection) as queries:
            assert lookup(consumer, str(target)) is None
        paths.append(
            [
                q["sql"]
                .replace(str(target), "SELECTOR")
                .replace(target.hex, "SELECTOR")
                for q in queries
            ]
        )
    assert len(paths[0]) == len(paths[1]) == 1
    assert paths[0] == paths[1]
    with CaptureQueriesContext(connection) as queries:
        assert lookup(consumer, str(state.private.pk)).pk == state.org.pk
    assert len(queries) == 1


def test_malformed_socket_read_keeps_validation_error(socket_harness):
    # This round is write-only. Invalid UUID connection/read validation must not
    # newly become a successful landing; the write contract covers valid UUIDs.
    consumer = FalconAIConsumer()
    consumer.user = socket_harness.state.member
    consumer.organization = socket_harness.state.org
    consumer.organization_id = str(socket_harness.state.org.pk)
    consumer.workspace_id = "not-a-workspace-id"
    with pytest.raises(ValidationError):
        FalconAIConsumer.__dict__["_get_workspace"].func(consumer)
