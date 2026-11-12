"""A deployment can pause custom code evaluations (CUSTOM_CODE_EVALS_PAUSED).

While it is on, code a customer wrote or changed never reaches the code
executor: the refusal is a typed setup error carrying a fixed sentence. Built-in
evaluators, whose code is a saved system template's own, keep running. With it
off, everything behaves as before. The executor client (urllib in
fi_utils.sandbox) is what the tests watch, not a mocked outcome.
"""

import importlib
import json

import pytest
from django.test import override_settings

from agentic_eval.core_evals.fi_utils import sandbox
from agentic_eval.core_evals.fi_utils.exceptions import CodeEvalSetupError
from model_hub.models.evals_metric import EvalTemplate

SENTENCE = "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
WITHHELD_BUILTINS = {
    "api_call",
    "contains_valid_link",
    "no_invalid_links",
    "ssim",
    "psnr",
    "image_properties",
}
BUILTIN_CODE = (
    "def evaluate(output, expected):\n    return 1.0 if output == expected else 0.0\n"
)
CUSTOMER_CODE = "def evaluate(output, expected):\n    return 0.5\n"
CUSTOMER_JS = "function evaluate({output}) { return 1; }\n"


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
def builtin_template(db):
    return EvalTemplate.no_workspace_objects.create(
        name="pause_test_builtin_code",
        owner="system",
        organization=None,
        workspace=None,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": BUILTIN_CODE},
    )


def customer_template(organization, workspace, code, name="pause_test_customer_code"):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        owner="user",
        organization=organization,
        workspace=workspace,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": code},
    )


def run(template, organization, **inputs):
    from evaluations.engine.runner import EvalRequest, run_eval

    return run_eval(
        EvalRequest(
            eval_template=template,
            inputs=inputs or {"output": "4", "expected": "4"},
            organization_id=str(organization.id) if organization else None,
            skip_params_preparation=True,
        )
    )


@pytest.mark.django_db
def test_the_refusal_is_a_setup_error_with_the_fixed_sentence():
    exceptions = importlib.import_module("agentic_eval.core_evals.fi_utils.exceptions")
    error = exceptions.CustomCodeEvalPausedError(pause_module().PAUSED_MESSAGE)
    assert isinstance(error, CodeEvalSetupError)
    assert str(error) == SENTENCE
    assert error.error_code == "CUSTOM_CODE_EVALS_PAUSED"
    assert SENTENCE in sandbox.SETUP_ERROR_MESSAGES


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_customer_template_is_refused_before_the_executor(
    executor, organization, workspace
):
    template = customer_template(organization, workspace, CUSTOMER_CODE)
    with pytest.raises(CodeEvalSetupError) as refused:
        run(template, organization)
    assert str(refused.value) == SENTENCE
    assert executor == []


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_customer_copy_of_a_builtin_is_refused(
    executor, builtin_template, organization, workspace
):
    # "Duplicate to customize": the customer's copy has the built-in's code byte for byte.
    copy = customer_template(
        organization, workspace, BUILTIN_CODE, name="pause_test_builtin_code_copy"
    )
    with pytest.raises(CodeEvalSetupError) as refused:
        run(copy, organization)
    assert str(refused.value) == SENTENCE
    assert executor == []


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_unsaved_draft_is_refused_although_it_looks_like_a_builtin(
    executor, builtin_template, organization
):
    # An EvalTemplate built in memory has owner "system" and no organization by default.
    draft = EvalTemplate(
        name="pause_test_draft",
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": BUILTIN_CODE},
    )
    with pytest.raises(CodeEvalSetupError) as refused:
        run(draft, organization)
    assert str(refused.value) == SENTENCE
    assert executor == []


@pytest.fixture
def caller_code_template(db):
    """The system template seeded from custom_code_evaluation.yaml (eval_id 34): system-owned,
    but its code is a parameter the caller supplies, with no code of its own."""
    return EvalTemplate.no_workspace_objects.create(
        name="pause_test_custom_code_evaluation",
        owner="system",
        organization=None,
        workspace=None,
        eval_type="code",
        config={
            "required_keys": [],
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "config": {"code": {"type": "code", "default": None}},
        },
    )


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
@pytest.mark.parametrize(
    "supplied",
    [CUSTOMER_CODE, BUILTIN_CODE],
    ids=["customer-code", "another-builtins-code"],
)
def test_paused_system_template_that_takes_caller_code_is_refused(
    executor, builtin_template, caller_code_template, organization, supplied
):
    from evaluations.engine.runner import EvalRequest, run_eval

    with pytest.raises(CodeEvalSetupError) as refused:
        run_eval(
            EvalRequest(
                eval_template=caller_code_template,
                inputs={"output": "4", "expected": "4"},
                organization_id=str(organization.id),
                config_overrides={"code": supplied},
                skip_params_preparation=True,
            )
        )
    assert str(refused.value) == SENTENCE
    assert executor == []


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_system_version_owned_by_an_organization_is_not_trusted(
    executor, builtin_template, organization, workspace
):
    from model_hub.models.evals_metric import EvalTemplateVersion

    # A version stamped with an organization, under a system template, holding a customer's code.
    EvalTemplateVersion._base_manager.create(
        eval_template=builtin_template,
        version_number=99,
        config_snapshot={"eval_type_id": "CustomCodeEval", "code": CUSTOMER_CODE},
        organization=organization,
        workspace=workspace,
    )
    pause = pause_module()
    assert not pause.is_system_template_code(builtin_template, CUSTOMER_CODE)
    assert pause.paused_code_refusal(CUSTOMER_CODE)["data"] == SENTENCE
    with pytest.raises(CodeEvalSetupError):
        from agentic_eval.core_evals.fi_evals.function.functions import custom_code_eval

        custom_code_eval(CUSTOMER_CODE, output="4", expected="4")
    assert executor == []


