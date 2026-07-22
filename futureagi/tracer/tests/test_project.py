"""
Project API Tests

Tests for /tracer/project/ endpoints.
"""

import json
import uuid
from datetime import UTC, timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.user import OrgApiKey
from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


def _iso_z(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _chart_filter(column_id, filter_type, filter_op, filter_value, col_type=None):
    filter_config = {
        "filter_type": filter_type,
        "filter_op": filter_op,
        "filter_value": filter_value,
    }
    if col_type:
        filter_config["col_type"] = col_type
    return {"column_id": column_id, "filter_config": filter_config}


def _traffic_sum(graph_payload):
    return sum(
        int(row.get("traffic", 0))
        for row in get_result_from_graph(graph_payload)["system_metrics"]["traffic"]
    )


def get_result_from_graph(payload):
    return payload.get("result", payload)


@pytest.mark.integration
@pytest.mark.api
class TestProjectListAPI:
    """Tests for GET /tracer/project/ endpoint."""

    def test_list_projects_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.get("/tracer/project/")
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_list_projects_empty(self, auth_client):
        """List returns empty when no projects exist."""
        response = auth_client.get("/tracer/project/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["projects"] == []
        assert data.get("total_count") == 0

    def test_list_projects_with_data(self, auth_client, project):
        """List returns projects for the organization."""
        response = auth_client.get("/tracer/project/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert len(data["projects"]) == 1
        assert data.get("total_count") == 1
        assert data["projects"][0]["name"] == "Test Project"
        assert data["projects"][0].get("trace_type") == "experiment"

    def test_list_projects_with_trace_count(self, auth_client, project, trace):
        """List includes trace count for each project."""
        response = auth_client.get("/tracer/project/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["projects"][0].get("trace_count") == 1

    def test_list_projects_filter_by_name(self, auth_client, project, observe_project):
        """Filter projects by name."""
        response = auth_client.get("/tracer/project/", {"name": "Test Project"})
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert len(data["projects"]) == 1
        assert data["projects"][0]["name"] == "Test Project"

    def test_list_projects_filter_by_type(self, auth_client, project, observe_project):
        """Filter projects by trace type."""
        response = auth_client.get("/tracer/project/", {"project_type": "observe"})
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert len(data["projects"]) == 1
        assert data["projects"][0].get("trace_type") == "observe"

    def test_list_projects_pagination(self, auth_client, organization, workspace):
        """Test pagination of projects list."""
        # Create 25 projects
        for i in range(25):
            Project.objects.create(
                name=f"Project {i}",
                organization=organization,
                workspace=workspace,
                model_type=AIModel.ModelTypes.GENERATIVE_LLM,
                trace_type="experiment",
            )

        # Get first page
        response = auth_client.get(
            "/tracer/project/", {"page_number": 0, "page_size": 10}
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert len(data["projects"]) == 10
        assert data.get("total_count") == 25

        # Get second page
        response = auth_client.get(
            "/tracer/project/", {"page_number": 1, "page_size": 10}
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert len(data["projects"]) == 10

        # Get third page (partial)
        response = auth_client.get(
            "/tracer/project/", {"page_number": 2, "page_size": 10}
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert len(data["projects"]) == 5

    def test_list_projects_sorting(self, auth_client, organization, workspace):
        """Test sorting of projects list."""
        # Create projects with different names
        Project.objects.create(
            name="Alpha Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
        )
        Project.objects.create(
            name="Zebra Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
        )

        # Sort by name ascending
        response = auth_client.get(
            "/tracer/project/", {"sort_by": "name", "sort_direction": "asc"}
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["projects"][0]["name"] == "Alpha Project"

        # Sort by name descending
        response = auth_client.get(
            "/tracer/project/", {"sort_by": "name", "sort_direction": "desc"}
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["projects"][0]["name"] == "Zebra Project"


@pytest.mark.integration
@pytest.mark.api
class TestObserveProjectListAPI:
    """Tests for GET /tracer/project/list_projects/ endpoint."""

    def test_list_projects_issues_sort_falls_back(self, auth_client, observe_project):
        """Synthetic issue-count sorting should not be passed to the ORM."""
        response = auth_client.get(
            "/tracer/project/list_projects/",
            {
                "project_type": "observe",
                "sort_by": "issues",
                "sort_direction": "desc",
                "page_number": 0,
                "page_size": 10,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["metadata"]["total_rows"] == 1
        assert data["table"][0]["id"] == str(observe_project.id)
        assert data["table"][0]["issues"] == 0


@pytest.mark.integration
@pytest.mark.api
class TestProjectCreateAPI:
    """Tests for POST /tracer/project/ endpoint."""

    def test_create_project_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/project/",
            {
                "name": "New Project",
                "model_type": "GenerativeLLM",
                "trace_type": "experiment",
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_create_project_success(self, auth_client, workspace, organization):
        """Create a new project successfully."""
        response = auth_client.post(
            "/tracer/project/",
            {
                "name": "New Project",
                "model_type": "GenerativeLLM",
                "trace_type": "experiment",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "project_id" in data
        project_id = data.get("project_id")
        assert data["name"] == "New Project"

        # Verify project was created in database
        project = Project.objects.get(id=project_id)
        assert project.name == "New Project"
        assert project.trace_type == "experiment"

    def test_create_project_with_metadata(self, auth_client, workspace, organization):
        """Create project with metadata."""
        response = auth_client.post(
            "/tracer/project/",
            {
                "name": "Project with Metadata",
                "model_type": "GenerativeLLM",
                "trace_type": "observe",
                "metadata": {"env": "production", "team": "ml"},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        project_id = data.get("project_id")

        project = Project.objects.get(id=project_id)
        assert project.metadata == {"env": "production", "team": "ml"}

    def test_create_project_missing_required_fields(self, auth_client):
        """Create project fails with missing required fields."""
        # Missing name
        response = auth_client.post(
            "/tracer/project/",
            {"model_type": "GenerativeLLM", "trace_type": "experiment"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Missing model_type
        response = auth_client.post(
            "/tracer/project/",
            {"name": "Test", "trace_type": "experiment"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Missing trace_type
        response = auth_client.post(
            "/tracer/project/",
            {"name": "Test", "model_type": "GenerativeLLM"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_project_invalid_trace_type(self, auth_client):
        """Create project fails with invalid trace type."""
        response = auth_client.post(
            "/tracer/project/",
            {
                "name": "Test Project",
                "model_type": "GenerativeLLM",
                "trace_type": "invalid_type",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_duplicate_project_name(self, auth_client, project):
        """Create project with duplicate name fails."""
        response = auth_client.post(
            "/tracer/project/",
            {
                "name": "Test Project",  # Same name as existing project fixture
                "model_type": "GenerativeLLM",
                "trace_type": "experiment",
            },
            format="json",
        )
        # Should fail due to unique constraint
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestProjectRetrieveAPI:
    """Tests for GET /tracer/project/{id}/ endpoint."""

    def test_retrieve_project_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/project/{project.id}/")
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_retrieve_project_success(self, auth_client, project):
        """Retrieve a project by ID."""
        response = auth_client.get(f"/tracer/project/{project.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["name"] == "Test Project"
        assert data.get("trace_type") == "experiment"
        assert "sampling_rate" in data
        assert data["sampling_rate"] == 0

    def test_retrieve_project_not_found(self, auth_client):
        """Retrieve non-existent project returns error."""
        import uuid

        fake_id = uuid.uuid4()
        response = auth_client.get(f"/tracer/project/{fake_id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_project_different_org(self, auth_client, organization):
        """Cannot retrieve project from different organization."""
        from accounts.models.organization import Organization

        # Create another organization and project
        other_org = Organization.objects.create(name="Other Org")
        other_project = Project.objects.create(
            name="Other Project",
            organization=other_org,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
        )

        response = auth_client.get(f"/tracer/project/{other_project.id}/")
        # Should return 400 or 404 because project is not in user's org
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestProjectDeleteAPI:
    """Tests for DELETE /tracer/project/ endpoint."""

    def test_delete_project_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.delete(
            "/tracer/project/",
            {"project_ids": [str(project.id)]},
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_delete_project_success(self, auth_client, project):
        """Delete a project successfully."""
        response = auth_client.delete(
            "/tracer/project/",
            {"project_ids": [str(project.id)], "project_type": "experiment"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify project is soft deleted
        project.refresh_from_db()
        assert project.deleted is True

    def test_delete_project_cascades(
        self, auth_client, project, trace, observation_span
    ):
        """Delete project cascades to related objects."""
        response = auth_client.delete(
            "/tracer/project/",
            {"project_ids": [str(project.id)], "project_type": "experiment"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify trace is soft deleted
        trace.refresh_from_db()
        assert trace.deleted is True

        # Verify span is soft deleted
        observation_span.refresh_from_db()
        assert observation_span.deleted is True

    def test_delete_project_missing_ids(self, auth_client):
        """Delete fails when project IDs are missing."""
        response = auth_client.delete(
            "/tracer/project/",
            {"project_ids": []},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_multiple_projects(self, auth_client, organization, workspace):
        """Delete multiple projects at once."""
        # Create multiple projects
        project1 = Project.objects.create(
            name="Project 1",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
        )
        project2 = Project.objects.create(
            name="Project 2",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
        )

        response = auth_client.delete(
            "/tracer/project/",
            {
                "project_ids": [str(project1.id), str(project2.id)],
                "project_type": "experiment",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        project1.refresh_from_db()
        project2.refresh_from_db()
        assert project1.deleted is True
        assert project2.deleted is True


@pytest.mark.integration
@pytest.mark.api
class TestProjectUpdateNameAPI:
    """Tests for POST /tracer/project/update_project_name/ endpoint."""

    def test_update_project_name_success(self, auth_client, project):
        """Update project name successfully."""
        response = auth_client.post(
            "/tracer/project/update_project_name/",
            {"project_id": str(project.id), "name": "Updated Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data.get("project_name") == "Updated Name"

        project.refresh_from_db()
        assert project.name == "Updated Name"

    def test_update_project_name_with_sampling_rate(self, auth_client, project):
        """Update project name and sampling rate together."""
        response = auth_client.post(
            "/tracer/project/update_project_name/",
            {
                "project_id": str(project.id),
                "name": "Updated Name",
                "sampling_rate": 0.5,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "sampling_rate" in data
        sampling_rate = data.get("sampling_rate")
        assert sampling_rate.get("new_rate") == 0.5

    def test_update_project_name_invalid_sampling_rate(self, auth_client, project):
        """Update with invalid sampling rate fails."""
        # Sampling rate > 1
        response = auth_client.post(
            "/tracer/project/update_project_name/",
            {"project_id": str(project.id), "name": "Test", "sampling_rate": 1.5},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Sampling rate < 0
        response = auth_client.post(
            "/tracer/project/update_project_name/",
            {"project_id": str(project.id), "name": "Test", "sampling_rate": -0.1},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_project_name_not_found(self, auth_client):
        """Update non-existent project fails."""
        import uuid

        response = auth_client.post(
            "/tracer/project/update_project_name/",
            {"project_id": str(uuid.uuid4()), "name": "Test"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestProjectUpdateConfigAPI:
    """Tests for POST /tracer/project/update_project_config/ endpoint."""

    def test_update_project_config_success(self, auth_client, project):
        """Update project config visibility."""
        response = auth_client.post(
            "/tracer/project/update_project_config/",
            {
                "project_id": str(project.id),
                "visibility": {"input": False, "output": True},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        project.refresh_from_db()
        input_config = next((c for c in project.config if c.get("id") == "input"), None)
        assert input_config is not None
        assert input_config["is_visible"] is False

    def test_update_project_config_not_found(self, auth_client):
        """Update config for non-existent project fails."""
        import uuid

        response = auth_client.post(
            "/tracer/project/update_project_config/",
            {"project_id": str(uuid.uuid4()), "visibility": {"input": True}},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestProjectListProjectIdsAPI:
    """Tests for GET /tracer/project/list_project_ids/ endpoint."""

    def test_list_project_ids_success(self, auth_client, project, observe_project):
        """List all project IDs and names."""
        response = auth_client.get("/tracer/project/list_project_ids/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "projects" in data
        assert len(data["projects"]) == 2

        project_names = [p["name"] for p in data["projects"]]
        assert "Test Project" in project_names
        assert "Test Observe Project" in project_names


@pytest.mark.integration
@pytest.mark.api
class TestProjectSDKCodeAPI:
    """Tests for GET /tracer/project/project_sdk_code/ endpoint."""

    def test_get_sdk_code_experiment(self, auth_client):
        """Get SDK code for experiment project type."""
        response = auth_client.get(
            "/tracer/project/project_sdk_code/", {"project_type": "experiment"}
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "installation_guide" in data
        assert "project_add_code" in data
        assert "keys" in data

    def test_get_sdk_code_uses_placeholders_and_does_not_create_keys(
        self, auth_client, organization, user
    ):
        """SDK code samples should not expose or create persisted user keys."""
        OrgApiKey.objects.create(
            organization=organization,
            type="user",
            enabled=True,
            user=user,
            api_key="a" * 32,
            secret_key="b" * 32,
        )
        key_count_before = OrgApiKey.objects.filter(
            organization=organization,
            type="user",
            user=user,
        ).count()

        response = auth_client.get(
            "/tracer/project/project_sdk_code/", {"project_type": "experiment"}
        )

        assert response.status_code == status.HTTP_200_OK
        key_count_after = OrgApiKey.objects.filter(
            organization=organization,
            type="user",
            user=user,
        ).count()
        assert key_count_after == key_count_before

        payload_text = str(get_result(response))
        assert "YOUR_FI_API_KEY" in payload_text
        assert "YOUR_FI_SECRET_KEY" in payload_text
        assert "a" * 32 not in payload_text
        assert "b" * 32 not in payload_text

    def test_get_sdk_code_observe(self, auth_client):
        """Get SDK code for observe project type."""
        response = auth_client.get(
            "/tracer/project/project_sdk_code/", {"project_type": "observe"}
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "installation_guide" in data
        assert "project_add_code" in data

    def test_get_sdk_code_invalid_type(self, auth_client):
        """Get SDK code with invalid type fails."""
        response = auth_client.get(
            "/tracer/project/project_sdk_code/", {"project_type": "invalid"}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestProjectFetchSystemMetricsAPI:
    """Tests for GET /tracer/project/fetch_system_metrics/ endpoint."""

    def test_fetch_system_metrics_success(self, auth_client):
        """Fetch available system metrics."""
        response = auth_client.get("/tracer/project/fetch_system_metrics/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Response is a list of metrics
        assert isinstance(data, list)
        assert "latency" in data
        assert "cost" in data
        assert "tokens" in data


@pytest.mark.integration
@pytest.mark.api
class TestProjectGraphDataAPI:
    """Tests for GET /tracer/project/get_graph_data/ endpoint."""

    def test_get_graph_data_missing_project_id(self, auth_client):
        """Get graph data without project ID fails."""
        response = auth_client.get("/tracer/project/get_graph_data/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_graph_data_success(self, auth_client, project):
        """Get graph data for a project."""
        response = auth_client.get(
            "/tracer/project/get_graph_data/",
            {"project_id": str(project.id), "interval": "hour"},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "system_metrics" in data
        assert "evaluations" in data

    def test_get_graph_data_applies_observe_chart_filters(
        self, auth_client, observe_project
    ):
        """Observe chart graphs must honor non-date filters from the UI."""
        suffix = uuid.uuid4().hex[:8]
        session = TraceSession.objects.create(
            project=observe_project,
            name=f"Chart filter session {suffix}",
            bookmarked=False,
        )
        trace_a = Trace.objects.create(
            project=observe_project,
            session=session,
            name=f"Chart filter trace A {suffix}",
        )
        trace_b = Trace.objects.create(
            project=observe_project,
            name=f"Chart filter trace B {suffix}",
        )

        now = timezone.now()
        span_a = ObservationSpan.objects.create(
            id=f"chart_filter_a_{suffix}",
            project=observe_project,
            trace=trace_a,
            name=f"Chart filter target {suffix}",
            observation_type="llm",
            start_time=now - timedelta(milliseconds=400),
            end_time=now,
            latency_ms=100,
            prompt_tokens=2,
            completion_tokens=3,
            total_tokens=5,
            cost=0.01,
            status="OK",
            span_attributes={"api_journey_marker": f"target-{suffix}"},
        )
        ObservationSpan.objects.create(
            id=f"chart_filter_a_child_{suffix}",
            project=observe_project,
            trace=trace_a,
            parent_span_id=span_a.id,
            name=f"Chart filter session peer {suffix}",
            observation_type="tool",
            start_time=now - timedelta(milliseconds=300),
            end_time=now,
            latency_ms=200,
            prompt_tokens=4,
            completion_tokens=6,
            total_tokens=10,
            cost=0.02,
            status="OK",
            span_attributes={"api_journey_marker": f"peer-{suffix}"},
        )
        span_b = ObservationSpan.objects.create(
            id=f"chart_filter_b_{suffix}",
            project=observe_project,
            trace=trace_b,
            name=f"Chart filter other {suffix}",
            observation_type="llm",
            start_time=now - timedelta(milliseconds=200),
            end_time=now,
            latency_ms=300,
            prompt_tokens=8,
            completion_tokens=12,
            total_tokens=20,
            cost=0.03,
            status="OK",
            span_attributes={"api_journey_marker": f"other-{suffix}"},
        )

        date_filter = _chart_filter(
            "created_at",
            "datetime",
            "between",
            [_iso_z(now - timedelta(days=1)), _iso_z(now + timedelta(days=1))],
        )

        def get_chart(filters):
            response = auth_client.get(
                "/tracer/project/get_graph_data/",
                {
                    "project_id": str(observe_project.id),
                    "interval": "day",
                    "filters": json.dumps(filters),
                },
            )
            assert response.status_code == status.HTTP_200_OK
            return get_result(response)

        assert _traffic_sum(get_chart([date_filter])) == 3
        assert (
            _traffic_sum(
                get_chart(
                    [
                        date_filter,
                        _chart_filter(
                            "trace_id",
                            "text",
                            "equals",
                            str(trace_a.id),
                            col_type="SYSTEM_METRIC",
                        ),
                    ]
                )
            )
            == 2
        )
        assert (
            _traffic_sum(
                get_chart(
                    [
                        date_filter,
                        _chart_filter(
                            "session_id",
                            "text",
                            "equals",
                            str(session.id),
                            col_type="SYSTEM_METRIC",
                        ),
                    ]
                )
            )
            == 2
        )
        assert (
            _traffic_sum(
                get_chart(
                    [
                        date_filter,
                        _chart_filter(
                            "span_id",
                            "text",
                            "equals",
                            span_b.id,
                            col_type="SYSTEM_METRIC",
                        ),
                    ]
                )
            )
            == 1
        )
        assert (
            _traffic_sum(
                get_chart(
                    [
                        date_filter,
                        _chart_filter(
                            "api_journey_marker",
                            "text",
                            "equals",
                            f"target-{suffix}",
                            col_type="SPAN_ATTRIBUTE",
                        ),
                    ]
                )
            )
            == 1
        )
