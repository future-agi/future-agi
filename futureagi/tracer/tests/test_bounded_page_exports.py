import csv
import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest
from rest_framework import status

from tracer.views.observation_span import ObservationSpanView
from tracer.views.trace import TraceView
from tracer.views.trace_session import TraceSessionView

pytestmark = pytest.mark.unit


def _request(query_params):
    request = MagicMock()
    request.query_params = query_params
    request.validated_query_data = {}
    request.user.organization = SimpleNamespace(id="org-1")
    return request


def _rows(response):
    body = (
        b"".join(response.streaming_content)
        if getattr(response, "streaming", False)
        else response.content
    )
    return list(csv.reader(io.StringIO(body.decode())))


def test_trace_export_returns_bounded_csv_and_marks_partial_page():
    request = _request({"project_id": "00000000-0000-0000-0000-000000000001"})
    project = SimpleNamespace(name="Observe")
    page = SimpleNamespace(
        status_code=status.HTTP_200_OK,
        data={
            "result": {
                "table": [{"trace_id": "trace-1", "cost": 1.25}],
                "metadata": {"has_more": True, "total_rows_is_lower_bound": True},
            }
        },
    )

    with (
        patch("tracer.views.trace._project_queryset_for_request") as projects,
        patch(
            "tracer.views.trace._has_voice_conversation_roots", return_value=False
        ) as voice_detection,
        patch.object(TraceView, "list_traces_of_session", return_value=page) as listing,
    ):
        projects.return_value.filter.return_value.first.return_value = project
        response = TraceView().get_trace_export_data(request)

    assert response.status_code == status.HTTP_200_OK
    assert (
        response["Content-Disposition"] == 'attachment; filename="Observe_traces.csv"'
    )
    assert _rows(response) == [
        ["trace_id", "cost"],
        ["trace-1", "1.25"],
        [
            "# export truncated after 1 rows; refine filters to export a complete bounded page"
        ],
    ]
    list_request = listing.call_args.args[0]
    assert listing.call_args.kwargs == {
        "bounded_export": True,
        "read_deadline": ANY,
    }
    assert (
        voice_detection.call_args.kwargs["read_deadline"]
        is listing.call_args.kwargs["read_deadline"]
    )
    assert list_request is request


def test_trace_export_keeps_requested_attribute_header_when_page_has_no_value():
    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "attribute_keys": ["prompt_slug"],
    }
    page = SimpleNamespace(
        status_code=status.HTTP_200_OK,
        data={
            "result": {
                "table": [{"trace_id": "trace-1"}],
                "metadata": {"has_more": False, "query_complete": True},
            }
        },
    )

    with (
        patch("tracer.views.trace._project_queryset_for_request") as projects,
        patch("tracer.views.trace._has_voice_conversation_roots", return_value=False),
        patch.object(TraceView, "list_traces_of_session", return_value=page),
    ):
        projects.return_value.filter.return_value.first.return_value = SimpleNamespace(
            name="Observe"
        )
        view = TraceView()
        response = view.get_trace_export_data.__wrapped__(view, request)

    assert _rows(response) == [["trace_id", "prompt_slug"], ["trace-1", ""]]


