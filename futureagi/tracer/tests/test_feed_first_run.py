"""OBS-SW-107: empty feeds are successful and membership scope is respected."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.views.feed import _permissions, list_view

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
    workspace = SimpleNamespace(id=uuid4())
    endpoint.request.workspace = workspace

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {"status": True, "result": endpoint.payload}
    endpoint.project_filter.assert_called_once_with(organization_id=endpoint.org.id)
    endpoint.projects.filter.assert_called_once_with(workspace_id=workspace.id)
    assert endpoint.service.call_args.kwargs["project_ids"] == []


def test_active_membership_without_legacy_org_resolves_projects(endpoint):
    project_id = uuid4()
    endpoint.request.organization = None
    endpoint.user.organization = None
    endpoint.membership_org.return_value = endpoint.org
    endpoint.projects.values_list.return_value = [project_id]

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_200_OK
    endpoint.membership_org.assert_called_once_with(endpoint.user)
    endpoint.project_filter.assert_called_once_with(organization_id=endpoint.org.id)
    assert endpoint.service.call_args.kwargs["project_ids"] == [str(project_id)]


def test_active_membership_precedes_stale_legacy_org(endpoint):
    project_id = uuid4()
    endpoint.request.organization = None
    endpoint.user.organization = SimpleNamespace(id=uuid4())
    endpoint.membership_org.return_value = endpoint.org
    endpoint.projects.values_list.return_value = [project_id]

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_200_OK
    endpoint.membership_org.assert_called_once_with(endpoint.user)
    endpoint.project_filter.assert_called_once_with(organization_id=endpoint.org.id)
    assert endpoint.service.call_args.kwargs["project_ids"] == [str(project_id)]


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


def test_legacy_organization_remains_a_fallback(endpoint):
    project_id = uuid4()
    endpoint.request.organization = None
    endpoint.projects.values_list.return_value = [project_id]

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_200_OK
    endpoint.project_filter.assert_called_once_with(organization_id=endpoint.org.id)
    assert endpoint.service.call_args.kwargs["project_ids"] == [str(project_id)]


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

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {"status": True, "result": endpoint.payload}
    endpoint.project_filter.assert_not_called()
    assert endpoint.service.call_args.kwargs["project_ids"] == []


def test_service_failure_remains_an_error_not_an_empty_success(endpoint):
    endpoint.projects.values_list.return_value = [uuid4()]
    endpoint.service.side_effect = RuntimeError("feed service unavailable")

    response = endpoint.view.as_view()(endpoint.request)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["status"] is False
    endpoint.service.assert_called_once()
