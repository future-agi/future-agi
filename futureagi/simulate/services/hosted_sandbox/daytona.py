from __future__ import annotations

import shlex
from collections.abc import Callable
from typing import Any

from django.conf import settings

from .base import (
    SandboxCommandRequest,
    SandboxConflictError,
    SandboxLaunchSpec,
    SandboxNotFoundError,
    SandboxPreview,
    SandboxProviderConfigurationError,
    SandboxProviderError,
    SandboxProviderUnavailableError,
    SandboxRuntimeProvider,
)


def _translate_error(exc: Exception) -> SandboxProviderError | None:
    from daytona import (
        DaytonaConflictError,
        DaytonaError,
        DaytonaNotFoundError,
    )

    status_code = getattr(exc, "status_code", None)
    if isinstance(exc, DaytonaNotFoundError):
        return SandboxNotFoundError(str(exc), status_code=status_code)
    if isinstance(exc, DaytonaConflictError):
        return SandboxConflictError(str(exc), status_code=status_code)
    if isinstance(exc, DaytonaError):
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


class DaytonaToolboxProxy:
    def __init__(self, toolbox: Any) -> None:
        self._toolbox = toolbox

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._toolbox, name)
        if not callable(value):
            return value

        def translated(*args: Any, **kwargs: Any) -> Any:
            return _call(value, *args, **kwargs)

        return translated


class DaytonaProcessProxy(DaytonaToolboxProxy):
    def execute_session_command(
        self, session_id: str, request: SandboxCommandRequest
    ) -> Any:
        from daytona import SessionExecuteRequest

        command = request.command
        if request.env:
            exports = " ".join(
                f"{name}={shlex.quote(value)}"
                for name, value in sorted(request.env.items())
            )
            command = f"export {exports} && {command}"
        daytona_request = SessionExecuteRequest(
            command=command,
            run_async=request.run_async,
            suppress_input_echo=request.suppress_input_echo,
        )
        return _call(
            self._toolbox.execute_session_command,
            session_id,
            daytona_request,
        )


class DaytonaSandbox:
    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox
        self.id = str(sandbox.id)
        self.fs = DaytonaToolboxProxy(sandbox.fs)
        self.process = DaytonaProcessProxy(sandbox.process)

    def delete(self) -> None:
        _call(self._sandbox.delete)

    def create_signed_preview_url(self, port: int, *, expires_in_seconds: int) -> Any:
        return _call(
            self._sandbox.create_signed_preview_url,
            port,
            expires_in_seconds=expires_in_seconds,
        )


class DaytonaSandboxRuntimeProvider(SandboxRuntimeProvider):
    supports_adjustments = False
    name = "daytona"
    max_egress_domains = 20
    create_timeout_seconds = 300
    supports_public_ingress = True

    def __init__(self) -> None:
        try:
            from daytona import Daytona, DaytonaConfig
        except ImportError as exc:
            # The Daytona SDK is the optional `sandbox` extra: the default
            # backend image does not ship it.
            raise SandboxProviderUnavailableError("Daytona", "daytona") from exc

        api_key = getattr(settings, "DAYTONA_API_KEY", "")
        self.snapshot = getattr(settings, "ALK_DAYTONA_SNAPSHOT", "")
        self.dockerfile = getattr(settings, "ALK_DAYTONA_DOCKERFILE", "")
        if not api_key or not (self.snapshot or self.dockerfile):
            raise SandboxProviderConfigurationError(
                "DAYTONA_API_KEY and either ALK_DAYTONA_SNAPSHOT or "
                "ALK_DAYTONA_DOCKERFILE are required"
            )
        self.supports_adjustments = bool(self.dockerfile)
        self.create_timeout_seconds = 1200 if self.dockerfile else 300
        self.runtime_name = (
            "direct-image-adjustments-v1" if self.dockerfile else self.snapshot
        )
        self.runtime_digest = getattr(settings, "ALK_DAYTONA_SNAPSHOT_DIGEST", "")
        self._client = Daytona(
            DaytonaConfig(
                api_key=api_key,
                api_url=getattr(settings, "DAYTONA_API_URL", None),
                target=getattr(settings, "DAYTONA_TARGET", None),
                organization_id=getattr(settings, "DAYTONA_ORGANIZATION_ID", None),
            )
        )

    def create(self, spec: SandboxLaunchSpec, *, timeout: int) -> DaytonaSandbox:
        from daytona import (
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
            Image,
            Resources,
        )

        ttl_minutes = max(1, (spec.ttl_seconds + 59) // 60)
        domain_allow_list = (
            None if spec.unrestricted_egress else ",".join(spec.allowed_domains) or None
        )
        common = {
            "language": "python",
            "os_user": spec.os_user,
            "labels": spec.labels,
            "network_block_all": (
                False if spec.unrestricted_egress else not domain_allow_list
            ),
            "domain_allow_list": domain_allow_list,
            "ephemeral": True,
            "ttl_minutes": ttl_minutes,
            "auto_delete_interval": ttl_minutes,
        }
        if self.dockerfile:
            params = CreateSandboxFromImageParams(
                image=Image.from_dockerfile(self.dockerfile),
                resources=Resources(
                    cpu=spec.cpu_units,
                    memory=max(4, (spec.memory_mb + 1023) // 1024),
                    disk=spec.disk_gb,
                ),
                **common,
            )
        else:
            params = CreateSandboxFromSnapshotParams(snapshot=self.snapshot, **common)
        sandbox = _call(self._client.create, params, timeout=timeout)
        return DaytonaSandbox(sandbox)

    def get(
        self, sandbox_id: str, *, request_timeout: int | None = None
    ) -> DaytonaSandbox:
        sandbox = _call(
            self._client.get,
            sandbox_id,
            request_timeout=request_timeout,
        )
        return DaytonaSandbox(sandbox)

    def delete(self, sandbox: DaytonaSandbox, *, timeout: int, wait: bool) -> bool:
        _call(
            self._client.delete,
            sandbox._sandbox,
            timeout=timeout,
            wait=wait,
        )
        return True

    def create_preview_url(
        self, sandbox: DaytonaSandbox, port: int, *, expires_in_seconds: int
    ) -> SandboxPreview:
        preview = sandbox.create_signed_preview_url(
            port, expires_in_seconds=expires_in_seconds
        )
        return SandboxPreview(url=str(getattr(preview, "url", "") or ""))
