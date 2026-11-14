"""The trace evaluator reads real stored calls, not caller-supplied summaries."""

import json
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from agentic_eval.metrics.tool_call_loop_repetition import (
    evaluate_tool_call_loop_repetition,
)
from model_hub.management.commands.seed_system_evals import (
    _yaml_to_template_fields,
    load_yaml_evals,
)
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.eval_loader import (
    EvalTelemetryReadError,
    eval_read_source,
)
from tracer.services.tool_call_loop_input import trace_tool_call_input
from tracer.tests._ch_seed import seed_ch_spans, seed_ch_trace


@pytest.fixture
def loop_template(db):
    definition = next(
        e for e in load_yaml_evals() if e["name"] == "tool_call_loop_repetition"
    )
    return EvalTemplate.objects.create(**_yaml_to_template_fields(definition))


def record(project, source, *, progress=False, count=3):
    trace = Trace(id=uuid.uuid4(), project=project, name="synthetic tool trace")
    if source == "postgres":
        trace.save()
    else:
        seed_ch_trace(trace)
    start = timezone.now() - timedelta(minutes=1)
    spans = [
        ObservationSpan(
            id=f"loop-{uuid.uuid4().hex[:16]}",
            project=project,
            trace=trace,
            name="tool execution",
            observation_type="tool",
            start_time=start + timedelta(seconds=i),
            end_time=start + timedelta(seconds=i, milliseconds=100),
            input=json.dumps({"id": "job-1"}),
            output={"status": i if progress else "waiting"},
            span_attributes={"tool.name": "poll"},
            status="OK",
        )
        for i in range(count)
    ]
    # Deliberately write in reverse arrival order; evaluation uses event time.
    if source == "postgres":
        for span in reversed(spans):
            span.save()
    else:
        seed_ch_spans(reversed(spans))
    return trace, spans


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["postgres", "clickhouse"])
@pytest.mark.parametrize("progress", [False, True])
def test_stored_tool_calls_score_with_progress(
    observe_project, loop_template, source, progress
):
    trace, _ = record(observe_project, source, progress=progress)
    with eval_read_source(source):
        inputs = trace_tool_call_input(trace, loop_template, {})
    assert [call["name"] for call in inputs["output"]] == ["poll"] * 3
    assert inputs["output"][0]["arguments"] == {"id": "job-1"}
    result = evaluate_tool_call_loop_repetition(inputs["output"])
    assert result["passed"] is progress
    assert result["score"] == (1.0 if progress else 0.2333)


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["postgres", "clickhouse"])
def test_stored_window_retains_latest_in_chronological_order(
    observe_project, loop_template, source
):
    trace, spans = record(observe_project, source, progress=True, count=5)
    # A same-trace-ID span in another project must not enter the window.
    from tracer.models.project import Project

    other = Project.objects.create(
        name="other synthetic project",
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        model_type=observe_project.model_type,
        trace_type="observe",
    )
    noise = ObservationSpan(
        id=f"noise-{uuid.uuid4().hex[:16]}",
        project=other,
        trace=trace,
        name="noise",
        observation_type="tool",
        start_time=timezone.now(),
        end_time=timezone.now(),
        input={},
        output="noise",
    )
    if source == "postgres":
        noise.save()
    else:
        seed_ch_spans([noise])
    with eval_read_source(source):
        inputs = trace_tool_call_input(
            trace, loop_template, {"params": {"window_size": 3}}
        )
    assert [c["result"]["status"] for c in inputs["output"]] == [2, 3, 4]


@pytest.mark.django_db
def test_preview_reads_ch_only_calls(auth_client, observe_project, loop_template):
    trace, _ = record(observe_project, "clickhouse")
    assert not Trace.objects.filter(id=trace.id).exists()
    assert not ObservationSpan.objects.filter(trace_id=trace.id).exists()
    response = auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(loop_template.id),
            "trace_id": str(trace.id),
            "model": "",
            "config": {"params": {"window_size": 8}, "mapping": {"output": "[]"}},
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    rendered = json.dumps(response.data)
    assert "exact_repeat" in rendered, response.data
    assert "job-1" in rendered
    assert "window_size=8" in rendered


