"""A denied landing is an empty data scope, not an unscoped request."""

from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey
from accounts.models.workspace import WorkspaceMembership
from accounts.tests import workspace_write_support as write_support
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from accounts.tests.test_workspace_landing import org_member
from accounts.tests.workspace_write_support import (
    credential_headers,
    membership_snapshot,
    request_view,
    write_snapshot,
)
from mcp_server.api_executor import (
    DjangoAPIExecutor,
    MCPRequestContext,
    ensure_urlconf_loaded,
)
from mcp_server.generated_registry import registry
from model_hub.models.develop_dataset import Dataset
from model_hub.views.develop_dataset import GetDatasetsView
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    WorkspaceContextMiddleware,
    clear_workspace_context,
    get_current_workspace,
    workspace_context,
)

landing = _landing_fixture
scope_no_egress = write_support.scope_no_egress
write_effects = write_support.write_effects


@pytest.fixture
def boundary(landing, request):
    clear_workspace_context()
    user, membership = org_member(
        landing.org,
        "unjoined-boundary@example.test",
        OrganizationRoles.MEMBER,
        Level.MEMBER,
    )
    if request.param in {"deactivated", "inactive-default"}:
        WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=(
                landing.default
                if request.param == "inactive-default"
                else landing.other
            ),
            organization_membership=membership,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=False,
        )
    hidden = Dataset.objects.create(
        name="Inaccessible workspace data",
        organization=landing.org,
        workspace=landing.other,
    )
    default = Dataset.objects.create(
        name="Inaccessible default data",
        organization=landing.org,
        workspace=landing.default,
    )
    legacy = Dataset.objects.create(
        name="Unassigned organization data", organization=landing.org
    )
    assert not user.can_access_workspace(landing.other)
    assert not user.can_access_workspace(landing.default)
    yield SimpleNamespace(
        landing=landing, user=user, hidden=hidden, default=default, legacy=legacy
    )
    clear_workspace_context()


def authenticate_data_request(boundary):
    key = OrgApiKey.no_workspace_objects.create(
        name="Synthetic boundary request",
        organization=boundary.landing.org,
        user=boundary.user,
        type="user",
    )
    return APIRequestFactory().get(
        "/model-hub/develops/get-datasets/",
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
    )


@pytest.mark.parametrize("boundary", ["absent", "deactivated"], indirect=True)
def test_no_workspace_landing_cannot_read_private_dataset(boundary):
    response = GetDatasetsView.as_view()(authenticate_data_request(boundary))
    assert response.status_code == 200, response.data
    payload = response.data.get("result", response.data)
    assert payload["datasets"] == []
    assert get_current_workspace() is None
    assert not boundary.user.can_access_workspace(boundary.landing.other)
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=boundary.user, is_active=True
    ).exists()


@pytest.mark.parametrize("boundary", ["absent", "deactivated"], indirect=True)
@pytest.mark.parametrize("manager", ["objects", "all_objects"])
def test_no_workspace_scope_cannot_query_or_bulk_mutate_data(boundary, manager):
    request = authenticate_data_request(boundary)
    APIKeyAuthentication()._set_workspace_context(request, boundary.user)
    assert request.workspace is None
    scoped = getattr(Dataset, manager)
    assert not scoped.filter(organization=boundary.landing.org).exists()
    assert scoped.filter(pk=boundary.hidden.pk).update(name="Changed") == 0
    assert scoped.filter(pk=boundary.hidden.pk).delete()[0] == 0
    boundary.hidden.refresh_from_db()
    assert boundary.hidden.name == "Inaccessible workspace data"
    # Org-level access and explicit membership checks must remain available.
    assert boundary.user.can_access_organization(boundary.landing.org)
    assert not boundary.user.can_access_workspace(boundary.landing.other)


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
def test_authorized_request_replaces_denied_scope(boundary):
    auth = APIKeyAuthentication()
    request = authenticate_data_request(boundary)
    auth._set_workspace_context(request, boundary.user)
    assert not Dataset.objects.exists()
    auth._set_workspace_context(request, boundary.landing.owner)
    assert request.workspace == boundary.landing.default
    assert Dataset.objects.filter(pk=boundary.default.pk).exists()
    assert not Dataset.objects.filter(pk=boundary.hidden.pk).exists()


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
def test_request_cleanup_restores_background_scope(boundary):
    request = authenticate_data_request(boundary)
    APIKeyAuthentication()._set_workspace_context(request, boundary.user)
    assert not Dataset.objects.exists()
    WorkspaceContextMiddleware(lambda request: None).process_response(request, None)
    assert Dataset.objects.filter(pk=boundary.hidden.pk).exists()
    with workspace_context(boundary.landing.other):
        assert Dataset.objects.filter(pk=boundary.hidden.pk).exists()
        assert not Dataset.objects.filter(pk=boundary.default.pk).exists()


@pytest.mark.parametrize("boundary", ["absent", "deactivated"], indirect=True)
def test_in_process_dataset_tool_keeps_denied_landing_empty(boundary):
    ensure_urlconf_loaded()
    payload = DjangoAPIExecutor().execute_sync(
        registry.get("list_datasets"),
        {},
        MCPRequestContext(boundary.user, boundary.landing.org, None),
    )
    assert payload["datasets"] == []
    assert not boundary.user.can_access_workspace(boundary.landing.other)
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=boundary.user, is_active=True
    ).exists()


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
@pytest.mark.parametrize("position", ["inside", "after", "exception"])
def test_nested_context_preserves_empty_authenticated_scope(boundary, position):
    request = authenticate_data_request(boundary)
    APIKeyAuthentication()._set_workspace_context(request, boundary.user)
    try:
        # In-process API execution (including MCP) wraps an already checked
        # request this way; it must not turn a None landing into background scope.
        with workspace_context(None, boundary.landing.org, boundary.user):
            if position == "inside":
                assert not Dataset.objects.exists()
            if position == "exception":
                raise RuntimeError("Synthetic nested failure")
    except RuntimeError:
        assert position == "exception"
    assert not Dataset.objects.exists()
    assert not boundary.user.can_access_workspace(boundary.landing.other)


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
def test_orgless_authentication_clears_previous_data_scope(boundary):
    auth = APIKeyAuthentication()
    request = authenticate_data_request(boundary)
    auth._set_workspace_context(request, boundary.landing.owner)
    boundary.user.organization = None
    boundary.user.save(update_fields=["organization"])
    OrganizationMembership.no_workspace_objects.filter(user=boundary.user).delete()
    auth._set_workspace_context(request, boundary.user)
    assert request.organization is None
    assert request.workspace is None
    assert get_current_workspace() is None
    assert not Dataset.objects.exists()


