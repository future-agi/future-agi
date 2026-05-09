from pathlib import Path

import jsonschema

from agent_playground.services.engine.runners.code_execution import (
    CodeExecutionError,
    CodeExecutionRunner,
    DockerCodeSandbox,
    SandboxResult,
)
from agent_playground.templates.code_execution import CODE_EXECUTION_TEMPLATE


class _FakeSandbox:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.result or SandboxResult(
            ok=True,
            value={"answer": 42},
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=7,
        )


def test_code_execution_runner_delegates_to_sandbox():
    sandbox = _FakeSandbox()
    runner = CodeExecutionRunner(sandbox=sandbox)

    outputs = runner.run(
        {
            "language": "python",
            "code": "result = {'answer': inputs['x']}",
            "timeout_ms": 1000,
            "memory_mb": 64,
        },
        {"x": 42},
        {"node_id": "node-1"},
    )

    assert outputs["result"]["ok"] is True
    assert outputs["result"]["value"] == {"answer": 42}
    assert outputs["result"]["metadata"] == {
        "language": "python",
        "runner": "python",
        "timed_out": False,
        "memory_mb": None,
    }
    assert sandbox.calls == [
        {
            "language": "python",
            "code": "result = {'answer': inputs['x']}",
            "inputs": {"x": 42},
            "timeout_ms": 1000,
            "memory_mb": 64,
        }
    ]


def test_code_execution_runner_preserves_nested_value_metadata():
    runner = CodeExecutionRunner(
        sandbox=_FakeSandbox(
            SandboxResult(
                ok=True,
                value={
                    "value": 8,
                    "metadata": {"row_count": 2, "source": "probe"},
                },
                stdout="rows 2",
                stderr="",
                exit_code=0,
                duration_ms=11,
            )
        )
    )

    outputs = runner.run(
        {"language": "python", "code": "result = {'value': 8}"},
        {"inputs": {"records": [{"x": 3}, {"x": 5}]}},
        {},
    )

    assert outputs["result"]["value"]["metadata"] == {
        "row_count": 2,
        "source": "probe",
    }
    assert outputs["result"]["stdout"] == "rows 2"
    assert outputs["result"]["duration_ms"] == 11


def test_code_execution_template_result_schema_matches_runner_payload():
    schema = CODE_EXECUTION_TEMPLATE["output_definition"][0]["data_schema"]
    payload = {
        "ok": False,
        "value": {"metadata": {"source": "probe"}},
        "stdout": "visible",
        "stderr": "traceback",
        "exit_code": 1,
        "duration_ms": 12,
        "error": "traceback",
        "metadata": {
            "language": "javascript",
            "runner": "node",
            "timed_out": False,
            "memory_mb": 128,
        },
    }

    jsonschema.validate(payload, schema)


def test_code_execution_runner_rejects_unsupported_language():
    runner = CodeExecutionRunner(sandbox=_FakeSandbox())

    try:
        runner.run({"language": "ruby", "code": "puts 1"}, {}, {})
    except ValueError as exc:
        assert "Invalid code execution config" in str(exc)
    else:
        raise AssertionError("Expected unsupported language to fail before sandbox.")


def test_code_execution_runner_rejects_invalid_resource_limits():
    runner = CodeExecutionRunner(sandbox=_FakeSandbox())

    try:
        runner.run(
            {"language": "python", "code": "result = 1", "memory_mb": 4096}, {}, {}
        )
    except ValueError as exc:
        assert "Invalid code execution config" in str(exc)
    else:
        raise AssertionError("Expected invalid memory limit to fail before sandbox.")


def test_code_execution_runner_rejects_non_object_config():
    runner = CodeExecutionRunner(sandbox=_FakeSandbox())

    try:
        runner.run(["not", "an", "object"], {}, {})
    except ValueError as exc:
        assert "config must be an object" in str(exc)
    else:
        raise AssertionError("Expected non-object config to fail before sandbox.")


def test_code_execution_runner_fails_graph_on_execution_error():
    runner = CodeExecutionRunner(
        sandbox=_FakeSandbox(
            SandboxResult(
                ok=False,
                value=None,
                stdout="",
                stderr="boom",
                exit_code=1,
                duration_ms=7,
                error="boom",
            )
        )
    )

    try:
        runner.run({"language": "python", "code": "raise Exception()"}, {}, {})
    except CodeExecutionError as exc:
        assert str(exc) == "boom"
        assert exc.outputs == {
            "result": {
                "ok": False,
                "value": None,
                "stdout": "",
                "stderr": "boom",
                "exit_code": 1,
                "duration_ms": 7,
                "error": "boom",
                "metadata": {
                    "language": "python",
                    "runner": "python",
                    "timed_out": False,
                    "memory_mb": None,
                },
            }
        }
    else:
        raise AssertionError("Expected graph execution failure.")


