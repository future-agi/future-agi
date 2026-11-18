"""TH-8209: Error Feed list/stats with an authorized workspace that has zero
projects must be a normal empty result, not a forbidden "organization" error.

Covers the approved contract (company-brain error-feed.md, TH-8209 r1.1):
- R-1/R-2  valid org+workspace, zero accessible projects → HTTP 200 empty
  list / zero stats through the existing serializers.
- R-3      no authenticated organization → existing 403 stays.
- R-4      explicit inaccessible project → existing 403 stays.
- R-5      entitlement gate (HTTP 402) still runs before empty admission.
- R-6      populated scope still goes through the normal list path.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from tfc.ee_gating import EEFeature, FeatureUnavailable
from tracer.models.project import Project
from tracer.models.trace_error_analysis import ClusterSource, TraceErrorGroup
from tracer.queries import feed as feed_queries
from tracer.views.feed.list_view import FeedListView, FeedStatsView

pytestmark = pytest.mark.django_db

LIST_URL = "/tracer/feed/issues/"
STATS_URL = "/tracer/feed/issues/stats/"


@pytest.fixture
def entitled(monkeypatch):
    """Error Feed is licensed for these requests (the gate itself is covered
    by test_error_feed_gating.py)."""
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *a, **k: None)


@pytest.fixture
def unentitled(monkeypatch):
    def _deny(*_a, **_k):
        raise FeatureUnavailable(EEFeature.ERROR_FEED)

    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", _deny)


def _assert_no_projects(user, workspace):
    assert not Project.objects.filter(organization=user.organization).exists()
    assert not Project.objects.filter(workspace=workspace).exists()


class TestAuthorizedEmptyScope:
    def test_list_returns_empty_page_for_workspace_with_zero_projects(
        self, auth_client, user, workspace, entitled
    ):
        _assert_no_projects(user, workspace)

        response = auth_client.get(LIST_URL, {"limit": 10, "offset": 0})

        assert response.status_code == 200, response.data
        assert response.data["status"] is True
        assert response.data["result"] == {
            "data": [],
            "total": 0,
            "limit": 10,
            "offset": 0,
        }

    def test_list_default_pagination_echoes_through_empty_scope(
        self, auth_client, user, workspace, entitled
    ):
        _assert_no_projects(user, workspace)

        response = auth_client.get(LIST_URL)

        assert response.status_code == 200, response.data
        assert response.data["result"]["data"] == []
        assert response.data["result"]["total"] == 0
        assert response.data["result"]["limit"] == 25
        assert response.data["result"]["offset"] == 0

    def test_stats_returns_zero_counters_for_workspace_with_zero_projects(
        self, auth_client, user, workspace, entitled
    ):
        _assert_no_projects(user, workspace)

        response = auth_client.get(STATS_URL)

        assert response.status_code == 200, response.data
        assert response.data["status"] is True
        assert response.data["result"] == {
            "total_errors": 0,
            "escalating": 0,
            "acknowledged": 0,
            "for_review": 0,
            "resolved": 0,
            "affected_users": 0,
        }

    def test_empty_scope_does_not_touch_clickhouse_enrichment(
        self, auth_client, user, workspace, entitled
    ):
        _assert_no_projects(user, workspace)
        with (
            patch.object(feed_queries, "_fetch_trends_batch") as trends,
            patch.object(feed_queries, "_fetch_users_affected_batch") as users,
        ):
            users.return_value = {}
            list_response = auth_client.get(LIST_URL)
            stats_response = auth_client.get(STATS_URL)

        assert list_response.status_code == 200
        assert stats_response.status_code == 200
        trends.assert_not_called()
        # stats asks the batch helper with an empty id list at most; it must
        # never be asked for real cluster ids when there are no projects.
        for call in users.call_args_list:
            assert call.args[0] == []

    def test_invalid_query_is_still_a_400_in_empty_scope(
        self, auth_client, user, workspace, entitled
    ):
        _assert_no_projects(user, workspace)

        response = auth_client.get(LIST_URL, {"limit": 0})

        assert response.status_code == 400


class TestFailuresStayVisible:
    def test_no_request_organization_is_still_forbidden(self, user, entitled):
        """force_authenticate skips the auth class, so the request carries no
        resolved organization. The user-FK fallback inside the shared
        resolver must not qualify as an authorized empty scope."""
        request = APIRequestFactory().get(LIST_URL)
        force_authenticate(request, user=user)

        response = FeedListView.as_view()(request)

        assert response.status_code == 403
        assert response.data["result"] == "User not associated with an organization"

    def test_no_request_organization_is_still_forbidden_for_stats(
        self, user, entitled
    ):
        request = APIRequestFactory().get(STATS_URL)
        force_authenticate(request, user=user)

        response = FeedStatsView.as_view()(request)

        assert response.status_code == 403
        assert response.data["result"] == "User not associated with an organization"

    def test_user_without_organization_is_still_forbidden(self, user, entitled):
        user.organization = None
        user.save(update_fields=["organization"])
        request = APIRequestFactory().get(LIST_URL)
        force_authenticate(request, user=user)

        response = FeedListView.as_view()(request)

        assert response.status_code == 403
        assert response.data["result"] == "User not associated with an organization"

    def test_explicit_inaccessible_project_is_still_forbidden(
        self, auth_client, user, workspace, entitled
    ):
        _assert_no_projects(user, workspace)

        response = auth_client.get(LIST_URL, {"project_id": str(uuid.uuid4())})

        assert response.status_code == 403
        assert response.data["result"] == "Access denied to this project"

    def test_entitlement_gate_runs_before_empty_admission(
        self, auth_client, user, workspace, unentitled
    ):
        _assert_no_projects(user, workspace)

        list_response = auth_client.get(LIST_URL)
        stats_response = auth_client.get(STATS_URL)

        assert list_response.status_code == 402
        assert stats_response.status_code == 402

    def test_service_failure_is_still_a_400_in_empty_scope(
        self, auth_client, user, workspace, entitled
    ):
        _assert_no_projects(user, workspace)
        with patch.object(
            feed_queries, "list_clusters", side_effect=RuntimeError("boom")
        ):
            response = auth_client.get(LIST_URL)

        assert response.status_code == 400
        assert response.data["result"] == "Failed to fetch feed issues"

    def test_stats_service_failure_is_still_a_400_in_empty_scope(
        self, auth_client, user, workspace, entitled
    ):
        """M1: the empty-scope admission change on FeedStatsView must not turn
        a stats service failure into an empty 200. Same envelope as the list
        failure above."""
        _assert_no_projects(user, workspace)
        with patch.object(
            feed_queries, "get_stats", side_effect=RuntimeError("boom")
        ):
            response = auth_client.get(STATS_URL)

        assert response.status_code == 400
        assert response.data["result"] == "Failed to fetch feed stats"


class TestPopulatedScopeUnchanged:
    """Uses the shared `observe_project` fixture from tracer/tests/conftest.py."""

    def test_project_without_clusters_lists_empty_through_normal_path(
        self, auth_client, observe_project, entitled
    ):
        response = auth_client.get(LIST_URL)

        assert response.status_code == 200, response.data
        assert response.data["result"]["data"] == []
        assert response.data["result"]["total"] == 0

    def test_project_with_cluster_lists_the_row(
        self, auth_client, observe_project, entitled
    ):
        cluster = TraceErrorGroup.objects.create(
            project=observe_project,
            cluster_id="TH8209-ROW-1",
            error_type="legacy",
            issue_group="Tool Failures",
            title="Tool call failed",
            source=ClusterSource.SCANNER,
            error_count=1,
            total_events=1,
            unique_traces=1,
        )
        with (
            patch.object(feed_queries, "_fetch_trends_batch", return_value={}),
            patch.object(feed_queries, "_fetch_users_affected_batch", return_value={}),
            patch.object(feed_queries, "_fetch_sessions_batch", return_value={}),
            patch.object(feed_queries, "_fetch_latest_trace_id_batch", return_value={}),
        ):
            response = auth_client.get(LIST_URL)

        assert response.status_code == 200, response.data
        assert response.data["result"]["total"] == 1
        assert [row["cluster_id"] for row in response.data["result"]["data"]] == [
            cluster.cluster_id
        ]