def test_voice_trace_export_preserves_legacy_schema_on_one_bounded_page():
    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "attribute_keys": ["prompt_slug", "missing.attribute"],
    }
    project = SimpleNamespace(name="Voice")
    page = SimpleNamespace(
        status_code=status.HTTP_200_OK,
        data={
            "count": 1,
            "count_is_lower_bound": True,
            "has_more": True,
            "query_complete": False,
            "results": [
                {
                    "id": "trace-1",
                    "call_id": "call-1",
                    "phone_number": "+15551234567",
                    "call_type": "inbound",
                    "status": "completed",
                    "started_at": "2026-08-11T12:00:00Z",
                    "ended_at": "2026-08-11T12:01:00Z",
                    "duration_seconds": 60,
                    "recording": {
                        "mono": {"combined_url": "https://example.test/mono.wav"},
                        "stereo_url": "https://example.test/stereo.wav",
                    },
                    "call_summary": "Resolved",
                    "overall_score": 0.9,
                    "response_time_ms": 250,
                    "cost_cents": 3.5,
                    "ended_reason": "customer-ended-call",
                    "transcript": [
                        {"role": "assistant", "content": "Hello"},
                        {"role": "user", "content": "Hi"},
                    ],
                    "prompt_slug": "agent_5_scores_narrative",
                    "eval_outputs": {
                        "eval-b": {
                            "name": "Quality",
                            "output": 88,
                            "reason": "Strong answer",
                        },
                        "eval-a": {
                            "name": "Accuracy",
                            "output": True,
                            "reason": "Grounded",
                        },
                    },
                }
            ],
            "_export_eval_names": ["Coverage", "Quality"],
        },
    )

    with (
        patch("tracer.views.trace._project_queryset_for_request") as projects,
        patch("tracer.views.trace._has_voice_conversation_roots", return_value=True),
        patch.object(TraceView, "list_voice_calls", return_value=page) as listing,
    ):
        projects.return_value.filter.return_value.first.return_value = project
        view = TraceView()
        response = view.get_trace_export_data.__wrapped__(view, request)

    assert response.status_code == status.HTTP_200_OK
    assert (
        response["Content-Disposition"]
        == 'attachment; filename="Voice_voice_calls.csv"'
    )
    rows = _rows(response)
    assert rows[0] == [
        "ID",
        "Call ID",
        "Phone Number",
        "Call Type",
        "Status",
        "Started At",
        "Ended At",
        "Duration (s)",
        "Recording URL",
        "Stereo Recording URL",
        "Call Summary",
        "Overall Score",
        "Response Time (ms)",
        "Cost (cents)",
        "Ended Reason",
        "Transcript",
        "Accuracy",
        "Accuracy_reason",
        "Coverage",
        "Coverage_reason",
        "Quality",
        "Quality_reason",
        "prompt_slug",
        "missing.attribute",
    ]
    assert rows[1] == [
        "trace-1",
        "call-1",
        "'+15551234567",
        "inbound",
        "completed",
        "2026-08-11T12:00:00Z",
        "2026-08-11T12:01:00Z",
        "60",
        "https://example.test/mono.wav",
        "https://example.test/stereo.wav",
        "Resolved",
        "0.9",
        "250",
        "3.5",
        "customer-ended-call",
        "assistant: Hello\nuser: Hi",
        "True",
        "Grounded",
        "",
        "",
        "88",
        "Strong answer",
        "agent_5_scores_narrative",
        "",
    ]
    assert rows[-1] == [
        "# export truncated after 1 rows; refine filters to export a complete bounded page; candidate membership or ordering is inexact"
    ]
    listing.assert_called_once_with(
        request,
        bounded_export=True,
        read_deadline=ANY,
    )


def test_simulator_voice_export_skips_the_expensive_modality_probe():
    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "attribute_keys": [],
    }
    page = SimpleNamespace(
        status_code=status.HTTP_200_OK,
        data={
            "count": 1,
            "has_more": True,
            "query_complete": False,
            "results": [{"id": "trace-1"}],
        },
    )

    with (
        patch("tracer.views.trace._project_queryset_for_request") as projects,
        patch(
            "tracer.views.trace._has_configured_voice_agent", return_value=True
        ) as configured_voice,
        patch(
            "tracer.views.trace._has_voice_conversation_roots",
            side_effect=AssertionError("simulator projects need no CH modality probe"),
        ) as voice_detection,
        patch.object(TraceView, "list_voice_calls", return_value=page) as listing,
    ):
        projects.return_value.filter.return_value.first.return_value = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            name="Voice",
            source="simulator",
        )
        view = TraceView()
        response = view.get_trace_export_data.__wrapped__(view, request)

    assert response.status_code == status.HTTP_200_OK
    assert len(_rows(response)) == 3
    assert _rows(response)[-1][0].startswith("# export truncated after 1 rows")
    configured_voice.assert_called_once()
    voice_detection.assert_not_called()
    listing.assert_called_once_with(
        request,
        bounded_export=True,
        read_deadline=ANY,
    )


def test_configured_voice_signal_requires_a_voice_agent_not_source_alone():
    from tracer.views.trace import _has_configured_voice_agent

    project = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        source="simulator",
    )

    with patch(
        "simulate.models.agent_definition.AgentDefinition.objects.filter"
    ) as agents:
        agents.return_value.exists.return_value = False
        assert not _has_configured_voice_agent(project)

    agents.assert_called_once_with(
        deleted=False,
        agent_type="voice",
        observability_provider__deleted=False,
        observability_provider__project_id=project.id,
    )


