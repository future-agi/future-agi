"""Template stream authorization with synthetic models and a stub channel layer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync

from sockets.consumer import DataConsumer
from sockets.membership import MEMBERSHIP_CLOSE_CODE


def test_only_the_templates_workspace_grants_access(user, organization, workspace):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from model_hub.models.run_prompt import PromptTemplate
    from sockets.template_scope import template_workspace_access
    from tfc.constants.levels import Level

    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        role="Member", level=Level.MEMBER
    )
    other = Workspace.no_workspace_objects.create(
        name="Other", organization=organization, created_by=user
    )
    WorkspaceMembership.no_workspace_objects.create(workspace=other, user=user)
    template = PromptTemplate.no_workspace_objects.create(
        name="Synthetic template", organization=organization, workspace=workspace
    )
    can_subscribe = async_to_sync(template_workspace_access)
    assert can_subscribe(user.pk, organization.pk, template.pk) is True
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(is_active=False)
    assert can_subscribe(user.pk, organization.pk, template.pk) is False
    assert user.can_access_workspace(other)


@pytest.fixture
def consumer():
    socket = DataConsumer()
    socket.organization_id = str(uuid4())
    socket.room_group_name = f"org_{socket.organization_id}"
    socket.user = SimpleNamespace(id=uuid4())
    socket.scope = {}
    socket.channel_name = "synthetic-channel"
    socket.channel_layer = AsyncMock()
    socket.close = AsyncMock()
    socket.send_json = AsyncMock()
    socket._membership.mark_validated()
    return socket


def test_workspace_denial_does_not_join_template_group(consumer):
    target = uuid4()
    with patch(
        "sockets.consumer.template_workspace_access",
        new_callable=AsyncMock,
        create=True,
    ) as access:
        access.return_value = False
        asyncio.run(consumer.handle_subscribe({"uuid": str(target)}))
    consumer.channel_layer.group_add.assert_not_awaited()
    assert consumer.current_subscription is None
    assert consumer.send_json.await_args.args[0]["type"] == "error"
    access.assert_awaited_once_with(consumer.user.id, consumer.organization_id, target)


def test_workspace_loss_blocks_delivery_despite_recent_org_check(consumer):
    target = uuid4()
    chunk = {"uuid": str(target), "data": "synthetic"}
    event = {"target": {"kind": "subscription", "id": str(target)}, "data": chunk}

    async def run():
        with patch(
            "sockets.consumer.template_workspace_access",
            new_callable=AsyncMock,
            create=True,
        ) as access:
            access.return_value = True
            await consumer.handle_subscribe({"uuid": str(target)})
        consumer.channel_layer.group_add.assert_awaited_once()
        consumer.send_json.reset_mock()
        with patch(
            "sockets.membership.template_workspace_access",
            new_callable=AsyncMock,
            create=True,
        ) as access:
            access.side_effect = [True, False]
            await consumer.send_data(event)
            consumer.send_json.assert_awaited_once_with(chunk)
            consumer.send_json.reset_mock()
            await consumer.send_data(event)
        consumer.send_json.assert_not_awaited()
        consumer.close.assert_awaited_once_with(code=MEMBERSHIP_CLOSE_CODE)

    asyncio.run(run())
