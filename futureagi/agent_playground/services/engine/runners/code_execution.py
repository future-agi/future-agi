"""
Docker-backed runner for the Agent Playground code_execution node.

The Django worker never evaluates user code. Execution is delegated to a short-lived
Docker container with no network, a read-only code mount, a tmpfs scratch area, a
memory cap, and an explicit timeout. If Docker is not available, the runner fails
closed with a structured SANDBOX_UNAVAILABLE result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema

from agent_playground.services.engine.node_runner import BaseNodeRunner, register_runner
from agent_playground.templates.code_execution import CODE_EXECUTION_TEMPLATE

_RESULT_MARKER = "__FAGI_CODE_RESULT__"
_CONFIG_SCHEMA = CODE_EXECUTION_TEMPLATE["config_schema"]
_SUPPORTED_LANGUAGES = frozenset(_CONFIG_SCHEMA["properties"]["language"]["enum"])
_CONFIG_DEFAULTS = {"timeout_ms": 5000, "memory_mb": 128}
_SANDBOX_UNAVAILABLE = (
    "SANDBOX_UNAVAILABLE: Docker is required for isolated code execution."
)
_IMAGE_UNAVAILABLE = "SANDBOX_UNAVAILABLE: Docker image '{image}' is not available and could not be pulled."
_IMAGE_PULL_TIMEOUT_MS = 120000


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    value: Any
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    language: str = "python"
    runner: str | None = "python"
    timed_out: bool = False
    memory_mb: int | None = None
    error: str | None = None


class CodeExecutionError(RuntimeError):
    """Execution failure that preserves the code node's structured result."""

    def __init__(self, result: SandboxResult) -> None:
        super().__init__(result.error or "Code execution failed.")
        self.result = result
        self.outputs = {"result": _result_payload(result)}


