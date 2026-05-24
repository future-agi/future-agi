"""
TraceSession API Tests

Tests for /tracer/trace-session/ endpoints.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from tracer.models.observation_span import EvalLogger, EvalTargetType, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession


def _create_session_with_span(project, name, created_at=None):
    """Helper to create a session with a trace and span so get_session_navigation can find it."""
    session = TraceSession.objects.create(project=project, name=name)
    if created_at:
        TraceSession.objects.filter(id=session.id).update(created_at=created_at)
        session.refresh_from_db()
    trace = Trace.objects.create(
        project=project, session=session, name=f"Trace for {name}",
        input={"prompt": "test"}, output={"response": "test"},
    )
    ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project, trace=trace, name="ChatCompletion",
        observation_type="llm",
        start_time=session.created_at or timezone.now(),
        end_time=(session.created_at or timezone.now()) + timedelta(seconds=1),
        input="test", output="test",
        total_tokens=10, prompt_tokens=5, completion_tokens=5,
        cost=0.0001, latency_ms=500, status="OK",
    )
    return session


def _create_other_workspace_session(organization, user):
    from accounts.models.workspace import Workspace
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    other_workspace = Workspace.objects.create(
        name=f"Other Workspace {uuid.uuid4()}",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_project = Project.objects.create(
        name=f"Other Workspace Observe {uuid.uuid4()}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    session = _create_session_with_span(other_project, "Other Workspace Session")
    EvalLogger.objects.create(
        trace_session=session,
        target_type=EvalTargetType.SESSION,
        output_bool=True,
        eval_explanation="other workspace session eval",
    )
    return other_project, session


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


@pytest.mark.integration
@pytest.mark.api
class TestTraceSessionRetrieveAPI:
    """Tests for GET /tracer/trace-session/{id}/ endpoint."""

    def test_retrieve_session_unauthenticated(self, api_client, trace_session):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/trace-session/{trace_session.id}/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_retrieve_session_success(self, auth_client, trace_session):
        """Retrieve a trace session by ID."""
        response = auth_client.get(f"/tracer/trace-session/{trace_session.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "session_metadata" in data
        assert data["session_metadata"]["session_id"] == str(trace_session.id)

    def test_retrieve_session_not_found(self, auth_client):
        """Retrieve non-existent session returns error."""
        fake_id = uuid.uuid4()
        response = auth_client.get(f"/tracer/trace-session/{fake_id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_session_from_different_org(self, auth_client, organization):
        """
        Test retrieving session from different organization.

        The API now enforces organization-level access control on session
        retrieval and rejects sessions outside the request organization.
        """
        from accounts.models.organization import Organization
        from model_hub.models.ai_model import AIModel
        from tracer.models.project import Project

        # Create another organization and session
        other_org = Organization.objects.create(name="Other Org")
        other_project = Project.objects.create(
            name="Other Project",
            organization=other_org,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        other_session = TraceSession.objects.create(
            project=other_project,
            name="Other Session",
        )

        response = auth_client.get(f"/tracer/trace-session/{other_session.id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_session_has_navigation_fields(self, auth_client, trace_session):
        """Session detail response includes previous/next session IDs in session_metadata."""
        response = auth_client.get(f"/tracer/trace-session/{trace_session.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        metadata = data["session_metadata"]
        assert "previous_session_id" in metadata
        assert "next_session_id" in metadata

    def test_retrieve_session_navigation_single_session(
        self, auth_client, observe_project, trace_session
    ):
        """With only one session, both prev and next should be None."""
        TraceSession.objects.filter(project=observe_project).exclude(
            id=trace_session.id
        ).delete()

        response = auth_client.get(f"/tracer/trace-session/{trace_session.id}/")
        assert response.status_code == status.HTTP_200_OK
        metadata = get_result(response)["session_metadata"]
        assert metadata["previous_session_id"] is None
        assert metadata["next_session_id"] is None

    def test_retrieve_session_navigation_middle_session(
        self, auth_client, observe_project
    ):
        """Middle session should have both prev and next."""
        base = timezone.now()
        s1 = _create_session_with_span(observe_project, "First", base - timedelta(minutes=2))
        s2 = _create_session_with_span(observe_project, "Middle", base - timedelta(minutes=1))
        s3 = _create_session_with_span(observe_project, "Last", base)

        response = auth_client.get(f"/tracer/trace-session/{s2.id}/")
        assert response.status_code == status.HTTP_200_OK
        metadata = get_result(response)["session_metadata"]
        assert metadata["previous_session_id"] == str(s3.id)
        assert metadata["next_session_id"] == str(s1.id)

    def test_retrieve_session_navigation_first_session(
        self, auth_client, observe_project
    ):
        """First session (newest) should have next but no previous."""
        base = timezone.now()
        s1 = _create_session_with_span(observe_project, "Older", base - timedelta(minutes=1))
        s2 = _create_session_with_span(observe_project, "Newest", base)

        response = auth_client.get(f"/tracer/trace-session/{s2.id}/")
        assert response.status_code == status.HTTP_200_OK
        metadata = get_result(response)["session_metadata"]
        assert metadata["previous_session_id"] is None
        assert metadata["next_session_id"] == str(s1.id)

    def test_retrieve_session_navigation_last_session(
        self, auth_client, observe_project
    ):
        """Last session (oldest) should have previous but no next."""
        base = timezone.now()
        s1 = _create_session_with_span(observe_project, "Oldest", base - timedelta(minutes=1))
        s2 = _create_session_with_span(observe_project, "Newer", base)

        response = auth_client.get(f"/tracer/trace-session/{s1.id}/")
        assert response.status_code == status.HTTP_200_OK
        metadata = get_result(response)["session_metadata"]
        assert metadata["previous_session_id"] == str(s2.id)
        assert metadata["next_session_id"] is None

    def test_retrieve_session_rejects_legacy_navigation_aliases(
        self, auth_client, trace_session
    ):
        response = auth_client.get(
            f"/tracer/trace-session/{trace_session.id}/",
            {
                "userId": "customer-1",
                "sortParams": "[]",
                "pageNumber": "1",
                "pageSize": "10",
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestTraceSessionListAPI:
    """Tests for GET /tracer/trace-session/list_sessions/ endpoint."""

    def test_list_sessions_unauthenticated(self, api_client, observe_project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/trace-session/list_sessions/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_list_sessions_missing_project(self, auth_client):
        """List sessions supports org-scoped listing without project ID."""
        response = auth_client.get("/tracer/trace-session/list_sessions/")
        assert response.status_code == status.HTTP_200_OK

    def test_list_sessions_success(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """List sessions for a project."""
        response = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "metadata" in data or "table" in data

    def test_list_sessions_with_pagination(self, auth_client, observe_project):
        """List sessions with pagination."""
        # Create multiple sessions
        for i in range(15):
            TraceSession.objects.create(
                project=observe_project,
                name=f"Session {i}",
            )

        response = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {
                "project_id": str(observe_project.id),
                "page_number": 0,
                "page_size": 10,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "metadata" in data

    def test_list_sessions_empty(self, auth_client, observe_project):
        """List returns empty when no sessions exist."""
        # Delete existing sessions
        TraceSession.objects.filter(project=observe_project).delete()

        response = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_sessions_filter_bookmarked(self, auth_client, observe_project):
        """Filter sessions by bookmarked status."""
        # Create bookmarked session
        TraceSession.objects.create(
            project=observe_project,
            name="Bookmarked Session",
            bookmarked=True,
        )

        response = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {
                "project_id": str(observe_project.id),
                "bookmarked": "true",
            },
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_sessions_falls_back_when_clickhouse_fails(
        self, auth_client, observe_project, monkeypatch
    ):
        session = _create_session_with_span(observe_project, "Fallback Session")

        monkeypatch.setattr(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService.should_use_clickhouse",
            lambda self, query_type: True,
        )
        monkeypatch.setattr(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService.execute_ch_query",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ch down")),
        )

        response = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {"project_id": str(observe_project.id), "page_number": 0, "page_size": 10},
        )

        assert response.status_code == status.HTTP_200_OK
        rows = get_result(response)["table"]
        assert any(row["session_id"] == str(session.id) for row in rows)


@pytest.mark.integration
@pytest.mark.api
class TestTraceSessionExportAPI:
    """Tests for GET /tracer/trace-session/get_trace_session_export_data/ endpoint."""

    def test_export_sessions_unauthenticated(self, api_client, observe_project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/trace-session/get_trace_session_export_data/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_export_sessions_missing_project(self, auth_client):
        """Export sessions fails without project ID."""
        response = auth_client.get(
            "/tracer/trace-session/get_trace_session_export_data/"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_export_sessions_success(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """Export sessions for a project."""
        response = auth_client.get(
            "/tracer/trace-session/get_trace_session_export_data/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestTraceSessionGraphAPI:
    """Tests for POST /tracer/trace-session/get_session_graph_data/ endpoint."""

    def test_get_session_graph_falls_back_when_clickhouse_fails(
        self, auth_client, observe_project, monkeypatch
    ):
        """Session graph returns a graph payload when ClickHouse is unavailable."""
        monkeypatch.setattr(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService.should_use_clickhouse",
            lambda self, query_type: True,
        )
        monkeypatch.setattr(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService.execute_ch_query",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ch down")),
        )

        response = auth_client.post(
            "/tracer/trace-session/get_session_graph_data/",
            {
                "project_id": str(observe_project.id),
                "interval": "day",
                "property": "average",
                "req_data_config": {"id": "session_count", "type": "SYSTEM_METRIC"},
                "filters": [],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert isinstance(get_result(response).get("data"), list)


@pytest.mark.integration
@pytest.mark.api
class TestTraceSessionWorkspaceScopeAPI:
    def test_create_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        other_project, _session = _create_other_workspace_session(organization, user)

        response = auth_client.post(
            "/tracer/trace-session/",
            {
                "project": str(other_project.id),
                "name": "Forbidden Session",
                "bookmarked": False,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not TraceSession.all_objects.filter(
            project=other_project,
            name="Forbidden Session",
        ).exists()

    def test_patch_rejects_same_org_other_workspace_project(
        self, auth_client, trace_session, organization, user
    ):
        other_project, _session = _create_other_workspace_session(organization, user)

        response = auth_client.patch(
            f"/tracer/trace-session/{trace_session.id}/",
            {"project": str(other_project.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        trace_session.refresh_from_db()
        assert trace_session.project_id != other_project.id

    def test_custom_actions_reject_same_org_other_workspace_project_or_session(
        self, auth_client, organization, user
    ):
        other_project, other_session = _create_other_workspace_session(
            organization,
            user,
        )

        detail = auth_client.get(f"/tracer/trace-session/{other_session.id}/")
        assert detail.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        )

        eval_logs = auth_client.get(
            f"/tracer/trace-session/{other_session.id}/eval_logs/"
        )
        assert eval_logs.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        )

        list_response = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {"project_id": str(other_project.id)},
        )
        assert list_response.status_code == status.HTTP_400_BAD_REQUEST

        export = auth_client.get(
            "/tracer/trace-session/get_trace_session_export_data/",
            {"project_id": str(other_project.id)},
        )
        assert export.status_code == status.HTTP_400_BAD_REQUEST

        filter_values = auth_client.get(
            "/tracer/trace-session/get_session_filter_values/",
            {"project_id": str(other_project.id), "column": "session_id"},
        )
        assert filter_values.status_code == status.HTTP_400_BAD_REQUEST

        graph = auth_client.post(
            "/tracer/trace-session/get_session_graph_data/",
            {
                "project_id": str(other_project.id),
                "interval": "day",
                "property": "average",
                "req_data_config": {"id": "session_count", "type": "SYSTEM_METRIC"},
                "filters": [],
            },
            format="json",
        )
        assert graph.status_code == status.HTTP_400_BAD_REQUEST

    def test_generic_delete_cascades_session_traces_spans_and_eval_logs(
        self, auth_client, observe_project
    ):
        session = _create_session_with_span(observe_project, "Delete Cascade Session")
        trace = Trace.objects.get(session=session)
        span = ObservationSpan.objects.get(trace=trace)
        session_eval_log = EvalLogger.objects.create(
            trace_session=session,
            target_type=EvalTargetType.SESSION,
            output_bool=True,
            eval_explanation="session eval",
        )
        trace_eval_log = EvalLogger.objects.create(
            trace=trace,
            observation_span=span,
            target_type=EvalTargetType.TRACE,
            output_bool=True,
            eval_explanation="trace eval",
        )

        response = auth_client.delete(f"/tracer/trace-session/{session.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert TraceSession.all_objects.get(id=session.id).deleted is True
        assert Trace.all_objects.get(id=trace.id).deleted is True
        assert ObservationSpan.all_objects.get(id=span.id).deleted is True
        assert EvalLogger.all_objects.get(id=session_eval_log.id).deleted is True
        assert EvalLogger.all_objects.get(id=trace_eval_log.id).deleted is True