def test_text_simulator_export_uses_the_legacy_voice_compatibility_probe():
    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "attribute_keys": [],
    }
    page = SimpleNamespace(
        status_code=status.HTTP_200_OK,
        data={
            "result": {
                "table": [{"trace_id": "trace-1"}],
                "metadata": {"has_more": False, "query_complete": True},
            }
        },
    )

    with (
        patch("tracer.views.trace._project_queryset_for_request") as projects,
        patch(
            "tracer.views.trace._has_configured_voice_agent", return_value=False
        ) as configured_voice,
        patch(
            "tracer.views.trace._has_voice_conversation_roots", return_value=False
        ) as voice_detection,
        patch.object(TraceView, "list_traces_of_session", return_value=page),
    ):
        project = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            name="Text simulator",
            source="simulator",
        )
        projects.return_value.filter.return_value.first.return_value = project
        view = TraceView()
        response = view.get_trace_export_data.__wrapped__(view, request)

    assert _rows(response) == [["trace_id"], ["trace-1"]]
    configured_voice.assert_called_once_with(project)
    voice_detection.assert_called_once_with(
        str(project.id),
        read_deadline=ANY,
    )


def test_empty_voice_trace_export_still_has_legacy_headers():
    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "attribute_keys": ["prompt_slug"],
    }
    page = SimpleNamespace(
        status_code=status.HTTP_200_OK,
        data={
            "count": 0,
            # Numbered voice responses intentionally report only a lower-bound
            # count even when the selector proves this page is exhausted. That
            # flag alone must not fabricate an export-truncation marker.
            "count_is_lower_bound": True,
            "has_more": False,
            "query_complete": True,
            "results": [],
        },
    )

    with (
        patch("tracer.views.trace._project_queryset_for_request") as projects,
        patch("tracer.views.trace._has_voice_conversation_roots", return_value=True),
        patch.object(TraceView, "list_voice_calls", return_value=page),
    ):
        projects.return_value.filter.return_value.first.return_value = SimpleNamespace(
            name="Voice"
        )
        view = TraceView()
        response = view.get_trace_export_data.__wrapped__(view, request)

    assert _rows(response) == [
        [
            "ID",
            "Call ID",
            "Phone Number",
            "Call Type",
            "Status",
            "Started At",
            "Ended At",
            "Duration (s)",
            "Recording URL",
            "Stereo Recording URL",
            "Call Summary",
            "Overall Score",
            "Response Time (ms)",
            "Cost (cents)",
            "Ended Reason",
            "Transcript",
            "prompt_slug",
        ]
    ]


def test_trace_export_surfaces_voice_detection_read_failure_as_retryable():
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "attribute_keys": [],
    }

    with (
        patch("tracer.views.trace._project_queryset_for_request") as projects,
        patch(
            "tracer.views.trace._has_voice_conversation_roots",
            side_effect=ReadDeadlineExceeded("bounded detection expired"),
        ),
    ):
        projects.return_value.filter.return_value.first.return_value = SimpleNamespace(
            name="Observe"
        )
        view = TraceView()
        response = view.get_trace_export_data.__wrapped__(view, request)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.data["result"] == (
        "Trace export data is temporarily unavailable. Please retry."
    )


def test_voice_modality_detection_is_one_tightly_bounded_select():
    from tracer.views.trace import _has_voice_conversation_roots

    deadline = MagicMock()
    deadline.remaining_ms.return_value = 1_234
    analytics = MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(data=[{"present": 1}])

    with patch("tracer.views.trace.V2AnalyticsQueryService", return_value=analytics):
        assert _has_voice_conversation_roots(
            "00000000-0000-0000-0000-000000000001",
            read_deadline=deadline,
        )

    deadline.remaining_ms.assert_called_once_with(1_500)
    query, params = analytics.execute_ch_query.call_args.args
    assert query.startswith("SELECT 1 AS present FROM spans FINAL")
    assert query.endswith("LIMIT 1")
    assert params == {"project_id": "00000000-0000-0000-0000-000000000001"}
    kwargs = analytics.execute_ch_query.call_args.kwargs
    assert kwargs["timeout_ms"] == 1_234
    assert kwargs["settings"]["max_result_rows"] == 1
    assert kwargs["settings"]["max_bytes_to_read"] == 512 * 1024 * 1024


def test_bounded_csv_cells_are_stable_and_formula_safe():
    from tracer.utils.bounded_csv import bounded_page_csv_response

    response = bounded_page_csv_response(
        rows=[
            {
                "payload": {"z": 2, "a": [1, True]},
                "created_at": datetime(2026, 8, 11, 12, 30, tzinfo=UTC),
                "customer": "=SUM(1,1)",
                "empty": None,
            }
        ],
        filename="safe.csv",
    )

    assert _rows(response) == [
        ["payload", "created_at", "customer", "empty"],
        [
            '{"a":[1,true],"z":2}',
            "2026-08-11T12:30:00+00:00",
            "'=SUM(1,1)",
            "",
        ],
    ]


