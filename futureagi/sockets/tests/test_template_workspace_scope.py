"""Template stream authorization with synthetic models and a stub channel layer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync

from sockets.consumer import DataConsumer
from sockets.membership import MEMBERSHIP_CLOSE_CODE


@pytest.mark.django_db(transaction=True)
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


async def subscribe(consumer, target):
    with patch(
        "sockets.consumer.template_workspace_access", AsyncMock(return_value=True)
    ):
        await consumer.handle_subscribe({"uuid": str(target)})
    consumer.send_json.reset_mock()


def event(kind, target, marker):
    return {"target": {"kind": kind, "id": str(target)}, "data": {"marker": marker}}


@pytest.mark.parametrize("access", [False, None], ids=["denied", "deleted"])
@pytest.mark.parametrize("trigger", ["broadcast", "signal"])
def test_workspace_loss_preserves_organization_notifications(consumer, access, trigger):
    target = uuid4()
    notification = event("organization", consumer.organization_id, "notice")
    org_check = AsyncMock(return_value=True)

    async def run():
        await subscribe(consumer, target)
        group = consumer.current_subscription
        chunk = event("subscription", target, "chunk")
        await consumer.send_data(chunk)
        consumer.send_json.assert_awaited_once_with(chunk["data"])
        consumer.send_json.reset_mock()
        if trigger == "signal":
            await consumer.membership_changed(
                {"organization_id": consumer.organization_id}
            )
        await consumer.send_data(notification)
        await consumer.send_data(chunk)  # Already queued for the discarded group.
        assert consumer.current_subscription is None
        assert consumer._membership.template_id is None
        consumer.channel_layer.group_discard.assert_awaited_once_with(
            group, consumer.channel_name
        )
        consumer.close.assert_not_awaited()
        denial, broadcast = consumer.send_json.await_args_list
        assert denial.args[0]["type"] == "subscription_denied"
        assert denial.args[0]["uuid"] == str(target)
        assert broadcast.args == (notification["data"],)
        org_check.assert_awaited_once_with(consumer.user.id, consumer.organization_id)

    with (
        patch(
            "sockets.membership.template_workspace_access",
            AsyncMock(side_effect=[True, access]),
        ),
        patch("sockets.membership.database_sync_to_async", return_value=org_check),
    ):
        asyncio.run(run())


def test_template_and_org_loss_closes_without_delivering(consumer):
    target = uuid4()

    async def run():
        await subscribe(consumer, target)
        await consumer.membership_changed({"organization_id": consumer.organization_id})
        await consumer.send_data(
            event("organization", consumer.organization_id, "notice")
        )
        consumer.send_json.assert_not_awaited()
        consumer.close.assert_awaited_once_with(code=MEMBERSHIP_CLOSE_CODE)

    with (
        patch(
            "sockets.membership.template_workspace_access",
            AsyncMock(return_value=False),
        ),
        patch(
            "sockets.membership.database_sync_to_async",
            return_value=AsyncMock(return_value=False),
        ),
    ):
        asyncio.run(run())


def test_uncertain_org_check_retries_without_dropping_subscription(consumer):
    from django.db import OperationalError

    target = uuid4()
    notification = event("organization", consumer.organization_id, "notice")

    async def run():
        await subscribe(consumer, target)
        await consumer.membership_changed({"organization_id": consumer.organization_id})
        assert consumer._membership.revalidation_pending
        assert consumer._membership.template_id == target
        consumer.channel_layer.group_discard.assert_not_awaited()
        consumer.send_json.assert_not_awaited()
        await consumer.send_data(notification)
        consumer.close.assert_not_awaited()
        assert consumer.send_json.await_args.args == (notification["data"],)
        assert not consumer._membership.revalidation_pending

    with (
        patch(
            "sockets.membership.template_workspace_access",
            AsyncMock(return_value=False),
        ),
        patch(
            "sockets.membership.database_sync_to_async",
            return_value=AsyncMock(side_effect=[OperationalError(), True]),
        ),
    ):
        asyncio.run(run())


def test_stale_workspace_denial_does_not_drop_replacement(consumer):
    target = uuid4()

    async def run():
        await subscribe(consumer, target)
        entered, release = asyncio.Event(), asyncio.Event()

        async def recheck(*args):
            entered.set()
            await release.wait()
            return False

        with (
            patch("sockets.membership.template_workspace_access", recheck),
            patch(
                "sockets.membership.database_sync_to_async",
                return_value=AsyncMock(return_value=True),
            ),
        ):
            delivery = asyncio.create_task(
                consumer.send_data(event("subscription", target, "old"))
            )
            await asyncio.wait_for(entered.wait(), timeout=2)
            await subscribe(
                consumer, target
            )  # Same UUID, newly authorized subscription.
            release.set()
            await delivery
        consumer.send_json.assert_not_awaited()
        assert consumer._membership.template_id == target
        assert consumer.current_subscription == consumer._uuid_group_name(target)

    asyncio.run(run())
