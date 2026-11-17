"""A code evaluation this deployment cannot run is answered as the server's
condition, not as the caller's mistake.

POST /sdk/api/v1/new-eval/ (one input or a batch), POST /sdk/api/v1/eval/
and POST /sdk/api/v1/configure-evaluations/ answer it with 503, the backend's own
sentence in message, detail, error and result, a code (CODE_EVAL_SETUP_ERROR
when the code executor or its sandbox cannot run evaluations here,
CUSTOM_CODE_EVALS_PAUSED while custom code evaluations are paused) and
retryable false: sending the request again does not help until the deployment
changes. The other 503 on new-eval, the usage check, stays retryable. Requests
the caller can fix still answer 400, and an asynchronous evaluation is still
accepted and then reported as failed with the sentence.

The code runner (CodeExecution.execute) is replaced with the answer the
executor client gives for each condition. For the three sentences the code
executor gives when its sandbox cannot start a run, the runner raises
CodeEvalSetupError with the sentence itself, so these rows test the answer to
that error, not which texts count as setup errors
(model_hub/tests/test_code_eval_setup_error.py covers those). The cost log,
analytics and the workflow start are stubbed.
"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.test import override_settings
from rest_framework import status

from agentic_eval.core_evals.fi_evals.function.internal_service_code import (
    INTERNAL_SERVICE_MESSAGE,
)
from agentic_eval.core_evals.fi_utils.exceptions import CodeEvalSetupError
from agentic_eval.core_evals.fi_utils.fi_code_execution import CodeExecution
from agentic_eval.core_evals.fi_utils.sandbox import (
    EXECUTOR_NO_NODE_MESSAGE,
    EXECUTOR_UNAVAILABLE_MESSAGE,
    LOCAL_NO_NODE_MESSAGE,
)
from model_hub.models.evals_metric import EXTERNAL_EVAL_TYPE, EvalTemplate
from sdk.utils import evaluations as sdk_evaluations
from sdk.utils.api_errors import SDKAdmissionError
from sdk.utils.evaluations import StandaloneEvaluationError
from sdk.views import evaluations as views
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal import evaluations as evaluation_workflows
from tfc.temporal.evaluations import activities

NEW_EVAL = "/sdk/api/v1/new-eval/"
V1_EVAL = "/sdk/api/v1/eval/"
CONFIGURE = "/sdk/api/v1/configure-evaluations/"
V1_EVAL_ID = 990_772_001

PAUSED_SENTENCE = (
    "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
)
SANDBOX_CANNOT_START = (
    "The code sandbox could not start a run on this host: nsjail could not "
    "create the run's isolated environment (its Linux namespaces). See "
    "INSTALLATION.md, Code evals and the sandbox."
)
SANDBOX_USERNS_REMAP = (
    "The code sandbox could not start a run on this host: nsjail could not "
    "create the run's isolated environment (its Linux namespaces). Docker on "
    "this host runs with userns-remap, which puts every container inside a "
    "remapped user namespace, and code evaluations do not run there. See "
    "INSTALLATION.md, Code evals and the sandbox."
)
SANDBOX_USERNS_RESTRICTED = (
    "The code sandbox could not start a run on this host: nsjail could not "
    "create the run's isolated environment (its Linux namespaces). This host "
    "restricts unprivileged user namespaces "
    "(kernel.apparmor_restrict_unprivileged_userns=1, the default on Ubuntu "
    "23.10 and later): load the fa-code-executor AppArmor profile on the Docker "
    "host and set CODE_EXECUTOR_APPARMOR_PROFILE=fa-code-executor. See "
    "INSTALLATION.md, Code evals and the sandbox."
)

CODE = "def evaluate(output, expected):\n    return output == expected\n"
# Built-in code that imports requests is withheld while custom code is paused.
WITHHELD_CODE = "import requests\n\n\n" + CODE
INTERNAL_SERVICE_CODE = (
    "def evaluate(output, expected):\n    return bool(_model_serving_url)\n"
)
ONE_INPUT = {"output": "Paris", "expected": "Paris"}
BATCH = {"output": ["Paris", "Rome"], "expected": ["Paris", "Paris"]}


def answered(sentence):
    """A runner that gets the executor client's answer for a condition that
    stops every run."""
    return Mock(return_value={"status": "error", "data": sentence, "setup_error": True})


def raised(sentence):
    return Mock(side_effect=CodeEvalSetupError(sentence))


def never_runs():
    return Mock(side_effect=AssertionError("this evaluation must not run"))


SETUP_CONDITIONS = [
    pytest.param(answered, EXECUTOR_UNAVAILABLE_MESSAGE, id="executor-unavailable"),
    pytest.param(answered, EXECUTOR_NO_NODE_MESSAGE, id="executor-without-node"),
    pytest.param(raised, SANDBOX_CANNOT_START, id="sandbox-cannot-start"),
    pytest.param(raised, SANDBOX_USERNS_REMAP, id="sandbox-userns-remap"),
    pytest.param(raised, SANDBOX_USERNS_RESTRICTED, id="sandbox-userns-restricted"),
    pytest.param(answered, LOCAL_NO_NODE_MESSAGE, id="local-runner-without-node"),
]


@pytest.fixture
def route(monkeypatch):
    """The SDK evaluation route with its cost log, analytics and workflow
    start stubbed."""
    cost_row = SimpleNamespace(config="{}", status=None, log_id=uuid4(), save=Mock())
    cost = Mock(return_value=cost_row)
    monkeypatch.setattr(
        sdk_evaluations, "_log_and_deduct_cost_for_standalone_eval", cost
    )
    monkeypatch.setattr(views, "get_mixpanel_properties", lambda **kwargs: kwargs)
    monkeypatch.setattr(views, "track_mixpanel_event", Mock())
    start = Mock()
    monkeypatch.setattr(evaluation_workflows, "start_evaluation_workflow", start)
    return SimpleNamespace(cost=cost, start=start)


def code_eval(name, organization, workspace, code=CODE):
    """A user's code eval: it takes the parameters of its evaluate() function."""
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        owner="user",
        organization=organization,
        workspace=workspace,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": code},
    )