def test_code_execution_runner_can_return_structured_error_for_test_mode():
    runner = CodeExecutionRunner(
        sandbox=_FakeSandbox(
            SandboxResult(
                ok=False,
                value=None,
                stdout="",
                stderr="boom",
                exit_code=1,
                duration_ms=7,
                error="boom",
            )
        )
    )

    outputs = runner.run(
        {"language": "python", "code": "raise Exception()"},
        {},
        {"raise_on_error": False},
    )

    assert outputs["result"]["ok"] is False
    assert outputs["result"]["error"] == "boom"
    assert outputs["result"]["stdout"] == ""
    assert outputs["result"]["stderr"] == "boom"
    assert outputs["result"]["exit_code"] == 1
    assert outputs["result"]["duration_ms"] == 7
    assert outputs["result"]["metadata"]["timed_out"] is False


def test_docker_sandbox_uses_last_valid_result_marker():
    sandbox = DockerCodeSandbox()

    stdout, value, found_marker = sandbox._parse_stdout(
        "\n".join(
            [
                "__FAGI_CODE_RESULT__not-json",
                "visible",
                '__FAGI_CODE_RESULT__{"ok": true, "result": {"answer": 42}}',
            ]
        )
    )

    assert stdout == "__FAGI_CODE_RESULT__not-json\nvisible"
    assert value == {"answer": 42}
    assert found_marker is True


def test_docker_sandbox_fails_when_runner_marker_is_missing(monkeypatch):
    sandbox = DockerCodeSandbox()

    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(sandbox, "_ensure_image", lambda image: True)
    monkeypatch.setattr(
        sandbox,
        "_write_runner",
        lambda workdir, language, code: (Path("runner.py"), ["python"]),
    )
    monkeypatch.setattr(
        "agent_playground.services.engine.runners.code_execution.subprocess.run",
        lambda *args, **kwargs: type(
            "Result",
            (),
            {"returncode": 0, "stdout": "visible only", "stderr": ""},
        )(),
    )

    result = sandbox.run(
        language="python",
        code="result = None",
        inputs={},
        timeout_ms=1000,
        memory_mb=128,
    )

    assert result.ok is False
    assert result.stdout == "visible only"
    assert result.error == "Execution completed without runner result marker."
    assert result.language == "python"
    assert result.runner == "python"
    assert result.memory_mb == 128


def test_docker_sandbox_pulls_missing_image_before_execution(monkeypatch):
    sandbox = DockerCodeSandbox()
    commands = []

    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(sandbox, "_image_exists", lambda image: False)
    monkeypatch.setattr(
        sandbox,
        "_write_runner",
        lambda workdir, language, code: (Path("runner.py"), ["python"]),
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '__FAGI_CODE_RESULT__{"result": 1}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr(
        "agent_playground.services.engine.runners.code_execution.subprocess.run",
        fake_run,
    )

    result = sandbox.run(
        language="python",
        code="result = 1",
        inputs={},
        timeout_ms=1000,
        memory_mb=128,
    )

    assert result.ok is True
    assert commands[0] == ["docker", "pull", "python:3.12-alpine"]
    assert commands[1][0:3] == ["docker", "run", "--rm"]


def test_docker_sandbox_returns_structured_failure_when_docker_run_cannot_start(
    monkeypatch,
):
    sandbox = DockerCodeSandbox()

    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(sandbox, "_ensure_image", lambda image: True)
    monkeypatch.setattr(
        sandbox,
        "_write_runner",
        lambda workdir, language, code: (Path("runner.py"), ["python"]),
    )

    def raise_os_error(*args, **kwargs):
        raise OSError("docker disappeared")

    monkeypatch.setattr(
        "agent_playground.services.engine.runners.code_execution.subprocess.run",
        raise_os_error,
    )

    result = sandbox.run(
        language="python",
        code="result = 1",
        inputs={},
        timeout_ms=1000,
        memory_mb=128,
    )

    assert result.ok is False
    assert result.exit_code is None
    assert result.error == "SANDBOX_EXECUTION_FAILED: docker disappeared"
    assert result.timed_out is False