def test_bounded_csv_headers_are_formula_safe():
    from tracer.utils.bounded_csv import bounded_page_csv_response

    response = bounded_page_csv_response(
        rows=[{"=dangerous-header": "safe"}],
        fieldnames=["=dangerous-header"],
        filename="safe.csv",
    )

    assert _rows(response) == [["'=dangerous-header"], ["safe"]]


def test_trace_list_forces_export_bound_after_request_revalidation():
    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "page_number": 9,
        "page_size": 1,
    }
    project = SimpleNamespace(trace_type="observe")
    sentinel = object()

    with (
        patch("tracer.views.trace._project_queryset_for_request") as projects,
        patch("tracer.views.trace.V2AnalyticsQueryService"),
        patch.object(
            TraceView,
            "_list_traces_of_session_clickhouse",
            return_value=sentinel,
        ) as internal_list,
    ):
        projects.return_value.filter.return_value.first.return_value = project
        response = TraceView.list_traces_of_session.__wrapped__(
            TraceView(), request, bounded_export=True
        )

    assert response is sentinel
    internal_data = internal_list.call_args.args[2]
    assert internal_data["page_number"] == 0
    assert internal_data["page_size"] == 100
    assert internal_data["cursor_mode"] is False


def test_voice_list_forces_export_bound_and_retains_csv_fields():
    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "page": 9,
        "page_size": 1,
        "cursor_mode": True,
        "allow_sampled": False,
    }
    view = TraceView()
    view.request = request
    sentinel = object()

    with (
        patch("tracer.views.trace.Project.objects.get"),
        patch("tracer.views.trace.V2AnalyticsQueryService"),
        patch.object(
            TraceView,
            "_list_voice_calls_clickhouse",
            return_value=sentinel,
        ) as internal_list,
    ):
        response = TraceView.list_voice_calls.__wrapped__(
            view, request, bounded_export=True
        )

    assert response is sentinel
    internal_data = internal_list.call_args.args[2]
    assert internal_data["page"] == 1
    assert internal_data["page_size"] == 100
    assert internal_data["cursor_mode"] is False
    assert internal_data["allow_sampled"] is True
    assert internal_list.call_args.kwargs == {
        "include_export_fields": True,
        "read_deadline": None,
    }


def test_span_list_forces_cursor_export_bound_after_request_revalidation():
    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "page_number": 9,
        "page_size": 1,
        "cursor_mode": False,
    }
    view = ObservationSpanView()
    view.request = request
    sentinel = object()

    with (
        patch("tracer.views.observation_span.Project.objects.get"),
        patch("tracer.views.observation_span.V2AnalyticsQueryService"),
        patch.object(
            ObservationSpanView,
            "_list_spans_clickhouse",
            return_value=sentinel,
        ) as internal_list,
    ):
        response = ObservationSpanView.list_spans_observe.__wrapped__(
            view, request, bounded_export=True
        )

    assert response is sentinel
    internal_data = internal_list.call_args.args[2]
    assert internal_data["page_number"] == 0
    assert internal_data["page_size"] == 20
    assert internal_data["cursor_mode"] is True


def test_session_list_forces_cursor_export_bound_after_request_revalidation():
    request = _request({})
    request.validated_query_data = {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "filters": [],
        "page_number": 9,
        "page_size": 1,
        "cursor_mode": False,
    }
    view = TraceSessionView()
    view.request = request
    project = SimpleNamespace(source="observe")
    sentinel = object()

    with (
        patch("tracer.views.trace_session._project_queryset_for_request") as projects,
        patch("tracer.views.trace_session.V2AnalyticsQueryService"),
        patch.object(
            TraceSessionView,
            "_build_bookmark_filter",
            return_value=None,
        ),
        patch.object(
            TraceSessionView,
            "_list_sessions_clickhouse",
            return_value=sentinel,
        ) as internal_list,
    ):
        projects.return_value.get.return_value = project
        response = TraceSessionView.list_sessions.__wrapped__(
            view, request, bounded_export=True
        )

    assert response is sentinel
    internal_data = internal_list.call_args.args[4]
    assert internal_data["page_number"] == 0
    assert internal_data["page_size"] == 20
    assert internal_data["cursor_mode"] is True


