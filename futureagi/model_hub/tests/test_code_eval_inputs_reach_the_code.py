"""A code eval's inputs reach its code under their own names.

An eval whose evaluate() takes ``language`` or ``code`` gets the value the
customer sent for it, and the code still runs in its own language (the one
detected from the code), whatever that input says. An eval without such
parameters runs as before. Each surface that runs a code eval is driven here:
dataset cells, the eval playground, Test Evaluation, the SDK evaluate route,
the queued evaluations behind the CI/CD route and traced inline evals, and
run_eval (Observe and online eval tasks).

The executor client (urllib in fi_utils.sandbox) is replaced. Each test reads
the request the backend sends to the code executor, which runs ``code`` in
``language`` and passes ``input_data`` to the code's evaluate()/main().
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from rest_framework import status

from agentic_eval.core_evals.fi_utils import sandbox
from evaluations.engine.runner import EvalRequest, run_eval
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.evaluation import Evaluation, StatusChoices
from model_hub.views.eval_runner import EvaluationRunner
from sdk.utils import evaluations as sdk_evaluations
from tfc.constants.api_calls import APICallStatusChoices
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal.evaluations import activities
from tracer.utils import inline_evals

PY_TAKES_LANGUAGE = (
    "def evaluate(output, language):\n"
    "    return language.lower() in output.lower()\n"
)
JS_TAKES_LANGUAGE = (
    "function evaluate({ output, language }) {\n"
    "  return output.toLowerCase().includes(language.toLowerCase());\n"
    "}\n"
)
PY_TAKES_CODE = (
    "def evaluate(output, code):\n    return output.strip() == code.strip()\n"
)
JS_TAKES_CODE = (
    "function evaluate({ output, code }) {\n"
    "  return output.trim() === code.trim();\n"
    "}\n"
)
PY_PLAIN = "def evaluate(output, expected):\n    return output == expected\n"
JS_PLAIN = (
    "function evaluate({ output, expected }) {\n  return output === expected;\n}\n"
)

# (the eval's code, the language it runs in, the inputs the customer sends).
# Each language input names the other language, so a run that took it as the
# code's language would send the code to the wrong runtime.
EVALS = [
    pytest.param(
        PY_TAKES_LANGUAGE,
        "python",
        {"output": "This answer is written in JavaScript.", "language": "javascript"},
        id="python-eval-takes-language",
    ),
    pytest.param(
        JS_TAKES_LANGUAGE,
        "javascript",
        {"output": "This answer is written in Python.", "language": "python"},
        id="javascript-eval-takes-language",
    ),
    pytest.param(
        PY_TAKES_CODE,
        "python",
        {"output": "SELECT 1;", "code": "SELECT 1;"},
        id="python-eval-takes-code",
    ),
    pytest.param(
        JS_TAKES_CODE,
        "javascript",
        {"output": "SELECT 1;", "code": "SELECT 1;"},
        id="javascript-eval-takes-code",
    ),
    # Without either parameter: runs as before.
    pytest.param(
        PY_PLAIN,
        "python",
        {"output": "4", "expected": "4"},
        id="python-eval-without-them",
    ),
    pytest.param(
        JS_PLAIN,
        "javascript",
        {"output": "4", "expected": "4"},
        id="javascript-eval-without-them",
    ),
]

REASON = "scored by the executor"


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
        answer = {"status": "success", "data": {"result": 1.0, "reason": REASON}}
        return _Response(json.dumps(answer).encode())

    monkeypatch.setattr(sandbox.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sandbox, "CODE_EXECUTOR_URL", "http://code-executor.test:8060")
    return sent


@pytest.fixture
def cost(monkeypatch):
    """The cost log the SDK path writes before the run, the usage check and the
    post-run usage emitter (stubbed when the enterprise package is installed)."""
    try:
        from ee.usage.services import metering
    except ImportError:
        metering = None
    if metering is not None:
        monkeypatch.setattr(
            metering, "check_usage", Mock(return_value=SimpleNamespace(allowed=True))
        )
    try:
        from ee.usage.services import emitter
    except ImportError:
        emitter = None
    if emitter is not None:
        monkeypatch.setattr(emitter, "emit", Mock())

    row = SimpleNamespace(
        status=APICallStatusChoices.PROCESSING.value,
        config="{}",
        log_id=uuid.uuid4(),
        save=Mock(),
    )
    log = Mock(return_value=row)
    monkeypatch.setattr(sdk_evaluations, "log_and_deduct_cost_for_api_request", log)
    return log


def code_eval(organization, workspace, code, inputs):
    """A customer's code eval that declares the inputs it is sent."""
    return EvalTemplate.no_workspace_objects.create(
        name=f"code-eval-inputs-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        config={
            "code": code,
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": list(inputs),
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def assert_sent(executor, code, language, inputs):
    """One request: the eval's own code and language, and the inputs as sent."""
    assert [
        {key: request[key] for key in ("code", "language", "input_data")}
        for request in executor
    ] == [{"code": code, "language": language, "input_data": inputs}]


# ─────────────────────────────────────────────────────────────────────
# Dataset cells (EvaluationRunner._run_evaluation)
# ─────────────────────────────────────────────────────────────────────


def _dataset_row(organization, user, workspace, inputs):
    dataset = Dataset.objects.create(
        name=f"code-eval-inputs-{uuid.uuid4().hex[:8]}",
        organization=organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
        column_config={},
        workspace=workspace,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    mapping = {}
    for key, value in inputs.items():
        column = Column.objects.create(
            dataset=dataset,
            name=f"{key}-column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        Cell.objects.create(
            dataset=dataset,
            row=row,
            column=column,
            value=value,
            status=CellStatus.PASS.value,
        )
        mapping[key] = str(column.id)
    return dataset, row, mapping


@pytest.mark.django_db
@pytest.mark.parametrize("code, language, inputs", EVALS)
def test_a_dataset_cell_gives_the_code_its_inputs(
    code, language, inputs, executor, organization, user, workspace
):
    template = code_eval(organization, workspace, code, inputs)
    dataset, row, mapping = _dataset_row(organization, user, workspace, inputs)
    metric = UserEvalMetric.objects.create(
        name=f"code-eval-inputs-{uuid.uuid4().hex[:8]}",
        dataset=dataset,
        template=template,
        user=user,
        workspace=workspace,
        organization=organization,
        model="turing_large",
        config={"mapping": mapping},
    )
    runner = EvaluationRunner(
        user_eval_metric_id=str(metric.id),
        is_only_eval=True,
        # Skips the constructor's metric loading; the run wires it below.
        format_output=True,
        organization_id=organization.id,
        workspace_id=workspace.id,
    )
    runner.user_eval_metric = metric
    runner.eval_template = template
    runner.dataset = dataset
    # Only read for its id while resolving the mapping.
    runner.column = MagicMock(id=uuid.uuid4())

    eval_result = runner._run_evaluation(row, mapping, {})[0].eval_results[0]

    assert_sent(executor, code, language, inputs)
    assert eval_result["reason"] == REASON
    assert eval_result["metrics"][0]["value"] == 1.0


# ─────────────────────────────────────────────────────────────────────
# The eval playground and Test Evaluation
# ─────────────────────────────────────────────────────────────────────


def _playground(auth_client, template, inputs):
    return auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "",
            "mapping": inputs,
            "config": {"params": {}},
        },
        format="json",
    )


