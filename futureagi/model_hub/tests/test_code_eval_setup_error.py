"""A code eval this install cannot run says how to fix the setup.

An executor without Node.js fails a JavaScript eval with a hint to point
CODE_EXECUTOR_URL at the code-executor service, which has it. Dataset cells show
it; the eval playground and "Test Evaluation" must show it too instead of their
generic retry message, while other code eval failures stay generic there.
Only the sandbox's own setup messages count: the executor passes an eval
script's result through as is, so a result's setup_error flag proves nothing.

On a host where nsjail cannot build a run's isolated environment, the
code-executor answers every run with a fixed sentence that points to
INSTALLATION.md. When it can see the host setting that stops the jail
(Docker's userns-remap, or Ubuntu's restriction of unprivileged user
namespaces), the sentence names it. Each of the three texts is a setup error
too: every caller gets it word for word, with nothing put in front of it.

urlopen is replaced at the urllib boundary with the code-executor's answer.
"""

import ast
import errno
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from rest_framework import status

from agentic_eval.core_evals.fi_evals.function.functions import custom_code_eval
from agentic_eval.core_evals.fi_utils import sandbox
from agentic_eval.core_evals.fi_utils.exceptions import CodeEvalSetupError
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate

JS_CODE = "function evaluate(output, expected) { return output === expected; }\n"
# code-executor/server.py's answer to a JavaScript eval without Node.js.
NO_NODE_MESSAGE = sandbox.EXECUTOR_NO_NODE_MESSAGE
NO_NODE_RESULT = {
    "status": "error",
    "data": NO_NODE_MESSAGE,
    "setup_error": True,
    "execution_time": 0.001,
}
PRIVATE_ERROR = "Sandbox error: /app/backend/secret-path"
PY_CODE = "def evaluate(output, expected):\n    return output == expected\n"
# The code-executor is deployed on its own and its dependencies are not
# installed here, so its messages are read from the source. One test loads
# server.py with falcon stubbed, as tests/test_code_executor_init.py does, to
# run its own sentence builder.
EXECUTOR_SERVER = Path(__file__).resolve().parents[2] / "code-executor" / "server.py"


