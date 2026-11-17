"""Signed-in readers keep their error feed list and stats.

Each request passes through the real authentication class, or through the
in-process tool executor, so the organization and workspace the views read are
the ones sign-in resolved. A scope with an issue and a scope with no projects
both answer 200.
"""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory

from accounts.models import OrgApiKey
from accounts.saml_credential import SamlCredential
from mcp_server.api_executor import (
    MCPRequestContext,
    executor,
    load_urlconf_outside_workspace,
)
from mcp_server.generated_registry import registry
from tracer.models.trace_error_analysis import ClusterSource, TraceErrorGroup
from tracer.queries import feed as feed_queries
from tracer.views.feed.list_view import FeedListView, FeedStatsView

pytestmark = pytest.mark.django_db

LIST_URL = "/tracer/feed/issues/"
STATS_URL = "/tracer/feed/issues/stats/"
SESSION_VALUE = "run-scoped-session"
KEY_ID = "run-scoped-key-id"
KEY_VALUE = "run-scoped-key-value"

ENDPOINTS = [
    pytest.param(FeedListView, LIST_URL, id="list"),
    pytest.param(FeedStatsView, STATS_URL, id="stats"),
]


@pytest.fixture(autouse=True)
def entitled(monkeypatch):
    """Licensing has its own tests; these requests are licensed."""
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def no_enrichment():
    """Rows come from the database alone; trend and user lookups stay empty."""
    with (
        patch.object(feed_queries, "_fetch_trends_batch", return_value={}),
        patch.object(feed_queries, "_fetch_users_affected_batch", return_value={}),
        patch.object(feed_queries, "_fetch_sessions_batch", return_value={}),
        patch.object(feed_queries, "_fetch_latest_trace_id_batch", return_value={}),
    ):
        yield


@pytest.fixture(params=["one_issue", "no_projects"])
def listed(request):
    """The cluster ids in the reader's scope."""
    if request.param == "no_projects":
        return []
    project = request.getfixturevalue("observe_project")
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="READER-ROW-1",
        error_type="legacy",
        issue_group="Tool Failures",
        title="Tool call failed",
        source=ClusterSource.SCANNER,
        error_count=1,
        total_events=1,
        unique_traces=1,
    )
    return [cluster.cluster_id]


def _assert_feed(response, path, listed):
    assert response.status_code == 200, response.data
    result = response.data["result"]
    if path == LIST_URL:
        assert [row["cluster_id"] for row in result["data"]] == listed
        assert result["total"] == len(listed)
    else:
        assert result["total_errors"] == len(listed)


@pytest.mark.parametrize("view, path", ENDPOINTS)
def test_signed_in_session_reads_its_feed(
    view, path, listed, user, organization, workspace, monkeypatch
):
    monkeypatch.setattr(
        "accounts.authentication.decode_token",
        lambda _token: (user, SESSION_VALUE, None),
    )
    request = APIRequestFactory().get(
        path,
        HTTP_AUTHORIZATION=f"Bearer {SESSION_VALUE}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )

    response = view.as_view()(request)

    _assert_feed(response, path, listed)


@pytest.mark.parametrize("view, path", ENDPOINTS)
def test_organization_bound_session_reads_its_feed(
    view, path, listed, user, organization, workspace, monkeypatch
):
    bound = SamlCredential(
        token_id=uuid4(),
        user_id=user.id,
        org_id=organization.id,
        idp_id=uuid4(),
        generation=1,
        validated_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        "accounts.authentication.decode_token",
        lambda _token: (user, SESSION_VALUE, bound),
    )
    request = APIRequestFactory().get(
        path,
        HTTP_AUTHORIZATION=f"Bearer {SESSION_VALUE}",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )

    response = view.as_view()(request)

    _assert_feed(response, path, listed)


@pytest.mark.parametrize("view, path", ENDPOINTS)
def test_personal_key_reads_its_feed(
    view, path, listed, user, organization, workspace, monkeypatch
):
    key = OrgApiKey(
        organization=organization, user=user, workspace=workspace, type="user"
    )
    monkeypatch.setattr(
        "accounts.authentication.authenticate_api_key", lambda *a, **k: key
    )
    request = APIRequestFactory().get(
        path, HTTP_X_API_KEY=KEY_ID, HTTP_X_SECRET_KEY=KEY_VALUE
    )

    response = view.as_view()(request)

    _assert_feed(response, path, listed)


def test_tool_call_reads_its_feed(listed, user, organization, workspace):
    load_urlconf_outside_workspace()
    context = MCPRequestContext(
        user=user, organization=organization, workspace=workspace
    )

    result = executor.execute_sync(registry.get("list_error_clusters"), {}, context)

    assert [row["cluster_id"] for row in result["data"]] == listed
    assert result["total"] == len(listed)
