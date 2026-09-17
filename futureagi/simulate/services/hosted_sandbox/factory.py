from __future__ import annotations

from django.conf import settings

from .base import SandboxProviderConfigurationError, SandboxRuntimeProvider
from .daytona import DaytonaSandboxRuntimeProvider
from .e2b import E2BSandboxRuntimeProvider


def sandbox_provider_name() -> str:
    name = str(
        getattr(settings, "HOSTED_SANDBOX_PROVIDER", "daytona") or "daytona"
    ).lower()
    if name not in {"daytona", "e2b"}:
        raise SandboxProviderConfigurationError(
            f"unsupported HOSTED_SANDBOX_PROVIDER {name!r}"
        )
    return name


def get_sandbox_provider() -> SandboxRuntimeProvider:
    if sandbox_provider_name() == "e2b":
        return E2BSandboxRuntimeProvider()
    return DaytonaSandboxRuntimeProvider()


def sandbox_runtime_reference() -> tuple[str, str]:
    if sandbox_provider_name() == "e2b":
        return (
            str(getattr(settings, "ALK_E2B_TEMPLATE_REFERENCE", "") or ""),
            str(getattr(settings, "ALK_E2B_TEMPLATE_BUILD_ID", "") or ""),
        )
    dockerfile = str(getattr(settings, "ALK_DAYTONA_DOCKERFILE", "") or "")
    return (
        "direct-image-adjustments-v1"
        if dockerfile
        else str(getattr(settings, "ALK_DAYTONA_SNAPSHOT", "") or ""),
        str(getattr(settings, "ALK_DAYTONA_SNAPSHOT_DIGEST", "") or ""),
    )


def sandbox_egress_domain_limit() -> int | None:
    return 20 if sandbox_provider_name() == "daytona" else None


def validate_sandbox_requirements(
    cpu_units: int, memory_mb: int, disk_gb: int, ttl_seconds: int
) -> None:
    if sandbox_provider_name() == "e2b":
        E2BSandboxRuntimeProvider.validate_requested_resources(
            cpu_units, memory_mb, disk_gb, ttl_seconds
        )
