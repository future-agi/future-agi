import importlib
import uuid
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.test import RequestFactory
from django.urls import resolve
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIClient

from accounts.authentication import (
    NO_ORGANIZATION_WRITE_DENIED,
    APIKeyAuthentication,
    ViewerReadOnly,
)
from accounts.models import OrgApiKey
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.test_rbac_regressions import (
    _jwt_client,
    _make_workspace_user,
    _viewer_client,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db

ID_ENDING_IN_2FA = "00000000-0000-4000-8000-0000000002fa"


def _org_only_user(organization, level, role):
    user = User.objects.create_user(
        email=f"org-only-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="org-only",
        organization=organization,
        organization_role=role,
    )
    OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=organization,
        defaults={"role": role, "level": level, "is_active": True},
    )
    return user


def _scoped_write(organization, path="/simulate/run-tests/"):
    request = Request(RequestFactory().post(path))
    request.auth_scope = SimpleNamespace(org_id=organization.id)
    return request


def test_a_viewer_removed_from_every_workspace_comes_back_as_a_viewer(
    organization, workspace
):
    only = Workspace.objects.create(
        name="Support team",
        organization=organization,
        is_active=True,
        created_by=workspace.created_by,
    )
    user = _make_workspace_user(
        organization,
        only,
        org_role=OrganizationRoles.MEMBER_VIEW_ONLY,
        org_level=Level.VIEWER,
        ws_role=OrganizationRoles.WORKSPACE_VIEWER,
        ws_level=Level.WORKSPACE_VIEWER,
        prefix="removed-viewer",
    )
    WorkspaceMembership.no_workspace_objects.filter(user=user).update(
        is_active=False, deleted=True
    )

    _jwt_client(user, organization, only).get("/simulate/agent-definitions/")

    roles = set(
        WorkspaceMembership.no_workspace_objects.filter(
            user=user, is_active=True
        ).values_list("role", flat=True)
    )
    assert OrganizationRoles.WORKSPACE_ADMIN not in roles
    assert roles <= {OrganizationRoles.WORKSPACE_VIEWER}


def test_the_default_workspace_grants_the_organization_level(organization):
    user = _org_only_user(organization, Level.MEMBER, OrganizationRoles.MEMBER)

    default = APIKeyAuthentication()._get_or_create_default_workspace(
        user, organization
    )

    membership = WorkspaceMembership.no_workspace_objects.get(
        user=user, workspace=default, is_active=True
    )
    assert membership.role == OrganizationRoles.WORKSPACE_MEMBER


def test_no_organization_membership_gets_no_default_workspace(organization):
    user = _org_only_user(
        organization, Level.VIEWER, OrganizationRoles.MEMBER_VIEW_ONLY
    )
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        is_active=False
    )

    assert (
        APIKeyAuthentication()._get_or_create_default_workspace(user, organization)
        is None
    )
    assert not WorkspaceMembership.no_workspace_objects.filter(user=user).exists()


def test_an_id_ending_in_2fa_still_gets_the_write_check(organization, workspace):
    path = f"/simulate/run-tests/{ID_ENDING_IN_2FA}/"
    assert resolve(path)

    response = _viewer_client(organization, workspace).delete(path)

    assert response.status_code == 403, response.content
    assert str(response.data["detail"]).startswith("Viewers can't")


def test_a_viewer_still_manages_their_own_second_factor(organization, workspace):
    response = _viewer_client(organization, workspace).post(
        "/accounts/2fa/totp/setup/", {}, format="json"
    )

    assert not (
        response.status_code == 403
        and str(response.data.get("detail", "")).startswith("Viewers can't")
    )


def test_a_scoped_viewer_with_no_workspace_cannot_write(organization):
    user = _org_only_user(
        organization, Level.VIEWER, OrganizationRoles.MEMBER_VIEW_ONLY
    )
    request = _scoped_write(organization)

    with pytest.raises(ViewerReadOnly):
        APIKeyAuthentication()._set_workspace_context(request, user)


