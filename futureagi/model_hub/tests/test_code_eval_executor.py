"""
Tests for how code evals reach the code-executor service.

The executor's answer is final: an HTTP error, a timeout or an unusable
response comes back as an eval error. Code runs in the worker only when the
executor cannot be reached at all, and only on a self-hosted install that sets
CODE_EXECUTOR_LOCAL_FALLBACK. Cloud deployments never run it locally.

urlopen is replaced at the urllib boundary; subprocess.run stands in for the
local runner so each test can tell whether it was started.
"""

import errno
import json
import socket
import subprocess
import urllib.error
import urllib.request
from email.message import Message
from io import BytesIO
from unittest import mock

import pytest
from structlog.testing import capture_logs

from agentic_eval.core_evals.fi_utils import sandbox

PYTHON_CODE = "def evaluate(output, **kwargs):\n    return True\n"
JS_CODE = "function evaluate(inputData) { return true; }\n"

LANGUAGES = [
    pytest.param(sandbox.execute_sandboxed_python, PYTHON_CODE, id="python"),
    pytest.param(sandbox.execute_sandboxed_javascript, JS_CODE, id="javascript"),
]

LOCAL_RESULT = {"status": "success", "data": {"result": 1.0, "reason": "local"}}
EXECUTOR_RESULT = {"status": "success", "data": {"result": 1.0, "reason": "executor"}}


@pytest.fixture
def local_run(monkeypatch):
    """Record whether the local runner was started, without starting it."""
    run = mock.Mock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(LOCAL_RESULT), stderr=""
        )
    )
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    # The JavaScript runner looks for a node binary before starting it.
    real_isfile = sandbox.os.path.isfile
    monkeypatch.setattr(
        sandbox.os.path,
        "isfile",
        lambda path: path == "/usr/local/bin/node" or real_isfile(path),
    )
    return run


@pytest.fixture
def urlopen(monkeypatch):
    opener = mock.MagicMock()
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    return opener


@pytest.fixture
def self_hosted(settings):
    settings.CLOUD_DEPLOYMENT = ""
    settings.CODE_EXECUTOR_LOCAL_FALLBACK = False
    return settings


def _respond(urlopen, body: bytes):
    response = urlopen.return_value.__enter__.return_value
    response.read.side_effect = lambda amt=-1: body if amt < 0 else body[:amt]


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        sandbox.CODE_EXECUTOR_URL, code, "error", Message(), BytesIO(b"")
    )


def _events(logs, level):
    return [entry["event"] for entry in logs if entry["log_level"] == level]


def _run(execute, code):
    with capture_logs() as logs:
        result = execute(code, {"output": "x"}, timeout=5)
    return result, logs