CREATE_DATASET = "/model-hub/develops/create-dataset-manually/"
CREATE_BODY = {
    "dataset_name": "scope-write-regression",
    "number_of_rows": 1,
    "number_of_columns": 1,
}


@pytest.mark.parametrize(
    "boundary", ["absent", "inactive-default", "deactivated"], indirect=True
)
@pytest.mark.parametrize("credential", ["session", "key"])
def test_empty_landing_refuses_dataset_insert(boundary, write_effects, credential):
    from tfc.utils.api_errors import first_error_code

    headers = credential_headers(boundary.user, boundary.landing.org, credential)
    before = write_snapshot()
    memberships = membership_snapshot()
    response = request_view(CREATE_DATASET, headers, CREATE_BODY)

    assert response.status_code == 403, response.data
    assert first_error_code(response.data) == "workspace_required"
    assert write_snapshot() == before
    assert membership_snapshot() == memberships
    write_effects.quota.assert_not_called()
    write_effects.quota.return_value.save.assert_not_called()
    write_effects.create.assert_not_called()


@pytest.mark.parametrize("actor", ["writer", "owner", "admin"])
@pytest.mark.parametrize("credential", ["session", "key"])
def test_authorized_dataset_insert_still_creates(
    landing, write_effects, actor, credential
):
    from model_hub.models.develop_dataset import Cell, Column, Row

    if actor == "writer":
        user = landing.user
        WorkspaceMembership._base_manager.filter(user=user).update(
            role=OrganizationRoles.WORKSPACE_MEMBER, level=Level.WORKSPACE_MEMBER
        )
    elif actor == "owner":
        user = landing.owner
    else:
        user, _ = org_member(
            landing.org,
            "write-admin@example.test",
            OrganizationRoles.ADMIN,
            Level.ADMIN,
        )
    headers = credential_headers(user, landing.org, credential)
    before = membership_snapshot()
    response = request_view(CREATE_DATASET, headers, CREATE_BODY)
    assert response.status_code == 200, response.data
    dataset = Dataset._base_manager.get(name=CREATE_BODY["dataset_name"])
    assert dataset.workspace_id == landing.default.pk
    assert dataset.organization_id == landing.org.pk
    for model in (Column, Row, Cell):
        assert model._base_manager.filter(dataset=dataset).count() == 1
    assert membership_snapshot() == before
    if actor != "writer":
        assert not WorkspaceMembership._base_manager.filter(user=user).exists()
    assert write_effects.quota.call_count == 2
    write_effects.create.assert_called_once()


@pytest.mark.parametrize("boundary", ["absent", "inactive-default"], indirect=True)
def test_empty_landing_gets_and_org_switch_still_work(
    boundary, write_effects, monkeypatch
):
    monkeypatch.setattr("accounts.views.user.get_user_checks", lambda *a, **kw: {})
    headers = credential_headers(boundary.user, boundary.landing.org)
    before = membership_snapshot()
    info = request_view("/accounts/user-info/", headers, method="get")
    assert info.status_code == 200, info.data
    assert info.data["default_workspace_id"] is None
    workspaces = request_view("/accounts/workspaces/", headers, method="get")
    assert workspaces.status_code == 200, workspaces.data
    assert workspaces.data["result"]["workspaces"] == []
    switched = request_view(
        "/accounts/organizations/switch/",
        headers,
        {"organization_id": str(boundary.landing.org.pk)},
    )
    assert switched.status_code == 200, switched.data
    assert membership_snapshot() == before


@pytest.mark.parametrize("role", ["Owner", "Admin"])
def test_global_actor_can_bootstrap_first_workspace(landing, write_effects, role):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    org = Organization.objects.create(name="Empty bootstrap org")
    user, _ = org_member(
        org,
        "bootstrap@example.test",
        role,
        Level.OWNER if role == "Owner" else Level.ADMIN,
    )
    assert not Workspace._base_manager.filter(organization=org).exists()
    headers = credential_headers(user, org)
    response = request_view(
        "/accounts/workspaces/", headers, {"name": "First workspace"}
    )
    assert response.status_code == 201, response.data
    workspace = Workspace._base_manager.get(organization=org)
    assert user.can_access_workspace(workspace)


@pytest.mark.parametrize("role", ["Owner", "Admin"])
def test_authorized_org_switch_can_bootstrap(landing, write_effects, role):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    org = Organization.objects.create(name="Switch bootstrap org")
    user, _ = org_member(
        org,
        "switch-bootstrap@example.test",
        role,
        Level.OWNER if role == "Owner" else Level.ADMIN,
    )
    headers = credential_headers(user, org)
    response = request_view(
        "/accounts/organizations/switch/", headers, {"organization_id": str(org.pk)}
    )
    assert response.status_code == 200, response.data
    workspace = Workspace._base_manager.get(organization=org)
    assert user.can_access_workspace(workspace)
