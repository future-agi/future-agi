"""Transport controls for live removal, restored access and cross-org reconnects.

Drive real connect/receive_json/_handle_chat via the existing Channels helper.
Only provider/buffer/quota/billing services are replaced. Authorization, landing,
conversation/message persistence and memory queries use actual database rows.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests import workspace_write_support as write_support
from ee.falcon_ai import consumers
from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.models import Conversation, FalconMemory, Message, Skill
from ee.falcon_ai.tests import workspace_gate_support as support
from saml2_auth.tests.ws_helpers import WebsocketCommunicator
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

scope_state = support.scope_state
licensed_features = support.licensed_features
scope_no_egress = write_support.scope_no_egress
pytestmark = pytest.mark.django_db(transaction=True)
EFFECT_METHODS = (
    "_save_user_message",
    "_save_assistant_message",
    "_get_conversation_and_history",
    "_load_file_contents",
    "_get_files_metadata",
)


@pytest.fixture
def socket_harness(
    scope_no_egress, scope_state, licensed_features, monkeypatch, settings
):
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    state = scope_state
    grant = support.grant_workspace(state)
    conversation = Conversation._base_manager.create(
        user=state.member, organization=state.org, workspace=state.private
    )
    FalconMemory._base_manager.create(
        organization=state.org,
        workspace=state.private,
        key="org-a-memory",
        value="Only while active in A",
    )
    instances = []
    runs = []
    memory_reads = Mock(wraps=FalconMemory.objects.filter)
    skill_reads = Mock(wraps=Skill.no_workspace_objects.filter)
    monkeypatch.setattr(FalconMemory.objects, "filter", memory_reads)
    monkeypatch.setattr(Skill.no_workspace_objects, "filter", skill_reads)
    fetch = Mock(return_value="")
    monkeypatch.setattr("ee.falcon_ai.url_fetcher.fetch_urls_from_message", fetch)
    stream = Mock(return_value=Mock())
    monkeypatch.setattr(consumers, "StreamBuffer", stream)
    quota = Mock(return_value=SimpleNamespace(allowed=True))
    monkeypatch.setattr(consumers, "check_usage", quota)
    billed = Mock()
    billed_cost = Mock()
    monkeypatch.setattr(consumers.FalconAIConsumer, "_emit_chat_usage", billed)
    monkeypatch.setattr(
        consumers.FalconAIConsumer, "_emit_chat_usage_from_cost", billed_cost
    )

    def build_agent(*, tool_context, conversation):
        async def run(**kwargs):
            memories = await AgentLoop.__dict__["_load_memories"](
                SimpleNamespace(tool_context=tool_context)
            )
            runs.append((tool_context, memories))
            return {"id": str(uuid.uuid4()), "content": "Authorized chat completed"}

        return SimpleNamespace(run=run, llm_client=SimpleNamespace(_gateway_cost=0))

    factory = Mock(side_effect=build_agent)
    monkeypatch.setattr(consumers, "AgentLoop", factory)

    async def application(scope, receive, send):
        consumer = consumers.FalconAIConsumer()
        # Observations wrap real DB methods, not success-returning replacements.
        for name in EFFECT_METHODS:
            setattr(consumer, name, AsyncMock(wraps=getattr(consumer, name)))
        instances.append(consumer)
        await consumer(scope, receive, send)

    async def connect(workspace_id=state.private.pk):
        query = f"?workspace_id={workspace_id}" if workspace_id else ""
        socket = WebsocketCommunicator(application, f"/ws/falcon-ai/{query}")
        # Authentication is already completed at the consumer boundary. Keep a
        # deliberately stale User.organization; never mock the live resolvers.
        socket.scope["user"] = state.member
        connected, detail = await socket.connect(timeout=5)
        assert connected, detail
        return socket, instances[-1]

    spies = (
        memory_reads,
        skill_reads,
        fetch,
        stream,
        quota,
        factory,
        billed,
        billed_cost,
    )
    return SimpleNamespace(
        state=state,
        grant=grant,
        conversation=conversation,
        connect=connect,
        spies=spies,
        runs=runs,
        billed=billed,
        quota=quota,
    )


def chat_frame(conversation):
    return {
        "type": "chat",
        "conversation_id": str(conversation.pk),
        "message": "/missing-scope-skill hello https://example.test/reference",
        "file_ids": [str(uuid.uuid4())],
    }


def assert_no_effects(harness, consumer):
    assert consumer._agent_task is None
    for spy in harness.spies:
        spy.assert_not_called()
    for name in EFFECT_METHODS:
        getattr(consumer, name).assert_not_awaited()
    assert harness.runs == []


async def successful_chat(harness, socket, consumer, conversation):
    await socket.send_json_to(chat_frame(conversation))
    event = await socket.receive_json_from(timeout=5)
    assert event["type"] == "done", event
    if consumer._agent_task is not None:
        await asyncio.wait_for(consumer._agent_task, timeout=5)
    for name in EFFECT_METHODS:
        getattr(consumer, name).assert_awaited_once()
    for spy in harness.spies[:-1]:
        spy.assert_called_once()
    harness.spies[-1].assert_not_called()  # Token billing, not cost billing.
    assert consumer.organization_id == str(conversation.organization_id)
    assert harness.billed.call_args.args[3] == str(conversation.pk)


@pytest.mark.parametrize("membership", ["organization", "workspace"])
@pytest.mark.parametrize("removal", ["inactive", "deleted"])
def test_revoked_membership_next_chat_closes_4003_without_effects(
    socket_harness, membership, removal
):
    harness = socket_harness
    before = list(Message._base_manager.order_by("pk").values())

    def revoke():
        if membership == "organization":
            rows = OrganizationMembership._base_manager.filter(
                pk=harness.state.membership.pk
            )
        else:
            rows = WorkspaceMembership._base_manager.filter(pk=harness.grant.pk)
        if removal == "inactive":
            rows.update(is_active=False)
        else:
            rows.delete()

    async def scenario():
        socket, consumer = await harness.connect()
        try:
            assert consumer.workspace.pk == harness.state.private.pk
            await database_sync_to_async(revoke)()
            await socket.send_json_to(chat_frame(harness.conversation))
            event = await socket.receive_json_from(timeout=5)
            assert event["type"] == "error", event
            assert event["data"]["code"] == "workspace_unavailable", event
            assert await socket.receive_output(timeout=5) == {
                "type": "websocket.close",
                "code": 4003,
            }
            assert_no_effects(harness, consumer)
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert list(Message._base_manager.order_by("pk").values()) == before


@pytest.mark.parametrize("deactivated", [False, True], ids=["active", "deactivated"])
def test_live_user_status_controls_next_chat(socket_harness, deactivated):
    harness = socket_harness
    before = list(Message._base_manager.order_by("pk").values())

    async def scenario():
        socket, consumer = await harness.connect()
        try:
            assert consumer.scope.get("auth_scope") is None  # Non-SAML socket.
            if deactivated:
                await database_sync_to_async(
                    lambda: User._base_manager.filter(
                        pk=harness.state.member.pk
                    ).update(is_active=False)
                )()
            # The socket caches the active object; only the DB row changes.
            assert consumer.user.is_active is True
            if deactivated:
                await socket.send_json_to(chat_frame(harness.conversation))
                event = await socket.receive_json_from(timeout=5)
                assert event["type"] == "error", event
                assert event["data"]["code"] == "workspace_unavailable", event
                assert await socket.receive_output(timeout=5) == {
                    "type": "websocket.close",
                    "code": 4003,
                }
                assert_no_effects(harness, consumer)
            else:
                await successful_chat(harness, socket, consumer, harness.conversation)
                assert harness.runs[0][1] == [
                    {"key": "org-a-memory", "value": "Only while active in A"}
                ]
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert OrganizationMembership._base_manager.get(
        pk=harness.state.membership.pk
    ).is_active
    assert WorkspaceMembership._base_manager.get(pk=harness.grant.pk).is_active
    assert User._base_manager.get(pk=harness.state.member.pk).is_active is (
        not deactivated
    )
    if deactivated:
        assert list(Message._base_manager.order_by("pk").values()) == before
    else:
        assert list(
            Message._base_manager.filter(conversation=harness.conversation)
            .order_by("created_at")
            .values_list("role", flat=True)
        ) == ["user", "assistant"]


def test_restored_membership_new_connect_and_chat_succeed(socket_harness):
    harness = socket_harness

    def set_active(active):
        OrganizationMembership._base_manager.filter(
            pk=harness.state.membership.pk
        ).update(is_active=active)

    async def scenario():
        socket, original = await harness.connect()
        try:
            await database_sync_to_async(set_active)(False)
            await socket.send_json_to(chat_frame(harness.conversation))
            event = await socket.receive_json_from(timeout=5)
            assert event["type"] == "error", event
            assert event["data"]["code"] == "workspace_unavailable", event
            assert await socket.receive_output(timeout=5) == {
                "type": "websocket.close",
                "code": 4003,
            }
            assert_no_effects(harness, original)
        finally:
            await socket.disconnect()
        await database_sync_to_async(set_active)(True)
        restored, consumer = await harness.connect()
        try:
            assert consumer is not original
            assert consumer.organization.pk == harness.state.org.pk
            assert consumer.workspace.pk == harness.state.private.pk
            await successful_chat(harness, restored, consumer, harness.conversation)
            context, memories = harness.runs[0]
            assert context.interactive_falcon_socket is True
            assert memories == [
                {"key": "org-a-memory", "value": "Only while active in A"}
            ]
        finally:
            await restored.disconnect()

    async_to_sync(scenario)()
    assert list(
        Message._base_manager.filter(conversation=harness.conversation)
        .order_by("created_at")
        .values_list("role", flat=True)
    ) == ["user", "assistant"]


@pytest.mark.parametrize("stale_selector", [False, True])
def test_removed_org_a_reconnect_resolves_only_active_org_b(
    socket_harness, stale_selector
):
    harness = socket_harness
    state = harness.state
    org_b = Organization.objects.create(name="Active reconnect org B")
    membership_b = OrganizationMembership._base_manager.create(
        user=state.member,
        organization=org_b,
        is_active=True,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
    )
    workspace_b = Workspace._base_manager.create(
        organization=org_b, name="Reconnect B", created_by=state.member, is_default=True
    )
    WorkspaceMembership._base_manager.create(
        user=state.member,
        workspace=workspace_b,
        organization_membership=membership_b,
        is_active=True,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
    )
    conversation_b = Conversation._base_manager.create(
        user=state.member, organization=org_b, workspace=workspace_b
    )
    FalconMemory._base_manager.create(
        organization=org_b, workspace=workspace_b, key="org-b-memory", value="B only"
    )

    async def scenario():
        original, old_consumer = await harness.connect()
        try:
            assert old_consumer.organization.pk == state.org.pk
            await database_sync_to_async(
                lambda: OrganizationMembership._base_manager.filter(
                    pk=state.membership.pk
                ).delete()
            )()
            await original.send_json_to(chat_frame(harness.conversation))
            event = await original.receive_json_from(timeout=5)
            assert event["type"] == "error", event
            assert event["data"]["code"] == "workspace_unavailable", event
            assert await original.receive_output(timeout=5) == {
                "type": "websocket.close",
                "code": 4003,
            }
            assert_no_effects(harness, old_consumer)
        finally:
            await original.disconnect()
        socket, consumer = await harness.connect(
            state.private.pk if stale_selector else None
        )
        try:
            assert consumer.organization.pk == org_b.pk
            assert consumer.organization.pk != state.org.pk
            assert consumer.workspace.pk == workspace_b.pk
            if stale_selector:
                await socket.send_json_to(chat_frame(conversation_b))
                event = await socket.receive_json_from(timeout=5)
                assert event["data"]["code"] == "workspace_unavailable", event
                assert await socket.receive_output(timeout=5) == {
                    "type": "websocket.close",
                    "code": 4003,
                }
                assert_no_effects(harness, consumer)
            else:
                await successful_chat(harness, socket, consumer, conversation_b)
                assert len(harness.runs) == 1
                context, memories = harness.runs[0]
                assert context.organization.pk == org_b.pk
                assert context.workspace.pk == workspace_b.pk
                assert memories == [{"key": "org-b-memory", "value": "B only"}]
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert (
        state.member.organization_id == state.org.pk
    )  # Stale legacy identity is not authority.
    assert not Message._base_manager.filter(conversation=harness.conversation).exists()
    assert list(
        Message._base_manager.filter(conversation=conversation_b)
        .order_by("created_at")
        .values_list("role", flat=True)
    ) == ([] if stale_selector else ["user", "assistant"])
