"""Default user contexts must not bypass the landing policy or provision access."""

from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from accounts.tests.test_workspace_landing import org_member, save_config
from accounts.views.workspace_management import UserListAPIView, UserRoleUpdateAPIView
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

landing = _landing_fixture


def management_request(landing, entry, actor):
    clear_workspace_context()
    factory = APIRequestFactory()
    if entry == "list":
        request = factory.get("/accounts/user/list/")
        view = UserListAPIView
    else:
        request = factory.post(
            "/accounts/user/role/update/",
            {
                "user_id": str(landing.user.id),
                "new_role": OrganizationRoles.WORKSPACE_VIEWER,
            },
            format="json",
        )
        view = UserRoleUpdateAPIView
    request.organization = landing.org
    force_authenticate(request, user=actor)
    return view.as_view()(request)


@pytest.mark.parametrize("entry", ["list", "update"])
def test_management_default_context_honors_saved_workspace(landing, entry):
    landing.other_mem.role = OrganizationRoles.WORKSPACE_MEMBER
    landing.other_mem.save(update_fields=["role"])
    save_config(
        landing,
        {"orgWorkspaceMap": {str(landing.org.id): str(landing.other.id)}},
        user=landing.owner,
    )
    response = management_request(landing, entry, landing.owner)
    assert response.status_code == 200, response.data
    if entry == "list":
        row = next(
            r for r in response.data["results"] if r["id"] == str(landing.user.id)
        )
        assert row["workspace_role"] == OrganizationRoles.WORKSPACE_MEMBER
    else:
        landing.other_mem.refresh_from_db()
        landing.default_mem.refresh_from_db()
        assert landing.other_mem.role == OrganizationRoles.WORKSPACE_VIEWER
        assert landing.default_mem.role == OrganizationRoles.WORKSPACE_ADMIN
    assert not WorkspaceMembership._base_manager.filter(user=landing.owner).exists()


@pytest.mark.parametrize("entry", ["list", "update"])
def test_management_default_context_cannot_select_denied_workspace(landing, entry):
    actor, _ = org_member(
        landing.org,
        "unjoined-context@example.test",
        OrganizationRoles.MEMBER,
        Level.MEMBER,
    )
    assert not actor.can_access_workspace(landing.default)
    response = management_request(landing, entry, actor)
    assert response.status_code == 400, response.data
    landing.default_mem.refresh_from_db()
    assert landing.default_mem.role == OrganizationRoles.WORKSPACE_ADMIN
    assert not WorkspaceMembership._base_manager.filter(user=actor).exists()


@pytest.mark.parametrize(
    "role,level",
    [
        (OrganizationRoles.MEMBER, Level.MEMBER),
        (OrganizationRoles.OWNER, Level.OWNER),
    ],
)
@pytest.mark.django_db
def test_normal_auth_empty_organization_does_not_provision(role, level):
    clear_workspace_context()
    org = Organization.objects.create(name="Unprovisioned organization")
    user, _ = org_member(org, "empty-context@example.test", role, level)
    request = SimpleNamespace(headers={}, GET={}, method="GET", path="/accounts/user/")
    APIKeyAuthentication()._set_workspace_context(request, user)
    assert request.organization == org
    assert request.workspace is None
    assert not Workspace._base_manager.filter(organization=org).exists()
    assert not WorkspaceMembership._base_manager.filter(user=user).exists()
