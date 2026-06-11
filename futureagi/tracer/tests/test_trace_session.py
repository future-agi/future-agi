"""
TraceSession API Tests

Tests for /tracer/trace-session/ endpoints.
"""

import uuid
from datetime import timedelta
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
            if "count(DISTINCT trace_id)" in query:
                bound_session_ids.append(params["session_id"])
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
            if "GROUP BY trace_id" in query:
                bound_session_ids.append(params["session_id"])
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
                mock.Mock(id=requested_id),
                observe_project.id,
                analytics,
                {"page_number": 0, "page_size": 10},
            )

        assert response.status_code == status.HTTP_200_OK
        assert bound_session_ids == [survivor_id, survivor_id]

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
