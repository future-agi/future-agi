"""
TraceSession API Tests

Tests for /tracer/trace-session/ endpoints.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession


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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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

    def test_retrieve_session_navigation_returns_none_when_ch_unavailable(
        self, auth_client, observe_project
    ):
        """With CH disabled and no PG fallback, navigation returns
        ``(None, None)`` and the page still renders 200."""
        base = timezone.now()
        _create_session_with_span(observe_project, "A", base - timedelta(minutes=1))
        s_focus = _create_session_with_span(observe_project, "B", base)

        response = auth_client.get(f"/tracer/trace-session/{s_focus.id}/")
        assert response.status_code == status.HTTP_200_OK
        metadata = get_result(response)["session_metadata"]
        assert metadata["previous_session_id"] is None
        assert metadata["next_session_id"] is None


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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
class TestGetSessionNavigationCHOnly:
    """Contract tests for ``tracer.utils.session.get_session_navigation``.

    Navigation is CH-only — there is no Postgres fallback. On a CH
    failure the wrapper must return ``(None, None)``.
    """

    @staticmethod
    def _request(organization, user):
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        req = APIRequestFactory().get("/tracer/trace-session/abc/")
        req.user = user
        req.organization = organization
        return Request(req)

    def test_returns_ch_result_when_clickhouse_succeeds(
        self, organization, user, observe_project, trace_session, monkeypatch
    ):
        """CH happy path: tuple returned verbatim."""
        from tracer.utils import session as session_utils

        sentinel = ("next-uuid", "prev-uuid")
        monkeypatch.setattr(
            session_utils, "_try_session_navigation_ch", lambda *a, **kw: sentinel
        )

        request = self._request(organization, user)
        result = session_utils.get_session_navigation(
            request, observe_project.id, trace_session.id
        )
        assert result == sentinel

    def test_returns_none_tuple_when_ch_fails(
        self, organization, user, observe_project, trace_session, monkeypatch
    ):
        """CH error (helper returns ``None``) → wrapper returns
        ``(None, None)``. Confirms no PG code is touched.
        """
        from tracer.utils import session as session_utils

        monkeypatch.setattr(
            session_utils, "_try_session_navigation_ch", lambda *a, **kw: None
        )

        request = self._request(organization, user)
        result = session_utils.get_session_navigation(
            request, observe_project.id, trace_session.id
        )
        assert result == (None, None)

    def test_returns_none_tuple_when_ch_returns_no_data(
        self, organization, user, observe_project, trace_session, monkeypatch
    ):
        """CH ran successfully but no neighbours → ``(None, None)``."""
        from tracer.utils import session as session_utils

        monkeypatch.setattr(
            session_utils,
            "_try_session_navigation_ch",
            lambda *a, **kw: (None, None),
        )

        request = self._request(organization, user)
        result = session_utils.get_session_navigation(
            request, observe_project.id, trace_session.id
        )
        assert result == (None, None)

    def test_pg_navigation_helper_is_no_longer_present(self):
        """Structural guard: the Postgres navigation helper must not be
        re-added — its full-project span aggregate breached the 30 s
        ``statement_timeout``."""
        from tracer.utils import session as session_utils

        assert not hasattr(
            session_utils, "_get_session_navigation_pg"
        ), "Navigation must remain ClickHouse-only."


@pytest.mark.integration
class TestTraceSessionRetrieveErrorHandling:
    """Contract tests for ``TraceSessionView.retrieve``.

    CH errors must surface as proper HTTP status codes (504 on
    ``OperationalError``, 400 otherwise) and must not silently invoke
    the legacy Postgres body.
    """

    def _force_ch_route(self, monkeypatch):
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
        )

        monkeypatch.setattr(
            AnalyticsQueryService, "should_use_clickhouse", lambda self, qt: True
        )

    def test_retrieve_returns_504_on_postgres_statement_timeout(
        self, auth_client, trace_session, monkeypatch
    ):
        """``OperationalError`` from the CH detail handler must surface
        as HTTP 504, not 400 with a raw psycopg string."""
        from django.db import OperationalError

        from tracer.views import trace_session as view_module

        def _raise(*a, **kw):
            raise OperationalError("canceling statement due to statement timeout")

        monkeypatch.setattr(
            view_module.TraceSessionView, "_retrieve_clickhouse", _raise
        )
        self._force_ch_route(monkeypatch)

        response = auth_client.get(f"/tracer/trace-session/{trace_session.id}/")
        assert response.status_code == status.HTTP_504_GATEWAY_TIMEOUT
        body = response.json()
        assert body["status"] is False
        assert "time budget" in body["result"].lower()

    def test_retrieve_returns_400_on_unexpected_error(
        self, auth_client, trace_session, monkeypatch
    ):
        """A non-database exception still returns 400, but the body must
        not leak the raw ``str(e)``."""
        from tracer.views import trace_session as view_module

        def _raise(*a, **kw):
            raise RuntimeError("super-secret internal detail")

        monkeypatch.setattr(
            view_module.TraceSessionView, "_retrieve_clickhouse", _raise
        )
        self._force_ch_route(monkeypatch)

        response = auth_client.get(f"/tracer/trace-session/{trace_session.id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "super-secret internal detail" not in str(response.json())

    def test_retrieve_does_not_silently_run_pg_after_ch_failure(
        self, auth_client, trace_session, monkeypatch
    ):
        """Regression guard: when CH errors, the legacy PG body must not
        execute. Patches the CH method to raise and patches
        ``Trace.objects.filter`` (the first ORM call in the PG body) to
        raise ``AssertionError`` — the test passes only if the assertion
        never fires.
        """
        from django.db import OperationalError

        from tracer.models.trace import Trace
        from tracer.views import trace_session as view_module

        def _ch_boom(*a, **kw):
            raise OperationalError("CH unavailable")

        monkeypatch.setattr(
            view_module.TraceSessionView, "_retrieve_clickhouse", _ch_boom
        )

        def _pg_must_not_run(*a, **kw):
            raise AssertionError(
                "PG fallback executed after CH error — silent fallback regressed"
            )

        monkeypatch.setattr(Trace.objects, "filter", _pg_must_not_run)

        self._force_ch_route(monkeypatch)

        response = auth_client.get(f"/tracer/trace-session/{trace_session.id}/")
        # The real assertion is that AssertionError above did not fire.
        assert response.status_code in (
            status.HTTP_504_GATEWAY_TIMEOUT,
            status.HTTP_400_BAD_REQUEST,
        )


@pytest.mark.integration
class TestRetrieveClickhouseInnerPGBound:
    """The ``CustomEvalConfig`` metadata fetch is the only Postgres hop
    inside ``_retrieve_clickhouse``; a failure there must degrade
    gracefully (200 with empty eval columns or 504), never bubble as
    an unhandled 500."""

    def test_eval_configs_degrade_to_empty_on_pg_timeout(
        self, auth_client, trace_session, monkeypatch
    ):
        """When the inner PG fetch trips its per-statement budget, the
        endpoint does not propagate the ``OperationalError`` as a 5xx —
        it either returns 200 (degraded eval columns) or 504 (a
        different PG hop hit the middleware timeout), never an
        unhandled 500.
        """
        from django.db import OperationalError

        from tracer.models.custom_eval_config import CustomEvalConfig

        def _force_timeout(*a, **kw):
            raise OperationalError("canceling statement due to statement timeout")

        monkeypatch.setattr(CustomEvalConfig.objects, "filter", _force_timeout)

        response = auth_client.get(f"/tracer/trace-session/{trace_session.id}/")
        assert response.status_code != 500
