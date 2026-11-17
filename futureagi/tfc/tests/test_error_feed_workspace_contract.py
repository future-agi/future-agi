"""Error-feed workspace selection is auth context, not a new endpoint filter.

Generate the four read contracts in-process; do not depend on the checked-in
Swagger snapshot. Runtime checks use the real auth workspace resolver and feed
permissions with mocked credentials, ORM lookups and feed services (no database).
"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from django.urls import path
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator
from rest_framework.test import APIRequestFactory

from accounts.authentication import APIKeyAuthentication
from accounts.models.workspace import Workspace
from tracer.models.project import Project
from tracer.utils import feed as feed_service
from tracer.views.feed.detail_view import FeedDetailView
from tracer.views.feed.list_view import FeedListView
from tracer.views.feed.tab_views import FeedOverviewView, FeedTracesView

READS = [
    ("tracer/feed/issues/", FeedListView, "list_feed_issues"),
    (
        "tracer/feed/issues/<str:cluster_id>/",
        FeedDetailView,
        "get_feed_detail",
    ),
    (
        "tracer/feed/issues/<str:cluster_id>/overview/",
        FeedOverviewView,
        "get_overview_tab",
    ),
    (
        "tracer/feed/issues/<str:cluster_id>/traces/",
        FeedTracesView,
        "get_traces_tab",
    ),
]
READ_IDS = ["list", "detail", "overview", "traces"]


@pytest.fixture(scope="module")
def feed_schema():
    generator = OpenAPISchemaGenerator(
        openapi.Info(title="Error feed", default_version="v1"),
        patterns=[path(route, view.as_view()) for route, view, _ in READS],
    )
    return generator.get_schema(request=None, public=True)


@pytest.mark.parametrize("route,view,service_name", READS, ids=READ_IDS)
def test_error_feed_reads_document_optional_workspace_uuid(
    feed_schema, route, view, service_name
):
    full_path = "/" + route.replace("<str:cluster_id>", "{cluster_id}")
    base_path = feed_schema.get("basePath", "/").rstrip("/")
    operation = feed_schema["paths"][full_path[len(base_path) :]]["get"]
    parameters = [
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("name") == "workspace_id" and parameter.get("in") == "query"
    ]
    assert len(parameters) == 1, f"{full_path} omits the auth workspace selector"
    parameter = parameters[0]
    assert parameter.get("required", False) is False
    assert parameter["type"] == "string"
    assert parameter["format"] == "uuid"


@pytest.mark.parametrize("route,view,service_name", READS, ids=READ_IDS)
@pytest.mark.parametrize("selection", ["query", "header", "default", "pinned"])
def test_error_feed_reads_keep_auth_workspace_selection(
    monkeypatch, route, view, service_name, selection
):
    organization = SimpleNamespace(id=UUID(int=1))
    landing = SimpleNamespace(id=UUID(int=2), is_active=True)
    selected = SimpleNamespace(id=UUID(int=3), is_active=True)
    user = SimpleNamespace(is_authenticated=True, organization=organization)
    project_id = UUID(int=4)
    expected_workspace = landing if selection in ("default", "pinned") else selected
    key = SimpleNamespace(workspace=landing if selection == "pinned" else None)
    lookup = Mock(return_value=selected)
    monkeypatch.setattr(Workspace.objects, "get", lookup)
    monkeypatch.setattr(
        APIKeyAuthentication, "_user_has_workspace_access", Mock(return_value=True)
    )

    def authenticate(auth, request):
        request.org_api_key = key
        request.organization = organization
        request.workspace = (
            auth._get_requested_workspace(request, user, organization) or landing
        )
        return user, None

    monkeypatch.setattr(APIKeyAuthentication, "authenticate", authenticate)
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", Mock())
    projects = Mock()
    projects.filter.return_value.values_list.return_value = [project_id]
    project_lookup = Mock(return_value=projects)
    monkeypatch.setattr(Project.objects, "filter", project_lookup)
    result = (
        {"data": [], "total": 0, "limit": 25, "offset": 0}
        if view is FeedListView
        else None
    )
    service = Mock(return_value=result)
    monkeypatch.setattr(feed_service, service_name, service)

    query = {}
    headers = {}
    if selection == "query":
        query["workspace_id"] = str(selected.id)
    elif selection == "header":
        query["workspace_id"] = str(landing.id)
        headers["HTTP_X_WORKSPACE_ID"] = str(selected.id)
    elif selection == "pinned":
        # Auth ignores this for an active pinned key; endpoint validation must
        # not introduce a new rejection for an otherwise ignored selector.
        query["workspace_id"] = "ignored-for-pinned-key"
    request_path = "/" + route.replace("<str:cluster_id>", "cluster-1")
    request = APIRequestFactory().get(request_path, query, **headers)
    kwargs = {"cluster_id": "cluster-1"} if "<str:cluster_id>" in route else {}
    response = view.as_view(authentication_classes=[APIKeyAuthentication])(
        request, **kwargs
    )

    assert response.status_code == (200 if view is FeedListView else 404)
    project_lookup.assert_called_once_with(organization_id=organization.id)
    projects.filter.assert_called_once_with(workspace_id=expected_workspace.id)
    projects.filter.return_value.values_list.assert_called_once_with("id", flat=True)
    service.assert_called_once()
    if view is FeedListView:
        assert service.call_args.kwargs["project_ids"] == [str(project_id)]
    else:
        assert service.call_args.args == ("cluster-1", [str(project_id)])
    if selection in ("query", "header"):
        lookup.assert_called_once_with(
            id=str(selected.id), organization=organization, is_active=True
        )
    else:
        lookup.assert_not_called()