def test_span_export_propagates_list_failure_before_starting_csv():
    request = _request({"project_id": "00000000-0000-0000-0000-000000000001"})
    failure = SimpleNamespace(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    project = SimpleNamespace(name="Observe")
    with (
        patch("tracer.views.observation_span.Project.objects.filter") as projects,
        patch.object(
            ObservationSpanView, "list_spans_observe", return_value=failure
        ) as listing,
    ):
        projects.return_value.first.return_value = project
        response = ObservationSpanView().get_spans_export_data(request)

    assert response is failure
    assert listing.call_args.kwargs == {"bounded_export": True}


def test_span_export_preserves_partial_hydrated_rows_and_marks_truncation():
    request = _request({"project_id": "00000000-0000-0000-0000-000000000001"})
    project = SimpleNamespace(name="Observe")
    page = SimpleNamespace(
        status_code=status.HTTP_200_OK,
        data={
            "result": {
                "table": [
                    {
                        "span_id": "span-1",
                        "input": {"prompt": "classified"},
                        "output": {"answer": "hydrated"},
                    }
                ],
                "metadata": {
                    "has_more": True,
                    "query_complete": True,
                    "total_rows_is_lower_bound": True,
                },
            }
        },
    )

    with (
        patch("tracer.views.observation_span.Project.objects.filter") as projects,
        patch.object(
            ObservationSpanView, "list_spans_observe", return_value=page
        ) as listing,
    ):
        projects.return_value.first.return_value = project
        response = ObservationSpanView().get_spans_export_data(request)

    assert response.status_code == status.HTTP_200_OK
    assert _rows(response) == [
        ["span_id", "input", "output"],
        [
            "span-1",
            '{"prompt":"classified"}',
            '{"answer":"hydrated"}',
        ],
        [
            "# export truncated after 1 rows; refine filters to export a complete bounded page"
        ],
    ]
    assert listing.call_args.kwargs == {"bounded_export": True}


def test_session_export_marks_lower_bound_page_in_band():
    request = _request({"project_id": "00000000-0000-0000-0000-000000000001"})
    project = SimpleNamespace(name="Observe")
    page = SimpleNamespace(
        status_code=status.HTTP_200_OK,
        data={
            "result": {
                "table": [{"session_id": "session-1"}],
                "metadata": {"total_rows_is_lower_bound": True},
            }
        },
    )

    with (
        patch.object(TraceSessionView, "list_sessions", return_value=page) as listing,
        patch("tracer.views.trace_session._project_queryset_for_request") as projects,
    ):
        projects.return_value.get.return_value = project
        response = TraceSessionView().get_trace_session_export_data(request)

    assert response.status_code == status.HTTP_200_OK
    assert _rows(response)[-1][0].startswith("# export truncated after 1 rows")
    assert listing.call_args.kwargs == {"bounded_export": True}


def test_session_export_marks_inexact_cursor_partial_page_in_band():
    request = _request({"project_id": "00000000-0000-0000-0000-000000000001"})
    project = SimpleNamespace(name="Observe")
    page = SimpleNamespace(
        status_code=status.HTTP_200_OK,
        data={
            "result": {
                "table": [
                    {
                        "session_id": "session-1",
                        "first_message": "classified",
                        "last_message": "hydrated",
                    }
                ],
                "metadata": {
                    "total_rows_exact": 21,
                    "total_rows_is_lower_bound": False,
                    "has_more": True,
                    "next_cursor": "signed-continuation",
                    "query_complete": True,
                    "query_exact": False,
                    "query_provenance": "spans_per_session_candidate",
                    "ordering_exact": False,
                },
            }
        },
    )

    with (
        patch.object(TraceSessionView, "list_sessions", return_value=page) as listing,
        patch("tracer.views.trace_session._project_queryset_for_request") as projects,
    ):
        projects.return_value.get.return_value = project
        response = TraceSessionView().get_trace_session_export_data(request)

    assert response.status_code == status.HTTP_200_OK
    assert _rows(response) == [
        ["session_id", "first_message", "last_message"],
        ["session-1", "classified", "hydrated"],
        [
            "# export truncated after 1 rows; refine filters to export a complete bounded page; candidate membership or ordering is inexact"
        ],
    ]
    assert listing.call_args.kwargs == {"bounded_export": True}


def test_bounded_csv_marks_inexact_candidate_order_without_truncation():
    from tracer.utils.bounded_csv import bounded_page_csv_response

    response = bounded_page_csv_response(
        rows=[{"session_id": "session-1"}],
        filename="sessions.csv",
        metadata={
            "has_more": False,
            "query_complete": True,
            "query_exact": True,
            "ordering_exact": False,
        },
    )

    assert _rows(response)[-1] == [
        "# export candidate membership or ordering is inexact; results are not an exact ordered population"
    ]
