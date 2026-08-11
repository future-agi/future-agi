"""
Project API Tests

Tests for /tracer/project/ endpoints.
"""

import json
from datetime import UTC, datetime, timedelta
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

    def test_list_projects_reads_latest_activity_from_direct_ch25(
        self, auth_client, observe_project
    ):
        last_active = datetime(2026, 8, 11, 12, tzinfo=UTC)
        day = last_active.strftime("%Y-%m-%d")
        remaining_timeouts = iter((30_000, 20_000, 10_000))
        deadline = SimpleNamespace(remaining_ms=lambda: next(remaining_timeouts))
        with (
            patch("tracer.views.project.V2AnalyticsQueryService") as service_class,
            patch("tracer.views.project.timezone.now", return_value=last_active),
            patch(
                "tracer.views.project.ReadDeadline.start",
                return_value=deadline,
            ) as deadline_start,
        ):
            service = service_class.return_value

            def activity_rows(_query, params, **_kwargs):
                if params["activity_end"] != "2026-08-12":
                    return SimpleNamespace(data=[])
                return SimpleNamespace(
                    data=[
                        {
                            "project_id_text": str(observe_project.id),
                            "volume": 3,
                            "last_active": last_active,
                            "daily_volume": [(day, 3)],
                        }
                    ]
                )

            service.execute_ch_query.side_effect = activity_rows
            response = auth_client.get("/tracer/project/list_projects/")

        assert response.status_code == status.HTTP_200_OK
        row = get_result(response)["table"][0]
        assert row["last_30_days_vol"] == 3
        assert row["last_active"] == last_active.isoformat()
        assert row["daily_volume"][-1] == 3
        assert len(row["daily_volume"]) == 30
        assert row["activity_query_complete"] is True
        assert row["activity_error_code"] is None

        deadline_start.assert_called_once_with(30_000)
        assert service.execute_ch_query.call_count == 3
        query = service.execute_ch_query.call_args_list[0].args[0]
        compact_query = " ".join(query.split())
        assert "FROM spans FINAL" not in compact_query
        assert "WITH latest_physical_spans AS" in compact_query
        assert "FROM traces" not in compact_query
        assert "trace_count_rollup" not in compact_query
        assert "argMax(" in compact_query
        assert "tuple( parent_span_id, start_time, is_deleted )" in compact_query
        assert "_version ) AS latest_span_state" in compact_query
        assert "PREWHERE project_id IN %(pids)s" in compact_query
        assert "start_time >= toDateTime64(" in compact_query
        assert "start_time < toDateTime64(" in compact_query
        assert "WHERE latest_span_state.3 = 0" in compact_query
        assert "AND latest_span_state.1 = ''" in compact_query
        assert "toDate(latest_span_state.2) AS day" in compact_query
        assert (
            "GROUP BY project_id, observation_type, service_name, "
            "toStartOfHour(start_time), trace_id, id"
        ) in compact_query
        assert "GROUP BY project_id, day" in compact_query
        assert [call.args[1] for call in service.execute_ch_query.call_args_list] == [
            {
                "pids": [str(observe_project.id)],
                "activity_start": "2026-05-14",
                "activity_end": "2026-06-13",
                "volume_start": "2026-07-13",
            },
            {
                "pids": [str(observe_project.id)],
                "activity_start": "2026-06-13",
                "activity_end": "2026-07-13",
                "volume_start": "2026-07-13",
            },
            {
                "pids": [str(observe_project.id)],
                "activity_start": "2026-07-13",
                "activity_end": "2026-08-12",
                "volume_start": "2026-07-13",
            },
        ]
        assert [
            call.kwargs["timeout_ms"]
            for call in service.execute_ch_query.call_args_list
        ] == [30_000, 20_000, 10_000]
        for call in service.execute_ch_query.call_args_list:
            call_kwargs = call.kwargs
            assert call_kwargs["settings"]["max_threads"] == 1
            assert call_kwargs["settings"]["max_block_size"] == 8_192
            assert "max_rows_to_read" not in call_kwargs["settings"]
            assert (
                call_kwargs["settings"]["max_bytes_to_read"] == 8 * 1024 * 1024 * 1024
            )
            assert (
                call_kwargs["settings"]["max_memory_usage"] == 36 * 1024 * 1024 * 1024
            )
            assert call_kwargs["settings"]["max_result_rows"] == 1_000
            assert "use_skip_indexes_if_final" not in call_kwargs["settings"]
            assert "force_optimize_projection" not in call_kwargs["settings"]

    def test_list_projects_latest_root_versions_use_event_time_and_tombstones(
        self, auth_client, observe_project
    ):
        now = datetime(2026, 8, 11, 12, tzinfo=UTC)
        pid = str(observe_project.id)
        versions = [
            {
                "id": "deleted-trace",
                "project_id": pid,
                "start_time": now - timedelta(days=1),
                "created_at": now,
                "is_deleted": 0,
                "version": 10,
            },
            {
                "id": "deleted-trace",
                "project_id": pid,
                "start_time": now - timedelta(days=1),
                "created_at": now,
                "is_deleted": 1,
                "version": 11,
            },
            {
                "id": "resurrected-trace",
                "project_id": pid,
                "start_time": now - timedelta(hours=2),
                "created_at": now,
                "is_deleted": 0,
                "version": 20,
            },
            {
                "id": "resurrected-trace",
                "project_id": pid,
                "start_time": now - timedelta(hours=2),
                "created_at": now,
                "is_deleted": 1,
                "version": 21,
            },
            {
                "id": "resurrected-trace",
                "project_id": pid,
                "start_time": now - timedelta(hours=2),
                "created_at": now,
                "is_deleted": 0,
                "version": 22,
            },
            {
                "id": "older-live-trace",
                "project_id": pid,
                "start_time": now - timedelta(days=45),
                "created_at": now,
                "is_deleted": 0,
                "version": 30,
            },
            {
                # A historical span imported today must stay outside the 90D
                # activity window; PG Trace.created_at would misclassify it.
                "id": "late-historical-import",
                "project_id": pid,
                "start_time": now - timedelta(days=120),
                "created_at": now,
                "is_deleted": 0,
                "version": 40,
            },
        ]

        with (
            patch("tracer.views.project.V2AnalyticsQueryService") as service_class,
            patch("tracer.views.project.timezone.now", return_value=now),
        ):
            service = service_class.return_value

            def execute_latest_state(query, params, **_kwargs):
                compact_query = " ".join(query.split())
                assert "FROM spans FINAL" not in compact_query
                assert "WITH latest_physical_spans AS" in compact_query
                assert "WHERE latest_span_state.3 = 0" in compact_query
                assert "AND latest_span_state.1 = ''" in compact_query
                assert "created_at" not in compact_query
                assert params["pids"] == [pid]

                chunk_start = datetime.fromisoformat(params["activity_start"]).replace(
                    tzinfo=UTC
                )
                chunk_end = datetime.fromisoformat(params["activity_end"]).replace(
                    tzinfo=UTC
                )
                latest = {}
                for version in versions:
                    if not chunk_start <= version["start_time"] < chunk_end:
                        continue
                    identity = (version["project_id"], version["id"])
                    current = latest.get(identity)
                    if current is None or version["version"] > current["version"]:
                        latest[identity] = version
                live = [row for row in latest.values() if not row["is_deleted"]]
                recent = [
                    row
                    for row in live
                    if row["start_time"].date() >= now.date() - timedelta(days=29)
                ]
                daily = {}
                for row in recent:
                    day = row["start_time"].strftime("%Y-%m-%d")
                    daily[day] = daily.get(day, 0) + 1
                if not live:
                    return SimpleNamespace(data=[])
                return SimpleNamespace(
                    data=[
                        {
                            "project_id_text": pid,
                            "volume": len(recent),
                            "last_active": max(row["start_time"] for row in live),
                            "daily_volume": sorted(daily.items()),
                        }
                    ]
                )

            service.execute_ch_query.side_effect = execute_latest_state
            response = auth_client.get("/tracer/project/list_projects/")

        assert response.status_code == status.HTTP_200_OK
        row = get_result(response)["table"][0]
        assert row["last_30_days_vol"] == 1
        assert row["daily_volume"][-1] == 1
        assert len(row["daily_volume"]) == 30
        assert row["last_active"] == (now - timedelta(hours=2)).isoformat()
        assert service.execute_ch_query.call_count == 3

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

    def test_list_projects_discards_earlier_chunk_when_later_chunk_fails(
        self, auth_client, observe_project
    ):
        now = datetime(2026, 8, 11, 12, tzinfo=UTC)
        with (
            patch("tracer.views.project.V2AnalyticsQueryService") as service_class,
            patch("tracer.views.project.timezone.now", return_value=now),
        ):
            service = service_class.return_value
            service.execute_ch_query.side_effect = [
                SimpleNamespace(
                    data=[
                        {
                            "project_id_text": str(observe_project.id),
                            "volume": 0,
                            "last_active": now - timedelta(days=70),
                            "daily_volume": [],
                        }
                    ]
                ),
                RuntimeError("second exact chunk unavailable"),
            ]
            response = auth_client.get("/tracer/project/list_projects/")

        assert response.status_code == status.HTTP_200_OK
        row = get_result(response)["table"][0]
        assert row["last_30_days_vol"] is None
        assert row["daily_volume"] is None
        assert row["last_active"] is None
        assert row["activity_query_complete"] is False
        assert row["activity_error_code"] == "project_activity_unavailable"
        assert service.execute_ch_query.call_count == 2

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
