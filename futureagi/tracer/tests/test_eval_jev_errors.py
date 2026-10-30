"""A failed Jev run on a span, trace or session records the fixed text for its
typed code: never the bare code, never the key or TypeSafe's response body."""

from __future__ import annotations

import pytest

# Breaks the tracer.utils.eval <-> model_hub.tasks import cycle, as in test_eval_task_runtime.py.
import model_hub.tasks  # noqa: F401
from tracer.models.observation_span import EvalLogger

pytestmark = pytest.mark.requires_ee

RUN_PARAMS = {"input": "hello", "output": "world"}
KEY_INVALID = (
    "TypeSafe rejected the API key saved under Settings → AI Providers. "
    "Update the key and try again."
)


@pytest.fixture
def observation_span(db, project, trace):
    """The span the eval reads, from Postgres (the default read source) only:
    nothing here needs it in ClickHouse."""
    from tracer.models.observation_span import ObservationSpan

    return ObservationSpan.objects.create(
        id="span_jev_errors",
        project=project,
        trace=trace,
        name="Test Span",
        observation_type="llm",
        input={"messages": [{"role": "user", "content": "Hello"}]},
        output={"choices": [{"message": {"content": "Hi there"}}]},
        status="OK",
    )


@pytest.fixture
def rejected_key(monkeypatch):
    from ee.jev.client import JevGatewayError

    def _reject(_request):
        error = JevGatewayError("JEV_KEY_INVALID")
        error.args = ("org-key-CANARY upstream body",)
        raise error

    monkeypatch.setattr("evaluations.engine.run_eval", _reject, raising=False)
    monkeypatch.setattr("evaluations.engine.runner.run_eval", _reject, raising=False)


def _run_span(observation_span, custom_eval_config, **_):
    from tracer.utils.eval import OBSERVE, _execute_evaluation

    _execute_evaluation(
        observation_span_id=observation_span.id,
        custom_eval_config_id=custom_eval_config.id,
        eval_task_id=None,
        type=OBSERVE,
        run_params=dict(RUN_PARAMS),
    )


def _run_trace(observation_span, custom_eval_config, trace, **_):
    from tracer.utils.eval import _execute_evaluation_for_trace

    _execute_evaluation_for_trace(
        trace=trace,
        anchor_span=observation_span,
        custom_eval_config=custom_eval_config,
        eval_task_id=None,
        run_params=dict(RUN_PARAMS),
    )


def _run_session(custom_eval_config, trace_session, **_):
    from tracer.utils.eval import _execute_evaluation_for_session

    _execute_evaluation_for_session(
        trace_session=trace_session,
        custom_eval_config=custom_eval_config,
        eval_task_id=None,
        run_params=dict(RUN_PARAMS),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("run", [_run_span, _run_trace, _run_session])
def test_a_failed_jev_run_records_its_message(
    run,
    organization,
    workspace,
    project,
    trace,
    observation_span,
    trace_session,
    custom_eval_config,
    stub_cost_log,
    rejected_key,
):
    run(
        observation_span=observation_span,
        custom_eval_config=custom_eval_config,
        trace=trace,
        trace_session=trace_session,
    )
    eval_log = (
        EvalLogger.objects.filter(custom_eval_config=custom_eval_config)
        .order_by("-created_at")
        .first()
    )
    assert eval_log is not None
    assert eval_log.error is True
    assert eval_log.error_message == f"Error during evaluation: {KEY_INVALID}"
    assert eval_log.output_metadata["error"] == KEY_INVALID
    assert "CANARY" not in str(eval_log.eval_explanation)


@pytest.mark.django_db
@pytest.mark.parametrize("run", [_run_span, _run_trace, _run_session])
def test_a_jev_template_is_logged_as_a_jev_run(
    run,
    monkeypatch,
    organization,
    workspace,
    project,
    trace,
    observation_span,
    trace_session,
    eval_template,
    custom_eval_config,
    stub_cost_log,
    rejected_key,
):
    """The config names no model and the saved template runs Jev: the legacy
    log, and so the per-call rate limit, use jev_evaluator, not Turing Large."""
    eval_template.config = {**eval_template.config, "model": "jev-latest"}
    eval_template.save(update_fields=["config"])
    seen = []

    def _record(**kwargs):
        seen.append(kwargs["api_call_type"])
        return stub_cost_log(**kwargs)

    monkeypatch.setattr(
        "tracer.utils.eval.log_and_deduct_cost_for_api_request", _record
    )
    run(
        observation_span=observation_span,
        custom_eval_config=custom_eval_config,
        trace=trace,
        trace_session=trace_session,
    )
    assert seen == ["jev_evaluator"]
