from __future__ import annotations

import shlex
import uuid
from collections.abc import Callable
from typing import Any

from django.conf import settings

from .base import (
    SandboxCommand,
    SandboxCommandLogs,
    SandboxCommandRequest,
    SandboxExecResult,
    SandboxLaunchSpec,
    SandboxNotFoundError,
    SandboxPreview,
    SandboxProviderConfigurationError,
    SandboxProviderError,
    SandboxRuntimeProvider,
)


def _translate_error(exc: Exception) -> SandboxProviderError | None:
    from e2b import SandboxException, SandboxNotFoundException

    status_code = getattr(exc, "status_code", None)
    if isinstance(exc, SandboxNotFoundException):
        return SandboxNotFoundError(str(exc), status_code=status_code)
    if isinstance(exc, SandboxException):
        return SandboxProviderError(str(exc), status_code=status_code)
    return None


def _call(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return operation(*args, **kwargs)
    except Exception as exc:
        translated = _translate_error(exc)
        if translated is None:
            raise
        raise translated from exc


class E2BFilesystem:
    def __init__(self, sandbox: Any, os_user: str) -> None:
        self._sandbox = sandbox
        self._os_user = os_user

    def upload_file(self, data: bytes, path: str) -> None:
        _call(self._sandbox.files.write, path, data, user=self._os_user)

    def download_file(self, path: str, timeout: int | None = None) -> bytes:
        body = _call(
            self._sandbox.files.read,
            path,
            format="bytes",
            request_timeout=timeout,
            user=self._os_user,
        )
        return bytes(body)


class E2BProcess:
    _COMMAND_DIR = "/run/futureagi/provider-commands"

    def __init__(self, sandbox: Any, os_user: str) -> None:
        self._sandbox = sandbox
        self._os_user = os_user

    def exec(
        self,
        command: str,
        *,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxExecResult:
        marker = f"__FUTUREAGI_EXIT_{uuid.uuid4().hex}__"
        wrapped = (
            f"set +e; ( {command} ); _futureagi_code=$?; "
            f"printf '\\n{marker}%s\\n' \"$_futureagi_code\"; exit 0"
        )
        result = _call(
            self._sandbox.commands.run,
            wrapped,
            timeout=timeout or 60,
            envs=env,
            user=self._os_user,
        )
        stdout = str(getattr(result, "stdout", "") or "")
        stderr = str(getattr(result, "stderr", "") or "")
        exit_code = 1
        cleaned: list[str] = []
        for line in stdout.splitlines():
            if line.startswith(marker):
                try:
                    exit_code = int(line.removeprefix(marker))
                except ValueError:
                    exit_code = 1
            else:
                cleaned.append(line)
        clean_stdout = "\n".join(cleaned)
        return SandboxExecResult(
            exit_code=exit_code,
            result="\n".join(part for part in (clean_stdout, stderr) if part),
            stdout=clean_stdout,
            stderr=stderr,
        )

    def create_session(self, _session_id: str) -> None:
        return None

    def execute_session_command(
        self, _session_id: str, request: SandboxCommandRequest
    ) -> SandboxCommand:
        if not request.run_async:
            result = self.exec(request.command, env=request.env)
            return SandboxCommand(
                cmd_id=uuid.uuid4().hex,
                exit_code=result.exit_code,
                status="finished",
            )
        command_token = uuid.uuid4().hex
        log_path = f"{self._COMMAND_DIR}/{command_token}.log"
        exit_path = f"{self._COMMAND_DIR}/{command_token}.exit"
        wrapped = (
            f"mkdir -p {shlex.quote(self._COMMAND_DIR)}; set +e; "
            f"( {request.command} ) > {shlex.quote(log_path)} 2>&1; "
            "_futureagi_code=$?; "
            f"printf '%s' \"$_futureagi_code\" > {shlex.quote(exit_path)}; "
            'exit "$_futureagi_code"'
        )
        handle = _call(
            self._sandbox.commands.run,
            wrapped,
            background=True,
            timeout=0,
            envs=request.env,
            user=self._os_user,
        )
        return SandboxCommand(cmd_id=f"{handle.pid}:{command_token}", status="running")

    @staticmethod
    def _command_parts(command_id: str) -> tuple[int, str]:
        pid, separator, token = str(command_id).partition(":")
        if not separator or not token:
            raise ValueError("invalid E2B command identifier")
        return int(pid), token

    def get_session_command(
        self,
        _session_id: str,
        command_id: str,
        request_timeout: int | None = None,
    ) -> SandboxCommand:
        pid, token = self._command_parts(command_id)
        exit_path = f"{self._COMMAND_DIR}/{token}.exit"
        try:
            value = self._sandbox.files.read(
                exit_path,
                request_timeout=request_timeout,
                user=self._os_user,
            )
        except Exception as exc:
            if _is_file_not_found(exc):
                pass
            else:
                translated = _translate_error(exc)
                if translated is None:
                    raise
                raise translated from exc
        else:
            return SandboxCommand(
                cmd_id=command_id,
                exit_code=int(str(value).strip()),
                status="finished",
            )
        processes = _call(self._sandbox.commands.list, request_timeout=request_timeout)
        running = any(int(getattr(process, "pid", -1)) == pid for process in processes)
        if not running:
            raise SandboxProviderError(
                f"E2B command {command_id!r} disappeared without an exit marker"
            )
        return SandboxCommand(
            cmd_id=command_id,
            exit_code=None,
            status="running",
        )

    def get_session_command_logs(
        self,
        _session_id: str,
        command_id: str,
        request_timeout: int | None = None,
    ) -> SandboxCommandLogs:
        _, token = self._command_parts(command_id)
        log_path = f"{self._COMMAND_DIR}/{token}.log"
        try:
            output = self._sandbox.files.read(
                log_path,
                request_timeout=request_timeout,
                user=self._os_user,
            )
        except Exception as exc:
            if _is_file_not_found(exc):
                output = ""
            else:
                translated = _translate_error(exc)
                if translated is None:
                    raise
                raise translated from exc
        return SandboxCommandLogs(output=str(output or ""))


class E2BSandbox:
    def __init__(self, sandbox: Any, os_user: str) -> None:
        self._sandbox = sandbox
        self.id = str(sandbox.sandbox_id)
        self.fs = E2BFilesystem(sandbox, os_user)
        self.process = E2BProcess(sandbox, os_user)

    def delete(self) -> None:
        _call(self._sandbox.kill)


class E2BSandboxRuntimeProvider(SandboxRuntimeProvider):
    name = "e2b"
    max_egress_domains = None
    supports_adjustments = True
    supports_public_ingress = True

    def renew_ttl(self, sandbox: E2BSandbox, ttl_seconds: int) -> None:
        self.validate_requested_resources(*self.configured_resources(), ttl_seconds)
        _call(sandbox._sandbox.set_timeout, ttl_seconds)

    def __init__(self) -> None:
        self.api_key = str(getattr(settings, "E2B_API_KEY", "") or "")
        self.runtime_name = str(
            getattr(settings, "ALK_E2B_TEMPLATE_REFERENCE", "") or ""
        )
        self.runtime_digest = str(
            getattr(settings, "ALK_E2B_TEMPLATE_BUILD_ID", "") or ""
        )
        if not self.api_key or not self.runtime_name or not self.runtime_digest:
            raise SandboxProviderConfigurationError(
                "E2B_API_KEY, ALK_E2B_TEMPLATE_REFERENCE, and "
                "ALK_E2B_TEMPLATE_BUILD_ID are required"
            )
        if not self.runtime_name.endswith(f":{self.runtime_digest}"):
            raise SandboxProviderConfigurationError(
                "ALK_E2B_TEMPLATE_REFERENCE must pin ALK_E2B_TEMPLATE_BUILD_ID"
            )
        self.template_reference = self.runtime_name
        (
            self.template_cpu_units,
            self.template_memory_mb,
            self.template_disk_gb,
        ) = self.configured_resources()

    @staticmethod
    def configured_resources() -> tuple[int, int, int]:
        return (
            int(getattr(settings, "ALK_E2B_TEMPLATE_CPU_UNITS", 4)),
            int(getattr(settings, "ALK_E2B_TEMPLATE_MEMORY_MB", 8192)),
            int(getattr(settings, "ALK_E2B_TEMPLATE_DISK_GB", 10)),
        )

    @classmethod
    def validate_requested_resources(
        cls,
        cpu_units: int,
        memory_mb: int,
        disk_gb: int,
        ttl_seconds: int | None = None,
    ) -> None:
        template_cpu_units, template_memory_mb, template_disk_gb = (
            cls.configured_resources()
        )
        template_name = str(getattr(settings, "ALK_E2B_TEMPLATE_REFERENCE", "") or "")
        if (
            cpu_units > template_cpu_units
            or memory_mb > template_memory_mb
            or disk_gb > template_disk_gb
        ):
            raise SandboxProviderConfigurationError(
                f"E2B template {template_name!r} provides "
                f"{template_cpu_units} vCPU/{template_memory_mb} MiB/{template_disk_gb} GiB, "
                f"but the job requires {cpu_units} vCPU/{memory_mb} MiB/{disk_gb} GiB"
            )
        max_ttl_seconds = int(getattr(settings, "ALK_E2B_MAX_TTL_SECONDS", 0))
        if max_ttl_seconds <= 0:
            raise SandboxProviderConfigurationError(
                "ALK_E2B_MAX_TTL_SECONDS must match the selected E2B plan limit"
            )
        if ttl_seconds is not None and ttl_seconds > max_ttl_seconds:
            raise SandboxProviderConfigurationError(
                f"E2B is configured for at most {max_ttl_seconds} seconds of continuous "
                f"runtime, but the job requires a {ttl_seconds}-second sandbox lifetime"
            )

    def create(self, spec: SandboxLaunchSpec, *, timeout: int) -> E2BSandbox:
        from e2b import Sandbox

        self.validate_requested_resources(
            spec.cpu_units,
            spec.memory_mb,
            spec.disk_gb,
            spec.ttl_seconds,
        )
        network: dict[str, Any] = {"allow_public_traffic": False}
        allow_out = [*spec.allowed_domains, *spec.allowed_cidrs]
        if not spec.unrestricted_egress:
            network["deny_out"] = ["0.0.0.0/0"]
            if allow_out:
                network["allow_out"] = allow_out
        sandbox = _call(
            Sandbox.create,
            template=self.template_reference,
            timeout=spec.ttl_seconds,
            secure=True,
            allow_internet_access=True,
            network=network,
            metadata=spec.labels,
            api_key=self.api_key,
            request_timeout=timeout,
        )
        try:
            initialized = _call(
                sandbox.commands.run,
                "install -d -o svc-control -g svc-control -m 0700 "
                "/run/futureagi /run/user/2000 && "
                "rm -f /usr/local/bin/python && "
                "printf '#!/bin/sh\\nexec /opt/alk-venv/bin/python \"$@\"\\n' "
                "> /usr/local/bin/python && chmod 0755 /usr/local/bin/python && "
                "ln -sfn /opt/alk-venv/bin/pip /usr/local/bin/pip && "
                "if [ -x /opt/alk-venv/bin/uv ]; then "
                "ln -sfn /opt/alk-venv/bin/uv /usr/local/bin/uv; fi && "
                "if [ -x /opt/alk-venv/bin/uvx ]; then "
                "ln -sfn /opt/alk-venv/bin/uvx /usr/local/bin/uvx; fi && "
                "test -x /usr/local/bin/uv && test -x /usr/local/bin/uvx",
                timeout=min(timeout, 60),
                user="root",
            )
            if getattr(initialized, "exit_code", None) != 0:
                raise SandboxProviderError(
                    "E2B sandbox runtime initialization failed",
                    status_code=502,
                )
        except Exception:
            try:
                _call(sandbox.kill)
            except Exception:
                pass
            raise
        return E2BSandbox(sandbox, spec.os_user)

    def get(self, sandbox_id: str, *, request_timeout: int | None = None) -> E2BSandbox:
        from e2b import Sandbox

        sandbox = _call(
            Sandbox.connect,
            sandbox_id,
            api_key=self.api_key,
            request_timeout=request_timeout,
        )
        return E2BSandbox(
            sandbox,
            str(getattr(settings, "ALK_HOSTED_SANDBOX_OS_USER", "svc-control")),
        )

    def delete(self, sandbox: E2BSandbox, *, timeout: int, wait: bool) -> bool:
        del timeout, wait
        return bool(_call(sandbox._sandbox.kill))

    def create_preview_url(
        self, sandbox: E2BSandbox, port: int, *, expires_in_seconds: int
    ) -> SandboxPreview:
        from simulate.services.hosted_sandbox.ingress_relay import mint_ingress_url

        return SandboxPreview(
            url=mint_ingress_url(
                sandbox_id=sandbox._sandbox.sandbox_id,
                port=port,
                expires_in_seconds=expires_in_seconds,
            )
        )


def _is_file_not_found(exc: Exception) -> bool:
    from e2b import FileNotFoundException

    return isinstance(exc, FileNotFoundException)
