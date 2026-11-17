"""The eval playground's cURL and JavaScript examples run as copied.

``eval-sdk-code`` fills one example per language for the selected evaluation.
The cURL example is one shell command written over several lines, so every
line but the last must end with a backslash; otherwise the shell runs ``curl``
with the URL alone and then tries each header line as a command of its own.
The JavaScript example must read the answer the playground sends back, which
wraps the evaluation result as ``{"status": true, "result": {...}}``.
"""

import json
import re
import shlex
import shutil
import subprocess

import pytest

from model_hub.constants import SDK_API_KEY_PLACEHOLDER, SDK_SECRET_KEY_PLACEHOLDER
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.types import PlaygroundEvalResponse
from tfc.utils.general_methods import GeneralMethods

MODEL = "gpt-4o-mini"
MAPPING = {"response": "Hello"}
EXPECTED_HEADERS = [
    f"X-Api-Key: {SDK_API_KEY_PLACEHOLDER}",
    f"X-Secret-Key: {SDK_SECRET_KEY_PLACEHOLDER}",
    "Content-Type: application/json",
]
JSON_ANSWER = re.compile(r"const (\w+) = await response\.json\(\);")
LOGGED = re.compile(r"console\.log\(([^)]*)\);")
MEMBER = re.compile(r"\.(\w+)|\[(\d+)\]")


@pytest.fixture
def template(user, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="eval-playground-examples",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "output": "Pass/Fail",
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": ["response"],
        },
        eval_tags=["llm"],
        criteria="Check {{response}}.",
        model="turing_large",
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _examples(auth_client, template):
    response = auth_client.get(
        "/model-hub/eval-sdk-code/",
        {
            "template_id": str(template.id),
            "model": MODEL,
            "mapping": json.dumps(MAPPING),
        },
    )
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _expected_body(template):
    return {
        "template_id": str(template.id),
        "model": MODEL,
        "mapping": MAPPING,
        "error_localizer": False,
    }


def _check_curl_args(args, template):
    assert args[0] == "curl"
    assert args[1].endswith("/model-hub/eval-playground/")
    headers = [args[i + 1] for i, arg in enumerate(args) if arg == "-H"]
    for header in EXPECTED_HEADERS:
        assert header in headers
    assert json.loads(args[args.index("--data-raw") + 1]) == _expected_body(template)


def _playground_answer():
    """A success answer exactly as the playground view sends it."""
    result = PlaygroundEvalResponse(
        output="Passed",
        reason="The response greets the reader.",
        model=MODEL,
        metadata={},
        output_type="Pass/Fail",
        log_id=None,
    ).model_dump()
    return json.loads(json.dumps(GeneralMethods().success_response(result).data))


def _read(expression, name, answer):
    """Follow a ``name.key[0].key`` expression through ``answer``."""
    assert expression.startswith(name), expression
    value, rest, at = answer, expression[len(name) :], 0
    for step in MEMBER.finditer(rest):
        assert step.start() == at, expression
        key, index = step.groups()
        if key is not None:
            assert isinstance(value, dict) and key in value, (expression, key)
            value = value[key]
        else:
            assert isinstance(value, list) and int(index) < len(value), expression
            value = value[int(index)]
        at = step.end()
    assert at == len(rest), expression
    return value


@pytest.mark.django_db
def test_curl_example_lines_continue_one_command(auth_client, template):
    code = _examples(auth_client, template)["curl"]

    lines = code.split("\n")
    assert len(lines) > 1
    assert all(line.endswith(" \\") for line in lines[:-1]), lines
    assert not lines[-1].endswith("\\")
    _check_curl_args(shlex.split(code.replace("\\\n", " ")), template)


@pytest.mark.django_db
@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_curl_example_runs_as_one_command_in_bash(auth_client, template):
    code = _examples(auth_client, template)["curl"]

    syntax = subprocess.run(
        ["bash", "-n"], input=code, capture_output=True, text=True, timeout=30
    )
    assert syntax.returncode == 0, syntax.stderr
    # A shell function stands in for curl and prints the arguments it gets.
    run = subprocess.run(
        ["bash", "-c", "curl() { printf '%s\\0' curl \"$@\"; }\n" + code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert run.stderr == ""
    _check_curl_args(run.stdout.split("\0")[:-1], template)


@pytest.mark.django_db
def test_javascript_example_reads_the_playground_answer(auth_client, template):
    code = _examples(auth_client, template)["javascript"]
    answer = _playground_answer()

    body, _ = json.JSONDecoder().raw_decode(code, code.index("body: '") + 7)
    assert body == _expected_body(template)
    assert "eval_results" not in code
    name = JSON_ANSWER.search(code).group(1)
    logged = [_read(expr.strip(), name, answer) for expr in LOGGED.findall(code)]
    assert logged == [answer["result"]["output"], answer["result"]["reason"]]
