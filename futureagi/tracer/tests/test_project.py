"""
Project API Tests

Tests for /tracer/project/ endpoints.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from clickhouse_driver.errors import ServerException
from rest_framework import status

from accounts.models.user import OrgApiKey
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


def _chart_filter(column_id, filter_type, filter_op, filter_value, col_type=None):
    filter_config = {
        "filter_type": filter_type,
        "filter_op": filter_op,
        "filter_value": filter_value,
    }
    if col_type:
        filter_config["col_type"] = col_type
    return {"column_id": column_id, "filter_config": filter_config}


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

    @pytest.mark.parametrize(
        "params",
        [
            {"page_size": 0},
            {"page_size": 101},
            {"page_number": -1},
            {"page_size": "invalid"},
            {"sort_direction": "sideways"},
        ],
    )
    def test_list_projects_rejects_unbounded_pagination_before_clickhouse(
        self, auth_client, observe_project, params
    ):
        with patch("tracer.views.project.V2AnalyticsQueryService") as analytics_service:
            response = auth_client.get("/tracer/project/list_projects/", params)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        analytics_service.assert_not_called()

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

    def test_list_projects_adds_stable_id_tie_breaker(
        self, auth_client, organization, workspace, observe_project
    ):
        """Numbered project pages must remain deterministic when sort values tie."""
        second = Project.objects.create(
            name="Second Observe Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        tied_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
        Project.objects.filter(id__in=[observe_project.id, second.id]).update(
            created_at=tied_at
        )

        with patch("tracer.views.project.V2AnalyticsQueryService") as service_class:
            service_class.return_value.supports_per_query_read_settings = False
            responses = [
                auth_client.get(
                    "/tracer/project/list_projects/",
                    {
                        "project_type": "observe",
                        "sort_by": "created_at",
                        "sort_direction": "desc",
                        "page_number": page_number,
                        "page_size": 1,
                    },
                )
                for page_number in (0, 1)
            ]

        assert all(response.status_code == status.HTTP_200_OK for response in responses)
        actual_ids = [get_result(response)["table"][0]["id"] for response in responses]
        expected_ids = [
            str(value)
            for value in sorted([observe_project.id, second.id], reverse=True)
        ]
        assert actual_ids == expected_ids

    def test_list_projects_reads_activity_from_hourly_rollup_once(
        self, auth_client, observe_project
    ):
        last_active = datetime(2026, 8, 11, 12, tzinfo=UTC)
        day = last_active.strftime("%Y-%m-%d")
        deadline = SimpleNamespace(remaining_ms=lambda: 9_500)
        with (
            patch("tracer.views.project.V2AnalyticsQueryService") as service_class,
            patch("tracer.views.project.timezone.now", return_value=last_active),
            patch(
                "tracer.views.project.ReadDeadline.start",
                return_value=deadline,
            ) as deadline_start,
        ):
            service = service_class.return_value

            service.execute_ch_query.return_value = SimpleNamespace(
                data=[
                    {
                        "project_id_text": str(observe_project.id),
                        "volume": 3,
                        "last_active": last_active,
                        "daily_volume": [(day, 3)],
                    }
                ]
            )
            response = auth_client.get("/tracer/project/list_projects/")

        assert response.status_code == status.HTTP_200_OK
        row = get_result(response)["table"][0]
        assert row["last_30_days_vol"] == 3
        assert row["last_active"] == last_active.isoformat()
        assert row["daily_volume"][-1] == 3
        assert len(row["daily_volume"]) == 30
        assert row["activity_query_complete"] is True
        assert row["activity_error_code"] is None
        assert row["activity_query_exact"] is False
        assert row["activity_query_provenance"] == "trace_count_rollup"

        deadline_start.assert_called_once_with(9_500)
        service.execute_ch_query.assert_called_once()
        query = service.execute_ch_query.call_args.args[0]
        compact_query = " ".join(query.split())
        assert "FROM spans" not in compact_query
        assert "FROM traces" not in compact_query
        assert "WITH daily AS" in compact_query
        assert "FROM trace_count_rollup" in compact_query
        assert "uniqExactMerge(uniq_traces_state)" in compact_query
        assert "PREWHERE project_id IN %(pids)s" in compact_query
        assert "hour >= toDateTime(" in compact_query
        assert "hour < toDateTime(" in compact_query
        assert "GROUP BY project_id, day" in compact_query
        assert service.execute_ch_query.call_args.args[1] == {
            "pids": [str(observe_project.id)],
            "activity_start": "2026-05-14",
            "activity_end": "2026-08-12",
            "volume_start": "2026-07-13",
        }
        call_kwargs = service.execute_ch_query.call_args.kwargs
        assert call_kwargs["timeout_ms"] == 9_500
        assert call_kwargs["settings"]["max_threads"] == 4
        assert call_kwargs["settings"]["max_block_size"] == 8_192
        assert "max_rows_to_read" not in call_kwargs["settings"]
        assert call_kwargs["settings"]["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
        assert call_kwargs["settings"]["max_memory_usage"] == 36 * 1024 * 1024 * 1024
        assert call_kwargs["settings"]["max_result_rows"] == 1_000
        assert call_kwargs["settings"]["max_result_bytes"] == 16 * 1024 * 1024

    def test_list_projects_rollup_contract_is_explicitly_non_exact(
        self, auth_client, observe_project
    ):
        now = datetime(2026, 8, 11, 12, tzinfo=UTC)
        with (
            patch("tracer.views.project.V2AnalyticsQueryService") as service_class,
            patch("tracer.views.project.timezone.now", return_value=now),
        ):
            service = service_class.return_value
            service.execute_ch_query.return_value = SimpleNamespace(data=[])
            response = auth_client.get("/tracer/project/list_projects/")

        assert response.status_code == status.HTTP_200_OK
        row = get_result(response)["table"][0]
        assert row["last_30_days_vol"] == 0
        assert row["daily_volume"] == [0] * 30
        assert row["last_active"] is None
        assert row["activity_query_complete"] is True
        assert row["activity_query_exact"] is False
        assert row["activity_query_provenance"] == "trace_count_rollup"

    def test_list_projects_discards_activity_atomically_on_malformed_result(
        self, auth_client, observe_project
    ):
        now = datetime(2026, 8, 11, 12, tzinfo=UTC)
        with (
            patch("tracer.views.project.V2AnalyticsQueryService") as service_class,
            patch("tracer.views.project.timezone.now", return_value=now),
        ):
            service = service_class.return_value
            service.execute_ch_query.return_value = SimpleNamespace(
                data=[
                    {
                        "project_id_text": str(observe_project.id),
                        "volume": 5,
                        "last_active": now,
                        "daily_volume": [("2026-08-11", "not-an-integer")],
                    }
                ]
            )
            response = auth_client.get("/tracer/project/list_projects/")

        assert response.status_code == status.HTTP_200_OK
        row = get_result(response)["table"][0]
        assert row["last_30_days_vol"] is None
        assert row["daily_volume"] is None
        assert row["last_active"] is None
        assert row["activity_query_complete"] is False
        assert row["activity_error_code"] == "project_activity_unavailable"
        service.execute_ch_query.assert_called_once()

    def test_list_projects_rejects_rollup_schema_drift(
        self, auth_client, observe_project
    ):
        with patch("tracer.views.project.V2AnalyticsQueryService") as service_class:
            service = service_class.return_value
            service.execute_ch_query.return_value = SimpleNamespace(
                data=[
                    {
                        "project_id_text": str(observe_project.id),
                        "volume": 5,
                        "daily_volume": [],
                    }
                ]
            )
            response = auth_client.get("/tracer/project/list_projects/")

        assert response.status_code == status.HTTP_200_OK
        row = get_result(response)["table"][0]
        assert row["last_30_days_vol"] is None
        assert row["daily_volume"] is None
        assert row["last_active"] is None
        assert row["activity_query_complete"] is False
        assert row["activity_error_code"] == "project_activity_unavailable"
        service.execute_ch_query.assert_called_once()

    def test_list_projects_discards_activity_atomically_when_exact_read_fails(
        self, auth_client, observe_project
    ):
        now = datetime(2026, 8, 11, 12, tzinfo=UTC)
        with (
            patch("tracer.views.project.V2AnalyticsQueryService") as service_class,
            patch("tracer.views.project.timezone.now", return_value=now),
        ):
            service = service_class.return_value
            service.execute_ch_query.side_effect = RuntimeError(
                "bounded exact read unavailable"
            )
            response = auth_client.get("/tracer/project/list_projects/")

        assert response.status_code == status.HTTP_200_OK
        row = get_result(response)["table"][0]
        assert row["last_30_days_vol"] is None
        assert row["daily_volume"] is None
        assert row["last_active"] is None
        assert row["activity_query_complete"] is False
        assert row["activity_error_code"] == "project_activity_unavailable"
        service.execute_ch_query.assert_called_once()

    def test_list_projects_skips_unbounded_locked_profile_read(
        self, auth_client, observe_project
    ):
        with patch("tracer.views.project.V2AnalyticsQueryService") as service_class:
            service = service_class.return_value
            service.supports_per_query_read_settings = False
            response = auth_client.get("/tracer/project/list_projects/")

        assert response.status_code == status.HTTP_200_OK
        row = get_result(response)["table"][0]
        assert row["last_30_days_vol"] is None
        assert row["daily_volume"] is None
        assert row["last_active"] is None
        assert row["activity_query_complete"] is False
        assert row["activity_error_code"] == "project_activity_unavailable"
        service.execute_ch_query.assert_not_called()


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

    def test_delete_project_publishes_cache_invalidation(
        self, auth_client, project, django_capture_on_commit_callbacks, mocker
    ):
        """Bulk delete publishes a collector cache-invalidation for the id."""
        publish = mocker.patch(
            "tracer.services.project_deletion.publish_project_invalidation"
        )
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.delete(
                "/tracer/project/",
                {"project_ids": [str(project.id)], "project_type": "experiment"},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        publish.assert_called_once_with([str(project.id)])

    def test_destroy_project_publishes_cache_invalidation(
        self, auth_client, project, django_capture_on_commit_callbacks, mocker
    ):
        """Single destroy() path also publishes the invalidation for its id."""
        publish = mocker.patch(
            "tracer.services.project_deletion.publish_project_invalidation"
        )
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.delete(f"/tracer/project/{project.id}/")
        assert response.status_code == status.HTTP_200_OK
        publish.assert_called_once_with([str(project.id)])

    def test_delete_project_publishes_on_wire_contract(
        self, auth_client, project, django_capture_on_commit_callbacks, mocker
    ):
        """The publish uses the literal channel + id payload the collector reads.

        Patches ``redis.Redis`` (not the helper) so a typo in the channel
        constant on the Python side is caught.
        """
        pipe = mocker.MagicMock()
        client = mocker.MagicMock()
        client.pipeline.return_value = pipe
        mocker.patch(
            "tracer.services.project_deletion.redis.Redis.from_url",
            return_value=client,
        )
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.delete(
                "/tracer/project/",
                {"project_ids": [str(project.id)], "project_type": "experiment"},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        pipe.publish.assert_called_once_with("fi:project:invalidate", str(project.id))
        pipe.execute.assert_called_once()

    def test_delete_project_publish_failure_is_swallowed(
        self, auth_client, project, django_capture_on_commit_callbacks, mocker
    ):
        """A Redis failure during publish never fails the delete."""
        mocker.patch(
            "tracer.services.project_deletion.redis.Redis.from_url",
            side_effect=ConnectionError("redis down"),
        )
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.delete(
                "/tracer/project/",
                {"project_ids": [str(project.id)], "project_type": "experiment"},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
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
class TestProjectUpdateSessionConfigAPI:
    """Tests for POST /tracer/project/update_project_session_config/ endpoint."""

    def _visibility(self, project, column_id):
        entry = next(
            (c for c in project.session_config if c.get("id") == column_id), None
        )
        return entry["is_visible"] if entry else None

    def test_update_existing_entry(self, auth_client, observe_project):
        """Toggle visibility on a column already in the stored config."""
        response = auth_client.post(
            "/tracer/project/update_project_session_config/",
            {
                "project_id": str(observe_project.id),
                "visibility": {"session_input": False},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        observe_project.refresh_from_db()
        assert self._visibility(observe_project, "session_input") is False

    def test_stale_config_merges_missing_defaults(self, auth_client, observe_project):
        """A default column missing from a stale stored config is persisted, not dropped."""
        response = auth_client.post(
            "/tracer/project/update_project_session_config/",
            {
                "project_id": str(observe_project.id),
                "visibility": {"total_tokens": True},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        observe_project.refresh_from_db()
        assert self._visibility(observe_project, "total_tokens") is True
        # Pre-existing stored entries survive the merge.
        assert self._visibility(observe_project, "session_input") is True

    def test_empty_config_seeds_defaults(self, auth_client, observe_project):
        """An empty stored config is seeded from defaults so toggles persist."""
        observe_project.session_config = []
        observe_project.save()

        response = auth_client.post(
            "/tracer/project/update_project_session_config/",
            {
                "project_id": str(observe_project.id),
                "visibility": {"user_id_type": True, "session_id": False},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        observe_project.refresh_from_db()
        assert self._visibility(observe_project, "user_id_type") is True
        assert self._visibility(observe_project, "session_id") is False
        # Untoggled merged-in defaults keep their default visibility.
        assert self._visibility(observe_project, "total_tokens") is False
        assert self._visibility(observe_project, "user_id") is True

    def test_stored_default_entry_not_duplicated(self, auth_client, observe_project):
        """A default column already stored is updated in place, not re-appended."""
        observe_project.session_config = [
            {"id": "total_tokens", "name": "Total Tokens", "is_visible": True},
        ]
        observe_project.save()

        response = auth_client.post(
            "/tracer/project/update_project_session_config/",
            {
                "project_id": str(observe_project.id),
                "visibility": {"total_tokens": False},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        observe_project.refresh_from_db()
        total_tokens_entries = [
            c for c in observe_project.session_config if c.get("id") == "total_tokens"
        ]
        assert len(total_tokens_entries) == 1
        assert total_tokens_entries[0]["is_visible"] is False

    def test_unknown_ids_are_not_appended(self, auth_client, observe_project):
        """Custom/annotation column ids don't pollute the stored config."""
        response = auth_client.post(
            "/tracer/project/update_project_session_config/",
            {
                "project_id": str(observe_project.id),
                "visibility": {"attributes.custom.key": True},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        observe_project.refresh_from_db()
        assert self._visibility(observe_project, "attributes.custom.key") is None

    def test_save_is_scoped_to_session_config(self, auth_client, observe_project):
        """Persist narrows to session_config so a concurrent field write isn't clobbered."""
        from unittest.mock import patch

        with patch("tracer.models.project.Project.save", autospec=True) as mock_save:
            response = auth_client.post(
                "/tracer/project/update_project_session_config/",
                {
                    "project_id": str(observe_project.id),
                    "visibility": {"total_tokens": True},
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert mock_save.called
        _, kwargs = mock_save.call_args
        assert kwargs.get("update_fields") == ["session_config"]


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
        exact_metrics = {
            "latency": [],
            "tokens": [],
            "cost": [],
            "traffic": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }
        with patch(
            "tracer.views.project.get_all_system_metrics",
            return_value=exact_metrics,
        ):
            response = auth_client.get(
                "/tracer/project/get_graph_data/",
                {"project_id": str(project.id), "interval": "hour"},
            )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data == {"system_metrics": exact_metrics, "evaluations": {}}

    @pytest.mark.parametrize(
        ("failure", "expected_status", "expected_code"),
        [
            (
                ServerException("private timeout stack", code=159),
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "service_unavailable",
            ),
            (
                RuntimeError("private programming defect"),
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "server_error",
            ),
        ],
    )
    def test_get_graph_data_classifies_and_sanitizes_failures(
        self,
        auth_client,
        project,
        failure,
        expected_status,
        expected_code,
    ):
        with patch(
            "tracer.views.project.get_all_system_metrics",
            side_effect=failure,
        ):
            response = auth_client.get(
                "/tracer/project/get_graph_data/",
                {"project_id": str(project.id), "interval": "hour"},
            )

        assert response.status_code == expected_status
        assert response.json()["code"] == expected_code
        assert "private" not in str(response.json())

    @patch("tracer.views.project.get_all_system_metrics")
    def test_get_graph_data_rejects_sample_even_with_legacy_opt_in(
        self,
        get_metrics,
        auth_client,
        observe_project,
    ):
        get_metrics.return_value = {
            "latency": [{"timestamp": "2026-08-03T00:00:00Z", "latency": 12}],
            "tokens": [],
            "cost": [],
            "traffic": [],
            "query_complete": False,
            "query_status": "sampled",
            "query_sampled": True,
            "query_error_code": "sample_limit",
            "query_sampling_strategy": "time_stratified_latest_state",
            "query_sampling_strata": 8,
            "query_sampling_strata_completed": 8,
        }
        params = {"project_id": str(observe_project.id), "interval": "day"}

        legacy_response = auth_client.get(
            "/tracer/project/get_graph_data/",
            params,
        )
        opted_in_response = auth_client.get(
            "/tracer/project/get_graph_data/",
            {**params, "allow_sampled": "true"},
        )

        assert legacy_response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert opted_in_response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    @patch("tracer.views.project.fetch_annotation_graph_ch")
    @patch("tracer.views.project.V2AnalyticsQueryService")
    def test_users_aggregate_graph_rejects_sample_even_with_legacy_opt_in(
        self,
        _analytics,
        fetch_annotation,
        auth_client,
        observe_project,
    ):
        fetch_annotation.return_value = {
            "metric_name": "annotation-id",
            "data": [{"timestamp": "2026-08-03T00:00:00Z", "value": 50}],
            "query_complete": False,
            "query_status": "sampled",
            "query_sampled": True,
            "query_error_code": "sample_limit",
            "query_sampling_strategy": "time_stratified_latest_state",
            "query_sampling_strata": 8,
            "query_sampling_strata_completed": 8,
        }
        request_body = {
            "project_id": str(observe_project.id),
            "interval": "day",
            "filters": [],
            "property": "average",
            "req_data_config": {
                "id": "annotation-id",
                "type": "ANNOTATION",
                "output_type": "SCORE",
            },
        }

        legacy_response = auth_client.post(
            "/tracer/project/get_users_aggregate_graph_data/",
            request_body,
            format="json",
        )
        opted_in_response = auth_client.post(
            ("/tracer/project/get_users_aggregate_graph_data/?allow_sampled=true"),
            request_body,
            format="json",
        )

        assert legacy_response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert opted_in_response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_get_graph_data_applies_observe_chart_filters(
        self, auth_client, observe_project
    ):
        """Observe chart graphs must honor non-date filters from the UI."""
        date_filter = _chart_filter(
            "created_at",
            "datetime",
            "between",
            ["2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"],
        )
        attribute_filter = _chart_filter(
            "api_journey_marker",
            "text",
            "equals",
            "target-value",
            col_type="SPAN_ATTRIBUTE",
        )
        exact_metrics = {
            "latency": [],
            "tokens": [],
            "cost": [],
            "traffic": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

        with patch(
            "tracer.views.project.get_all_system_metrics",
            return_value=exact_metrics,
        ) as get_metrics:
            response = auth_client.get(
                "/tracer/project/get_graph_data/",
                {
                    "project_id": str(observe_project.id),
                    "interval": "day",
                    "filters": json.dumps([date_filter, attribute_filter]),
                },
            )

        assert response.status_code == status.HTTP_200_OK
        kwargs = get_metrics.call_args.kwargs
        assert kwargs["system_metric_filters"] == {
            "project_id": str(observe_project.id)
        }
        assert kwargs["filters"] == [date_filter, attribute_filter]
        assert kwargs["interval"] == "day"