@pytest.mark.parametrize("execute,code", LANGUAGES)
class TestExecutorAnswers:
    def test_executor_result_is_returned(
        self, execute, code, urlopen, local_run, self_hosted
    ):
        _respond(urlopen, json.dumps(EXECUTOR_RESULT).encode())

        result, _ = _run(execute, code)

        local_run.assert_not_called()
        assert result == EXECUTOR_RESULT

    @pytest.mark.parametrize("status", [400, 500, 502, 503])
    def test_http_error_is_an_eval_error(
        self, execute, code, status, urlopen, local_run, self_hosted
    ):
        self_hosted.CODE_EXECUTOR_LOCAL_FALLBACK = True
        urlopen.side_effect = _http_error(status)

        result, logs = _run(execute, code)

        local_run.assert_not_called()
        assert result == {
            "status": "error",
            "data": f"Code executor error: HTTP {status}",
        }
        assert "code_executor_service_error" in _events(logs, "warning")

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(TimeoutError("timed out"), id="read"),
            pytest.param(
                urllib.error.URLError(TimeoutError("timed out")), id="connect"
            ),
        ],
    )
    def test_timeout_is_an_eval_error(
        self, execute, code, failure, urlopen, local_run, self_hosted
    ):
        self_hosted.CODE_EXECUTOR_LOCAL_FALLBACK = True
        urlopen.side_effect = failure

        result, logs = _run(execute, code)

        local_run.assert_not_called()
        assert result["status"] == "error"
        assert result["data"].startswith("Code executor error:")
        assert "code_executor_service_error" in _events(logs, "warning")

    def test_dropped_connection_is_an_eval_error(
        self, execute, code, urlopen, local_run, self_hosted
    ):
        self_hosted.CODE_EXECUTOR_LOCAL_FALLBACK = True
        urlopen.side_effect = ConnectionResetError(
            errno.ECONNRESET, "Connection reset by peer"
        )

        result, _ = _run(execute, code)

        local_run.assert_not_called()
        assert result == {
            "status": "error",
            "data": "Code executor error: request failed",
        }

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(b"<html>Bad Gateway</html>", id="not-json"),
            pytest.param(b"\xff\xfe", id="not-utf8"),
            pytest.param(b"[1, 2]", id="not-an-object"),
            pytest.param(b'{"data": 1}', id="no-status"),
        ],
    )
    def test_invalid_response_is_an_eval_error(
        self, execute, code, body, urlopen, local_run, self_hosted
    ):
        self_hosted.CODE_EXECUTOR_LOCAL_FALLBACK = True
        _respond(urlopen, body)

        result, logs = _run(execute, code)

        local_run.assert_not_called()
        assert result == {
            "status": "error",
            "data": "Code executor error: invalid response",
        }
        assert "code_executor_service_error" in _events(logs, "warning")

    def test_oversize_response_is_an_eval_error(
        self, execute, code, urlopen, local_run, self_hosted
    ):
        self_hosted.CODE_EXECUTOR_LOCAL_FALLBACK = True
        padding = "x" * (sandbox.MAX_EXECUTOR_RESPONSE_BYTES + 1)
        _respond(urlopen, json.dumps({"status": "success", "data": padding}).encode())

        result, _ = _run(execute, code)

        local_run.assert_not_called()
        assert result == {
            "status": "error",
            "data": "Code executor error: response too large",
        }


UNREACHABLE = [
    pytest.param(
        urllib.error.URLError(
            ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
        ),
        id="refused",
    ),
    pytest.param(
        urllib.error.URLError(
            socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        ),
        id="dns",
    ),
    pytest.param(
        urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host")),
        id="no-route",
    ),
]


@pytest.mark.parametrize("execute,code", LANGUAGES)
@pytest.mark.parametrize("failure", UNREACHABLE)
class TestExecutorUnreachable:
    def test_without_opt_in_returns_unavailable(
        self, execute, code, failure, urlopen, local_run, self_hosted
    ):
        urlopen.side_effect = failure

        result, logs = _run(execute, code)

        local_run.assert_not_called()
        assert result == {
            "status": "error",
            "data": sandbox.EXECUTOR_UNAVAILABLE_MESSAGE,
        }
        assert "code_executor_service_unavailable" in _events(logs, "warning")

    def test_with_opt_in_runs_locally(
        self, execute, code, failure, urlopen, local_run, self_hosted
    ):
        self_hosted.CODE_EXECUTOR_LOCAL_FALLBACK = True
        urlopen.side_effect = failure

        result, logs = _run(execute, code)

        local_run.assert_called_once()
        assert result == LOCAL_RESULT
        warnings = _events(logs, "warning")
        assert "code_executor_service_unavailable" in warnings
        assert "code_executor_local_fallback" in warnings

    @pytest.mark.parametrize("deployment", ["US", "EU", "DEV", " us "])
    def test_cloud_deployment_refuses_opt_in(
        self, execute, code, failure, deployment, urlopen, local_run, self_hosted
    ):
        self_hosted.CODE_EXECUTOR_LOCAL_FALLBACK = True
        self_hosted.CLOUD_DEPLOYMENT = deployment
        urlopen.side_effect = failure

        result, logs = _run(execute, code)

        local_run.assert_not_called()
        assert result == {
            "status": "error",
            "data": sandbox.EXECUTOR_UNAVAILABLE_MESSAGE,
        }
        assert "code_executor_local_fallback_refused" in _events(logs, "warning")


@pytest.mark.parametrize("execute,code", LANGUAGES)
def test_unset_opt_in_means_no_local_run(
    execute, code, urlopen, local_run, self_hosted
):
    del self_hosted.CODE_EXECUTOR_LOCAL_FALLBACK
    urlopen.side_effect = UNREACHABLE[0].values[0]

    result, _ = _run(execute, code)

    local_run.assert_not_called()
    assert result == {"status": "error", "data": sandbox.EXECUTOR_UNAVAILABLE_MESSAGE}
