"""Open relay sockets follow the user's current organization membership.

Sockets go through the production JWT middleware and URL router with real
access tokens. Relay sends and membership changes go through the real HTTP
views; only synthetic account setup uses the ORM directly.
"""

import asyncio
import json
import sys
import types
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from rest_framework.test import APIClient

try:
    from channels.testing import WebsocketCommunicator
except ModuleNotFoundError as exc:
    if exc.name != "daphne":
        raise
    # channels.testing imports its optional live-server class from daphne, which
    # the backend environment omits; the communicator itself never uses it.
    daphne_testing = types.ModuleType("daphne.testing")
    daphne_testing.DaphneProcess = type("DaphneProcess", (), {})
    sys.modules.setdefault("daphne", types.ModuleType("daphne"))
    sys.modules.setdefault("daphne.testing", daphne_testing)
    from channels.testing import WebsocketCommunicator

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.token_service import issue_tokens
from sockets.routing import websocket_urlpatterns
from tfc.constants.levels import Level
from tfc.middleware.jwt_auth import JWTAuthMiddleware
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = pytest.mark.django_db(transaction=True)

PASSWORD = "Synthetic-socket-membership-password"
# The close code for a socket whose organization no longer authorizes the user;
# the frontend does not reconnect after it.
MEMBERSHIP_CLOSE_CODE = 4002
APPLICATION = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))


@pytest.fixture(autouse=True)
def process_channel_layer(settings):
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "tfc.channel_layers.ThreadSafeInMemoryChannelLayer"}
    }


def create_organization():
    """Return ``(org, owner, workspace, system_key)`` for a synthetic tenant."""

    suffix = uuid4().hex
    org = Organization.objects.create(name=f"Synthetic socket tenant {suffix}")
    set_workspace_context(organization=org)
    try:
        owner = User.objects.create_user(
            email=f"owner-{suffix}@example.invalid",
            password=PASSWORD,
            name="Synthetic Owner",
            organization=org,
            organization_role="Owner",
        )
        workspace = Workspace.no_workspace_objects.create(
            name="Synthetic Workspace",
            organization=org,
            created_by=owner,
            is_active=True,
            is_default=True,
        )
        add_membership(owner, org, workspace, role="Owner", level=Level.OWNER)
        key = OrgApiKey.no_workspace_objects.create(
            name="Synthetic socket relay",
            organization=org,
            workspace=workspace,
            type="system",
            enabled=True,
        )
        return org, owner, workspace, key
    finally:
        clear_workspace_context()


def add_membership(user, org, workspace, *, role="Member", level=Level.MEMBER):
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=org, role=role, level=level, is_active=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        organization_membership=membership,
        role="Workspace Admin" if level >= Level.OWNER else "Workspace Member",
        level=Level.WORKSPACE_ADMIN if level >= Level.OWNER else Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    return membership


def create_member(org, workspace):
    suffix = uuid4().hex
    set_workspace_context(organization=org)
    try:
        member = User.objects.create_user(
            email=f"member-{suffix}@example.invalid",
            password=PASSWORD,
            name="Synthetic Member",
            organization=org,
            organization_role="Member",
        )
        add_membership(member, org, workspace)
        return member
    finally:
        clear_workspace_context()


def api_client():
    # Test settings omit WorkspaceContextMiddleware, which clears the previous
    # request's workspace context in production; requests here span tenants.
    clear_workspace_context()
    return APIClient()


def bearer(user):
    return {"HTTP_AUTHORIZATION": "Bearer " + issue_tokens(user)["access"]}


def relay(key, target=None):
    """Publish through the real relay endpoint with the tenant's system key."""

    message = {"alert_title": "Synthetic notification", "marker": uuid4().hex}
    body = {"message": message}
    if target:
        body.update(send_to_uuid=True, uuid=target)
        message["uuid"] = target
    response = api_client().post(
        "/call-websocket/",
        body,
        format="json",
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
    )
    assert response.status_code == 200, response.content
    return message


def remove_member(owner, member):
    response = api_client().delete(
        "/accounts/organization/members/remove/",
        {"user_id": str(member.pk)},
        format="json",
        **bearer(owner),
    )
    assert response.status_code == 200, response.content


