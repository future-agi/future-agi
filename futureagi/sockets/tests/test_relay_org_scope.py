"""Relay subscriptions follow the selected tenant without trusting its query."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator

from accounts.models import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from sockets.consumer import DataConsumer
from sockets.org_scope import resolve_socket_organization
from sockets.simulation_consumer import SimulationUpdateConsumer
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


@pytest.mark.django_db(transaction=True)
def test_socket_uses_authorized_selected_membership_not_first(user, organization):
    selected = Organization.objects.create(name="Selected synthetic organization")
    OrganizationMembership.no_workspace_objects.create(user=user, organization=selected)
    first = OrganizationMembership.no_workspace_objects.filter(user=user).first()
    if first.organization_id == selected.id:
        selected = organization
    scope = {"query_string": f"organization_id={selected.id}".encode()}
    assert async_to_sync(resolve_socket_organization)(scope, user) == selected.id
    consumer = DataConsumer()
    consumer.scope = scope
    consumer.user = user
    consumer.room_group_name = async_to_sync(consumer.get_room_group_name)()
    target = uuid4()
    assert consumer._uuid_group_name(target) == f"uuid_org_{selected.id}_{target}"
    assert async_to_sync(resolve_socket_organization)({}, user) == first.organization_id


@pytest.mark.django_db(transaction=True)
def test_socket_refuses_unrelated_or_inactive_requested_membership(user):
    other = Organization.objects.create(name="Unrelated synthetic organization")
    scope = {"query_string": f"organization_id={other.id}".encode()}
    assert async_to_sync(resolve_socket_organization)(scope, user) is None
    OrganizationMembership.no_workspace_objects.create(
        user=user, organization=other, is_active=False
    )
    assert async_to_sync(resolve_socket_organization)(scope, user) is None


@pytest.mark.parametrize(
    "query",
    [
        b"organization_id=invalid",
        b"organization_id=",
        b"organization_id=a&organization_id=b",
        b"\xff",
    ],
)
def test_invalid_requested_organization_is_rejected_without_fallback(query):
    assert (
        async_to_sync(resolve_socket_organization)({"query_string": query}, None)
        is None
    )


def test_requested_organization_cannot_widen_saml_scope():
    own, other = uuid4(), uuid4()
    scope = {
        "auth_scope": SimpleNamespace(org_id=own),
        "query_string": f"organization_id={other}".encode(),
    }
    assert async_to_sync(resolve_socket_organization)(scope, None) is None
    scope["query_string"] = f"organization_id={own}".encode()
    assert async_to_sync(resolve_socket_organization)(scope, None) == own


@pytest.mark.parametrize("scoped", [False, True])
def test_uuid_group_normalizes_subscription_and_message_identifiers(scoped):
    org_id = uuid4()
    consumer = DataConsumer()
    consumer.scope = {"auth_scope": SimpleNamespace(org_id=org_id)} if scoped else {}
    consumer.room_group_name = f"org_{org_id}"
    target = uuid4()
    for form in (str(target), str(target).upper(), target.hex):
        assert consumer._uuid_group_name(form) == f"uuid_org_{org_id}_{target}"


IN_MEMORY_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


def _member_with_legacy_fk(*, membership_active):
    """Synthetic user whose legacy FK points at a new organization.

    ``membership_active`` is True or False for a single membership row in that
    organization, or None for a truly legacy account with no membership rows.
    """
    suffix = uuid4().hex
    org = Organization.objects.create(name=f"Synthetic socket scope {suffix}")
    set_workspace_context(organization=org)
    try:
        user = User.objects.create_user(
            email=f"scope-{suffix}@example.invalid",
            password="Synthetic-socket-scope-password",
            name="Synthetic Scope User",
            organization=org,
            organization_role="Member",
        )
        if membership_active is not None:
            OrganizationMembership.no_workspace_objects.create(
                user=user,
                organization=org,
                role="Member",
                level=Level.MEMBER,
                is_active=membership_active,
            )
    finally:
        clear_workspace_context()
    return org, User.objects.get(pk=user.pk)


def _connect(consumer, user, query=""):
    """Return ``(accepted, close_code)`` for the consumer's handshake."""

    async def run():
        scope = {
            "type": "websocket",
            "path": "/ws/connect/",
            "query_string": query.encode(),
            "headers": [],
            "subprotocols": [],
            "user": user,
        }
        communicator = ApplicationCommunicator(consumer.as_asgi(), scope)
        await communicator.send_input({"type": "websocket.connect"})
        try:
            response = await communicator.receive_output(timeout=10)
        finally:
            await communicator.send_input(
                {"type": "websocket.disconnect", "code": 1000}
            )
            await communicator.wait(timeout=10)
        if response["type"] == "websocket.accept":
            return True, None
        assert response["type"] == "websocket.close", response
        return False, response.get("code")

    return async_to_sync(run)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("requested", [False, True], ids=["implicit", "former-org"])
def test_removed_member_does_not_resolve_former_org_from_legacy_fk(requested):
    # The state member removal leaves behind: inactive membership, retained FK.
    org, user = _member_with_legacy_fk(membership_active=False)
    assert user.organization_id == org.id
    query = f"organization_id={org.id}" if requested else ""
    scope = {"query_string": query.encode()}
    assert async_to_sync(resolve_socket_organization)(scope, user) is None
    consumer = DataConsumer()
    consumer.scope = scope
    consumer.user = user
    assert async_to_sync(consumer.get_room_group_name)() is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "consumer, query",
    [(DataConsumer, ""), (SimulationUpdateConsumer, f"test_id={uuid4()}")],
    ids=["connect", "simulation-updates"],
)
def test_removed_member_socket_is_refused_before_joining_former_org(
    settings, consumer, query
):
    settings.CHANNEL_LAYERS = IN_MEMORY_CHANNEL_LAYERS
    _, user = _member_with_legacy_fk(membership_active=False)
    assert _connect(consumer, user, query) == (False, 4002)


@pytest.mark.django_db(transaction=True)
def test_truly_legacy_account_still_connects_to_its_fk_organization(settings):
    settings.CHANNEL_LAYERS = IN_MEMORY_CHANNEL_LAYERS
    org, user = _member_with_legacy_fk(membership_active=None)
    assert not OrganizationMembership.no_workspace_objects.filter(user=user).exists()
    assert async_to_sync(resolve_socket_organization)({}, user) == org.id
    assert _connect(DataConsumer, user) == (True, None)


@pytest.mark.django_db(transaction=True)
def test_active_membership_wins_over_legacy_fk_and_removed_membership():
    removed_from, user = _member_with_legacy_fk(membership_active=False)
    current = Organization.objects.create(name=f"Synthetic current {uuid4().hex}")
    OrganizationMembership.no_workspace_objects.create(
        user=user, organization=current, role="Member", level=Level.MEMBER
    )
    assert user.organization_id == removed_from.id
    assert async_to_sync(resolve_socket_organization)({}, user) == current.id