def builtin_template(name, eval_type="code", **config):
    template = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner="system",
        organization=None,
        workspace=None,
        eval_type=eval_type,
        config=config,
    )
    # save() stamps the request's organization and workspace on a row saved while a
    # workspace context is set; a built-in has neither, so clear them without save().
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
        organization=None, workspace=None
    )
    template.refresh_from_db()
    return template


def paused_custom_code(organization, workspace):
    return code_eval("paused-custom-code", organization, workspace), PAUSED_SENTENCE


def paused_withheld_builtin(organization, workspace):
    template = builtin_template(
        "paused_withheld_builtin",
        eval_type_id="CustomCodeEval",
        code=WITHHELD_CODE,
        required_keys=["output", "expected"],
    )
    return template, f"{template.name} is temporarily unavailable on Future AGI Cloud."


PAUSED_TEMPLATES = [
    pytest.param(paused_custom_code, id="custom-code"),
    pytest.param(paused_withheld_builtin, id="withheld-built-in"),
]


def evaluate(client, name, inputs, **request):
    return client.post(
        NEW_EVAL, {"eval_name": name, "inputs": inputs, **request}, format="json"
    )


def evaluate_on_v1(client, organization, workspace, code=CODE):
    """POST /sdk/api/v1/eval/ for a user's code eval, which that route finds by
    its numeric eval_id."""
    template = code_eval("v1-code-eval", organization, workspace, code)
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(eval_id=V1_EVAL_ID)
    return client.post(
        V1_EVAL,
        {"inputs": [{"input": "Paris"}], "config": {str(V1_EVAL_ID): {}}},
        format="json",
    )


def configure(client, name):
    return client.post(
        CONFIGURE,
        {
            "eval_config": {
                "eval_templates": name,
                "inputs": {"output": "output", "expected": "expected"},
            },
            "platform": "langfuse",
        },
        format="json",
    )


def assert_cannot_run(response, code, sentence):
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, response.data
    body = response.data
    assert body["status"] is False
    assert body["type"] == "service_unavailable"
    assert body["code"] == code
    assert body["error_code"] == code
    assert body["retryable"] is False
    for field in ("message", "detail", "error", "result"):
        assert body[field] == sentence, field


def assert_caller_mistake(response):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
    assert response.data["code"] == "invalid"
    assert "retryable" not in response.data


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
@pytest.mark.parametrize("inputs", [ONE_INPUT, BATCH], ids=["one-input", "batch"])
@pytest.mark.parametrize("runner, sentence", SETUP_CONDITIONS)
def test_new_eval_answers_a_code_eval_this_deployment_cannot_run_with_503(
    auth_client, organization, workspace, route, monkeypatch, runner, sentence, inputs
):
    template = code_eval("cannot-run-code-eval", organization, workspace)
    monkeypatch.setattr(CodeExecution, "execute", runner(sentence))

    response = evaluate(auth_client, template.name, inputs)

    assert_cannot_run(response, "CODE_EVAL_SETUP_ERROR", sentence)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