def deactivate_member(owner, member):
    response = api_client().post(
        "/accounts/user/deactivate/",
        {"user_id": str(member.pk)},
        format="json",
        **bearer(owner),
    )
    assert response.status_code == 200, response.content


async def open_socket(user, org, target=None):
    token = await sync_to_async(lambda: issue_tokens(user)["access"])()
    socket = WebsocketCommunicator(
        APPLICATION, f"/ws/connect/?token={token}&organization_id={org.pk}"
    )
    connected, code = await socket.connect(timeout=10)
    assert connected, code
    await socket.send_json_to({"type": "ping"})
    assert await socket.receive_json_from(timeout=10) == {"type": "pong"}
    if target:
        await socket.send_json_to({"type": "subscribe", "uuid": target})
        confirmed = await socket.receive_json_from(timeout=10)
        assert confirmed["current_subscription"] == f"uuid_org_{org.pk}_{target}"
    return socket


async def outputs(socket, settle=1.0):
    """Return every ASGI event the socket emits until it is quiet for ``settle``."""

    events = []
    while not await socket.receive_nothing(timeout=settle):
        events.append(await socket.receive_output(timeout=5))
    return events


def frames(events):
    return [json.loads(e["text"]) for e in events if e["type"] == "websocket.send"]


def closed(events):
    return [e.get("code") for e in events if e["type"] == "websocket.close"]


async def disconnect(*sockets):
    for socket in sockets:
        await socket.disconnect()


@pytest.mark.parametrize("targeted", [False, True], ids=["broadcast", "targeted"])
def test_relay_reaches_only_the_sending_organization(targeted):
    org, owner, workspace, key = create_organization()
    other_org, other_owner, _, _ = create_organization()
    member = create_member(org, workspace)
    target = str(uuid4()) if targeted else None

    async def run():
        own = await open_socket(owner, org, target)
        peer = await open_socket(member, org, target)
        other = await open_socket(other_owner, other_org, target)
        try:
            message = await sync_to_async(relay)(key, target)
            assert await own.receive_json_from(timeout=10) == message
            assert await peer.receive_json_from(timeout=10) == message
            assert await other.receive_nothing(timeout=0.5)
        finally:
            await disconnect(own, peer, other)

    asyncio.run(run())


def test_open_socket_closes_when_membership_is_removed():
    org, owner, workspace, key = create_organization()
    other_org, other_owner, _, other_key = create_organization()
    member = create_member(org, workspace)
    target = str(uuid4())

    async def run():
        own = await open_socket(owner, org, target)
        removed = await open_socket(member, org, target)
        other = await open_socket(other_owner, other_org, target)
        try:
            before = await sync_to_async(relay)(key)
            assert await removed.receive_json_from(timeout=10) == before
            assert await own.receive_json_from(timeout=10) == before

            await sync_to_async(remove_member)(owner, member)
            sent = [
                await sync_to_async(relay)(key),
                await sync_to_async(relay)(key, target),
            ]
            # The organization's remaining member is the positive control.
            for message in sent:
                assert await own.receive_json_from(timeout=10) == message
            events = await outputs(removed)
            assert (frames(events), closed(events)) == ([], [MEMBERSHIP_CLOSE_CODE])
            # Another tenant's socket on the same target is unaffected.
            other_message = await sync_to_async(relay)(other_key, target)
            assert await other.receive_json_from(timeout=10) == other_message
        finally:
            await disconnect(own, removed, other)

    asyncio.run(run())


def test_open_socket_closes_when_member_is_deactivated_in_bulk():
    org, owner, workspace, key = create_organization()
    elsewhere, _, elsewhere_workspace, _ = create_organization()
    member = create_member(org, workspace)
    # A membership elsewhere keeps the account active, so only the
    # organization's membership rows change.
    add_membership(member, elsewhere, elsewhere_workspace)
    target = str(uuid4())

    async def run():
        own = await open_socket(owner, org, target)
        deactivated = await open_socket(member, org, target)
        try:
            await sync_to_async(deactivate_member)(owner, member)
            sent = [
                await sync_to_async(relay)(key),
                await sync_to_async(relay)(key, target),
            ]
            for message in sent:
                assert await own.receive_json_from(timeout=10) == message
            events = await outputs(deactivated)
            assert (frames(events), closed(events)) == ([], [MEMBERSHIP_CLOSE_CODE])
        finally:
            await disconnect(own, deactivated)

    asyncio.run(run())
    assert User.objects.get(pk=member.pk).is_active


