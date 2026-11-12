"""The custom code pause on the SDK evaluation paths.

While CUSTOM_CODE_EVALS_PAUSED is on, a customer's CustomCodeEval template run
through the SDK (standalone, batched or protect) is refused before its cost is
logged: nothing is charged, the code executor is never called, and the caller
gets StandaloneEvaluationError carrying the fixed sentence. Built-in templates,
and every template with the setting off, are charged and reach the executor as
before. The real path functions run; only the usage check, the cost log, the
usage emitter and the executor client (urllib in fi_utils.sandbox) are patched.
"""

import importlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import override_settings

from agentic_eval.core_evals.fi_utils import sandbox
from agentic_eval.core_evals.fi_utils.exceptions import CodeEvalSetupError
from model_hub.models.evals_metric import EvalTemplate
from sdk.utils import evaluations as sdk_evaluations
from tfc.constants.api_calls import APICallStatusChoices

SENTENCE = "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
BUILTIN_CODE = (
    "def evaluate(output, expected):\n    return 1.0 if output == expected else 0.0\n"
)
CUSTOMER_CODE = "def evaluate(output, expected):\n    return 0.5\n"
PROTECT_EVAL_ID = 990_771_001


def pause_module():
    return importlib.import_module("agentic_eval.core_evals.fi_utils.custom_code_pause")


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
    try:
        monkeypatch.setattr(pause_module(), "_system_code_cache", None)
    except ImportError:
        pass


@pytest.fixture
def cost(monkeypatch):
    """The cost log the SDK path writes before the run; every call is recorded.

    The usage check and the post-run usage emitter are external services and
    are stubbed when the enterprise package is installed.
    """
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


@pytest.fixture
def builtin_template(db):
    template = EvalTemplate.no_workspace_objects.create(
        name="pause_test_sdk_builtin_code",
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


def customer_template(organization, workspace, code, **extra):
    return EvalTemplate.no_workspace_objects.create(
        name="pause_test_sdk_customer_code",
        owner="user",
        organization=organization,
        workspace=workspace,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": code},
        **extra,
    )


def run_standalone(template, user, organization, workspace, *, batched=False):
    kwargs = {
        "eval_template": template,
        "inputs": {"output": "4", "expected": "4"},
        "model": None,
        "user": user,
        "workspace": workspace,
        "organization": organization,
        "eval_config": {},
        "error_localizer_enabled": False,
        "trace_eval": False,
        "custom_eval_name": None,
        "span_id": None,
    }
    if batched:
        kwargs["inputs"] = {"output": ["4", "5"], "expected": ["4", "4"]}
        return sdk_evaluations._run_batched_standalone_eval(**kwargs)
    return sdk_evaluations._run_standalone_eval(**kwargs)


def assert_refused_with_the_sentence(refused):
    assert str(refused.value) == SENTENCE
    assert isinstance(refused.value.args[0], CodeEvalSetupError)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
@pytest.mark.parametrize("batched", [False, True], ids=["single", "batched"])
def test_paused_customer_template_sdk_eval_is_refused_before_the_charge(
    batched, executor, cost, user, organization, workspace
):
    template = customer_template(organization, workspace, CUSTOMER_CODE)
    with pytest.raises(sdk_evaluations.StandaloneEvaluationError) as refused:
        run_standalone(template, user, organization, workspace, batched=batched)
    assert executor == []
    cost.assert_not_called()
    assert_refused_with_the_sentence(refused)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_customer_template_protect_is_refused_before_the_charge(
    executor, cost, user, organization, workspace
):
    customer_template(organization, workspace, CUSTOMER_CODE, eval_id=PROTECT_EVAL_ID)
    with pytest.raises(sdk_evaluations.StandaloneEvaluationError) as refused:
        sdk_evaluations._run_protect(
            {"input": "4"},
            {},
            False,
            PROTECT_EVAL_ID,
            user,
            str(uuid.uuid4()),
            workspace=workspace,
        )
    assert executor == []
    cost.assert_not_called()
    assert_refused_with_the_sentence(refused)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_builtin_template_sdk_eval_is_charged_and_reaches_the_executor(
    executor, cost, builtin_template, user, organization, workspace
):
    result = run_standalone(builtin_template, user, organization, workspace)
    cost.assert_called_once()
    assert [request["code"] for request in executor] == [BUILTIN_CODE]
    assert result["eval_id"] is not None


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_without_the_setting_customer_sdk_eval_is_charged_and_reaches_the_executor(
    executor, cost, user, organization, workspace
):
    template = customer_template(organization, workspace, CUSTOMER_CODE)
    result = run_standalone(template, user, organization, workspace)
    cost.assert_called_once()
    assert [request["code"] for request in executor] == [CUSTOMER_CODE]
    assert result["eval_id"] is not None