class DockerCodeSandbox:
    """Minimal Docker adapter with fail-closed capability probing."""

    def available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        try:
            completed = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def run(
        self,
        *,
        language: str,
        code: str,
        inputs: dict[str, Any],
        timeout_ms: int,
        memory_mb: int,
    ) -> SandboxResult:
        if language not in _SUPPORTED_LANGUAGES:
            return SandboxResult(
                ok=False,
                value=None,
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=0,
                language=language,
                runner=None,
                error=f"Unsupported language '{language}'.",
            )
        if not self.available():
            return SandboxResult(
                ok=False,
                value=None,
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=0,
                language=language,
                runner=self._runner_for_language(language),
                memory_mb=memory_mb,
                error=_SANDBOX_UNAVAILABLE,
            )
        image = self._image_for_language(language)
        if not self._ensure_image(image):
            return SandboxResult(
                ok=False,
                value=None,
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=0,
                language=language,
                runner=self._runner_for_language(language),
                memory_mb=memory_mb,
                error=_IMAGE_UNAVAILABLE.format(image=image),
            )

        with tempfile.TemporaryDirectory(prefix="fagi-code-") as tmp:
            workdir = Path(tmp)
            try:
                (workdir / "inputs.json").write_text(
                    json.dumps(inputs), encoding="utf-8"
                )
            except (OSError, TypeError) as exc:
                return SandboxResult(
                    ok=False,
                    value=None,
                    stdout="",
                    stderr="",
                    exit_code=None,
                    duration_ms=0,
                    language=language,
                    runner=self._runner_for_language(language),
                    memory_mb=memory_mb,
                    error=f"INPUT_SERIALIZATION_FAILED: {exc}",
                )
            script_path, command = self._write_runner(workdir, language, code)
            container_name = f"fagi-code-{uuid.uuid4().hex}"
            docker_cmd = [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "--network",
                "none",
                "--memory",
                f"{memory_mb}m",
                "--cpus",
                "1",
                "--pids-limit",
                "64",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "65534:65534",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=16m",
                "-v",
                f"{workdir.resolve()}:/workspace:ro",
                "-w",
                "/workspace",
                image,
                *command,
                f"/workspace/{script_path.name}",
            ]
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    docker_cmd,
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=timeout_ms / 1000,
                )
                duration_ms = int((time.monotonic() - started) * 1000)
            except subprocess.TimeoutExpired as exc:
                self._remove_container(container_name)
                return SandboxResult(
                    ok=False,
                    value=None,
                    stdout=exc.stdout or "",
                    stderr=exc.stderr or "",
                    exit_code=None,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    language=language,
                    runner=self._runner_for_language(language),
                    timed_out=True,
                    memory_mb=memory_mb,
                    error=f"TIMEOUT: execution exceeded {timeout_ms} ms.",
                )
            except OSError as exc:
                return SandboxResult(
                    ok=False,
                    value=None,
                    stdout="",
                    stderr=str(exc),
                    exit_code=None,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    language=language,
                    runner=self._runner_for_language(language),
                    memory_mb=memory_mb,
                    error=f"SANDBOX_EXECUTION_FAILED: {exc}",
                )

            stdout, value, found_marker = self._parse_stdout(completed.stdout)
            error = None if completed.returncode == 0 else completed.stderr.strip()
            if completed.returncode != 0 and not error:
                error = "Execution failed without stderr."
            if completed.returncode == 0 and not found_marker:
                error = "Execution completed without runner result marker."

            return SandboxResult(
                ok=completed.returncode == 0 and found_marker,
                value=value,
                stdout=stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
                duration_ms=duration_ms,
                language=language,
                runner=self._runner_for_language(language),
                memory_mb=memory_mb,
                error=error,
            )

    def _write_runner(
        self, workdir: Path, language: str, code: str
    ) -> tuple[Path, list[str]]:
        if language == "python":
            path = workdir / "runner.py"
            path.write_text(self._python_wrapper(code), encoding="utf-8")
            return path, ["python"]

        extension = "ts" if language == "typescript" else "mjs"
        path = workdir / f"runner.{extension}"
        path.write_text(self._node_wrapper(code), encoding="utf-8")
        if language == "typescript":
            return path, ["node", "--experimental-strip-types"]
        return path, ["node"]

    def _image_for_language(self, language: str) -> str:
        if language == "python":
            return os.environ.get(
                "FAGI_CODE_EXECUTION_PYTHON_IMAGE", "python:3.12-alpine"
            )
        return os.environ.get("FAGI_CODE_EXECUTION_NODE_IMAGE", "node:22-alpine")

    def _runner_for_language(self, language: str) -> str | None:
        if language == "python":
            return "python"
        if language in {"javascript", "typescript"}:
            return "node"
        return None

    def _ensure_image(self, image: str) -> bool:
        if self._image_exists(image):
            return True
        return self._pull_image(image)

    def _image_exists(self, image: str) -> bool:
        try:
            completed = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def _pull_image(self, image: str) -> bool:
        timeout_ms = self._image_pull_timeout_ms()
        try:
            completed = subprocess.run(
                ["docker", "pull", image],
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_ms / 1000,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def _image_pull_timeout_ms(self) -> int:
        raw_timeout = os.environ.get("FAGI_CODE_EXECUTION_IMAGE_PULL_TIMEOUT_MS")
        if raw_timeout is None:
            return _IMAGE_PULL_TIMEOUT_MS
        try:
            timeout_ms = int(raw_timeout)
        except ValueError:
            return _IMAGE_PULL_TIMEOUT_MS
        return max(1000, timeout_ms)

    def _python_wrapper(self, code: str) -> str:
        return "\n".join(
            [
                "import json",
                "import os",
                "import sys",
                "import traceback",
                "inputs = json.load(open('/workspace/inputs.json', encoding='utf-8'))",
                "scope = {'inputs': inputs, 'result': None}",
                "def _emit(payload, exit_code):",
                "    sys.__stdout__.write('__FAGI_CODE_RESULT__' + json.dumps(payload, default=str) + '\\n')",
                "    sys.__stdout__.flush()",
                "    os._exit(exit_code)",
                "try:",
                f"    exec(compile({code!r}, '<code_execution>', 'exec'), scope)",
                "    payload = {'ok': True, 'result': scope.get('result')}",
                "    _emit(payload, 0)",
                "except Exception:",
                "    error = traceback.format_exc()",
                "    sys.__stderr__.write(error)",
                "    sys.__stderr__.flush()",
                "    _emit({'ok': False, 'error': error}, 1)",
            ]
        )

    def _node_wrapper(self, code: str) -> str:
        return "\n".join(
            [
                "import { readFileSync } from 'node:fs';",
                "const writeStdout = process.stdout.write.bind(process.stdout);",
                "const writeStderr = process.stderr.write.bind(process.stderr);",
                "const inputs = JSON.parse(readFileSync('/workspace/inputs.json', 'utf8'));",
                "let result = null;",
                "const emit = (payload, exitCode) => {",
                "  writeStdout('__FAGI_CODE_RESULT__' + JSON.stringify(payload) + '\\n');",
                "  process.exit(exitCode);",
                "};",
                "try {",
                "  result = await (async () => {",
                code,
                "    return typeof result === 'undefined' ? null : result;",
                "  })();",
                "  emit({ ok: true, result }, 0);",
                "} catch (error) {",
                "  const message = String(error?.stack || error);",
                "  writeStderr(message + '\\n');",
                "  emit({ ok: false, error: message }, 1);",
                "}",
            ]
        )

    def _parse_stdout(self, stdout: str) -> tuple[str, Any, bool]:
        lines = stdout.splitlines()
        marker_index = None
        payload = None
        for index in range(len(lines) - 1, -1, -1):
            line = lines[index]
            if not line.startswith(_RESULT_MARKER):
                continue
            try:
                payload = json.loads(line[len(_RESULT_MARKER) :])
            except json.JSONDecodeError:
                continue
            marker_index = index
            break
        if marker_index is None:
            return stdout, None, False
        visible_stdout = "\n".join([*lines[:marker_index], *lines[marker_index + 1 :]])
        return visible_stdout, payload.get("result"), True

    def _remove_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                check=False,
                text=True,
            )
        except OSError:
            pass


class CodeExecutionRunner(BaseNodeRunner):
    """Run code_execution nodes through the configured sandbox adapter."""

    def __init__(self, sandbox: DockerCodeSandbox | None = None) -> None:
        self.sandbox = sandbox or DockerCodeSandbox()

    def run(
        self,
        config: dict[str, Any],
        inputs: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        config = _validate_config(config)

        result = self.sandbox.run(
            language=config["language"],
            code=config["code"],
            inputs=inputs,
            timeout_ms=int(config.get("timeout_ms") or 5000),
            memory_mb=int(config.get("memory_mb") or 128),
        )
        if not result.ok and execution_context.get("raise_on_error", True):
            raise CodeExecutionError(result)
        return {"result": _result_payload(result)}


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if config is not None and not isinstance(config, dict):
        raise ValueError("Invalid code execution config: config must be an object")
    normalized = {**_CONFIG_DEFAULTS, **(config or {})}
    try:
        validate_json_schema(normalized, _CONFIG_SCHEMA)
    except JsonSchemaValidationError as exc:
        raise ValueError(f"Invalid code execution config: {exc.message}") from exc
    return normalized


def _result_payload(result: SandboxResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "value": result.value,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "error": result.error,
        "metadata": {
            "language": result.language,
            "runner": result.runner,
            "timed_out": result.timed_out,
            "memory_mb": result.memory_mb,
        },
    }


register_runner("code_execution", CodeExecutionRunner())