def test_a_scoped_member_with_no_workspace_is_told_to_get_one(organization):
    user = _org_only_user(organization, Level.MEMBER, OrganizationRoles.MEMBER)

    with pytest.raises(PermissionDenied) as raised:
        APIKeyAuthentication()._set_workspace_context(_scoped_write(organization), user)

    assert not isinstance(raised.value, ViewerReadOnly)
    assert "add you to a workspace" in str(raised.value.detail)


def test_a_scoped_admin_still_writes(organization):
    user = _org_only_user(organization, Level.ADMIN, OrganizationRoles.ADMIN)

    APIKeyAuthentication()._set_workspace_context(_scoped_write(organization), user)


def test_a_removed_members_own_key_reads_nothing(organization, workspace):
    member = _make_workspace_user(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER,
        org_level=Level.MEMBER,
        ws_role=OrganizationRoles.WORKSPACE_MEMBER,
        ws_level=Level.WORKSPACE_MEMBER,
        prefix="removed-key",
    )
    key = OrgApiKey.objects.create(
        name="left behind", organization=organization, type="user", user=member
    )
    OrganizationMembership.no_workspace_objects.filter(user=member).update(
        is_active=False
    )
    WorkspaceMembership.no_workspace_objects.filter(user=member).update(
        is_active=False, deleted=True
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)

    response = client.get("/simulate/agent-definitions/")

    assert response.status_code == 403, response.content
    assert "not a member of this organization" in str(response.data)


def _lower_self_granted_admins():
    module = importlib.import_module(
        "accounts.migrations.0029_lower_self_granted_default_workspace_admins"
    )
    module.lower_self_granted_admins(django_apps, None)


@pytest.mark.parametrize(
    ("level", "role", "expected"),
    [
        (Level.VIEWER, OrganizationRoles.MEMBER_VIEW_ONLY, "workspace_viewer"),
        (Level.MEMBER, OrganizationRoles.MEMBER, "workspace_member"),
        (Level.ADMIN, OrganizationRoles.ADMIN, "workspace_admin"),
    ],
)
def test_the_migration_lowers_self_granted_admin_memberships(
    organization, workspace, level, role, expected
):
    default = Workspace.objects.filter(
        organization=organization, is_default=True
    ).first() or Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=workspace.created_by,
    )
    user = _org_only_user(organization, level, role)
    WorkspaceMembership.no_workspace_objects.create(
        workspace=default, user=user, role="workspace_admin", level=8, invited_by=user
    )

    _lower_self_granted_admins()

    row = WorkspaceMembership.no_workspace_objects.get(workspace=default, user=user)
    assert row.role == expected


def test_the_migration_keeps_an_admin_membership_someone_granted(
    organization, workspace
):
    granter = _org_only_user(organization, Level.ADMIN, OrganizationRoles.ADMIN)
    user = _org_only_user(
        organization, Level.VIEWER, OrganizationRoles.MEMBER_VIEW_ONLY
    )
    row = WorkspaceMembership.no_workspace_objects.create(
        workspace=workspace,
        user=user,
        role="workspace_admin",
        level=8,
        invited_by=granter,
    )

    _lower_self_granted_admins()

    row.refresh_from_db()
    assert row.role == "workspace_admin"


def _client_without_an_organization(organization, workspace):
    user = _org_only_user(organization, Level.MEMBER, OrganizationRoles.MEMBER)
    client = _jwt_client(user, organization, workspace)
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        is_active=False
    )
    return client


def test_a_user_with_no_organization_cannot_write(organization, workspace):
    response = _client_without_an_organization(organization, workspace).post(
        "/simulate/run-tests/create/", {}, format="json"
    )

    assert response.status_code == 403, response.content
    assert response.data["detail"] == NO_ORGANIZATION_WRITE_DENIED


def test_a_user_with_no_organization_still_manages_their_second_factor(
    organization, workspace
):
    response = _client_without_an_organization(organization, workspace).post(
        "/accounts/2fa/totp/setup/", {}, format="json"
    )

    assert response.status_code != 403, response.content
