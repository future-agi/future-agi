"""An empty feed succeeds in the request's organization; without one nothing is read."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.views.feed import _permissions, list_view, tab_views

pytestmark = pytest.mark.unit


@pytest.fixture(params=["list", "stats"])
def endpoint(request, monkeypatch):
    """Exercise real request validation and project resolution without data stores."""
    org = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(is_authenticated=True, organization=org)
    if request.param == "list":
        view = list_view.FeedListView
        path = "/tracer/feed/issues/"
        params = {"limit": 7, "offset": 14, "time_range_days": 7}
        payload = {"data": [], "total": 0, "limit": 7, "offset": 14}
        service_name = "list_feed_issues"
    else:
        view = list_view.FeedStatsView
        path = "/tracer/feed/issues/stats/"
        params = {"time_range_days": 7}
        payload = {
            "total_errors": 0,
            "escalating": 0,
            "for_review": 0,
            "acknowledged": 0,
            "resolved": 0,
            "affected_users": 0,
        }
        service_name = "get_feed_stats"

    api_request = APIRequestFactory().get(path, params)
    api_request.organization = org
    api_request.workspace = None
    force_authenticate(api_request, user=user)

    projects = Mock()
    projects.filter.return_value = projects
    projects.values_list.return_value = []
    project_filter = Mock(return_value=projects)
    monkeypatch.setattr(_permissions.Project.objects, "filter", project_filter)
    membership_org = Mock(return_value=None)
    monkeypatch.setattr("accounts.utils.get_user_organization", membership_org)
    service = Mock(return_value=payload)
    monkeypatch.setattr(list_view.feed_service, service_name, service)
    # Licensing has its own tests; retain the real authentication/permission path.
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *args, **kwargs: None)

    return SimpleNamespace(
        view=view,
        request=api_request,
        org=org,
        user=user,
        projects=projects,
        project_filter=project_filter,
        membership_org=membership_org,
        service=service,
        payload=payload,
    )


def test_new_organization_without_projects_returns_empty_success(endpoint):
    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {"status": True, "result": endpoint.payload}
    endpoint.project_filter.assert_called_once_with(organization_id=endpoint.org.id)
    endpoint.service.assert_called_once()
    assert endpoint.service.call_args.kwargs["project_ids"] == []
    assert endpoint.service.call_args.kwargs["time_range_days"] == 7


def test_empty_workspace_returns_empty_success(endpoint):
    workspace = SimpleNamespace(id=uuid4(), organization_id=endpoint.org.id)
    endpoint.request.workspace = workspace

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {"status": True, "result": endpoint.payload}
    endpoint.project_filter.assert_called_once_with(organization_id=endpoint.org.id)
    endpoint.projects.filter.assert_called_once_with(workspace_id=workspace.id)
    assert endpoint.service.call_args.kwargs["project_ids"] == []


def test_membership_alone_without_a_request_organization_is_forbidden(endpoint):
    endpoint.request.organization = None
    endpoint.user.organization = None
    endpoint.membership_org.return_value = endpoint.org
    endpoint.projects.values_list.return_value = [uuid4()]

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["result"] == "User not associated with an organization"
    endpoint.membership_org.assert_not_called()
    endpoint.project_filter.assert_not_called()
    endpoint.service.assert_not_called()


def test_request_organization_precedes_membership_and_legacy_org(endpoint):
    project_id = uuid4()
    endpoint.user.organization = SimpleNamespace(id=uuid4())
    endpoint.membership_org.return_value = SimpleNamespace(id=uuid4())
    endpoint.projects.values_list.return_value = [project_id]

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_200_OK
    endpoint.membership_org.assert_not_called()
    endpoint.project_filter.assert_called_once_with(organization_id=endpoint.org.id)
    assert endpoint.service.call_args.kwargs["project_ids"] == [str(project_id)]


def test_legacy_organization_alone_is_forbidden(endpoint):
    endpoint.request.organization = None
    endpoint.projects.values_list.return_value = [uuid4()]

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["result"] == "User not associated with an organization"
    endpoint.project_filter.assert_not_called()
    endpoint.service.assert_not_called()


@pytest.mark.parametrize("workspace_scoped", [False, True], ids=["org", "workspace"])
def test_inaccessible_explicit_project_still_returns_forbidden(
    endpoint, workspace_scoped
):
    if workspace_scoped:
        endpoint.request.workspace = SimpleNamespace(id=uuid4())
    endpoint.projects.values_list.return_value = [uuid4()]
    endpoint.request.GET = endpoint.request.GET.copy()
    endpoint.request.GET["project_id"] = str(uuid4())

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["result"] == "Access denied to this project"
    endpoint.project_filter.assert_called_once_with(organization_id=endpoint.org.id)
    if workspace_scoped:
        endpoint.projects.filter.assert_called_once_with(
            workspace_id=endpoint.request.workspace.id
        )
    endpoint.service.assert_not_called()


def test_missing_organization_never_opens_an_unscoped_project_query(endpoint):
    endpoint.request.organization = None
    endpoint.user.organization = None

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["result"] == "User not associated with an organization"
    endpoint.project_filter.assert_not_called()
    endpoint.service.assert_not_called()


def test_service_failure_remains_an_error_not_an_empty_success(endpoint):
    endpoint.projects.values_list.return_value = [uuid4()]
    endpoint.service.side_effect = RuntimeError("feed service unavailable")

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["status"] is False
    endpoint.service.assert_called_once()


# The project scope is chosen only by the organization on the request.

_NO_ATTRIBUTE = object()


class _ProjectRows:
    """Project rows in memory, filtered by exact field values like the ORM."""

    def __init__(self, rows):
        self._rows = rows

    def filter(self, **lookup):
        return _ProjectRows(
            [row for row in self._rows if all(row[k] == v for k, v in lookup.items())]
        )

    def values_list(self, field, *, flat=False):
        return [row[field] for row in self._rows]


def _row(project_id, organization, workspace_id):
    return {
        "id": project_id,
        "organization_id": organization.id,
        "workspace_id": workspace_id,
    }


def _scope_request(organization, account_organization, workspace=None):
    """A request as the views receive it, with the user's own organization."""
    request = SimpleNamespace(
        workspace=workspace,
        user=SimpleNamespace(organization=account_organization),
    )
    if organization is not _NO_ATTRIBUTE:
        request.organization = organization
    return request