def test_docker_sandbox_marks_timeout_metadata(monkeypatch):
    sandbox = DockerCodeSandbox()

    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(sandbox, "_ensure_image", lambda image: True)
    monkeypatch.setattr(
        sandbox,
        "_write_runner",
        lambda workdir, language, code: (Path("runner.py"), ["python"]),
    )
    monkeypatch.setattr(sandbox, "_remove_container", lambda container_name: None)

    import subprocess

    monkeypatch.setattr(
        "agent_playground.services.engine.runners.code_execution.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args[0], timeout=1, output="partial")
        ),
    )

    result = sandbox.run(
        language="javascript",
        code="while (true) {}",
        inputs={},
        timeout_ms=1000,
        memory_mb=64,
    )

    assert result.ok is False
    assert result.timed_out is True
    assert result.language == "javascript"
    assert result.runner == "node"
    assert result.memory_mb == 64


def test_docker_sandbox_returns_structured_failure_for_unserializable_inputs():
    sandbox = DockerCodeSandbox()
    sandbox.available = lambda: True
    sandbox._ensure_image = lambda image: True

    result = sandbox.run(
        language="python",
        code="result = 1",
        inputs={"bad": {object()}},
        timeout_ms=1000,
        memory_mb=128,
    )

    assert result.ok is False
    assert result.error.startswith("INPUT_SERIALIZATION_FAILED:")
    assert result.memory_mb == 128


def test_docker_sandbox_fails_before_execution_when_image_cannot_be_pulled(monkeypatch):
    sandbox = DockerCodeSandbox()

    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(sandbox, "_image_exists", lambda image: False)
    monkeypatch.setattr(sandbox, "_pull_image", lambda image: False)

    result = sandbox.run(
        language="python",
        code="result = 1",
        inputs={},
        timeout_ms=1000,
        memory_mb=128,
    )

    assert result.ok is False
    assert result.error == (
        "SANDBOX_UNAVAILABLE: Docker image 'python:3.12-alpine' is not available "
        "and could not be pulled."
    )


def test_docker_sandbox_uses_safe_default_for_invalid_pull_timeout(monkeypatch):
    sandbox = DockerCodeSandbox()

    monkeypatch.setenv("FAGI_CODE_EXECUTION_IMAGE_PULL_TIMEOUT_MS", "not-an-int")

    assert sandbox._image_pull_timeout_ms() == 120000


def test_docker_sandbox_clamps_tiny_pull_timeout(monkeypatch):
    sandbox = DockerCodeSandbox()

    monkeypatch.setenv("FAGI_CODE_EXECUTION_IMAGE_PULL_TIMEOUT_MS", "1")

    assert sandbox._image_pull_timeout_ms() == 1000


def test_docker_sandbox_runs_with_least_privilege_flags(monkeypatch):
    sandbox = DockerCodeSandbox()
    commands = []

    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(sandbox, "_ensure_image", lambda image: True)
    monkeypatch.setattr(
        sandbox,
        "_write_runner",
        lambda workdir, language, code: (Path("runner.py"), ["python"]),
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '__FAGI_CODE_RESULT__{"result": 1}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr(
        "agent_playground.services.engine.runners.code_execution.subprocess.run",
        fake_run,
    )

    sandbox.run(
        language="python",
        code="result = 1",
        inputs={},
        timeout_ms=1000,
        memory_mb=128,
    )

    docker_cmd = commands[0]
    assert "--network" in docker_cmd and "none" in docker_cmd
    assert "--read-only" in docker_cmd
    assert "--cap-drop" in docker_cmd and "ALL" in docker_cmd
    assert "--security-opt" in docker_cmd and "no-new-privileges" in docker_cmd
    assert "--pids-limit" in docker_cmd and "64" in docker_cmd
    assert "--user" in docker_cmd and "65534:65534" in docker_cmd


def test_docker_sandbox_fails_closed_when_daemon_is_unavailable(monkeypatch):
    sandbox = DockerCodeSandbox()

    monkeypatch.setattr(
        "agent_playground.services.engine.runners.code_execution.shutil.which",
        lambda executable: "docker" if executable == "docker" else None,
    )
    monkeypatch.setattr(
        "agent_playground.services.engine.runners.code_execution.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 1})(),
    )

    result = sandbox.run(
        language="python",
        code="result = 1",
        inputs={},
        timeout_ms=1000,
        memory_mb=128,
    )

    assert result.ok is False
    assert (
        result.error
        == "SANDBOX_UNAVAILABLE: Docker is required for isolated code execution."
    )
