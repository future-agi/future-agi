"""A code eval runs in the language its code is written in, on every surface.

Code that parses as Python and defines evaluate() or main() runs as Python.
Otherwise code with a JavaScript sign runs as JavaScript, and so does code
saved with JavaScript chosen (the editor's Language select, or code_language
on the API). The saved language decides only code that shows neither.

Before, the language came from patterns over the raw text and the saved
language was never read, so a JavaScript eval with ``def evaluate(`` in a
comment ran as Python, a Python eval with ``function main(`` in a string ran
as JavaScript, and both failed.

Each surface that builds a code eval is driven here: dataset cells, the eval
playground, the editor's Test with unsaved changes, the SDK evaluate route and
run_eval (Observe and online eval tasks). The executor client (urllib in
fi_utils.sandbox) is replaced; each test reads the request the backend sends
to the code executor, which runs ``code`` in ``language``.
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
from model_hub.views.eval_runner import EvaluationRunner
from sdk.utils import evaluations as sdk_evaluations
from tfc.constants.api_calls import APICallStatusChoices

JS_WITH_A_PYTHON_COMMENT = (
    "// Ported from our Python check: def evaluate(output, expected)\n"
    "const evaluate = ({ output, expected }) => ({\n"
    "  score: output === expected ? 1 : 0,\n"
    '  reason: "ran as JavaScript",\n'
    "});\n"
)
PY_WITH_A_JS_STRING = (
    "def evaluate(output, **kwargs):\n"
    '    return "function main(" in output\n'
)
PY_PLAIN = "def evaluate(output, expected, **kwargs):\n    return output == expected\n"
# JavaScript with no sign the code shows: its only declaration follows a comment
# on the same line.
JS_AFTER_A_COMMENT = (
    "/* exact match */ const evaluate = ({ output, expected }) =>\n"
    "  output === expected;\n"
)
JS_AFTER_A_COMMENT_TAKES_LANGUAGE = (
    "/* names the language */ const evaluate = ({ output, language }) =>\n"
    "  output.toLowerCase().includes(language.toLowerCase());\n"
)

SAME = {"output": "4", "expected": "4"}

# (the eval's code, the language it was saved with, the language it runs in,
# the inputs the customer sends).
EVALS = [
    pytest.param(
        JS_WITH_A_PYTHON_COMMENT,
        "javascript",
        "javascript",
        SAME,
        id="javascript-with-def-evaluate-in-a-comment-saved-javascript",
    ),
    pytest.param(
        JS_WITH_A_PYTHON_COMMENT,
        "python",
        "javascript",
        SAME,
        id="javascript-with-def-evaluate-in-a-comment-saved-python",
    ),
    pytest.param(
        JS_WITH_A_PYTHON_COMMENT,
        None,
        "javascript",
        SAME,
        id="javascript-with-def-evaluate-in-a-comment-nothing-saved",
    ),
    pytest.param(
        PY_WITH_A_JS_STRING,
        "python",
        "python",
        {"output": "function main() { return 1; }"},
        id="python-with-function-main-in-a-string-saved-python",
    ),
    pytest.param(
        PY_PLAIN,
        "javascript",
        "python",
        SAME,
        id="python-saved-javascript",
    ),
    pytest.param(
        JS_AFTER_A_COMMENT,
        "javascript",
        "javascript",
        SAME,
        id="shows-neither-saved-javascript",
    ),
    pytest.param(
        JS_AFTER_A_COMMENT,
        "python",
        "python",
        SAME,
        id="shows-neither-saved-python",
    ),
    # An input named language reaches the code; the saved language is the
    # runner's own.
    pytest.param(
        JS_AFTER_A_COMMENT_TAKES_LANGUAGE,
        "javascript",
        "javascript",
        {"output": "This answer is written in Python.", "language": "python"},
        id="shows-neither-takes-a-language-input-saved-javascript",
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


def code_eval(organization, workspace, code, saved, inputs):
    """A customer's code eval saved with ``saved`` as its language (None: the
    eval has no saved language, as a system eval and its copies)."""
    config = {
        "code": code,
        "output": "Pass/Fail",
        "eval_type_id": "CustomCodeEval",
        "required_keys": list(inputs),
    }
    if saved is not None:
        config["language"] = saved
    return EvalTemplate.no_workspace_objects.create(
        name=f"code-eval-language-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        config=config,
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def assert_sent(executor, code, language, inputs):
    """One request: the eval's own code, the language it runs in, and the
    inputs as sent."""
    assert [
        {key: request[key] for key in ("code", "language", "input_data")}
        for request in executor
    ] == [{"code": code, "language": language, "input_data": inputs}]


# ─────────────────────────────────────────────────────────────────────
# Dataset cells (EvaluationRunner._run_evaluation)
# ─────────────────────────────────────────────────────────────────────


def _dataset_row(organization, user, workspace, inputs):
    dataset = Dataset.objects.create(
        name=f"code-eval-language-{uuid.uuid4().hex[:8]}",
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
@pytest.mark.parametrize("code, saved, language, inputs", EVALS)
def test_a_dataset_cell_runs_the_code_in_its_language(
    code, saved, language, inputs, executor, organization, user, workspace
):
    template = code_eval(organization, workspace, code, saved, inputs)
    dataset, row, mapping = _dataset_row(organization, user, workspace, inputs)
    metric = UserEvalMetric.objects.create(
        name=f"code-eval-language-{uuid.uuid4().hex[:8]}",
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


# ─────────────────────────────────────────────────────────────────────
# The eval playground, and the editor's Test with unsaved changes
# ─────────────────────────────────────────────────────────────────────


def _playground(auth_client, template, inputs, config=None):
    return auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "",
            "mapping": inputs,
            "config": config if config is not None else {"params": {}},
        },
        format="json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("code, saved, language, inputs", EVALS)
def test_the_playground_runs_the_code_in_its_language(
    code, saved, language, inputs, executor, auth_client, user, workspace
):
    template = code_eval(user.organization, workspace, code, saved, inputs)

    response = _playground(auth_client, template, inputs)

    assert_sent(executor, code, language, inputs)
    assert response.status_code == status.HTTP_200_OK, response.data
    assert REASON in str(response.data)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "code, chosen, language",
    [
        pytest.param(
            JS_AFTER_A_COMMENT,
            "javascript",
            "javascript",
            id="shows-neither-javascript-chosen",
        ),
        pytest.param(
            JS_AFTER_A_COMMENT, "python", "python", id="shows-neither-python-chosen"
        ),
        pytest.param(
            JS_WITH_A_PYTHON_COMMENT,
            "python",
            "javascript",
            id="javascript-with-def-evaluate-in-a-comment-python-chosen",
        ),
    ],
)
def test_the_editors_test_runs_unsaved_code_with_the_chosen_language(
    code, chosen, language, executor, auth_client, user, workspace
):
    # Saved as Python code with Python chosen; the editor's Test sends the
    # unsaved code and the language chosen in the editor.
    template = code_eval(user.organization, workspace, PY_PLAIN, "python", SAME)
    unsaved = {"code": code, "code_language": chosen}

    response = _playground(
        auth_client, template, SAME, config={"unsaved_definition": unsaved}
    )

    assert_sent(executor, code, language, SAME)
    assert response.status_code == status.HTTP_200_OK, response.data
    template.refresh_from_db()
    assert (template.config["code"], template.config["language"]) == (
        PY_PLAIN,
        "python",
    )


# ─────────────────────────────────────────────────────────────────────
# The SDK evaluate route (sync)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("code, saved, language, inputs", EVALS)
def test_the_sdk_route_runs_the_code_in_its_language(
    code, saved, language, inputs, executor, cost, organization, user, workspace
):
    template = code_eval(organization, workspace, code, saved, inputs)

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
# run_eval: Observe and online eval tasks
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("code, saved, language, inputs", EVALS)
def test_run_eval_runs_the_code_in_its_language(
    code, saved, language, inputs, executor, organization, workspace
):
    template = code_eval(organization, workspace, code, saved, inputs)

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
