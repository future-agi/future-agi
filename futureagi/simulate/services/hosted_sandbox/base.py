from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SandboxLaunchSpec:
    cpu_units: int
    memory_mb: int
    disk_gb: int
    ttl_seconds: int
    os_user: str
    labels: dict[str, str]
    allowed_domains: tuple[str, ...] = ()
    allowed_cidrs: tuple[str, ...] = ()
    unrestricted_egress: bool = False
    runtime_name: str | None = None


@dataclass(frozen=True)
class SandboxCommandRequest:
    command: str
    env: dict[str, str] | None = None
    run_async: bool = False
    suppress_input_echo: bool = False


@dataclass(frozen=True)
class SandboxExecResult:
    exit_code: int
    result: str
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class SandboxCommand:
    cmd_id: str
    exit_code: int | None = None
    status: str | None = None


@dataclass(frozen=True)
class SandboxCommandLogs:
    output: str
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class SandboxPreview:
    url: str
    # Headers needed by a platform relay. These are never returned to the guest.
    headers: Mapping[str, str] = field(default_factory=dict)


class SandboxProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SandboxNotFoundError(SandboxProviderError):
    pass


class SandboxConflictError(SandboxProviderError):
    pass


class SandboxProviderConfigurationError(SandboxProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422)


class SandboxRuntimeProvider(ABC):
    name: str
    runtime_name: str
    runtime_digest: str
    max_egress_domains: int | None = None
    create_timeout_seconds = 300
    supports_adjustments = False
    supports_public_ingress = False

    @abstractmethod
    def create(self, spec: SandboxLaunchSpec, *, timeout: int) -> Any:
        raise NotImplementedError

    @abstractmethod
    def get(self, sandbox_id: str, *, request_timeout: int | None = None) -> Any:
        raise NotImplementedError

    @abstractmethod
    def delete(self, sandbox: Any, *, timeout: int, wait: bool) -> bool:
        raise NotImplementedError

    @abstractmethod
    def create_preview_url(
        self, sandbox: Any, port: int, *, expires_in_seconds: int
    ) -> SandboxPreview:
        raise NotImplementedError
