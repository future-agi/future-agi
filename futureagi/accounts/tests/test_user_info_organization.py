"""User-info names an organization only where the account acts in it.

An account that acts in no organization (its only membership row is
deactivated or removed) is answered 200 with ``requires_org_setup`` true, no
organization and no organization role. An account with no membership row in
any state is named in the organization stored on its account record. An
account that acts in an organization is named in that organization.

Requests take the production request path: the account signs in through
``/accounts/token/`` and sends only its bearer token, as the web app does
before it knows an organization.
"""

import uuid
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.production_path import (
    PASSWORD,
    make_tenant,
    sign_in,
    use_production_request_settings,
)

USER_INFO_URL = "/accounts/user-info/"

pytestmark = [pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def world(db, settings, monkeypatch):
    """Organizations A and B, each with an owner and a default workspace."""
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    data = SimpleNamespace(a=make_tenant("A"), b=make_tenant("B"))
    yield data
    clear_workspace_context()


def _member(world, kind):
    """An account whose stored organization is A.

    ``kind`` is its membership: ``active`` (in A and A's workspace),
    ``deactivated`` or ``removed`` (its only row, in A), ``no-row`` (no row in
    any state) or ``elsewhere`` (active in B and B's workspace only).
    """
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"member-{uuid.uuid4().hex[:8]}@example.test",
        password=PASSWORD,
        name="Member",
        organization=world.a.organization,
        organization_role="Member",
    )
    if kind in ("active", "elsewhere"):
        tenant = world.a if kind == "active" else world.b
        membership = OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=tenant.organization,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=tenant.workspace,
            role="Workspace Viewer",
            level=Level.WORKSPACE_VIEWER,
            is_active=True,
            organization_membership=membership,
        )
    elif kind in ("deactivated", "removed"):
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=world.a.organization,
            role="Member",
            level=Level.MEMBER,
            is_active=False,
            deleted=kind == "removed",
            deleted_at=timezone.now() if kind == "removed" else None,
        )
    clear_workspace_context()
    return user


def _user_info(user):
    """The status and the organization fields of the account's user-info
    answer, read without keeping the body."""
    response = sign_in(user.email).get(USER_INFO_URL)
    clear_workspace_context()
    try:
        body = response.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    organization = body.get("organization")
    return SimpleNamespace(
        status=response.status_code,
        requires_org_setup=body.get("requires_org_setup") is True,
        organization_id=(
            organization.get("id") if isinstance(organization, dict) else organization
        ),
        organization_role=body.get("organization_role"),
    )


@pytest.mark.parametrize("kind", ["deactivated", "removed"])
def test_user_info_names_no_organization_when_the_account_acts_in_none(world, kind):
    answer = _user_info(_member(world, kind))

    assert answer.status == status.HTTP_200_OK
    assert answer.requires_org_setup is True
    assert answer.organization_id is None
    assert answer.organization_role is None


def test_user_info_names_the_stored_organization_with_no_membership_row(world):
    user = _member(world, "no-row")
    assert not OrganizationMembership._base_manager.filter(user=user).exists()

    answer = _user_info(user)

    assert answer.status == status.HTTP_200_OK
    assert answer.organization_id == str(world.a.organization.id)
    assert answer.organization_role == "Member"


@pytest.mark.parametrize(("kind", "place"), [("active", "a"), ("elsewhere", "b")])
def test_user_info_names_the_organization_the_account_acts_in(world, kind, place):
    answer = _user_info(_member(world, kind))

    assert answer.status == status.HTTP_200_OK
    assert answer.requires_org_setup is False
    assert answer.organization_id == str(getattr(world, place).organization.id)