@pytest.mark.django_db
def test_scheduled_run_reads_ch_only_calls(observe_project, loop_template, monkeypatch):
    from tracer.utils.eval import _execute_evaluation_for_trace

    trace, spans = record(observe_project, "clickhouse")
    config = CustomEvalConfig.objects.create(
        project=observe_project,
        eval_template=loop_template,
        name="loop check",
        config={"params": {"window_size": 8}},
        mapping={},
        model="",
    )
    monkeypatch.setattr("tracer.utils.eval.log_and_deduct_cost_for_api_request", None)
    with eval_read_source("clickhouse"):
        _execute_evaluation_for_trace(
            trace=trace,
            anchor_span=spans[0],
            custom_eval_config=config,
            eval_task_id=None,
            run_params={"output": "[]"},
        )
    result = EvalLogger.objects.get(custom_eval_config=config)
    assert "exact_repeat" in result.eval_explanation
    assert "job-1" in result.eval_explanation
    assert "window_size=8" in result.eval_explanation
    assert result.output_float == pytest.approx(0.2333)


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["input", "output", "end_time"])
def test_incomplete_recording_is_not_scored_as_healthy(
    observe_project, loop_template, missing
):
    trace, spans = record(observe_project, "postgres")
    setattr(spans[1], missing, None)
    spans[1].save()
    with (
        eval_read_source("postgres"),
        pytest.raises(ValueError, match="recording is incomplete|requires recorded"),
    ):
        trace_tool_call_input(trace, loop_template, {})


@pytest.mark.django_db
def test_ch_read_failure_never_scores_pg_fallback(
    observe_project, loop_template, monkeypatch
):
    trace, _ = record(observe_project, "postgres", progress=True)

    def unavailable():
        raise TimeoutError("local fixture read unavailable")

    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", unavailable)
    with (
        eval_read_source("clickhouse"),
        pytest.raises(EvalTelemetryReadError, match="could not be loaded"),
    ):
        trace_tool_call_input(trace, loop_template, {})


@pytest.mark.django_db
def test_preview_does_not_use_caller_data_when_trace_is_unavailable(
    auth_client, loop_template
):
    response = auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(loop_template.id),
            "trace_id": str(uuid.uuid4()),
            "model": "",
            "mapping": {"output": "[]"},
            "trace_context": {"id": "caller supplied", "spans": []},
        },
        format="json",
    )
    assert response.status_code == 400
    assert "could not be loaded" in json.dumps(response.data)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("error_type", "log_method", "event"),
    [
        (RuntimeError, "exception", "tool_call_loop_preview_failed"),
        (
            EvalTelemetryReadError,
            "warning",
            "tool_call_loop_preview_telemetry_unavailable",
        ),
        (Trace.DoesNotExist, "warning", "tool_call_loop_preview_telemetry_unavailable"),
    ],
)
def test_preview_logs_read_failure_without_exposing_details(
    auth_client, loop_template, monkeypatch, error_type, log_method, event
):
    logger = Mock()
    monkeypatch.setattr("model_hub.views.separate_evals.logger", logger)

    def unavailable(*args, **kwargs):
        raise error_type("synthetic internal failure details")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.eval_loader.get_trace", unavailable
    )
    response = auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(loop_template.id),
            "trace_id": str(uuid.uuid4()),
            "model": "",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["message"] == (
        "Tool-call telemetry could not be loaded. Please retry."
    )
    assert "synthetic internal failure details" not in json.dumps(response.data)
    getattr(logger, log_method).assert_called_once_with(event)
    if log_method == "warning":
        logger.exception.assert_not_called()


@pytest.mark.django_db
def test_preview_refuses_a_trace_from_an_unavailable_project(
    auth_client, observe_project, loop_template
):
    trace, _ = record(observe_project, "clickhouse")
    observe_project.deleted = True
    observe_project.save()
    response = auth_client.post(
        "/model-hub/eval-playground/",
        {"template_id": str(loop_template.id), "trace_id": str(trace.id), "model": ""},
        format="json",
    )
    assert response.status_code == 400
    assert "selected trace is unavailable" in json.dumps(response.data)


@pytest.mark.django_db
def test_empty_recording_is_not_a_pass(observe_project, loop_template):
    trace = Trace.objects.create(project=observe_project, name="empty trace")
    with (
        eval_read_source("postgres"),
        pytest.raises(ValueError, match="no recorded tool calls"),
    ):
        trace_tool_call_input(trace, loop_template, {})
