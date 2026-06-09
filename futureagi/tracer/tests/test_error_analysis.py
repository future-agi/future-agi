"""
Error Analysis API Tests

Tests for trace error analysis endpoints.
"""

import uuid

import pytest
from rest_framework import status


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


@pytest.mark.integration
@pytest.mark.api
class TestTraceErrorAnalysisAPI:
    """Tests for GET /tracer/trace-error-analysis/{trace_id}/ endpoint."""

    def test_get_error_analysis_unauthenticated(self, api_client, trace):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/trace-error-analysis/{trace.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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


@pytest.mark.integration
@pytest.mark.api
class TestErrorClusterDetailAPI:
    """Tests for GET /tracer/feed/issues/{cluster_id}/ endpoint."""

    def test_get_cluster_detail_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        fake_cluster_id = "cluster_123"
        response = api_client.get(
            f"/tracer/feed/issues/{fake_cluster_id}/"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_cluster_detail_not_found(self, auth_client):
        """Get cluster detail for non-existent cluster."""
        fake_cluster_id = "nonexistent_cluster"
        response = auth_client.get(
            f"/tracer/feed/issues/{fake_cluster_id}/"
        )
        assert response.status_code in [
            status.HTTP_200_OK,  # May return empty
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestTraceErrorTaskAPI:
    """Tests for /tracer/trace-error-task/{project_id}/ endpoint."""

    def test_get_error_task_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/trace-error-task/{project.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_error_task_success(self, auth_client, project):
        """Get error task status for a project."""
        response = auth_client.get(f"/tracer/trace-error-task/{project.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Should return task status info
        assert isinstance(data, dict)

    def test_create_error_task_unauthenticated(self, api_client, project):
        """Unauthenticated POST requests should be rejected."""
        response = api_client.post(
            f"/tracer/trace-error-task/{project.id}/",
            {"sampling_rate": 0.5},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