@pytest.mark.parametrize("runner, sentence", SETUP_CONDITIONS)
def test_v1_eval_answers_a_code_eval_this_deployment_cannot_run_with_503(
    auth_client, organization, workspace, route, monkeypatch, runner, sentence
):
    monkeypatch.setattr(CodeExecution, "execute", runner(sentence))

    response = evaluate_on_v1(auth_client, organization, workspace)

    assert_cannot_run(response, "CODE_EVAL_SETUP_ERROR", sentence)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
@pytest.mark.parametrize("inputs", [ONE_INPUT, BATCH], ids=["one-input", "batch"])
@pytest.mark.parametrize("paused", PAUSED_TEMPLATES)
def test_new_eval_answers_a_paused_code_eval_with_503(
    auth_client, organization, workspace, route, monkeypatch, paused, inputs
):
    template, sentence = paused(organization, workspace)
    runner = never_runs()
    monkeypatch.setattr(CodeExecution, "execute", runner)

    response = evaluate(auth_client, template.name, inputs)

    assert_cannot_run(response, "CUSTOM_CODE_EVALS_PAUSED", sentence)
    runner.assert_not_called()
    route.cost.assert_not_called()


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
@pytest.mark.parametrize("paused", PAUSED_TEMPLATES)
def test_configure_answers_a_paused_code_eval_with_503(
    auth_client, organization, workspace, paused
):
    template, sentence = paused(organization, workspace)

    response = configure(auth_client, template.name)

    assert_cannot_run(response, "CUSTOM_CODE_EVALS_PAUSED", sentence)


def unknown_template(organization, workspace):
    return {"eval_name": "no-such-code-eval", "inputs": ONE_INPUT}


def external_metric(organization, workspace):
    template = EvalTemplate.no_workspace_objects.create(
        name="scored-by-the-caller",
        owner="user",
        organization=organization,
        workspace=workspace,
        eval_type=EXTERNAL_EVAL_TYPE,
        config={"required_keys": ["output"]},
    )
    return {"eval_name": template.name, "inputs": {"output": "Paris"}}


def system_eval_without_its_model(organization, workspace):
    template = builtin_template(
        "judge_needs_a_model",
        eval_type="llm",
        eval_type_id="CustomPromptEvaluator",
        models=["turing_large"],
        required_keys=["output"],
    )
    return {"eval_name": template.name, "inputs": {"output": "Paris"}}


def inputs_not_an_object(organization, workspace):
    template = code_eval("caller-code-eval", organization, workspace)
    return {"eval_name": template.name, "inputs": "Paris"}


def unexpected_input_key(organization, workspace):
    template = code_eval("caller-code-eval", organization, workspace)
    return {"eval_name": template.name, "inputs": {**ONE_INPUT, "question": "?"}}


def missing_input_key(organization, workspace):
    template = builtin_template(
        "builtin_needs_two_keys",
        eval_type_id="CustomCodeEval",
        code=CODE,
        required_keys=["output", "expected"],
    )
    return {"eval_name": template.name, "inputs": {"output": "Paris"}}


def config_not_an_object(organization, workspace):
    template = code_eval("caller-code-eval", organization, workspace)
    return {"eval_name": template.name, "inputs": ONE_INPUT, "config": "strict"}


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
@pytest.mark.parametrize(
    "request_body",
    [
        unknown_template,
        external_metric,
        system_eval_without_its_model,
        inputs_not_an_object,
        unexpected_input_key,
        missing_input_key,
        config_not_an_object,
    ],
    ids=[
        "unknown-template",
        "external-metric",
        "system-eval-without-its-model",
        "inputs-not-an-object",
        "unexpected-input-key",
        "missing-input-key",
        "config-not-an-object",
    ],
)
def test_a_request_the_caller_can_fix_still_answers_400(
    auth_client, organization, workspace, route, monkeypatch, request_body
):
    monkeypatch.setattr(CodeExecution, "execute", never_runs())

    response = auth_client.post(
        NEW_EVAL, request_body(organization, workspace), format="json"
    )

    assert_caller_mistake(response)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
