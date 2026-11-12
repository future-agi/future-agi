"""Observe evals on a span, a trace or a session while a deployment pauses
custom code evaluations (CUSTOM_CODE_EVALS_PAUSED).

A customer's code template is refused before the run's cost is logged: nothing
is charged, nothing reaches the code executor, and the stored result carries
the fixed sentence. A built-in template is still charged and runs, and with the
setting off a customer's template is charged and runs as before. The executor
client (urllib in fi_utils.sandbox) and the cost call are what the tests watch.
"""

from __future__ import annotations

import importlib
import json

import pytest
from django.test import override_settings

# Breaks the tracer.utils.eval <-> model_hub.tasks import cycle, as in test_eval_jev_errors.py.
import model_hub.tasks  # noqa: F401
from agentic_eval.core_evals.fi_utils import sandbox
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan

SENTENCE = "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
BUILTIN_CODE = (
    "def evaluate(output, expected):\n    return 1.0 if output == expected else 0.0\n"
)
CUSTOMER_CODE = "def evaluate(output, expected):\n    return 0.5\n"
RUN_PARAMS = {"output": "4", "expected": "4"}


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def executor(monkeypatch):
    """Every request the executor client sends, answered with a score."""
    sent = []

    def urlopen(request, timeout=None):
        sent.append(json.loads(request.data))
        answer = {"status": "success", "data": {"result": 1.0, "reason": "scored"}}
        return _Response(json.dumps(answer).encode())

    monkeypatch.setattr(sandbox.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sandbox, "CODE_EXECUTOR_URL", "http://code-executor.test:8060")
    return sent


@pytest.fixture(autouse=True)
def fresh_system_code(monkeypatch):
    pause = importlib.import_module(
        "agentic_eval.core_evals.fi_utils.custom_code_pause"
    )
    monkeypatch.setattr(pause, "_system_code_cache", None)


@pytest.fixture
def charges(monkeypatch, stub_cost_log):
    """Every call that logs and deducts an eval run's cost, answered as allowed."""
    calls = []

    def _record(**kwargs):
        calls.append(kwargs)
        return stub_cost_log(**kwargs)

    monkeypatch.setattr(
        "tracer.utils.eval.log_and_deduct_cost_for_api_request", _record
    )
    return calls


@pytest.fixture
def observation_span(db, project, trace):
    """The span the eval reads, from Postgres only: nothing here needs it in
    ClickHouse."""
    return ObservationSpan.objects.create(
        id="span_custom_code_pause_observe",
        project=project,
        trace=trace,
        name="Test Span",
        observation_type="llm",
        input={"messages": [{"role": "user", "content": "Hello"}]},
        output={"choices": [{"message": {"content": "Hi there"}}]},
        status="OK",
    )


@pytest.fixture
def targets(observation_span, trace, trace_session):
    """What each path evaluates: a span, a trace anchored on it, a session."""
    return {
        "observation_span": observation_span,
        "trace": trace,
        "trace_session": trace_session,
    }


def customer_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="pause_observe_customer_code",
        owner="user",
        organization=organization,
        workspace=workspace,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": CUSTOMER_CODE},
    )


def builtin_template():
    template = EvalTemplate.no_workspace_objects.create(
        name="pause_observe_builtin_code",
        owner="system",
        organization=None,
        workspace=None,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": BUILTIN_CODE},
    )
    # save() stamps the request's organization and workspace on a row saved while a
    # workspace context is set; a built-in has neither, so clear them without save().
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
        organization=None, workspace=None
    )
    template.refresh_from_db()
    return template


def bind(project, template):
    return CustomEvalConfig.objects.create(
        name=f"{template.name} on observe",
        project=project,
        eval_template=template,
        config={},
        mapping={"output": "output", "expected": "expected"},
        filters={},
    )


def run_span(custom_eval_config, observation_span, **_):
    from tracer.utils.eval import OBSERVE, _execute_evaluation

    _execute_evaluation(
        observation_span_id=observation_span.id,
        custom_eval_config_id=custom_eval_config.id,
        eval_task_id=None,
        type=OBSERVE,
        run_params=dict(RUN_PARAMS),
        observation_span=observation_span,
        project_id=observation_span.project_id,
    )


def run_trace(custom_eval_config, observation_span, trace, **_):
    from tracer.utils.eval import _execute_evaluation_for_trace

    _execute_evaluation_for_trace(
        trace=trace,
        anchor_span=observation_span,
        custom_eval_config=custom_eval_config,
        eval_task_id=None,
        run_params=dict(RUN_PARAMS),
    )


def run_session(custom_eval_config, trace_session, **_):
    from tracer.utils.eval import _execute_evaluation_for_session

    _execute_evaluation_for_session(
        trace_session=trace_session,
        custom_eval_config=custom_eval_config,
        eval_task_id=None,
        run_params=dict(RUN_PARAMS),
    )


def stored_result(custom_eval_config):
    return (
        EvalLogger.objects.filter(custom_eval_config=custom_eval_config)
        .order_by("-created_at")
        .first()
    )


def assert_refused_and_not_charged(custom_eval_config, executor, charges):
    assert executor == []
    assert charges == []
    stored = stored_result(custom_eval_config)
    assert stored is not None
    assert stored.error is True
    assert stored.results_explanation == {"reason": SENTENCE}


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_span_eval_refuses_customer_code_before_charging(
    executor, charges, organization, workspace, project, targets
):
    config = bind(project, customer_template(organization, workspace))
    run_span(config, **targets)
    assert_refused_and_not_charged(config, executor, charges)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_trace_eval_refuses_customer_code_before_charging(
    executor, charges, organization, workspace, project, targets
):
    config = bind(project, customer_template(organization, workspace))
    run_trace(config, **targets)
    assert_refused_and_not_charged(config, executor, charges)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_session_eval_refuses_customer_code_before_charging(
    executor, charges, organization, workspace, project, targets
):
    config = bind(project, customer_template(organization, workspace))
    run_session(config, **targets)
    assert_refused_and_not_charged(config, executor, charges)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
@pytest.mark.parametrize("run", [run_span, run_trace, run_session])
def test_paused_builtin_template_is_still_charged_and_reaches_the_executor(
    run, executor, charges, organization, workspace, project, targets
):
    config = bind(project, builtin_template())
    run(config, **targets)
    assert len(charges) == 1
    assert [request["code"] for request in executor] == [BUILTIN_CODE]
    stored = stored_result(config)
    assert stored is not None
    assert (stored.results_explanation or {}).get("reason") != SENTENCE


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
@pytest.mark.parametrize("run", [run_span, run_trace, run_session])
def test_without_the_setting_customer_code_is_charged_and_runs_as_before(
    run, executor, charges, organization, workspace, project, targets
):
    config = bind(project, customer_template(organization, workspace))
    run(config, **targets)
    assert len(charges) == 1
    assert [request["code"] for request in executor] == [CUSTOMER_CODE]
