import uuid
from types import SimpleNamespace

import pytest
from django.test import RequestFactory
from django.urls import resolve
from rest_framework.request import Request

from accounts.authentication import APIKeyAuthentication, ViewerReadOnly
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


def test_a_scoped_member_with_no_workspace_can_still_write(organization):
    user = _org_only_user(organization, Level.MEMBER, OrganizationRoles.MEMBER)
    request = _scoped_write(organization)

    APIKeyAuthentication()._set_workspace_context(request, user)

    assert request.workspace is None
