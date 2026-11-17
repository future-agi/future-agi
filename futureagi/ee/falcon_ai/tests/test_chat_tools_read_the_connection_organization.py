"""The chat's tools read the organization the chat connection opened in.

Where the organization stored on the account is one the account no longer acts
in, the account object the connection hands to the chat's tools names the
organization the connection opened in. Nothing is saved: the organization
stored on the account stays as it is. An account that acts in its stored
organization keeps it. The other socket tests' harness replaces the link fetch,
stream buffer, quota check, billing and agent loop; accounts, memberships and
workspaces are real rows.
"""

import asyncio
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from ee.falcon_ai.models import Conversation
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import chat_frame
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


def organization_b(state):
    """A second organization where the account is an active member, with a chat."""
    org = Organization.objects.create(name="Chat tools organization B")
    membership = OrganizationMembership._base_manager.create(
        user=state.member,
        organization=org,
        is_active=True,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
    )
    workspace = Workspace._base_manager.create(
        organization=org, name="Workspace B", created_by=state.member, is_default=True
    )
    WorkspaceMembership._base_manager.create(
        user=state.member,
        workspace=workspace,
        organization_membership=membership,
        is_active=True,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
    )
    conversation = Conversation._base_manager.create(
        user=state.member, organization=org, workspace=workspace
    )
    return SimpleNamespace(org=org, workspace=workspace, conversation=conversation)


def end_membership_in_a(state, how):
    """End the account's membership in A, the organization stored on it."""
    rows = OrganizationMembership._base_manager.filter(pk=state.membership.pk)
    if how == "inactive":
        rows.update(is_active=False)
    else:
        rows.delete()


def stored_organization_id(account):
    """The organization stored on the account, read from the database."""
    return User._base_manager.get(pk=account.pk).organization_id


@pytest.mark.parametrize("membership_in_a", ["inactive", "deleted"])
@pytest.mark.parametrize("address", ["no-workspace", "workspace-in-a"])
def test_chat_tools_read_the_organization_the_connection_opened_in(
    socket_harness, membership_in_a, address
):
    harness = socket_harness
    state = harness.state
    b = organization_b(state)
    end_membership_in_a(state, membership_in_a)

    async def scenario():
        socket, consumer = await harness.connect(
            state.private.pk if address == "workspace-in-a" else None
        )
        try:
            assert consumer.organization.pk == b.org.pk
            assert consumer.user.organization_id == b.org.pk
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert stored_organization_id(state.member) == state.org.pk


@pytest.mark.parametrize("membership_in_a", ["inactive", "deleted"])
def test_a_chat_turn_hands_its_tools_the_connection_organization(
    socket_harness, membership_in_a
):
    harness = socket_harness
    state = harness.state
    b = organization_b(state)
    end_membership_in_a(state, membership_in_a)

    async def scenario():
        socket, consumer = await harness.connect(None)
        try:
            await socket.send_json_to(chat_frame(b.conversation))
            event = await socket.receive_json_from(timeout=5)
            assert event["type"] == "done", event
            if consumer._agent_task is not None:
                await asyncio.wait_for(consumer._agent_task, timeout=5)
            assert len(harness.runs) == 1
            context, _ = harness.runs[0]
            assert context.organization.pk == b.org.pk
            assert context.user.organization_id == b.org.pk
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert stored_organization_id(state.member) == state.org.pk


@pytest.mark.parametrize("address", ["workspace-in-a", "workspace-in-b"])
def test_an_account_that_acts_in_its_stored_organization_keeps_it(
    socket_harness, address
):
    harness = socket_harness
    state = harness.state
    b = organization_b(state)
    opens_in = state.org if address == "workspace-in-a" else b.org

    async def scenario():
        socket, consumer = await harness.connect(
            state.private.pk if address == "workspace-in-a" else b.workspace.pk
        )
        try:
            assert consumer.organization.pk == opens_in.pk
            assert consumer.user.organization_id == state.org.pk
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()
    assert stored_organization_id(state.member) == state.org.pk
