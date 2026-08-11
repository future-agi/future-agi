"""
Tests for the session-scoped eval logs endpoint.

GET /tracer/trace-session/<id>/eval_logs/ is the only surface that
exposes session-target EvalLogger rows — session evals are walled
off from the spans table, trace drawer, and span detail panel, so
the TracesDrawer "Evals" tab (built on top of this endpoint) is the
sole entry point for users to see them.

Pin four things:

  1. Auth — unauthenticated requests are rejected (403).
  2. Filtering — only ``target_type='session'`` rows for the given
     session show up. Span and trace rows on the same session are NOT
     surfaced (the wall-off rule). Soft-deleted rows are skipped. Rows
     belonging to other sessions are not surfaced.
  3. Shape — log_items mirror EvalTaskView.get_usage so the FE renderer
     (ScoreCell, DetailRow, ErrorDetails, ExpandableJson) is reused.
  4. Pagination — page / page_size params behave as documented and
     ``total`` reflects the unpaginated count.
"""

import uuid

import pytest
from django.utils import timezone
from rest_framework import status

from tracer.models.observation_span import (
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.tests._ch_seed import seed_ch_eval_loggers, truncate_ch_eval_logger


@pytest.fixture(autouse=True)
def _ch_eval_logger(settings):
    """Pin the eval-logger read to CH ``tracer_eval_logger`` (prod default, not
    the ``_v2`` test default) so the endpoint matches prod, and truncate the
    table around each test so seeded rows never leak into another's ``total``."""
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    truncate_ch_eval_logger()
    yield
    truncate_ch_eval_logger()


def _seed(*rows):
    """Seed the given EvalLogger rows into CH. Call right before the request,
    after any soft-delete mutation, so each row's ``deleted`` flag is final."""
    seed_ch_eval_loggers([r for r in rows if r is not None])


def _result(response):
    """Unwrap the success_response envelope ``{"result": {...}}``."""
    return response.json().get("result", response.json())


def _make_session(project, name="Session"):
    return TraceSession.objects.create(project=project, name=name)


def _make_trace(project, session=None, name="Trace"):
    return Trace.objects.create(
        project=project,
        session=session,
        name=name,
        input={"prompt": "p"},
        output={"response": "r"},
    )


def _make_span(project, trace, *, span_id=None, name="Span"):
    span = ObservationSpan.objects.create(
        id=span_id or f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=name,
        observation_type="llm",
        start_time=timezone.now(),
        end_time=timezone.now(),
        input={"x": 1},
        output={"y": 2},
    )
    return span


def _make_session_eval(session, custom_eval_config, **overrides):
    """``EvalLogger`` row with ``target_type='session'``.

    Pre-set safe defaults so callers only override what each test cares
    about. The ``eval_logger_target_type_fks`` check constraint requires
    NULL span/trace and a populated ``trace_session`` for session rows;
    passing extra FKs here will rightly fail at save() — that's by design.
    """
    fields = dict(
        target_type=EvalTargetType.SESSION,
        trace_session=session,
        observation_span=None,
        trace=None,
        custom_eval_config=custom_eval_config,
        eval_task_id=str(uuid.uuid4()),
        output_bool=True,
        eval_explanation="ok",
        error=False,
    )
    fields.update(overrides)
    return EvalLogger.objects.create(**fields)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSessionEvalLogsAuth:
    def test_unauthenticated_returns_403(self, api_client, trace_session):
        response = api_client.get(
            f"/tracer/trace-session/{trace_session.id}/eval_logs/"
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSessionEvalLogsFiltering:
    """The wall-off rule made concrete: only session-target rows surface."""

    def test_returns_only_session_target_rows_for_this_session(
        self,
        auth_client,
        observe_project,
        project,
        custom_eval_config,
    ):
        """Insert one row per target_type sharing the same logical session
        anchor; assert only the session-target row comes back.

        Span rows and trace rows on a trace inside this session would
        normally be reachable via the spans table / trace drawer — they
        MUST NOT leak into the session evals tab.
        """
        session = _make_session(observe_project, name="S1")
        trace = _make_trace(observe_project, session=session)
        span = _make_span(project, trace)

        session_row = _make_session_eval(
            session, custom_eval_config, eval_explanation="session-level"
        )
        # Span-target on a span inside the session
        span_row = EvalLogger.objects.create(
            target_type=EvalTargetType.SPAN,
            observation_span=span,
            trace=trace,
            trace_session=None,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(uuid.uuid4()),
            output_bool=False,
            eval_explanation="span-level",
        )
        # Trace-target anchored to the root span of the session's trace
        trace_row = EvalLogger.objects.create(
            target_type=EvalTargetType.TRACE,
            observation_span=span,
            trace=trace,
            trace_session=None,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(uuid.uuid4()),
            output_bool=True,
            eval_explanation="trace-level",
        )

        # Seed all three so the wall-off is proven at the CH read layer
        # (target_type='session' filter), not just by absence of data.
        _seed(session_row, span_row, trace_row)
        response = auth_client.get(
            f"/tracer/trace-session/{session.id}/eval_logs/"
        )
        assert response.status_code == status.HTTP_200_OK
        result = _result(response)
        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["id"] == str(session_row.id)
        assert result["items"][0]["detail"]["target_type"] == "session"

    def test_excludes_soft_deleted_rows(
        self, auth_client, observe_project, custom_eval_config
    ):
        session = _make_session(observe_project, name="S1")
        live = _make_session_eval(
            session, custom_eval_config, eval_explanation="live"
        )
        deleted = _make_session_eval(
            session, custom_eval_config, eval_explanation="deleted"
        )
        EvalLogger.objects.filter(id=deleted.id).update(deleted=True)
        deleted.refresh_from_db()

        # Seed AFTER the soft-delete so CH captures deleted=1 for that row.
        _seed(live, deleted)
        response = auth_client.get(
            f"/tracer/trace-session/{session.id}/eval_logs/"
        )
        assert response.status_code == status.HTTP_200_OK
        result = _result(response)
        assert result["total"] == 1
        ids = [item["id"] for item in result["items"]]
        assert ids == [str(live.id)]

    def test_does_not_surface_other_sessions_rows(
        self, auth_client, observe_project, custom_eval_config
    ):
        s1 = _make_session(observe_project, name="S1")
        s2 = _make_session(observe_project, name="S2")
        s1_row = _make_session_eval(s1, custom_eval_config)
        s2_row = _make_session_eval(s2, custom_eval_config)

        _seed(s1_row, s2_row)
        response = auth_client.get(f"/tracer/trace-session/{s1.id}/eval_logs/")
        assert response.status_code == status.HTTP_200_OK
        result = _result(response)
        assert result["total"] == 1
        assert result["items"][0]["id"] == str(s1_row.id)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSessionEvalLogsShape:
    """log_items mirrors EvalTaskView.get_usage so the FE reuses the renderer."""

    def test_log_item_carries_session_fields_and_omits_span_trace_ids(
        self, auth_client, observe_project, custom_eval_config
    ):
        session = _make_session(observe_project, name="My Session")
        row = _make_session_eval(
            session,
            custom_eval_config,
            output_bool=True,
            eval_explanation="passed",
        )

        _seed(row)
        response = auth_client.get(
            f"/tracer/trace-session/{session.id}/eval_logs/"
        )
        assert response.status_code == status.HTTP_200_OK
        item = _result(response)["items"][0]

        # Top-level fields the FE renders in the row
        assert item["id"] == str(row.id)
        assert item["session_id"] == str(session.id)
        assert item["result"] == "Passed"
        assert item["score"] == 1.0
        assert item["status"] == "success"
        assert item["source"] == "eval_task"
        assert item["eval_id"] == str(custom_eval_config.id)
        assert item["eval_name"] == custom_eval_config.name
        assert item["model"] == custom_eval_config.model
        assert item["input"] == "My Session"

        # Detail payload — span/trace IDs intentionally NOT keys here.
        # The eval_logger_target_type_fks check constraint guarantees
        # NULL on session rows so surfacing them would just clutter
        # the panel.
        detail = item["detail"]
        assert detail["target_type"] == "session"
        assert detail["session_id"] == str(session.id)
        assert detail["session_name"] == "My Session"
        # CH stores output_bool as Nullable(UInt8) → the read path surfaces the
        # int 1/0, not Python True/False (matches prod). Pin the int-ness
        # explicitly: `True == 1` in Python, so `== 1` alone wouldn't catch a
        # regression that surfaced a bool here.
        assert detail["output_bool"] == 1
        # `type() is int` (not isinstance): bool subclasses int, so isinstance
        # would pass for True too — this pins the surfaced value as a plain int.
        assert type(detail["output_bool"]) is int
        assert detail["error_message"] is None
        assert "span_id" not in detail
        assert "trace_id" not in detail
        assert "span_name" not in detail

    def test_error_row_has_status_error_and_reason_from_error_message(
        self, auth_client, observe_project, custom_eval_config
    ):
        session = _make_session(observe_project, name="Errored")
        row = _make_session_eval(
            session,
            custom_eval_config,
            error=True,
            error_message="boom",
            output_bool=None,
            eval_explanation=None,
        )

        _seed(row)
        response = auth_client.get(
            f"/tracer/trace-session/{session.id}/eval_logs/"
        )
        item = _result(response)["items"][0]
        assert item["status"] == "error"
        assert item["result"] == "Error"
        assert item["score"] is None
        assert item["reason"] == "boom"
        assert item["detail"]["error_message"] == "boom"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSessionEvalLogsPagination:
    def test_page_size_param_caps_results(
        self, auth_client, observe_project, custom_eval_config
    ):
        session = _make_session(observe_project, name="Pagey")
        rows = [_make_session_eval(session, custom_eval_config) for _ in range(5)]

        _seed(*rows)
        response = auth_client.get(
            f"/tracer/trace-session/{session.id}/eval_logs/",
            {"page": 0, "page_size": 2},
        )
        assert response.status_code == status.HTTP_200_OK
        result = _result(response)
        assert result["total"] == 5
        assert result["page"] == 0
        assert result["page_size"] == 2
        assert len(result["items"]) == 2

    def test_second_page_returns_remaining(
        self, auth_client, observe_project, custom_eval_config
    ):
        session = _make_session(observe_project, name="Pagey")
        rows = [_make_session_eval(session, custom_eval_config) for _ in range(5)]

        _seed(*rows)
        response = auth_client.get(
            f"/tracer/trace-session/{session.id}/eval_logs/",
            {"page": 2, "page_size": 2},
        )
        assert response.status_code == status.HTTP_200_OK
        result = _result(response)
        assert result["total"] == 5
        # Page 2 of size 2 = items 4..5 → only 1 item left
        assert len(result["items"]) == 1

    def test_page_size_clamped_to_100(
        self, auth_client, observe_project, custom_eval_config
    ):
        """Ridiculous page_size requests are clamped, not honoured."""
        session = _make_session(observe_project, name="Pagey")
        _seed(_make_session_eval(session, custom_eval_config))

        response = auth_client.get(
            f"/tracer/trace-session/{session.id}/eval_logs/",
            {"page_size": 9999},
        )
        assert response.status_code == status.HTTP_200_OK
        assert _result(response)["page_size"] == 100

    def test_default_page_size_is_25(
        self, auth_client, observe_project, custom_eval_config
    ):
        session = _make_session(observe_project, name="Pagey")
        _seed(_make_session_eval(session, custom_eval_config))

        response = auth_client.get(
            f"/tracer/trace-session/{session.id}/eval_logs/"
        )
        assert response.status_code == status.HTTP_200_OK
        assert _result(response)["page_size"] == 25
