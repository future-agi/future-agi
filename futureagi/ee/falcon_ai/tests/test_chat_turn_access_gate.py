"""An interactive turn must reauthorize before any workspace read or side effect."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from asgiref.sync import async_to_sync

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.tests import workspace_write_support as write_support
from ee.falcon_ai import consumers
from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.models import Conversation, FalconMemory, Message, Skill
from ee.falcon_ai.tests import test_workspace_scope as native_scope
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

scope_state = native_scope.scope_state
scope_no_egress = write_support.scope_no_egress
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("revoked", [False, True])
def test_chat_turn_checks_live_access_before_effects(
    scope_no_egress, scope_state, monkeypatch, empty, revoked
):
    state = scope_state
    WorkspaceMembership._base_manager.update_or_create(
        user=state.member,
        workspace=state.private,
        defaults={
            "organization_membership": state.membership,
            "is_active": True,
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
        },
    )
    FalconMemory._base_manager.create(
        organization=state.org,
        workspace=state.private,
        key="private-turn-memory",
        value="Do not load after removal",
    )
    conversation = Conversation._base_manager.create(
        user=state.member, organization=state.org, workspace=state.private
    )
    consumer = consumers.FalconAIConsumer()
    consumer.user = state.member
    consumer.organization = state.org
    consumer.organization_id = str(state.org.pk)
    consumer.workspace = None if empty else state.private
    if revoked:
        other = Organization.objects.create(name="Still active for chat elsewhere")
        OrganizationMembership._base_manager.create(
            user=state.member,
            organization=other,
            is_active=True,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
        )
        OrganizationMembership._base_manager.filter(pk=state.membership.pk).delete()
        assert state.member.can_access_organization(other)
    consumer.close = AsyncMock()
    consumer.send_json = AsyncMock()
    consumer._save_user_message = AsyncMock(return_value=object())
    consumer._save_assistant_message = AsyncMock()
    consumer._get_conversation_and_history = AsyncMock(return_value=(object(), []))
    consumer._emit_chat_usage = Mock()
    consumer._emit_chat_usage_from_cost = Mock()
    consumer._load_file_contents = AsyncMock(return_value={})
    consumer._get_files_metadata = AsyncMock(return_value=[])
    usage = Mock(return_value=SimpleNamespace(allowed=True))
    monkeypatch.setattr(consumers, "check_usage", usage)
    stream_factory = Mock(return_value=Mock())
    monkeypatch.setattr(consumers, "StreamBuffer", stream_factory)
    # Preserve the actual memory read but replace all provider execution.
    memory_reads = Mock(wraps=FalconMemory.objects.filter)
    monkeypatch.setattr(FalconMemory.objects, "filter", memory_reads)
    skill_reads = Mock(return_value=Mock(first=Mock(return_value=None)))
    monkeypatch.setattr(Skill.no_workspace_objects, "filter", skill_reads)
    fetch = Mock(return_value="")
    monkeypatch.setattr("ee.falcon_ai.url_fetcher.fetch_urls_from_message", fetch)

    def build_agent(*, tool_context, conversation):
        async def run(**kwargs):
            await AgentLoop.__dict__["_load_memories"](
                SimpleNamespace(tool_context=tool_context)
            )
            return {"id": "test-assistant-message"}

        return SimpleNamespace(run=run, llm_client=SimpleNamespace(_gateway_cost=0))

    factory = Mock(side_effect=build_agent)
    monkeypatch.setattr(consumers, "AgentLoop", factory)
    before = list(Message._base_manager.order_by("pk").values())

    async def chat():
        await consumer._handle_chat(
            {
                "conversation_id": str(conversation.pk),
                "message": "/private-skill hello",
                "file_ids": ["test-file"],
            }
        )
        if consumer._agent_task is not None:
            await consumer._agent_task

    async_to_sync(chat)()
    assert list(Message._base_manager.order_by("pk").values()) == before
    if revoked or empty:
        consumer.close.assert_awaited_once_with(code=4003)
        for spy in (
            memory_reads,
            skill_reads,
            factory,
            usage,
            stream_factory,
            fetch,
            consumer._emit_chat_usage,
            consumer._emit_chat_usage_from_cost,
        ):
            spy.assert_not_called()
        for spy in (
            consumer._save_user_message,
            consumer._save_assistant_message,
            consumer._get_conversation_and_history,
            consumer._load_file_contents,
            consumer._get_files_metadata,
        ):
            spy.assert_not_awaited()
    else:
        consumer.close.assert_not_awaited()
        consumer._save_user_message.assert_awaited_once()
        consumer._save_assistant_message.assert_awaited_once()
        factory.assert_called_once()
        memory_reads.assert_called_once()
        usage.assert_called_once()
        consumer._emit_chat_usage.assert_called_once()