@pytest.fixture
def two_organizations(monkeypatch):
    """The request's organization and another one, each with projects.

    The request's organization has one project in the selected workspace and
    one in another workspace; the other organization has one project.
    """
    selected = SimpleNamespace(id=uuid4())
    other = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), organization_id=selected.id)
    in_workspace, elsewhere, other_project = uuid4(), uuid4(), uuid4()
    rows = _ProjectRows(
        [
            _row(in_workspace, selected, workspace.id),
            _row(elsewhere, selected, uuid4()),
            _row(other_project, other, uuid4()),
        ]
    )
    lookups = Mock(side_effect=rows.filter)
    monkeypatch.setattr(_permissions.Project.objects, "filter", lookups)
    return SimpleNamespace(
        selected=selected,
        other=other,
        workspace=workspace,
        in_workspace=str(in_workspace),
        elsewhere=str(elsewhere),
        other_project=str(other_project),
        lookups=lookups,
    )


@pytest.mark.parametrize("request_organization", ["missing", "none"])
def test_account_organization_alone_resolves_no_projects(
    two_organizations, request_organization
):
    orgs = two_organizations
    request = _scope_request(
        _NO_ATTRIBUTE if request_organization == "missing" else None,
        account_organization=orgs.other,
    )

    assert _permissions.get_accessible_project_ids(request) == []
    orgs.lookups.assert_not_called()


def test_requested_project_in_the_account_organization_is_refused(
    two_organizations,
):
    orgs = two_organizations
    request = _scope_request(None, account_organization=orgs.other)

    resolved = _permissions.resolve_requested_project_ids(request, orgs.other_project)

    assert resolved is None
    orgs.lookups.assert_not_called()


@pytest.mark.parametrize(
    "in_workspace", [False, True], ids=["whole_organization", "one_workspace"]
)
def test_scope_is_the_request_organizations_projects(two_organizations, in_workspace):
    orgs = two_organizations
    request = _scope_request(
        orgs.selected,
        account_organization=None,
        workspace=orgs.workspace if in_workspace else None,
    )

    project_ids = _permissions.get_accessible_project_ids(request)

    if in_workspace:
        assert project_ids == [orgs.in_workspace]
    else:
        assert project_ids == [orgs.in_workspace, orgs.elsewhere]
    orgs.lookups.assert_called_once_with(organization_id=orgs.selected.id)


def test_request_organization_wins_over_a_different_account_organization(
    two_organizations,
):
    orgs = two_organizations
    request = _scope_request(orgs.selected, account_organization=orgs.other)

    project_ids = _permissions.get_accessible_project_ids(request)

    assert project_ids == [orgs.in_workspace, orgs.elsewhere]
    assert orgs.other_project not in project_ids
    orgs.lookups.assert_called_once_with(organization_id=orgs.selected.id)


TABS = {
    "overview": (tab_views.FeedOverviewView, "get_overview_tab"),
    "traces": (tab_views.FeedTracesView, "get_traces_tab"),
    "trends": (tab_views.FeedTrendsView, "get_trends_tab"),
    "sidebar": (tab_views.FeedSidebarView, "get_sidebar"),
}


@pytest.mark.parametrize("tab", list(TABS))
def test_tab_reads_no_projects_from_the_account_organization(
    two_organizations, monkeypatch, tab
):
    orgs = two_organizations
    view, service_name = TABS[tab]
    service = Mock(return_value=None)
    monkeypatch.setattr(tab_views.feed_service, service_name, service)
    # Licensing has its own tests; retain the real authentication/permission path.
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *args, **kwargs: None)
    user = SimpleNamespace(is_authenticated=True, organization=orgs.other)
    request = APIRequestFactory().get(f"/tracer/feed/issues/cluster-1/{tab}/")
    request.organization = None
    request.workspace = None
    force_authenticate(request, user=user)

    response = view.as_view()(request, cluster_id="cluster-1")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["result"] == "User not associated with an organization"
    orgs.lookups.assert_not_called()
    service.assert_not_called()
