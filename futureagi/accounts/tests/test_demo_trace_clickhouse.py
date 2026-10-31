"""Demo telemetry must reach the store used by CH25 trace/session views."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from temporalio.common import WorkflowIDConflictPolicy

from accounts.user_onboard import _write_demo_trace_to_clickhouse
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession


@pytest.fixture
def demo_trace():
    now = timezone.now()
    project_id = uuid.uuid4()
    session = TraceSession(
        project_id=project_id, name="Demo session", created_at=now, updated_at=now
    )
    trace = Trace(
        project_id=project_id,
        session=session,
        name="SQL Agent Trace",
        created_at=now,
        updated_at=now,
    )
    span = ObservationSpan(
        id="0123456789abcdef",
        project_id=project_id,
        trace=trace,
        org_id=uuid.uuid4(),
        name="SQL Agent Executor",
        observation_type="agent",
        start_time=now,
        end_time=now,
        created_at=now,
        updated_at=now,
        input={"question": "sales"},
        output={"answer": "42"},
        span_attributes={"demo": True, "tokens": 42, "model": "demo-model"},
    )
    return trace, [span]


def test_publishes_demo_trace_session_and_spans_with_matching_ids(
    demo_trace, monkeypatch
):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "true")
    from tracer.services.clickhouse.v2.adapter import CH_INSERT_COLUMNS

    client = MagicMock()
    client.query.return_value.result_rows = []
    client.query.return_value.named_results.return_value = [
        {"name": name, "type": "String"} for name in [*CH_INSERT_COLUMNS, "trace_name"]
    ]
    with patch("clickhouse_connect.get_client") as connect:
        connect.return_value.__enter__.return_value = client
        _write_demo_trace_to_clickhouse(*demo_trace)

    writes = {
        call.args[0]: [
            dict(zip(call.kwargs["column_names"], row, strict=True))
            for row in call.args[1]
        ]
        for call in client.insert.call_args_list
    }
    trace, (span,) = demo_trace
    assert set(writes) == {"traces", "trace_sessions", "spans"}
    assert writes["traces"][0]["id"] == str(trace.id)
    assert writes["traces"][0]["session_id"] == str(trace.session_id)
    assert writes["trace_sessions"][0]["trace_session_id"] == str(trace.session_id)
    written_span = writes["spans"][0]
    assert written_span["id"] == span.id
    assert written_span["trace_id"] == str(trace.id)
    assert written_span["trace_session_id"] == str(trace.session_id)
    assert written_span["project_id"] == str(trace.project_id)
    assert written_span["trace_name"] == trace.name
    assert client.insert.call_args.kwargs["settings"] == {
        "insert_allow_materialized_columns": 1,
        "async_insert": 0,
    }
    assert written_span["input"] == '{"question": "sales"}'
    assert written_span["output"] == '{"answer": "42"}'
    assert written_span["attrs_bool"] == {"demo": 1}
    assert written_span["attrs_number"] == {"tokens": 42.0}
    assert written_span["attrs_string"] == {"model": "demo-model"}


def test_legacy_deployment_keeps_cdc_path(demo_trace, monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")
    with patch("clickhouse_connect.get_client") as connect:
        _write_demo_trace_to_clickhouse(*demo_trace)
    connect.assert_not_called()


def test_clickhouse_failure_is_not_reported_as_a_success(demo_trace, monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "true")
    with patch(
        "clickhouse_connect.get_client", side_effect=RuntimeError("CH unavailable")
    ):
        with pytest.raises(RuntimeError, match="CH unavailable"):
            _write_demo_trace_to_clickhouse(*demo_trace)


def test_publication_dispatch_is_gated_and_uses_a_stable_trace_id(monkeypatch):
    from accounts.user_onboard import _enqueue_demo_trace_publication

    with patch("tfc.temporal.drop_in.start_activity") as start:
        monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")
        _enqueue_demo_trace_publication("trace-123")
        start.assert_not_called()
        monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "true")
        _enqueue_demo_trace_publication("trace-123")
        start.assert_called_once_with(
            "publish_demo_trace_activity",
            args=("trace-123",),
            queue="default",
            task_id="publish-demo-trace-trace-123",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            dispatch_timeout_seconds=2,
        )