def _test_evaluation(auth_client, template, inputs):
    return auth_client.post(
        "/model-hub/test-evaluation/",
        {
            "name": "code-eval-inputs-test",
            "template_type": "Function",
            "template_id": str(template.id),
            "eval_type_id": "CustomCodeEval",
            "model": "",
            "output_type": "Pass/Fail",
            "required_keys": list(inputs),
            "input_data_types": {key: "text" for key in inputs},
            "config": {
                "mapping": inputs,
                "config": {"code": template.config["code"]},
            },
        },
        format="json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("call", [_playground, _test_evaluation])
@pytest.mark.parametrize("code, language, inputs", EVALS)
def test_the_playground_and_test_evaluation_give_the_code_its_inputs(
    code, language, inputs, call, executor, auth_client, user, workspace
):
    template = code_eval(user.organization, workspace, code, inputs)

    response = call(auth_client, template, inputs)

    assert_sent(executor, code, language, inputs)
    assert response.status_code == status.HTTP_200_OK, response.data
    assert REASON in str(response.data)


# ─────────────────────────────────────────────────────────────────────
# The SDK evaluate route (sync)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("code, language, inputs", EVALS)
def test_the_sdk_route_gives_the_code_its_inputs(
    code, language, inputs, executor, cost, organization, user, workspace
):
    template = code_eval(organization, workspace, code, inputs)

    result = sdk_evaluations._run_standalone_eval(
        eval_template=template,
        inputs=dict(inputs),
        model=None,
        user=user,
        workspace=workspace,
        eval_config={},
        error_localizer_enabled=False,
        trace_eval=False,
        custom_eval_name=None,
        span_id=None,
        organization=organization,
    )

    assert_sent(executor, code, language, inputs)
    assert result["reason"] == REASON


# ─────────────────────────────────────────────────────────────────────
# Queued evaluations: the CI/CD route and traced inline evals
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "trace_data",
    [None, {"span_id": str(uuid.uuid4()), "custom_eval_name": "inline-code-eval"}],
    ids=["ci-cd", "traced-inline"],
)
@pytest.mark.parametrize("code, language, inputs", EVALS)
def test_a_queued_evaluation_gives_the_code_its_inputs(
    code,
    language,
    inputs,
    trace_data,
    executor,
    cost,
    monkeypatch,
    organization,
    user,
    workspace,
):
    template = code_eval(organization, workspace, code, inputs)
    # As the CI/CD route and the SDK's async and traced calls store it.
    evaluation = Evaluation.objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=template,
        model_name=None,
        input_data=dict(inputs),
        eval_config={},
        trace_data=trace_data,
    )
    # Connection cleanup is transport plumbing; keep pytest's DB transaction open.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    monkeypatch.setattr(inline_evals, "trigger_inline_eval", Mock())
    clear_workspace_context()

    activities._run_single_evaluation_sync(str(evaluation.pk))
    evaluation = Evaluation.no_workspace_objects.get(pk=evaluation.pk)

    assert_sent(executor, code, language, inputs)
    assert evaluation.status == StatusChoices.COMPLETED, evaluation.error_message
    assert evaluation.reason == REASON


# ─────────────────────────────────────────────────────────────────────
# run_eval: Observe and online eval tasks
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("code, language, inputs", EVALS)
def test_run_eval_gives_the_code_its_inputs(
    code, language, inputs, executor, organization, workspace
):
    template = code_eval(organization, workspace, code, inputs)

    result = run_eval(
        EvalRequest(
            eval_template=template,
            inputs=dict(inputs),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )
    )

    assert_sent(executor, code, language, inputs)
    assert result.reason == REASON
