"""
TraceSession API Tests

Tests for /tracer/trace-session/ endpoints.
"""

import uuid
from datetime import datetime, timedelta
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework import status

from tracer.models.observation_span import EvalLogger, EvalTargetType, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession, TraceSessionOverlay
from tracer.views.trace_session import TraceSessionView


def _create_session_with_span(project, name, created_at=None):
    """Helper to create a session with a trace and span so get_session_navigation can find it."""
    session = TraceSession.objects.create(project=project, name=name)
    if created_at:
        TraceSession.objects.filter(id=session.id).update(created_at=created_at)
        session.refresh_from_db()
    trace = Trace.objects.create(
        project=project,
        session=session,
        name=f"Trace for {name}",
        input={"prompt": "test"},
        output={"response": "test"},
    )
    ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="ChatCompletion",
        observation_type="llm",
        start_time=session.created_at or timezone.now(),
        end_time=(session.created_at or timezone.now()) + timedelta(seconds=1),
        input="test",
        output="test",
        total_tokens=10,
        prompt_tokens=5,
        completion_tokens=5,
        cost=0.0001,
        latency_ms=500,
        status="OK",
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

    def test_retrieve_ch_only_session_requires_accessible_project(
        self, auth_client, observe_project
    ):
        session_id = uuid.uuid4()
        inaccessible_project_id = uuid.uuid4()

        with mock.patch(
            "tracer.services.clickhouse.v2.trace_session_dict_reader."
            "resolve_session_fields",
            return_value={
                str(session_id): {"project_id": str(inaccessible_project_id)}
            },
        ):
            response = auth_client.get(f"/tracer/trace-session/{session_id}/")

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

    # The test env routes CH_ROUTE_SESSION_ANALYTICS to postgres and does
    # not seed ClickHouse, so the navigation tests below monkeypatch
    # _try_session_navigation_ch to simulate CH returning known
    # neighbours.

    def test_retrieve_session_navigation_middle_session(
        self, auth_client, observe_project, monkeypatch
    ):
        """Middle session should have both prev and next."""
        base = timezone.now()
        s1 = _create_session_with_span(
            observe_project, "First", base - timedelta(minutes=2)
        )
        s2 = _create_session_with_span(
            observe_project, "Middle", base - timedelta(minutes=1)
        )
        s3 = _create_session_with_span(observe_project, "Last", base)

        from tracer.utils import session as session_utils

        monkeypatch.setattr(
            session_utils,
            "_try_session_navigation_ch",
            lambda req, pid, sid: (str(s1.id), str(s3.id)),
        )

        response = auth_client.get(f"/tracer/trace-session/{s2.id}/")
        assert response.status_code == status.HTTP_200_OK
        metadata = get_result(response)["session_metadata"]
        assert metadata["previous_session_id"] == str(s3.id)
        assert metadata["next_session_id"] == str(s1.id)

    def test_retrieve_session_navigation_first_session(
        self, auth_client, observe_project, monkeypatch
    ):
        """First session (newest) should have next but no previous."""
        base = timezone.now()
        s1 = _create_session_with_span(
            observe_project, "Older", base - timedelta(minutes=1)
        )
        s2 = _create_session_with_span(observe_project, "Newest", base)

        from tracer.utils import session as session_utils

        monkeypatch.setattr(
            session_utils,
            "_try_session_navigation_ch",
            lambda req, pid, sid: (str(s1.id), None),
        )

        response = auth_client.get(f"/tracer/trace-session/{s2.id}/")
        assert response.status_code == status.HTTP_200_OK
        metadata = get_result(response)["session_metadata"]
        assert metadata["previous_session_id"] is None
        assert metadata["next_session_id"] == str(s1.id)

    def test_retrieve_session_navigation_last_session(
        self, auth_client, observe_project, monkeypatch
    ):
        """Last session (oldest) should have previous but no next."""
        base = timezone.now()
        s1 = _create_session_with_span(
            observe_project, "Oldest", base - timedelta(minutes=1)
        )
        s2 = _create_session_with_span(observe_project, "Newer", base)

        from tracer.utils import session as session_utils

        monkeypatch.setattr(
            session_utils,
            "_try_session_navigation_ch",
            lambda req, pid, sid: (None, str(s2.id)),
        )

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

    def test_fetch_end_user_info_survives_null_end_user_id(self, monkeypatch):
        """Regression: a user-less session (NULL ``end_user_id`` from CH) must
        not stringify to the literal ``"None"`` and poison the downstream
        ``Array(UUID)`` cast in ``resolve_end_user_fields`` — that CANNOT_PARSE_UUID
        crashed the whole CH session list, silently falling back to an empty PG
        list (every session vanished from the grid)."""
        import tracer.views.trace_session as ts_mod

        sid = "11111111-1111-1111-1111-111111111111"

        analytics = mock.Mock()
        analytics.execute_ch_query.return_value = mock.Mock(
            data=[{"session_id": sid, "end_user_id": None}]
        )
        # Isolate the null-eu path: identity canonical mapping (no id remap).
        monkeypatch.setattr(
            ts_mod, "_resolve_session_ids_to_canonical", lambda analytics, ids: {}
        )

        # Spy mimics the real Array(UUID) cast — a non-UUID id ("None") raises
        # exactly as ClickHouse would, so a regression fails here loudly.
        seen = {}

        def _fake_resolve(end_user_ids):
            ids = {str(e) for e in end_user_ids}
            seen["ids"] = ids
            for e in ids:
                uuid.UUID(e)
            return {}

        monkeypatch.setattr(
            "tracer.services.clickhouse.v2.end_user_dict_reader.resolve_end_user_fields",
            _fake_resolve,
        )

        # Must not raise; the user-less session is simply absent (all-None).
        out = TraceSessionView._fetch_end_user_info([sid], analytics)

        assert out == {}
        assert "None" not in seen.get("ids", set())


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

    def test_session_filter_uses_clickhouse_graph(
        self, auth_client, observe_project
    ):
        session_id = "003b76f1-2b4a-4af5-b0dc-224d687374d4"
        analytics = mock.Mock()
        analytics.execute_ch_query.return_value = mock.Mock(data=[], columns=[])

        with mock.patch(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService",
            return_value=analytics,
        ):
            response = auth_client.post(
                "/tracer/trace-session/get_session_graph_data/",
                {
                    "project_id": str(observe_project.id),
                    "interval": "day",
                    "property": "average",
                    "req_data_config": {
                        "id": "session_count",
                        "type": "SYSTEM_METRIC",
                    },
                    "filters": [
                        {
                            "column_id": "created_at",
                            "filter_config": {
                                "filter_type": "datetime",
                                "filter_op": "between",
                                "filter_value": [
                                    "2026-06-18T00:00:00Z",
                                    "2026-06-19T00:00:00Z",
                                ],
                            },
                        },
                        {
                            "column_id": "session",
                            "display_name": "Session",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "in",
                                "filter_value": [session_id],
                                "col_type": "SYSTEM_METRIC",
                            },
                        },
                    ],
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert get_result(response)["metric_name"] == "session_count"
        query, params = analytics.execute_ch_query.call_args.args[:2]
        assert "IN %(session_id_1)s" in query
        assert params["session_id_1"] == (session_id,)


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

    def test_user_filter_values_return_external_user_ids(
        self, auth_client, observe_project
    ):
        analytics = mock.Mock()
        analytics.execute_ch_query.return_value = mock.Mock(
            data=[{"val": "alice"}, {"val": "bob"}]
        )

        with mock.patch(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService",
            return_value=analytics,
        ):
            response = auth_client.get(
                "/tracer/trace-session/get_session_filter_values/",
                {"project_id": str(observe_project.id), "column": "user_id"},
            )

        assert response.status_code == status.HTTP_200_OK
        assert get_result(response)["values"] == ["alice", "bob"]
        query = analytics.execute_ch_query.call_args.args[0]
        assert "FROM end_users FINAL" in query
        assert "user_id AS val" in query

    def test_session_filter_values_use_external_id_as_label(
        self, auth_client, observe_project
    ):
        analytics = mock.Mock()
        analytics.execute_ch_query.return_value = mock.Mock(
            data=[{"val": str(uuid.uuid4()), "label": "session-alpha"}]
        )
        session_id = analytics.execute_ch_query.return_value.data[0]["val"]

        with (
            mock.patch(
                "tracer.services.clickhouse.query_service.AnalyticsQueryService",
                return_value=analytics,
            ),
            mock.patch(
                "tracer.services.clickhouse.v2.trace_session_dict_reader."
                "resolve_session_fields",
                return_value={
                    session_id: {
                        "external_session_id": "session-alpha",
                        "display_name": None,
                    }
                },
            ),
        ):
            response = auth_client.get(
                "/tracer/trace-session/get_session_filter_values/",
                {"project_id": str(observe_project.id), "column": "session_id"},
            )

        assert response.status_code == status.HTTP_200_OK
        assert get_result(response)["values"] == [
            {"value": session_id, "label": "session-alpha"}
        ]

    def test_session_filter_values_dedupe_straddlers_through_remap(
        self, auth_client, observe_project
    ):
        survivor_id = str(uuid.uuid4())
        analytics = mock.Mock()
        analytics.execute_ch_query.return_value = mock.Mock(
            data=[{"val": survivor_id, "label": "session-alpha"}]
        )

        with mock.patch(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService",
            return_value=analytics,
        ):
            response = auth_client.get(
                "/tracer/trace-session/get_session_filter_values/",
                {"project_id": str(observe_project.id), "column": "session_id"},
            )

        assert response.status_code == status.HTTP_200_OK
        assert get_result(response)["values"] == [
            {"value": survivor_id, "label": "session-alpha"}
        ]
        query = analytics.execute_ch_query.call_args.args[0]
        assert "trace_session_id_remap" in query
        assert "GROUP BY val_id" in query
        assert "toString(val_id) AS val" in query

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


@pytest.mark.integration
@pytest.mark.api
class TestTraceSessionOverlayWritePath:
    """Slice 2b — the bookmark/rename WRITE path mirrors the PG overlay.

    Drives the REAL update path (DRF ``PATCH`` → ``TraceSessionView.perform_update``
    → ``TraceSessionSerializer.save``) and asserts the PG ``TraceSessionOverlay``
    is upserted so slice 2a's overlay-backed reads stay fresh, the legacy
    ``TraceSession`` write is preserved, and the two PG writes share one
    transaction (DESIGN §5 / §5.1).
    """

    def test_patch_bookmark_upserts_overlay(self, auth_client, trace_session):
        """PATCH bookmarked=True → overlay row created with bookmarked=True.

        Also asserts the legacy ``TraceSession.bookmarked`` write is preserved
        (additive cutover) and the overlay carries project_id + the current name
        as display_name.
        """
        assert not TraceSessionOverlay.objects.filter(
            trace_session_id=trace_session.id
        ).exists()

        response = auth_client.patch(
            f"/tracer/trace-session/{trace_session.id}/",
            {"bookmarked": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Legacy TraceSession write preserved (PG-fallback path still reads it).
        trace_session.refresh_from_db()
        assert trace_session.bookmarked is True

        # Overlay upserted, mirroring the post-save instance state.
        overlay = TraceSessionOverlay.objects.get(trace_session_id=trace_session.id)
        assert overlay.bookmarked is True
        assert overlay.project_id == trace_session.project_id
        # display_name mirrors current name (the fixture session's name).
        assert overlay.display_name == trace_session.name

    def test_patch_bookmark_writes_overlay_for_ch_only_session(
        self, auth_client, observe_project
    ):
        session_id = uuid.uuid4()
        assert not TraceSession.objects.filter(id=session_id).exists()

        with mock.patch(
            "tracer.views.trace_session._resolve_ch_session_fields",
            return_value={
                "project_id": observe_project.id,
                "bookmarked": False,
                "display_name": "collector-session",
                "first_seen": None,
            },
        ):
            response = auth_client.patch(
                f"/tracer/trace-session/{session_id}/",
                {"bookmarked": True},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert not TraceSession.objects.filter(id=session_id).exists()
        overlay = TraceSessionOverlay.objects.get(trace_session_id=session_id)
        assert overlay.project_id == observe_project.id
        assert overlay.bookmarked is True
        assert overlay.display_name == "collector-session"

    def test_patch_rename_sets_overlay_display_name(self, auth_client, trace_session):
        """PATCH name='renamed-via-2b' → overlay.display_name reflects the rename."""
        response = auth_client.patch(
            f"/tracer/trace-session/{trace_session.id}/",
            {"name": "renamed-via-2b"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Legacy name write preserved.
        trace_session.refresh_from_db()
        assert trace_session.name == "renamed-via-2b"

        # Overlay carries the new label as the display_name override.
        overlay = TraceSessionOverlay.objects.get(trace_session_id=trace_session.id)
        assert overlay.display_name == "renamed-via-2b"

    def test_partial_patch_does_not_clobber_other_overlay_field(
        self, auth_client, trace_session
    ):
        """A later partial PATCH must keep the previously-set overlay field.

        Reading the overlay defaults from the POST-SAVE instance (not
        ``validated_data``) means a ``{"bookmarked": ...}``-only PATCH still
        carries the existing ``name`` into ``display_name`` (and vice-versa).
        """
        # 1) rename
        auth_client.patch(
            f"/tracer/trace-session/{trace_session.id}/",
            {"name": "renamed-via-2b"},
            format="json",
        )
        # 2) bookmark-only PATCH (no name in the body)
        auth_client.patch(
            f"/tracer/trace-session/{trace_session.id}/",
            {"bookmarked": True},
            format="json",
        )

        overlay = TraceSessionOverlay.objects.get(trace_session_id=trace_session.id)
        # bookmarked applied AND the earlier rename survived (not clobbered to None).
        assert overlay.bookmarked is True
        assert overlay.display_name == "renamed-via-2b"

    def test_overlay_write_composes_with_slice_2a_bookmark_read(
        self, auth_client, observe_project, trace_session
    ):
        """End-to-end: the 2b write makes slice 2a's bookmark filter include it.

        ``_build_bookmark_filter`` is pure PG (overlay → ids), so it is exercised
        for real. Before the write the new session must NOT be in the bookmarked
        id set; after the PATCH it MUST be.
        """
        sid = str(trace_session.id)
        proj_ids = [str(observe_project.id)]

        before = TraceSessionView._build_bookmark_filter(True, proj_ids)
        assert sid not in before["filter_config"]["filter_value"]

        response = auth_client.patch(
            f"/tracer/trace-session/{trace_session.id}/",
            {"bookmarked": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        after = TraceSessionView._build_bookmark_filter(True, proj_ids)
        assert after["filter_config"]["filter_op"] == "in"
        assert sid in after["filter_config"]["filter_value"]

    def test_bookmark_filter_canonicalizes_overlay_ids_for_clickhouse(
        self, observe_project
    ):
        raw_id = str(uuid.uuid4())
        survivor_id = str(uuid.uuid4())
        TraceSessionOverlay.objects.create(
            trace_session_id=raw_id,
            project_id=observe_project.id,
            bookmarked=True,
        )
        analytics = mock.Mock()
        analytics.execute_ch_query.return_value = mock.Mock(
            data=[{"any_id": raw_id, "survivor_id": survivor_id}]
        )

        bookmark_filter = TraceSessionView._build_bookmark_filter(
            True, [str(observe_project.id)], analytics=analytics
        )

        assert bookmark_filter["filter_config"]["filter_op"] == "in"
        assert bookmark_filter["filter_config"]["filter_value"] == [survivor_id]
        sql = analytics.execute_ch_query.call_args.args[0]
        assert "trace_session_id_remap" in sql
        assert "WHERE any_id IN %(ids)s" in sql

    def test_retrieve_clickhouse_binds_canonical_requested_session_id(
        self, observe_project
    ):
        requested_id = str(uuid.uuid4())
        survivor_id = str(uuid.uuid4())
        bound_session_ids = []
        analytics = mock.Mock()

        def execute_ch_query(query, params, timeout_ms):
            if "WHERE any_id IN %(ids)s" in query:
                return mock.Mock(
                    data=[{"any_id": requested_id, "survivor_id": survivor_id}]
                )
            if "trace_session_id_remap" in query:
                return mock.Mock(data=[])
            if "count(DISTINCT trace_id)" in query:
                bound_session_ids.append(params.get("session_group_ids"))
                return mock.Mock(
                    data=[
                        {
                            "session_start": None,
                            "session_end": None,
                            "total_cost": 0,
                            "total_tokens": 0,
                            "total_traces": 0,
                        }
                    ]
                )
            if "GROUP BY trace_id" in query:
                bound_session_ids.append(params.get("session_group_ids"))
                return mock.Mock(data=[])
            raise AssertionError(f"unexpected ClickHouse query: {query}")

        analytics.execute_ch_query.side_effect = execute_ch_query

        with mock.patch(
            "tracer.views.trace_session.get_session_navigation",
            return_value=(None, None),
        ):
            response = TraceSessionView()._retrieve_clickhouse(
                mock.Mock(),
                requested_id,
                observe_project.id,
                analytics,
                {"page_number": 0, "page_size": 10},
            )

        assert response.status_code == status.HTTP_200_OK
        expected_group = (survivor_id,)
        assert bound_session_ids == [expected_group, expected_group]

    def test_retrieve_clickhouse_applies_time_window_to_span_scans(
        self, observe_project
    ):
        requested_id = str(uuid.uuid4())
        captured_span_scan_params = []
        analytics = mock.Mock()

        def execute_ch_query(query, params, timeout_ms):
            if "WHERE any_id IN %(ids)s" in query:
                return mock.Mock(data=[])
            if "FROM spans" in query and (
                "count(DISTINCT trace_id)" in query or "GROUP BY trace_id" in query
            ):
                assert "start_time >= %(start_date)s" in query
                assert "start_time < %(end_date)s" in query
                captured_span_scan_params.append(params)
                if "count(DISTINCT trace_id)" in query:
                    return mock.Mock(
                        data=[
                            {
                                "start_time": None,
                                "end_time": None,
                                "total_cost": 0,
                                "total_tokens": 0,
                                "total_traces": 0,
                            }
                        ]
                    )
                return mock.Mock(data=[])
            raise AssertionError(f"unexpected ClickHouse query: {query}")

        analytics.execute_ch_query.side_effect = execute_ch_query

        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 31, 23, 59, 59)
        query_data = {
            "page_number": 0,
            "page_size": 10,
            "filters": [
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            "2026-01-01T00:00:00Z",
                            "2026-01-31T23:59:59Z",
                        ],
                    },
                }
            ],
        }

        with mock.patch(
            "tracer.views.trace_session.get_session_navigation",
            return_value=(None, None),
        ):
            response = TraceSessionView()._retrieve_clickhouse(
                mock.Mock(),
                requested_id,
                observe_project.id,
                analytics,
                query_data,
            )

        assert response.status_code == status.HTTP_200_OK
        assert len(captured_span_scan_params) == 2
        for params in captured_span_scan_params:
            assert params["start_date"] == start
            assert params["end_date"] == end

    def test_overlay_write_composes_with_slice_2a_name_read(
        self, auth_client, observe_project, trace_session
    ):
        """End-to-end: the 2b rename surfaces through slice 2a's name COALESCE.

        ``_fetch_session_names`` resolves ``external_session_id`` from CH FIRST
        (``resolve_external_session_ids``, which re-raises on error and is
        unreachable from host pytest), THEN overlays ``display_name``. We mock the
        CH half to ``{}`` and assert the PG overlay override wins:
        ``COALESCE(overlay.display_name, external)`` → the new label.
        """
        sid = str(trace_session.id)
        proj_ids = [str(observe_project.id)]

        response = auth_client.patch(
            f"/tracer/trace-session/{trace_session.id}/",
            {"name": "renamed-via-2b"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        with mock.patch(
            "tracer.services.clickhouse.v2.trace_session_dict_reader."
            "resolve_external_session_ids",
            return_value={},
        ):
            name_map = TraceSessionView._fetch_session_names([sid], proj_ids)

        assert name_map[sid] == "renamed-via-2b"

    def test_overlay_upsert_failure_rolls_back_trace_session(self, trace_session):
        """Atomicity: a failing overlay upsert rolls back the TraceSession write.

        The overlay is PG (same DB as TraceSession), so ``perform_update`` wraps
        ``save()`` + the overlay ``update_or_create`` in ONE
        ``transaction.atomic()`` — both-or-neither. We drive ``perform_update``
        directly (the exact code under test) so the assertion targets the
        transactional guarantee, not DRF's exception-to-status mapping: a raw
        ``RuntimeError`` is not an ``APIException`` and would otherwise propagate
        through the test client unconverted. Force the overlay upsert to raise,
        then assert the legacy TraceSession row is UNCHANGED (the save did not
        stick) and NO overlay row was created.
        """
        from tracer.serializers.trace_session import TraceSessionSerializer

        original_name = trace_session.name
        assert original_name != "renamed-via-2b"

        view = TraceSessionView()
        serializer = TraceSessionSerializer(
            instance=trace_session,
            data={"name": "renamed-via-2b"},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors

        with mock.patch.object(
            TraceSessionOverlay.objects,
            "update_or_create",
            side_effect=RuntimeError("overlay write boom"),
        ):
            with pytest.raises(RuntimeError, match="overlay write boom"):
                view.perform_update(serializer)

        # The atomic() block rolled the TraceSession save back with the failed
        # overlay upsert (shared transaction) — re-read straight from the DB.
        fresh = TraceSession.objects.get(id=trace_session.id)
        assert fresh.name == original_name
        # No overlay row leaked.
        assert not TraceSessionOverlay.objects.filter(
            trace_session_id=trace_session.id
        ).exists()


@pytest.mark.integration
@pytest.mark.api
class TestTraceSessionCHOnlyDestroyPath:
    """CH-only sessions (no PG row) must be deletable via the same endpoint."""

    def test_delete_ch_only_session_returns_204(self, auth_client, observe_project):
        session_id = uuid.uuid4()
        assert not TraceSession.objects.filter(id=session_id).exists()

        with mock.patch(
            "tracer.views.trace_session._resolve_ch_session_fields",
            return_value={
                "project_id": observe_project.id,
                "external_session_id": "ext-session-1",
                "first_seen": timezone.now(),
                "bookmarked": False,
                "display_name": None,
            },
        ), mock.patch(
            "tracer.services.clickhouse.v2.curated_writer._get_client"
        ) as mock_ch_client:
            mock_ch_client.return_value = mock.Mock()
            response = auth_client.delete(
                f"/tracer/trace-session/{session_id}/",
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_delete_ch_only_session_removes_overlay(
        self, auth_client, observe_project
    ):
        session_id = uuid.uuid4()
        TraceSessionOverlay.objects.create(
            trace_session_id=session_id,
            project_id=observe_project.id,
            bookmarked=True,
            display_name="bookmarked-session",
        )

        with mock.patch(
            "tracer.views.trace_session._resolve_ch_session_fields",
            return_value={
                "project_id": observe_project.id,
                "external_session_id": "ext-session-1",
                "first_seen": timezone.now(),
                "bookmarked": True,
                "display_name": "bookmarked-session",
            },
        ), mock.patch(
            "tracer.services.clickhouse.v2.curated_writer._get_client"
        ) as mock_ch_client:
            mock_ch_client.return_value = mock.Mock()
            response = auth_client.delete(
                f"/tracer/trace-session/{session_id}/",
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not TraceSessionOverlay.objects.filter(
            trace_session_id=session_id
        ).exists()

    def test_delete_ch_only_session_inserts_deletion_marker(
        self, auth_client, observe_project
    ):
        session_id = uuid.uuid4()

        with mock.patch(
            "tracer.views.trace_session._resolve_ch_session_fields",
            return_value={
                "project_id": observe_project.id,
                "external_session_id": "ext-session-1",
                "first_seen": timezone.now(),
                "bookmarked": False,
                "display_name": None,
            },
        ), mock.patch(
            "tracer.services.clickhouse.v2.curated_writer._get_client"
        ) as mock_ch_client:
            ch_client = mock.Mock()
            mock_ch_client.return_value = ch_client
            auth_client.delete(f"/tracer/trace-session/{session_id}/")

        ch_client.insert.assert_called_once()
        call_args = ch_client.insert.call_args
        assert call_args.args[0] == "trace_sessions"
        row = call_args.args[1][0]
        is_deleted_col_idx = 5
        assert row[is_deleted_col_idx] == 1

    def test_delete_ch_only_session_not_found_returns_404(
        self, auth_client, observe_project
    ):
        session_id = uuid.uuid4()

        with mock.patch(
            "tracer.views.trace_session._resolve_ch_session_fields",
            return_value=None,
        ):
            response = auth_client.delete(
                f"/tracer/trace-session/{session_id}/",
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_pg_session_still_works(self, auth_client, trace_session):
        response = auth_client.delete(
            f"/tracer/trace-session/{trace_session.id}/",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        trace_session.refresh_from_db()
        assert trace_session.deleted is True


@pytest.mark.integration
@pytest.mark.api
class TestTraceSessionResponseContract:
    """Both PG and CH-only PATCH paths must return the same response shape."""

    EXPECTED_KEYS = {"id", "project", "bookmarked", "name", "created_at"}

    def test_pg_patch_response_shape(self, auth_client, trace_session):
        response = auth_client.patch(
            f"/tracer/trace-session/{trace_session.id}/",
            {"bookmarked": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert self.EXPECTED_KEYS == set(data.keys())

    def test_ch_only_patch_response_shape(self, auth_client, observe_project):
        session_id = uuid.uuid4()

        with mock.patch(
            "tracer.views.trace_session._resolve_ch_session_fields",
            return_value={
                "project_id": observe_project.id,
                "bookmarked": False,
                "display_name": "ch-session",
                "first_seen": timezone.now(),
            },
        ):
            response = auth_client.patch(
                f"/tracer/trace-session/{session_id}/",
                {"bookmarked": True},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert self.EXPECTED_KEYS == set(data.keys())
        assert data["id"] == str(session_id)
        assert data["project"] == str(observe_project.id)
        assert data["bookmarked"] is True
        assert data["name"] == "ch-session"
        assert data["created_at"] is not None

    def test_pg_and_ch_created_at_use_same_format(
        self, auth_client, observe_project, trace_session
    ):
        """Both paths must serialize created_at to the same ISO format (Z suffix)."""
        first_seen = trace_session.created_at

        pg_response = auth_client.patch(
            f"/tracer/trace-session/{trace_session.id}/",
            {"bookmarked": True},
            format="json",
        )
        pg_created_at = pg_response.json()["created_at"]

        ch_session_id = uuid.uuid4()
        with mock.patch(
            "tracer.views.trace_session._resolve_ch_session_fields",
            return_value={
                "project_id": observe_project.id,
                "bookmarked": False,
                "display_name": "ch-session",
                "first_seen": first_seen,
            },
        ):
            ch_response = auth_client.patch(
                f"/tracer/trace-session/{ch_session_id}/",
                {"bookmarked": True},
                format="json",
            )
        ch_created_at = ch_response.json()["created_at"]

        assert pg_created_at == ch_created_at


@pytest.mark.integration
@pytest.mark.api
class TestTraceSessionUserIdFilterValidation:
    """Unsupported user_id filter operators must be rejected, not silently matched."""

    def test_contains_op_rejected(self, auth_client, observe_project):
        import json

        filters = json.dumps([
            {
                "column_id": "user_id",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "alice",
                },
            }
        ])
        response = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {"project_id": str(observe_project.id), "filters": filters},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_starts_with_op_rejected(self, auth_client, observe_project):
        import json

        filters = json.dumps([
            {
                "column_id": "user_id",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "starts_with",
                    "filter_value": "ali",
                },
            }
        ])
        response = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {"project_id": str(observe_project.id), "filters": filters},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_equals_op_accepted(self, auth_client, observe_project):
        import json

        filters = json.dumps([
            {
                "column_id": "user_id",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "alice",
                },
            }
        ])
        with mock.patch(
            "tracer.views.trace_session._resolve_end_user_ids_for_user_id",
            return_value=([], None),
        ):
            response = auth_client.get(
                "/tracer/trace-session/list_sessions/",
                {"project_id": str(observe_project.id), "filters": filters},
            )
        assert response.status_code != status.HTTP_400_BAD_REQUEST


# ===========================================================================
# Benchmark: Session List Query Latency (requires running ClickHouse)
# ===========================================================================


@pytest.mark.benchmark
class TestSessionListLatency:
    """Wall-time benchmarks for /tracer/trace-session/list_sessions/.

    These tests hit the real ClickHouse instance and measure end-to-end
    latency for common filter combinations. They skip automatically when
    CH is not reachable.

    Run with: pytest -m benchmark futureagi/tracer/tests/test_trace_session.py -v
    """

    @staticmethod
    def _ch_available():
        try:
            from tracer.services.clickhouse.client import (
                ClickHouseClient,
                is_clickhouse_enabled,
            )

            if not is_clickhouse_enabled():
                return False
            client = ClickHouseClient()
            client.execute_read("SELECT 1", {})
            return True
        except Exception:
            return False

    @staticmethod
    def _get_test_project_id():
        from tracer.services.clickhouse.client import ClickHouseClient

        client = ClickHouseClient()
        rows, _, _ = client.execute_read(
            "SELECT toString(project_id), count() AS n "
            "FROM spans WHERE is_deleted = 0 "
            "GROUP BY project_id ORDER BY n DESC LIMIT 1",
            {},
        )
        if rows:
            return rows[0][0] if isinstance(rows[0], (list, tuple)) else rows[0].get("project_id")
        return None

    @pytest.fixture(autouse=True)
    def skip_if_no_ch(self):
        if not self._ch_available():
            pytest.skip("ClickHouse not reachable for benchmark")
        self.project_id = self._get_test_project_id()
        if not self.project_id:
            pytest.skip("No spans data in ClickHouse for benchmark")

    @pytest.fixture(autouse=True)
    def seed_benchmark_spans(self, skip_if_no_ch):
        """Seed 1000 sessions × 5 spans = 5000 spans, 30-day spread, 200 end_users."""
        from datetime import timedelta

        from tracer.services.clickhouse.client import ClickHouseClient

        client = ClickHouseClient()
        rows, _, _ = client.execute_read(
            "SELECT count() FROM spans WHERE is_deleted = 0 "
            "AND project_id = %(pid)s "
            "AND trace_session_id IS NOT NULL "
            "AND (parent_span_id IS NULL OR parent_span_id = '')",
            {"pid": self.project_id},
        )
        row_count = rows[0][0] if rows and isinstance(rows[0], (list, tuple)) else (rows[0].get("count()", 0) if rows else 0)

        eu_rows, _, _ = client.execute_read(
            "SELECT count() FROM end_users WHERE project_id = %(pid)s AND is_deleted = 0",
            {"pid": self.project_id},
        )
        eu_count = eu_rows[0][0] if eu_rows and isinstance(eu_rows[0], (list, tuple)) else (eu_rows[0].get("count()", 0) if eu_rows else 0)

        if row_count >= 1000 and eu_count >= 100:
            return

        now = datetime.now(timezone.utc) if hasattr(timezone, 'utc') else datetime.utcnow()
        import os

        import clickhouse_connect

        from django.conf import settings

        ch_settings = getattr(settings, "CLICKHOUSE", {})
        ch = clickhouse_connect.get_client(
            host=ch_settings.get("CH_HOST", "localhost"),
            port=int(os.environ.get("CH_HTTP_PORT", 8123)),
            username=ch_settings.get("CH_USERNAME", "default"),
            password=ch_settings.get("CH_PASSWORD", "") or "",
            database=ch_settings.get("CH_DATABASE", "test_tfc"),
        )

        num_sessions = 1000
        spans_per_session = 5
        session_ids = [str(uuid.uuid4()) for _ in range(num_sessions)]
        end_user_ids = [str(uuid.uuid4()) for _ in range(200)]

        batch_values = []
        for s_idx, sid in enumerate(session_ids):
            trace_id = str(uuid.uuid4())
            session_start = now - timedelta(days=s_idx % 30, hours=s_idx % 24)

            for sp_idx in range(spans_per_session):
                span_id = f"bench_{uuid.uuid4().hex[:16]}"
                start = session_start + timedelta(seconds=sp_idx * 3)
                end = start + timedelta(seconds=2)
                is_root = sp_idx == 0
                euid = end_user_ids[s_idx % len(end_user_ids)]

                batch_values.append(
                    f"('{span_id}', '{trace_id}', '{self.project_id}', '{sid}', "
                    f"'{euid}', 'llm', "
                    f"'{start.strftime('%Y-%m-%d %H:%M:%S')}', "
                    f"'{end.strftime('%Y-%m-%d %H:%M:%S')}', "
                    f"{100 + sp_idx * 50}, {0.001 * (sp_idx + 1)}, "
                    f"{10 * (sp_idx + 1)}, {5 * (sp_idx + 1)}, {5 * (sp_idx + 1)}, "
                    f"'{'ERROR' if s_idx % 20 == 0 else 'OK'}', 0, "
                    f"{'NULL' if is_root else repr(span_id + '_parent')}, "
                    f"'bench_span_{s_idx}_{sp_idx}', "
                    f"'hello session {s_idx}', 'response {s_idx}')"
                )

                if len(batch_values) >= 500:
                    ch.command(
                        "INSERT INTO spans "
                        "(id, trace_id, project_id, trace_session_id, end_user_id, "
                        "observation_type, start_time, end_time, latency_ms, cost, "
                        "total_tokens, prompt_tokens, completion_tokens, status, "
                        "is_deleted, parent_span_id, name, input, output) VALUES "
                        + ", ".join(batch_values)
                    )
                    batch_values = []

        if batch_values:
            ch.command(
                "INSERT INTO spans "
                "(id, trace_id, project_id, trace_session_id, end_user_id, "
                "observation_type, start_time, end_time, latency_ms, cost, "
                "total_tokens, prompt_tokens, completion_tokens, status, "
                "is_deleted, parent_span_id, name, input, output) VALUES "
                + ", ".join(batch_values)
            )

        org_id = "00000000-0000-0000-0000-000000000001"
        eu_values = []
        for i, euid in enumerate(end_user_ids):
            eu_values.append(
                f"('{self.project_id}', '{euid}', '{org_id}', "
                f"'bench_user_{i}', 'email', '', '{{}}', "
                f"'{now.strftime('%Y-%m-%d %H:%M:%S')}', "
                f"'{now.strftime('%Y-%m-%d %H:%M:%S')}', 0)"
            )
        ch.command(
            "INSERT INTO end_users "
            "(project_id, end_user_id, organization_id, user_id, "
            "user_id_type, user_id_hash, metadata, first_seen, version, is_deleted) "
            "VALUES " + ", ".join(eu_values)
        )

    def _run_session_list_query(self, filters, project_id=None, sort_params=None, page_number=0, page_size=30):
        import time

        from tracer.services.clickhouse.query_service import AnalyticsQueryService
        from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

        _Cls = get_query_builder_class("SESSION_LIST")
        builder = _Cls(
            project_id=project_id or self.project_id,
            page_number=page_number,
            page_size=page_size,
            filters=filters,
            sort_params=sort_params or [],
        )
        analytics = AnalyticsQueryService()
        query, params = builder.build()

        t0 = time.time()
        result = analytics.execute_ch_query(query, params, timeout_ms=15000)
        main_ms = (time.time() - t0) * 1000

        session_ids = [str(row.get("session_id", "")) for row in result.data[:30]]

        enrichment_ms = 0
        if session_ids:
            from concurrent.futures import ThreadPoolExecutor

            def _content():
                cq, cp = builder.build_content_query(session_ids)
                if cq:
                    analytics.execute_ch_query(cq, cp, timeout_ms=10000)

            def _attrs():
                aq, ap = builder.build_span_attributes_query(session_ids)
                if aq:
                    analytics.execute_ch_query(aq, ap, timeout_ms=5000)

            t1 = time.time()
            with ThreadPoolExecutor(max_workers=3) as pool:
                pool.submit(_content)
                pool.submit(_attrs)
            enrichment_ms = (time.time() - t1) * 1000

        return main_ms, enrichment_ms, len(session_ids)

    def test_latency_with_project_id_and_time_filter(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(filters)
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] project_id + time: main={main_ms:.0f}ms enrich={enrich_ms:.0f}ms total={total:.0f}ms sessions={count}")
        assert count >= 30, f"Benchmark should find seeded sessions (got {count}, expected >=30)"
        assert total < 3000, f"Session list with project_id took {total:.0f}ms (threshold: 3000ms)"

    def test_latency_with_project_id_and_cost_filter(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            },
            {
                "column_id": "total_cost",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                },
            },
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(filters)
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] project_id + time + cost>0: main={main_ms:.0f}ms enrich={enrich_ms:.0f}ms total={total:.0f}ms sessions={count}")
        assert total < 3000, f"Session list with cost filter took {total:.0f}ms (threshold: 3000ms)"

    def test_latency_without_project_id(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(filters, project_id=None)
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] no project_id + time: main={main_ms:.0f}ms enrich={enrich_ms:.0f}ms total={total:.0f}ms sessions={count}")
        assert total < 3000, f"Session list without project_id took {total:.0f}ms (threshold: 3000ms)"

    def test_latency_with_sort_by_duration(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(
            filters, sort_params=[{"column_id": "duration", "direction": "desc"}]
        )
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] sort by duration DESC: {total:.0f}ms sessions={count}")
        assert total < 2000, f"Session list sorted by duration took {total:.0f}ms (threshold: 2000ms)"

    def test_latency_with_tokens_having_filter(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            },
            {
                "column_id": "total_tokens",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                },
            },
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(filters)
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] tokens>0 HAVING: main={main_ms:.0f}ms enrich={enrich_ms:.0f}ms total={total:.0f}ms sessions={count}")
        assert total < 3000, f"Session list with tokens HAVING took {total:.0f}ms (threshold: 3000ms)"

    def test_latency_with_traces_count_filter(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            },
            {
                "column_id": "traces_count",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than_or_equal",
                    "filter_value": 1,
                },
            },
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(filters)
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] traces_count>=1 HAVING: main={main_ms:.0f}ms enrich={enrich_ms:.0f}ms total={total:.0f}ms sessions={count}")
        assert total < 3000, f"Session list with traces_count HAVING took {total:.0f}ms (threshold: 3000ms)"

    def test_latency_sort_by_cost_asc(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(
            filters, sort_params=[{"column_id": "total_cost", "direction": "asc"}]
        )
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] sort by cost ASC: {total:.0f}ms sessions={count}")
        assert total < 2000, f"Session list sorted by cost took {total:.0f}ms (threshold: 2000ms)"

    def test_latency_narrow_time_range_24h(self):
        from datetime import timedelta

        now = datetime.utcnow()
        start = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start, end],
                },
            }
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(filters)
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] 24h window: main={main_ms:.0f}ms enrich={enrich_ms:.0f}ms total={total:.0f}ms sessions={count}")
        assert total < 1500, f"Session list with 24h window took {total:.0f}ms (threshold: 1500ms)"

    def test_latency_combined_filters_and_sort(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            },
            {
                "column_id": "total_tokens",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 5,
                },
            },
            {
                "column_id": "total_cost",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                },
            },
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(
            filters, sort_params=[{"column_id": "duration", "direction": "desc"}]
        )
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] tokens+cost+sort_duration: {total:.0f}ms sessions={count}")
        assert total < 3000, f"Combined filters + sort took {total:.0f}ms (threshold: 3000ms)"

    def test_latency_page_2(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        main_ms, enrich_ms, count = self._run_session_list_query(
            filters, page_number=1, page_size=10
        )
        total = main_ms + enrich_ms
        print(f"\n  [BENCHMARK] page 2 (offset 10): {total:.0f}ms sessions={count}")
        assert total < 2000, f"Session list page 2 took {total:.0f}ms (threshold: 2000ms)"


class TestUserListLatency:

    @staticmethod
    def _ch_available():
        try:
            from tracer.services.clickhouse.client import (
                ClickHouseClient,
                is_clickhouse_enabled,
            )

            if not is_clickhouse_enabled():
                return False
            client = ClickHouseClient()
            client.execute_read("SELECT 1", {})
            return True
        except Exception:
            return False

    @staticmethod
    def _get_test_project_id():
        from tracer.services.clickhouse.client import ClickHouseClient

        client = ClickHouseClient()
        rows, _, _ = client.execute_read(
            "SELECT toString(project_id), count() AS n "
            "FROM spans WHERE is_deleted = 0 "
            "AND end_user_id IS NOT NULL "
            "GROUP BY project_id ORDER BY n DESC LIMIT 1",
            {},
        )
        if rows:
            return rows[0][0] if isinstance(rows[0], (list, tuple)) else rows[0].get("project_id")
        return None

    @pytest.fixture(autouse=True)
    def skip_if_no_ch(self):
        if not self._ch_available():
            pytest.skip("ClickHouse not reachable for benchmark")
        self.project_id = self._get_test_project_id()
        if not self.project_id:
            pytest.skip("No spans with end_user_id in ClickHouse for benchmark")

    def _run_user_list_query(self, filters, sort_params=None):
        import time

        from tracer.services.clickhouse.query_service import AnalyticsQueryService
        from tracer.services.clickhouse.v2.query_builders.user_list import (
            UserListQueryBuilderV2,
        )

        builder = UserListQueryBuilderV2(
            organization_id="00000000-0000-0000-0000-000000000001",
            project_ids=[self.project_id],
            filters=filters,
            sort_params=sort_params or [],
            limit=30,
            offset=0,
        )
        analytics = AnalyticsQueryService()
        query, params = builder.build()

        t0 = time.time()
        result = analytics.execute_ch_query(query, params, timeout_ms=15000)
        total_ms = (time.time() - t0) * 1000

        return total_ms, len(result.data)

    def test_latency_default_time_range(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        ms, count = self._run_user_list_query(filters)
        print(f"\n  [BENCHMARK] users default: {ms:.0f}ms users={count}")
        assert count > 0, f"Expected users, got {count}"
        assert ms < 3000, f"User list took {ms:.0f}ms (threshold: 3000ms)"

    def test_latency_sort_by_cost(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        ms, count = self._run_user_list_query(
            filters, sort_params=[{"column_id": "total_cost", "direction": "desc"}]
        )
        print(f"\n  [BENCHMARK] users sort by cost: {ms:.0f}ms users={count}")
        assert ms < 3000, f"User list sorted by cost took {ms:.0f}ms (threshold: 3000ms)"

    def test_latency_sort_by_tokens(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        ms, count = self._run_user_list_query(
            filters, sort_params=[{"column_id": "total_tokens", "direction": "desc"}]
        )
        print(f"\n  [BENCHMARK] users sort by tokens: {ms:.0f}ms users={count}")
        assert ms < 3000, f"User list sorted by tokens took {ms:.0f}ms (threshold: 3000ms)"

    def test_latency_sort_by_trace_count(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        ms, count = self._run_user_list_query(
            filters, sort_params=[{"column_id": "trace_count", "direction": "desc"}]
        )
        print(f"\n  [BENCHMARK] users sort by trace_count: {ms:.0f}ms users={count}")
        assert ms < 3000, f"User list sorted by trace_count took {ms:.0f}ms (threshold: 3000ms)"

    def test_latency_narrow_24h_window(self):
        from datetime import timedelta

        now = datetime.utcnow()
        start = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start, end],
                },
            }
        ]
        ms, count = self._run_user_list_query(filters)
        print(f"\n  [BENCHMARK] users 24h window: {ms:.0f}ms users={count}")
        assert ms < 2000, f"User list 24h took {ms:.0f}ms (threshold: 2000ms)"

    def test_latency_page_2(self):
        import time

        from tracer.services.clickhouse.query_service import AnalyticsQueryService
        from tracer.services.clickhouse.v2.query_builders.user_list import (
            UserListQueryBuilderV2,
        )

        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01T00:00:00.000Z", "2026-12-31T23:59:59.000Z"],
                },
            }
        ]
        builder = UserListQueryBuilderV2(
            organization_id="00000000-0000-0000-0000-000000000001",
            project_ids=[self.project_id],
            filters=filters,
            sort_params=[],
            limit=10,
            offset=10,
        )
        analytics = AnalyticsQueryService()
        query, params = builder.build()

        t0 = time.time()
        result = analytics.execute_ch_query(query, params, timeout_ms=15000)
        ms = (time.time() - t0) * 1000
        print(f"\n  [BENCHMARK] users page 2: {ms:.0f}ms users={len(result.data)}")
        assert ms < 3000, f"User list page 2 took {ms:.0f}ms (threshold: 3000ms)"

    def test_latency_wide_6_month_range(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-12-01T00:00:00.000Z", "2026-06-30T23:59:59.000Z"],
                },
            }
        ]
        ms, count = self._run_user_list_query(filters)
        print(f"\n  [BENCHMARK] users 6-month range: {ms:.0f}ms users={count}")
        assert ms < 5000, f"User list 6-month took {ms:.0f}ms (threshold: 5000ms)"