def test_socket_for_a_remaining_membership_stays_open():
    org, owner, workspace, _ = create_organization()
    elsewhere, _, elsewhere_workspace, elsewhere_key = create_organization()
    member = create_member(org, workspace)
    add_membership(member, elsewhere, elsewhere_workspace)
    target = str(uuid4())

    async def run():
        remaining = await open_socket(member, elsewhere, target)
        try:
            await sync_to_async(remove_member)(owner, member)
            assert await remaining.receive_nothing(timeout=0.5)
            for message_target in (None, target):
                message = await sync_to_async(relay)(elsewhere_key, message_target)
                assert await remaining.receive_json_from(timeout=10) == message
        finally:
            await disconnect(remaining)

    asyncio.run(run())


@pytest.mark.parametrize("targeted", [False, True], ids=["broadcast", "targeted"])
def test_delivery_revalidates_membership_without_a_notification(settings, targeted):
    settings.SOCKET_MEMBERSHIP_REVALIDATE_SECONDS = 0
    org, owner, workspace, key = create_organization()
    member = create_member(org, workspace)
    target = str(uuid4()) if targeted else None

    def deactivate_rows():
        # A queryset update sends no model signal.
        updated = OrganizationMembership.no_workspace_objects.filter(
            user=member, organization=org
        ).update(is_active=False)
        assert updated == 1

    async def run():
        own = await open_socket(owner, org, target)
        socket = await open_socket(member, org, target)
        try:
            before = await sync_to_async(relay)(key, target)
            assert await socket.receive_json_from(timeout=10) == before
            assert await own.receive_json_from(timeout=10) == before

            await sync_to_async(deactivate_rows)()
            after = await sync_to_async(relay)(key, target)
            assert await own.receive_json_from(timeout=10) == after
            events = await outputs(socket)
            assert (frames(events), closed(events)) == ([], [MEMBERSHIP_CLOSE_CODE])
        finally:
            await disconnect(own, socket)

    asyncio.run(run())


def test_client_frames_are_not_relayed_to_subscribers():
    org, owner, workspace, _ = create_organization()
    member = create_member(org, workspace)
    target = str(uuid4())

    async def run():
        own = await open_socket(owner, org, target)
        peer = await open_socket(member, org, target)
        try:
            await peer.send_json_to(
                {"type": "peer_msg", "uuid": target, "data": {"marker": "x"}}
            )
            assert await peer.receive_json_from(timeout=10) == {
                "type": "error",
                "message": "Unsupported message type.",
            }
            assert await own.receive_nothing(timeout=0.5)
            # Keep-alive and subscription control frames still work.
            await peer.send_json_to({"type": "ping", "uuid": target})
            assert await peer.receive_json_from(timeout=10) == {"type": "pong"}
            assert await own.receive_nothing(timeout=0.5)
            await peer.send_json_to({"type": "unsubscribe", "uuid": target})
            confirmed = await peer.receive_json_from(timeout=10)
            assert confirmed["type"] == "unsubscription_confirmed"
        finally:
            await disconnect(own, peer)

    asyncio.run(run())


def _membership_state(state):
    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    rows = OrganizationMembership.no_workspace_objects.filter(user=member)
    if state == "inactive-row":
        rows.update(is_active=False)
    elif state == "legacy-no-rows":
        OrganizationMembership._base_manager.filter(user=member).delete()
    elif state == "inactive-account":
        User.objects.filter(pk=member.pk).update(is_active=False)
    elif state == "row-elsewhere-only":
        elsewhere, _, elsewhere_workspace, _ = create_organization()
        rows.update(is_active=False)
        add_membership(member, elsewhere, elsewhere_workspace)
    return member, org


@pytest.mark.parametrize(
    "state, expected",
    [
        ("active-row", True),
        ("inactive-row", False),
        ("legacy-no-rows", True),
        ("inactive-account", False),
        ("row-elsewhere-only", False),
    ],
)
def test_membership_check_matches_connect_rules(state, expected):
    from sockets.org_scope import organization_membership_is_active

    member, org = _membership_state(state)
    assert organization_membership_is_active(member.pk, org.pk) is expected
