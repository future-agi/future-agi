"""Selectors and feedback targets cannot distinguish inaccessible resources."""

from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from ee.falcon_ai import consumers
from ee.falcon_ai.consumers import FalconAIConsumer
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
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db(transaction=True)


def test_unjoined_and_missing_selectors_have_same_connect_and_reconnect(
    socket_harness, monkeypatch
):
    harness = socket_harness
    state = harness.state
    unjoined = Workspace.objects.create(
        organization=state.org, name="Unjoined A", created_by=state.owner
    )
    org_b = Organization.objects.create(name="Landing B")
    member_b = OrganizationMembership.no_workspace_objects.create(
        id=UUID(int=0),
        user=state.member,
        organization=org_b,
        is_active=True,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
    )
    workspace_b = Workspace.objects.create(
        organization=org_b, name="B", created_by=state.member, is_default=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=state.member,
        workspace=workspace_b,
        organization_membership=member_b,
        is_active=True,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
    )
    replay = {"type": "text_delta", "data": {"delta": "A replay"}}
    buffer = Mock(
        get_status=Mock(return_value="streaming"),
        get_all_events=Mock(return_value=[replay]),
    )
    factory = Mock(return_value=buffer)
    monkeypatch.setattr(consumers, "StreamBuffer", factory)

    async def scenario():
        outcomes = []
        for target in (unjoined.pk, uuid4()):
            socket, consumer = await harness.connect(target)
            try:
                await socket.send_json_to(
                    {
                        "type": "reconnect",
                        "conversation_id": str(harness.conversation.pk),
                    }
                )
                event = await socket.receive_json_from(timeout=5)
                outcomes.append(
                    (
                        consumer.organization.pk,
                        consumer.workspace.pk,
                        consumer._explicit_workspace_unavailable,
                        event,
                    )
                )
            finally:
                await socket.disconnect()
        assert outcomes[0] == outcomes[1]
        assert outcomes[0] == (
            org_b.pk,
            workspace_b.pk,
            True,
            {"type": "reconnect_status", "data": {"status": "none"}},
        )

    async_to_sync(scenario)()
    factory.assert_not_called()


@pytest.mark.parametrize(
    "method", ["_get_organization_from_workspace", "_get_workspace"]
)
def test_unjoined_and_missing_selectors_have_same_query_path(socket_harness, method):
    state = socket_harness.state
    unjoined = Workspace.objects.create(
        organization=state.org, name="Unjoined", created_by=state.owner
    )
    consumer = FalconAIConsumer()
    consumer.user = state.member
    consumer.organization = state.org
    consumer.organization_id = str(state.org.pk)
    lookup = FalconAIConsumer.__dict__[method].func
    paths = []
    for target in (unjoined.pk, uuid4()):
        consumer.workspace_id = str(target)
        with CaptureQueriesContext(connection) as queries:
            result = (
                lookup(consumer, str(target))
                if method == "_get_organization_from_workspace"
                else lookup(consumer)
            )
        assert (
            result is None
            if method == "_get_organization_from_workspace"
            else result.pk == state.private.pk
        )
        paths.append(
            [
                q["sql"]
                .replace(str(target), "SELECTOR")
                .replace(target.hex, "SELECTOR")
                for q in queries
            ]
        )
    assert paths[0] == paths[1]
    if method == "_get_organization_from_workspace":
        assert len(paths[0]) == 1


def test_feedback_for_revoked_workspace_matches_unknown_message(socket_harness):
    harness = socket_harness
    state = harness.state
    message = Message._base_manager.create(
        conversation=harness.conversation, role="assistant", content="unchanged"
    )
    WorkspaceMembership._base_manager.filter(pk=harness.grant.pk).delete()
    WorkspaceMembership.no_workspace_objects.create(
        user=state.member,
        workspace=state.default,
        organization_membership=state.membership,
        is_active=True,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
    )
    before = list(Message._base_manager.order_by("pk").values())

    async def scenario():
        socket, consumer = await harness.connect(state.default.pk)
        try:
            assert consumer.workspace.pk == state.default.pk
            outcomes = []
            for target in (message.pk, uuid4()):
                await socket.send_json_to(
                    {"type": "feedback", "message_id": str(target), "feedback": "up"}
                )
                outcomes.append(await socket.receive_json_from(timeout=5))
            assert (
                outcomes[0]
                == outcomes[1]
                == {"type": "error", "data": {"error": "Message not found"}}
            )
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert list(Message._base_manager.order_by("pk").values()) == before


def test_feedback_revalidates_target_after_socket_gate(socket_harness):
    harness = socket_harness
    message = Message._base_manager.create(
        conversation=harness.conversation, role="assistant", content="unchanged"
    )
    before = list(Message._base_manager.order_by("pk").values())

    async def scenario():
        socket, consumer = await harness.connect()
        try:
            assert await consumer._chat_access_allowed()
            await database_sync_to_async(
                lambda: WorkspaceMembership._base_manager.filter(
                    pk=harness.grant.pk
                ).delete()
            )()
            assert not await consumer._update_message_feedback(str(message.pk), "up")
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert list(Message._base_manager.order_by("pk").values()) == before