def _executor_message(name: str) -> str:
    """The text code-executor/server.py assigns to ``name`` at module level."""
    tree = ast.parse(EXECUTOR_SERVER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and [
            target.id for target in node.targets if isinstance(target, ast.Name)
        ] == [name]:
            return ast.literal_eval(node.value)
    raise AssertionError(f"code-executor/server.py assigns no {name}")


# code-executor/server.py's _sandbox_unavailable() puts the host setting it can
# see between these two sentences.
SANDBOX_FIRST_SENTENCE = (
    "The code sandbox could not start a run on this host: nsjail could not "
    "create the run's isolated environment (its Linux namespaces)."
)
SANDBOX_SEE_INSTALLATION = "See INSTALLATION.md, Code evals and the sandbox."


def _executor_sandbox_sentence(cause_name) -> str:
    """The executor's answer to a run whose jail cannot start: with the host
    cause server.py assigns to ``cause_name``, or, for None, without one."""
    if cause_name is None:
        return _executor_message("SANDBOX_UNAVAILABLE_MESSAGE")
    cause = _executor_message(cause_name)
    return f"{SANDBOX_FIRST_SENTENCE} {cause} {SANDBOX_SEE_INSTALLATION}"


# The three texts: no host cause visible, Docker's userns-remap, and the
# user-namespace restriction without the fa-code-executor profile.
SANDBOX_CAUSES = pytest.mark.parametrize(
    "cause_name",
    [None, "REMAP_CAUSE", "RESTRICT_CAUSE"],
    ids=["no-cause", "remap", "restrict"],
)
# What the executor reads of its host for each, as
# tests/test_code_executor_init.py's SandboxFailureCauseTests writes it.
SANDBOX_HOSTS = {
    None: {},
    "REMAP_CAUSE": {"uid_map": "         0     100000      65536\n", "restrict": "0\n"},
    "RESTRICT_CAUSE": {
        "uid_map": "         0          0 4294967295\n",
        "restrict": "1\n",
        "label": "unconfined\n",
    },
}


def _load_executor_server():
    """code-executor/server.py run as a module of its own, falcon stubbed."""
    modules = {"falcon": SimpleNamespace(App=lambda: mock.Mock())}
    guard = EXECUTOR_SERVER.with_name("egress_guard.py")
    spec = importlib.util.spec_from_file_location("egress_guard", guard)
    modules["egress_guard"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modules["egress_guard"])
    spec = importlib.util.spec_from_file_location(
        "executor_server_under_test", EXECUTOR_SERVER
    )
    server = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(server)
    return server


@pytest.fixture
def executor_answers(monkeypatch, settings):
    settings.CLOUD_DEPLOYMENT = ""
    settings.CODE_EXECUTOR_LOCAL_FALLBACK = False
    opener = mock.MagicMock()
    monkeypatch.setattr(urllib.request, "urlopen", opener)

    def answer(result: dict):
        body = json.dumps(result).encode()
        response = opener.return_value.__enter__.return_value
        response.read.side_effect = lambda amt=-1: body if amt < 0 else body[:amt]

    return answer


@pytest.mark.parametrize(
    "result, message",
    [
        (NO_NODE_RESULT, NO_NODE_MESSAGE),
        # An executor image from before the flag gives the same text.
        ({"status": "error", "data": NO_NODE_MESSAGE}, NO_NODE_MESSAGE),
        (
            {
                "status": "error",
                "data": sandbox.EXECUTOR_UNAVAILABLE_MESSAGE,
                "setup_error": True,
            },
            sandbox.EXECUTOR_UNAVAILABLE_MESSAGE,
        ),
        (
            {
                "status": "error",
                "data": sandbox.LOCAL_NO_NODE_MESSAGE,
                "setup_error": True,
            },
            sandbox.LOCAL_NO_NODE_MESSAGE,
        ),
    ],
    ids=["no-node", "no-node-unflagged", "unavailable", "local-no-node"],
)
def test_a_setup_error_keeps_the_sandbox_message(result, message):
    with mock.patch(
        "agentic_eval.core_evals.fi_evals.function.functions.CodeExecution.execute",
        return_value=result,
    ):
        with pytest.raises(CodeEvalSetupError) as raised:
            custom_code_eval(JS_CODE, language="javascript", output="a")

    assert str(raised.value) == message


# The eval's own code can print this result: the executor returns it as is.
FAKED_SETUP_ERROR = {"status": "error", "data": PRIVATE_ERROR, "setup_error": True}


@pytest.mark.parametrize(
    "result",
    [{"status": "error", "data": PRIVATE_ERROR}, FAKED_SETUP_ERROR],
    ids=["plain", "flagged"],
)
def test_other_code_eval_errors_are_not_setup_errors(result):
    with mock.patch(
        "agentic_eval.core_evals.fi_evals.function.functions.CodeExecution.execute",
        return_value=result,
    ):
        with pytest.raises(ValueError) as raised:
            custom_code_eval(JS_CODE, language="javascript", output="a")

    assert not isinstance(raised.value, CodeEvalSetupError)


def test_an_unreachable_executor_is_a_setup_error_even_with_the_local_opt_in(
    executor_answers, settings, monkeypatch
):
    """CODE_EXECUTOR_LOCAL_FALLBACK with the executor down and no Node.js: the
    eval gets the executor's unavailable sentence, not the local runner's."""
    settings.CODE_EXECUTOR_LOCAL_FALLBACK = True
    # urlopen is the fixture's stand-in: the executor is not reachable.
    urllib.request.urlopen.side_effect = urllib.error.URLError(
        ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
    )
    # A local runner, if one were tried, would find no Node.js here.
    monkeypatch.setattr(sandbox.os.path, "isfile", lambda path: False)

    with pytest.raises(CodeEvalSetupError) as raised:
        custom_code_eval(JS_CODE, language="javascript", output="a")

    assert str(raised.value) == sandbox.EXECUTOR_UNAVAILABLE_MESSAGE


def test_the_backend_keeps_the_executors_sandbox_unavailable_sentence():
    """The backend and the code-executor are deployed separately, so the
    backend keeps its own copy of the sentence: it must stay word for word
    the executor's, and count as a setup error."""
    sentence = _executor_message("SANDBOX_UNAVAILABLE_MESSAGE")

    assert sandbox.EXECUTOR_SANDBOX_UNAVAILABLE_MESSAGE == sentence
    assert sentence in sandbox.SETUP_ERROR_MESSAGES


@pytest.mark.parametrize(
    "cause_name, backend_cause, backend_sentence",
    [
        (
            "REMAP_CAUSE",
            "EXECUTOR_SANDBOX_REMAP_CAUSE",
            "EXECUTOR_SANDBOX_REMAP_MESSAGE",
        ),
        (
            "RESTRICT_CAUSE",
            "EXECUTOR_SANDBOX_RESTRICT_CAUSE",
            "EXECUTOR_SANDBOX_RESTRICT_MESSAGE",
        ),
    ],
    ids=["remap", "restrict"],
)
def test_the_backend_keeps_the_executors_sentence_for_each_host_cause(
    cause_name, backend_cause, backend_sentence
):
    """When the executor sees the host setting that stops the jail, it names
    it in the sentence. The backend keeps a copy of each cause and of the
    sentence the executor builds with it, and counts each as a setup error."""
    sentence = _executor_sandbox_sentence(cause_name)

    assert getattr(sandbox, backend_cause) == _executor_message(cause_name)
    assert getattr(sandbox, backend_sentence) == sentence
    assert sentence in sandbox.SETUP_ERROR_MESSAGES


@SANDBOX_CAUSES
def test_each_sentence_the_executor_builds_is_a_setup_error(tmp_path, cause_name):
    """server.py's own _sandbox_unavailable(), with the host files it reads
    pointed at what each host shows."""
    server = _load_executor_server()
    for name, attribute in (
        ("uid_map", "UID_MAP_PATH"),
        ("restrict", "USERNS_RESTRICT_PATH"),
        ("label", "APPARMOR_LABEL_PATH"),
    ):
        path = tmp_path / name
        if name in SANDBOX_HOSTS[cause_name]:
            path.write_text(SANDBOX_HOSTS[cause_name][name], encoding="utf-8")
        setattr(server, attribute, str(path))

    sentence = server._sandbox_unavailable()

    assert sentence == _executor_sandbox_sentence(cause_name)
    assert sentence in sandbox.SETUP_ERROR_MESSAGES


@SANDBOX_CAUSES
@pytest.mark.parametrize(
    "code, language",
    [(PY_CODE, "python"), (JS_CODE, "javascript")],
    ids=["python", "javascript"],
)
def test_a_sandbox_that_cannot_start_a_run_is_a_setup_error(
    executor_answers, code, language, cause_name
):
    """nsjail cannot build the run's isolated environment on this host."""
    sentence = _executor_sandbox_sentence(cause_name)
    executor_answers({"status": "error", "data": sentence, "execution_time": 0.004})

    with pytest.raises(CodeEvalSetupError) as raised:
        custom_code_eval(code, language=language, output="a", expected="a")

    assert str(raised.value) == sentence


def _js_code_eval(user, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="js-setup-error",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        config={
            "code": JS_CODE,
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["output", "expected"],
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _playground(auth_client, template):
    return auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "",
            "mapping": {"output": "same", "expected": "same"},
            "config": {"params": {}},
        },
        format="json",
    )


def _test_evaluation(auth_client, template):
    return auth_client.post(
        "/model-hub/test-evaluation/",
        {
            "name": "js-setup-error-test",
            "template_type": "Function",
            "template_id": str(template.id),
            "eval_type_id": "CustomCodeEval",
            "model": "",
            "output_type": "Pass/Fail",
            "required_keys": ["output", "expected"],
            "input_data_types": {"output": "text", "expected": "text"},
            "config": {
                "mapping": {"output": "same", "expected": "same"},
                "config": {"code": JS_CODE},
            },
        },
        format="json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("call", [_playground, _test_evaluation])
def test_javascript_eval_without_node_shows_the_sandbox_hint(
    call, auth_client, user, workspace, executor_answers
):
    executor_answers(NO_NODE_RESULT)

    response = call(auth_client, _js_code_eval(user, workspace))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert NO_NODE_MESSAGE in str(response.data)
    assert "Please retry" not in str(response.data)


@pytest.mark.django_db
@SANDBOX_CAUSES
@pytest.mark.parametrize("call", [_playground, _test_evaluation])
def test_a_sandbox_that_cannot_start_a_run_shows_its_sentence(
    call, cause_name, auth_client, user, workspace, executor_answers
):
    sentence = _executor_sandbox_sentence(cause_name)
    executor_answers({"status": "error", "data": sentence, "execution_time": 0.004})

    response = call(auth_client, _js_code_eval(user, workspace))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["message"] == sentence


@pytest.mark.django_db
@pytest.mark.parametrize("call", [_playground, _test_evaluation])
@pytest.mark.parametrize(
    "result",
    [{"status": "error", "data": PRIVATE_ERROR}, FAKED_SETUP_ERROR],
    ids=["plain", "flagged"],
)
def test_other_code_eval_failures_stay_generic(
    call, result, auth_client, user, workspace, executor_answers
):
    executor_answers({**result, "execution_time": 0})

    response = call(auth_client, _js_code_eval(user, workspace))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert PRIVATE_ERROR not in str(response.data)
    assert "could not be completed" in str(response.data)
