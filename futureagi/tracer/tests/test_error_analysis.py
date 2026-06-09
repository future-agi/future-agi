"""
Error Analysis API Tests

Tests for trace error analysis endpoints.
"""

import uuid

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.models.trace import Trace, TraceErrorAnalysisStatus
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    Priority,
    TraceErrorAnalysis,
    TraceErrorGroup,
)
from tracer.models.trace_error_analysis_task import (
    TraceErrorAnalysisTask,
    TraceErrorTaskStatus,
)

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


def make_feed_group(project, cluster_id, *, priority=Priority.MEDIUM):
    now = timezone.now()
    return TraceErrorGroup.objects.create(
        project=project,
        cluster_id=cluster_id,
        error_type=f"{cluster_id}-error",
        source=ClusterSource.SCANNER,
        issue_group="Tool Failures",
        issue_category="Language-only",
        fix_layer="Tools",
        title=f"{cluster_id} test issue",
        status=FeedIssueStatus.ESCALATING,
        priority=priority,
        first_seen=now,
        last_seen=now,
        error_count=1,
        unique_traces=1,
    )


@pytest.mark.integration
@pytest.mark.api
class TestTraceErrorAnalysisAPI:
    """Tests for GET /tracer/trace-error-analysis/{trace_id}/ endpoint."""

    def test_get_error_analysis_unauthenticated(self, api_client, trace):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/trace-error-analysis/{trace.id}/")
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_error_analysis_success(self, auth_client, trace):
        """Get error analysis for a trace."""
        response = auth_client.get(f"/tracer/trace-error-analysis/{trace.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Should return analysis data or empty if not analyzed
        assert isinstance(data, dict)

    def test_get_error_analysis_not_found(self, auth_client):
        """Get error analysis for non-existent trace."""
        fake_id = uuid.uuid4()
        response = auth_client.get(f"/tracer/trace-error-analysis/{fake_id}/")
        assert response.status_code in [
            status.HTTP_200_OK,  # May return empty analysis
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestErrorClusterFeedAPI:
    """Tests for GET /tracer/feed/issues/ endpoint."""

    url = "/tracer/feed/issues/"

    def test_get_cluster_feed_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            self.url,
            {"project_id": str(project.id)},
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_cluster_feed_missing_project(self, auth_client):
        """Get cluster feed without project ID returns empty or default."""
        response = auth_client.get(self.url)
        # Org-scoped feed requires the user to have accessible projects.
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_get_cluster_feed_success(self, auth_client, project):
        """Get error cluster feed for a project."""
        response = auth_client.get(
            self.url,
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "data" in data and "total" in data

    def test_get_cluster_feed_with_pagination(self, auth_client, project):
        """Get cluster feed with pagination."""
        response = auth_client.get(
            self.url,
            {
                "project_id": str(project.id),
                "offset": 0,
                "limit": 10,
            },
        )
        assert response.status_code == status.HTTP_200_OK

    def test_get_cluster_feed_filters_by_severity(self, auth_client, project):
        """Severity filter should map frontend severity to backend priority."""
        make_feed_group(project, "TEST-HIGH", priority=Priority.HIGH)
        make_feed_group(project, "TEST-LOW", priority=Priority.LOW)

        response = auth_client.get(
            self.url,
            {"project_id": str(project.id), "severity": "high"},
        )

        assert response.status_code == status.HTTP_200_OK
        rows = get_result(response)["data"]
        assert {row["cluster_id"] for row in rows} == {"TEST-HIGH"}

    def test_get_cluster_feed_sorts_by_severity(self, auth_client, project):
        """Severity sort should not silently fall back to last_seen."""
        make_feed_group(project, "TEST-LOW", priority=Priority.LOW)
        make_feed_group(project, "TEST-HIGH", priority=Priority.HIGH)
        make_feed_group(project, "TEST-CRITICAL", priority=Priority.URGENT)

        response = auth_client.get(
            self.url,
            {
                "project_id": str(project.id),
                "sort_by": "severity",
                "sort_dir": "desc",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        rows = get_result(response)["data"]
        assert [row["cluster_id"] for row in rows[:3]] == [
            "TEST-CRITICAL",
            "TEST-HIGH",
            "TEST-LOW",
        ]


@pytest.mark.integration
@pytest.mark.api
class TestErrorClusterDetailAPI:
    """Tests for GET /tracer/feed/issues/{cluster_id}/ endpoint."""

    def test_get_cluster_detail_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        fake_cluster_id = "cluster_123"
        response = api_client.get(f"/tracer/feed/issues/{fake_cluster_id}/")
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_cluster_detail_not_found(self, auth_client):
        """Get cluster detail for non-existent cluster."""
        fake_cluster_id = "nonexistent_cluster"
        response = auth_client.get(f"/tracer/feed/issues/{fake_cluster_id}/")
        assert response.status_code in [
            status.HTTP_200_OK,  # May return empty
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_detail_tabs_do_not_expose_other_workspace_clusters(
        self, auth_client, user, project
    ):
        """Tab endpoints must use the same workspace scope as list/detail."""
        other_org = Organization.objects.create(name="Other Feed Org")
        other_workspace = Workspace.objects.create(
            name="Other Feed Workspace",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        other_project = Project.objects.create(
            name="Other Feed Project",
            organization=other_org,
            workspace=other_workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        other_cluster = make_feed_group(other_project, "OTHER-FEED")
        other_trace = Trace.objects.create(
            project=other_project,
            name="Other Trace",
            input={"prompt": "hidden"},
            output={"response": "hidden"},
        )
        ErrorClusterTraces.objects.create(
            cluster=other_cluster,
            trace=other_trace,
        )

        paths = [
            f"/tracer/feed/issues/{other_cluster.cluster_id}/overview/",
            f"/tracer/feed/issues/{other_cluster.cluster_id}/traces/",
            f"/tracer/feed/issues/{other_cluster.cluster_id}/trends/",
            f"/tracer/feed/issues/{other_cluster.cluster_id}/sidebar/",
            (
                f"/tracer/feed/issues/{other_cluster.cluster_id}/root-cause/"
                f"?trace_id={other_trace.id}"
            ),
        ]

        for path in paths:
            response = auth_client.get(path)
            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_patch_cluster_updates_and_clears_assignee(
        self, auth_client, project, user
    ):
        """PATCH should persist status/severity and treat assignee=null as clear."""
        cluster = make_feed_group(project, "PATCH-FEED", priority=Priority.MEDIUM)

        response = auth_client.patch(
            f"/tracer/feed/issues/{cluster.cluster_id}/",
            {
                "status": FeedIssueStatus.ACKNOWLEDGED,
                "severity": "low",
                "assignee": user.email,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert result["row"]["status"] == FeedIssueStatus.ACKNOWLEDGED
        assert result["row"]["severity"] == "low"
        assert user.email in result["row"]["assignees"]
        cluster.refresh_from_db()
        assert cluster.status == FeedIssueStatus.ACKNOWLEDGED
        assert cluster.priority == Priority.LOW
        assert cluster.assignee_id == user.id

        response = auth_client.patch(
            f"/tracer/feed/issues/{cluster.cluster_id}/",
            {"assignee": None},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        cluster.refresh_from_db()
        assert cluster.assignee_id is None

    def test_patch_cluster_rejects_other_org_assignee(self, auth_client, project):
        """Assignee emails must belong to the cluster organization."""
        cluster = make_feed_group(project, "PATCH-FOREIGN", priority=Priority.MEDIUM)
        other_org = Organization.objects.create(name="Other Assignee Org")
        other_user = User.objects.create_user(
            email=f"other-assignee-{uuid.uuid4()}@example.com",
            password="test",
            name="Other Assignee",
            organization=other_org,
        )

        response = auth_client.patch(
            f"/tracer/feed/issues/{cluster.cluster_id}/",
            {"assignee": other_user.email},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        cluster.refresh_from_db()
        assert cluster.assignee_id is None

    def test_deep_analysis_cached_dispatch_does_not_start_task(
        self, auth_client, project, mocker
    ):
        """Cached analysis POST should return done without queueing a new task."""
        cluster = make_feed_group(project, "DEEP-CACHED", priority=Priority.HIGH)
        trace = Trace.objects.create(project=project, name="Cached analysis trace")
        ErrorClusterTraces.objects.create(cluster=cluster, trace=trace)
        TraceErrorAnalysis.objects.create(trace=trace, project=project)
        trace.error_analysis_status = TraceErrorAnalysisStatus.COMPLETED
        trace.save(update_fields=["error_analysis_status"])
        delay = mocker.patch("tracer.tasks.run_deep_analysis_on_demand.delay")

        response = auth_client.post(
            f"/tracer/feed/issues/{cluster.cluster_id}/deep-analysis/",
            {"trace_id": str(trace.id), "force": False},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert result["status"] == "done"
        assert result["trace_id"] == str(trace.id)
        delay.assert_not_called()

    def test_create_linear_issue_without_connection_does_not_mutate_cluster(
        self, auth_client, project
    ):
        """Without Linear integration, create-linear-issue must fail closed."""
        cluster = make_feed_group(project, "LINEAR-NONE", priority=Priority.MEDIUM)

        response = auth_client.post(
            f"/tracer/feed/issues/{cluster.cluster_id}/create-linear-issue/",
            {"team_id": "team-local-test"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        cluster.refresh_from_db()
        assert not cluster.external_issue_url
        assert not cluster.external_issue_id


@pytest.mark.integration
@pytest.mark.api
class TestTraceErrorTaskAPI:
    """Tests for /tracer/trace-error-task/{project_id}/ endpoint."""

    def test_get_error_task_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/trace-error-task/{project.id}/")
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_error_task_success(self, auth_client, project):
        """Get error task status for a project."""
        response = auth_client.get(f"/tracer/trace-error-task/{project.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Should return task status info
        assert isinstance(data, dict)

    def test_get_error_task_reactivates_soft_deleted_task(self, auth_client, project):
        """Soft-deleted one-to-one task rows should not make GET return 500."""
        task = TraceErrorAnalysisTask.objects.create(
            project=project,
            sampling_rate=0.42,
            status=TraceErrorTaskStatus.PAUSED,
        )
        task.delete()

        response = auth_client.get(f"/tracer/trace-error-task/{project.id}/")

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert result["project_id"] == str(project.id)
        assert result["sampling_rate"] == 0.01
        assert result["status"] == TraceErrorTaskStatus.WAITING
        task.refresh_from_db()
        assert task.deleted is False
        assert task.deleted_at is None

    def test_create_error_task_unauthenticated(self, api_client, project):
        """Unauthenticated POST requests should be rejected."""
        response = api_client.post(
            f"/tracer/trace-error-task/{project.id}/",
            {"sampling_rate": 0.5},
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_create_error_task_success(self, auth_client, project):
        """Create or update error task for a project."""
        response = auth_client.post(
            f"/tracer/trace-error-task/{project.id}/",
            {"sampling_rate": 0.5},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_create_error_task_invalid_sampling_rate(self, auth_client, project):
        """Create error task with invalid sampling rate fails."""
        # Sampling rate > 1
        response = auth_client.post(
            f"/tracer/trace-error-task/{project.id}/",
            {"sampling_rate": 1.5},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_error_task_not_found(self, auth_client):
        """Get error task for non-existent project."""
        fake_id = uuid.uuid4()
        response = auth_client.get(f"/tracer/trace-error-task/{fake_id}/")
        assert response.status_code in [
            status.HTTP_200_OK,  # May return default
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]
