"""Hosted harness pieces the default backend image leaves out.

The Daytona and E2B SDKs are the optional `sandbox` extra and git is the
WITH_GIT build arg (futureagi/Dockerfile.oss). Without them the gateway must
fail with a typed, non-retryable 501 instead of an unhandled ImportError or a
retryable 503 that can never succeed.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import (
    HostedHarnessGateway,
    HostedSourceAcquirer,
)
from simulate.services.hosted_sandbox import (
    DaytonaSandboxRuntimeProvider,
    E2BSandboxRuntimeProvider,
    SandboxProviderConfigurationError,
    SandboxProviderUnavailableError,
)


@pytest.mark.parametrize(
    ("module", "provider_cls"),
    [("daytona", DaytonaSandboxRuntimeProvider), ("e2b", E2BSandboxRuntimeProvider)],
)
def test_provider_without_sdk_raises_unavailable(monkeypatch, module, provider_cls):
    # A None entry makes `import <module>` raise ModuleNotFoundError.
    monkeypatch.setitem(sys.modules, module, None)

    with pytest.raises(SandboxProviderUnavailableError) as raised:
        provider_cls()

    assert raised.value.module == module
    assert "EXTRAS=sandbox" in str(raised.value)
    # Existing handlers of configuration errors keep catching it.
    assert isinstance(raised.value, SandboxProviderConfigurationError)


@pytest.mark.parametrize("provider", ["daytona", "e2b"])
def test_gateway_maps_missing_sdk_to_non_retryable_501(settings, monkeypatch, provider):
    settings.HOSTED_SANDBOX_PROVIDER = provider
    monkeypatch.setitem(sys.modules, provider, None)

    with pytest.raises(HostedHarnessError) as raised:
        HostedHarnessGateway()

    assert raised.value.code == "sandbox_sdk_missing"
    assert raised.value.status_code == 501
    assert raised.value.retryable is False


def test_gateway_still_maps_missing_configuration_to_503(settings, monkeypatch):
    settings.HOSTED_SANDBOX_PROVIDER = "e2b"
    settings.E2B_API_KEY = ""
    monkeypatch.setitem(sys.modules, "e2b", SimpleNamespace())

    with pytest.raises(HostedHarnessError) as raised:
        HostedHarnessGateway()

    assert raised.value.code == "sandbox_provider_not_configured"
    assert raised.value.status_code == 503


def test_github_source_without_git_is_a_clean_501(monkeypatch):
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.shutil.which", lambda _: None
    )

    def no_subprocess(*args, **kwargs):
        raise AssertionError("git must not be invoked")

    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.subprocess.run", no_subprocess
    )
    job = SimpleNamespace(
        id="job-1",
        payload={
            "source": {"kind": "github", "repository": "acme/agent", "ref": "main"}
        },
    )

    with pytest.raises(HostedHarnessError) as raised:
        HostedSourceAcquirer().acquire(job)

    assert raised.value.code == "git_unavailable"
    assert raised.value.status_code == 501
    assert raised.value.retryable is False