@pytest.mark.parametrize(
    "answer",
    [
        {"status": "error", "data": "NameError: name 'answer' is not defined"},
        {"status": "skip"},
        {"status": "success", "data": {"value": 1}},
        # The executor returns the code's own result as is, so the code can
        # set the flag on any text.
        {"status": "error", "data": "Sandbox error: /app/private", "setup_error": True},
    ],
    ids=[
        "code-raises",
        "code-returns-none",
        "code-returns-a-bad-shape",
        "code-sets-the-setup-flag",
    ],
)
def test_evaluation_code_that_fails_still_answers_400(
    auth_client, organization, workspace, route, monkeypatch, answer
):
    template = code_eval("failing-code-eval", organization, workspace)
    monkeypatch.setattr(CodeExecution, "execute", Mock(return_value=answer))

    response = evaluate(auth_client, template.name, ONE_INPUT)

    assert_caller_mistake(response)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_code_that_calls_an_internal_service_still_answers_400(
    auth_client, organization, workspace, route, monkeypatch
):
    template = code_eval(
        "internal-service-code-eval", organization, workspace, INTERNAL_SERVICE_CODE
    )
    runner = never_runs()
    monkeypatch.setattr(CodeExecution, "execute", runner)

    response = evaluate(auth_client, template.name, ONE_INPUT)

    assert_caller_mistake(response)
    assert response.data["message"] == INTERNAL_SERVICE_MESSAGE.format(
        name="embedding_similarity"
    )
    runner.assert_not_called()


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_code_that_calls_an_internal_service_still_answers_400_on_v1_eval(
    auth_client, organization, workspace, route, monkeypatch
):
    runner = never_runs()
    monkeypatch.setattr(CodeExecution, "execute", runner)

    response = evaluate_on_v1(
        auth_client, organization, workspace, INTERNAL_SERVICE_CODE
    )

    assert_caller_mistake(response)
    assert response.data["message"] == INTERNAL_SERVICE_MESSAGE.format(
        name="embedding_similarity"
    )
    runner.assert_not_called()


@pytest.mark.django_db
def test_a_jev_run_error_keeps_its_typed_400(
    auth_client, organization, workspace, route, monkeypatch
):
    jev_client = pytest.importorskip("ee.jev.client")
    template = code_eval("jev-failure-code-eval", organization, workspace)
    failed = StandaloneEvaluationError(
        jev_client.JevGatewayError("JEV_RATE_LIMITED", retryable=True)
    )
    monkeypatch.setattr(views, "_run_standalone_eval", Mock(side_effect=failed))

    response = evaluate(auth_client, template.name, ONE_INPUT)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
    assert response.data["code"] == "JEV_RATE_LIMITED"
    assert "retryable" not in response.data


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_a_usage_check_outage_on_the_same_route_is_still_retryable(
    auth_client, organization, workspace, route, monkeypatch
):
    template = code_eval("usage-check-code-eval", organization, workspace)
    route.cost.side_effect = SDKAdmissionError.unavailable()
    monkeypatch.setattr(CodeExecution, "execute", never_runs())

    response = evaluate(auth_client, template.name, ONE_INPUT)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, response.data
    assert response.data["code"] == "USAGE_CHECK_UNAVAILABLE"
    assert response.data["error_code"] == "USAGE_CHECK_UNAVAILABLE"
    assert response.data["retryable"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "paused, sentence",
    [(False, EXECUTOR_UNAVAILABLE_MESSAGE), (True, PAUSED_SENTENCE)],
    ids=["executor-unavailable", "custom-code-paused"],
)
def test_an_async_evaluation_is_accepted_and_then_fails_with_the_sentence(
    auth_client, organization, workspace, route, monkeypatch, settings, paused, sentence
):
    settings.CUSTOM_CODE_EVALS_PAUSED = paused
    monkeypatch.setattr(CodeExecution, "execute", answered(sentence))
    # Connection cleanup is the worker's; keep the test's transaction open.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    template = code_eval("async-code-eval", organization, workspace)

    submitted = evaluate(auth_client, template.name, ONE_INPUT, is_async=True)

    assert submitted.status_code == status.HTTP_200_OK, submitted.data
    eval_id = submitted.data["result"][0]["evaluations"][0]["eval_id"]
    route.start.assert_called_once_with(evaluation_id=str(eval_id))

    # The worker runs outside any request's workspace context.
    clear_workspace_context()
    activities._run_single_evaluation_sync(str(eval_id))
    answer = auth_client.get(NEW_EVAL, {"eval_id": eval_id})

    assert answer.status_code == status.HTTP_200_OK, answer.data
    assert answer.data["result"]["eval_status"] == "failed"
    assert answer.data["result"]["result"]["reason"] == sentence
    assert answer.data["result"]["result"]["error_message"] == sentence
