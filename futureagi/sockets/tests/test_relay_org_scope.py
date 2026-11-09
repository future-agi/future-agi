"""Relay subscriptions follow the selected tenant without trusting its query."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync

from accounts.models import Organization
from accounts.models.organization_membership import OrganizationMembership
from sockets.consumer import DataConsumer
from sockets.org_scope import resolve_socket_organization


@pytest.mark.django_db
def test_socket_uses_authorized_selected_membership_not_first(user, organization):
    selected = Organization.objects.create(name="Selected synthetic organization")
    OrganizationMembership.no_workspace_objects.create(user=user, organization=selected)
    scope = {"query_string": f"organization_id={selected.id}".encode()}
    assert async_to_sync(resolve_socket_organization)(scope, user) == selected.id
    consumer = DataConsumer()
    consumer.scope = scope
    consumer.user = user
    consumer.room_group_name = async_to_sync(consumer.get_room_group_name)()
    target = uuid4()
    assert consumer._uuid_group_name(target) == f"uuid_org_{selected.id}_{target}"
    assert async_to_sync(resolve_socket_organization)({}, user) == organization.id


@pytest.mark.django_db
def test_socket_refuses_unrelated_or_inactive_requested_membership(user):
    other = Organization.objects.create(name="Unrelated synthetic organization")
    scope = {"query_string": f"organization_id={other.id}".encode()}
    assert async_to_sync(resolve_socket_organization)(scope, user) is None
    OrganizationMembership.no_workspace_objects.create(
        user=user, organization=other, is_active=False
    )
    assert async_to_sync(resolve_socket_organization)(scope, user) is None


@pytest.mark.parametrize("query", [b"organization_id=invalid", b"organization_id=", b"organization_id=a&organization_id=b", b"\xff"])
def test_invalid_requested_organization_is_rejected_without_fallback(query):
    assert async_to_sync(resolve_socket_organization)({"query_string": query}, None) is None


def test_requested_organization_cannot_widen_saml_scope():
    own, other = uuid4(), uuid4()
    scope = {"auth_scope": SimpleNamespace(org_id=own), "query_string": f"organization_id={other}".encode()}
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