WITHHELD_CODE_A = (
    "def evaluate(output, expected):\n"
    "    import os\n"
    "    from PIL import Image\n"
    "    if os.path.isfile(output):\n"
    "        Image.open(output)\n"
    "    return 1.0\n"
)
WITHHELD_CODE_B = (
    "def evaluate(url, headers=None, payload=None):\n"
    "    import json\n"
    "    import urllib.request\n"
    "    request = urllib.request.Request(url, data=json.dumps(payload or {}).encode())\n"
    "    with urllib.request.urlopen(request, timeout=5) as response:\n"
    "        return {'result': 1.0, 'reason': response.read(200).decode()}\n"
)


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
@pytest.mark.parametrize(
    "name, code",
    [
        ("pause_test_withheld_a", WITHHELD_CODE_A),
        ("pause_test_withheld_b", WITHHELD_CODE_B),
    ],
    ids=["first", "second"],
)
def test_paused_withheld_builtin_is_refused_with_its_name(
    executor, organization, name, code
):
    template = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner="system",
        organization=None,
        workspace=None,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": code},
    )
    expected = f"{name} is temporarily unavailable on Future AGI Cloud."
    with pytest.raises(CodeEvalSetupError) as refused:
        run(template, organization, output="4", expected="4")
    assert str(refused.value) == expected
    assert pause_module().paused_code_refusal(code)["data"] == expected
    from agentic_eval.core_evals.fi_evals.function.functions import custom_code_eval

    with pytest.raises(CodeEvalSetupError) as refused_at_run:
        custom_code_eval(code, output="4", expected="4")
    assert str(refused_at_run.value) == expected
    assert executor == []


def test_only_the_six_listed_builtins_are_withheld():
    """Every shipped built-in definition with code: the six listed are withheld, all others run."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1] / "model_hub" / "system_evals"
    withheld, runs = set(), set()
    for path in sorted(root.rglob("*.y*ml")):
        definition = yaml.safe_load(path.read_text()) or {}
        code = definition.get("code") or (definition.get("config") or {}).get("code")
        if isinstance(code, str):
            (withheld if pause_module().is_withheld_builtin_code(code) else runs).add(
                definition["name"]
            )
    assert withheld == WITHHELD_BUILTINS
    assert len(runs) == 80
    assert "accuracy" in runs


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_builtin_template_still_reaches_the_executor(
    executor, builtin_template, organization
):
    result = run(builtin_template, organization)
    assert [request["code"] for request in executor] == [BUILTIN_CODE]
    assert result is not None


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_custom_code_eval_refuses_customer_code_at_run_time(
    executor, builtin_template
):
    from agentic_eval.core_evals.fi_evals.function.functions import custom_code_eval

    with pytest.raises(CodeEvalSetupError) as refused:
        custom_code_eval(CUSTOMER_CODE, output="4", expected="4")
    assert str(refused.value) == SENTENCE
    assert executor == []
    assert custom_code_eval(BUILTIN_CODE, output="4", expected="4")["result"] == 1.0
    assert [request["code"] for request in executor] == [BUILTIN_CODE]


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_executor_client_sends_nothing_but_builtin_code(
    executor, builtin_template
):
    python = sandbox.execute_sandboxed_python(CUSTOMER_CODE, {"output": "4"})
    javascript = sandbox.execute_sandboxed_javascript(CUSTOMER_JS, {"output": "4"})
    for refusal in (python, javascript):
        assert refusal["status"] == "error"
        assert refusal["data"] == SENTENCE
        assert refusal["setup_error"] is True
    assert executor == []
    sandbox.execute_sandboxed_python(BUILTIN_CODE, {"output": "4", "expected": "4"})
    assert [request["code"] for request in executor] == [BUILTIN_CODE]


@pytest.mark.django_db
def test_without_the_setting_customer_code_runs_as_before(
    executor, organization, workspace
):
    template = customer_template(organization, workspace, CUSTOMER_CODE)
    run(template, organization)
    assert [request["code"] for request in executor] == [CUSTOMER_CODE]
