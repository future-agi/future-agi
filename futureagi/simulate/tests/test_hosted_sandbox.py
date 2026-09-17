from __future__ import annotations

import re
import sys
from types import SimpleNamespace

import pytest

from simulate.services.hosted_sandbox import (
    DaytonaSandboxRuntimeProvider,
    E2BSandboxRuntimeProvider,
    SandboxCommandRequest,
    SandboxLaunchSpec,
    SandboxProviderConfigurationError,
    SandboxProviderError,
    sandbox_provider_name,
    sandbox_runtime_reference,
)
from simulate.services.hosted_sandbox.e2b import E2BProcess


def _spec(**overrides) -> SandboxLaunchSpec:
    values = {
        "cpu_units": 4,
        "memory_mb": 8192,
        "disk_gb": 10,
        "ttl_seconds": 7200,
        "os_user": "svc-control",
        "labels": {"futureagi.job": "job-1"},
        "allowed_domains": ("api.deepgram.com", "platform.example.com"),
        "allowed_cidrs": ("143.223.88.0/21",),
        "unrestricted_egress": False,
    }
    values.update(overrides)
    return SandboxLaunchSpec(**values)


class _Value:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _DaytonaSandbox:
    id = "daytona-sandbox"
    fs = SimpleNamespace()
    process = SimpleNamespace()

    def create_signed_preview_url(self, port, *, expires_in_seconds):
        return SimpleNamespace(
            url=f"https://daytona.example/{port}?ttl={expires_in_seconds}"
        )


class _DaytonaClient:
    created = None

    def __init__(self, _config):
        self.sandbox = _DaytonaSandbox()

    def create(self, params, *, timeout):
        type(self).created = (params, timeout)
        return self.sandbox

    def get(self, _sandbox_id, *, request_timeout=None):
        return self.sandbox

    def delete(self, _sandbox, *, timeout, wait):
        return None


def _fake_daytona_module():
    class Image:
        @staticmethod
        def from_dockerfile(value):
            return value

    error = type("DaytonaError", (Exception,), {})
    return SimpleNamespace(
        Daytona=_DaytonaClient,
        DaytonaConfig=_Value,
        CreateSandboxFromImageParams=_Value,
        CreateSandboxFromSnapshotParams=_Value,
        Image=Image,
        Resources=_Value,
        DaytonaError=error,
        DaytonaNotFoundError=type("DaytonaNotFoundError", (error,), {}),
        DaytonaConflictError=type("DaytonaConflictError", (error,), {}),
    )


def test_daytona_adapter_maps_provider_neutral_launch_spec(settings, monkeypatch):
    monkeypatch.setitem(sys.modules, "daytona", _fake_daytona_module())
    settings.DAYTONA_API_KEY = "key"
    settings.ALK_DAYTONA_SNAPSHOT = "alk-hosted-v1"
    settings.ALK_DAYTONA_DOCKERFILE = ""
    settings.ALK_DAYTONA_SNAPSHOT_DIGEST = "sha256:digest"

    provider = DaytonaSandboxRuntimeProvider()
    sandbox = provider.create(_spec(), timeout=300)

    params, timeout = _DaytonaClient.created
    assert sandbox.id == "daytona-sandbox"
    assert timeout == 300
    assert params.snapshot == "alk-hosted-v1"
    assert params.ttl_minutes == 120
    assert params.network_block_all is False
    assert params.domain_allow_list == "api.deepgram.com,platform.example.com"
    assert not hasattr(params, "network_allow_list")
    assert provider.runtime_digest == "sha256:digest"


def test_daytona_adapter_ignores_cidrs_for_restricted_empty_policy(
    settings, monkeypatch
):
    monkeypatch.setitem(sys.modules, "daytona", _fake_daytona_module())
    settings.DAYTONA_API_KEY = "key"
    settings.ALK_DAYTONA_SNAPSHOT = "alk-hosted-v1"
    settings.ALK_DAYTONA_DOCKERFILE = ""

    provider = DaytonaSandboxRuntimeProvider()
    provider.create(
        _spec(
            allowed_domains=(),
            allowed_cidrs=tuple(f"143.223.{index}.0/24" for index in range(11)),
        ),
        timeout=300,
    )

    params, _ = _DaytonaClient.created
    assert not hasattr(params, "network_allow_list")
    assert params.domain_allow_list is None
    assert params.network_block_all is True


def test_daytona_adapter_preserves_unrestricted_network_policy(settings, monkeypatch):
    monkeypatch.setitem(sys.modules, "daytona", _fake_daytona_module())
    settings.DAYTONA_API_KEY = "key"
    settings.ALK_DAYTONA_SNAPSHOT = "alk-hosted-v1"
    settings.ALK_DAYTONA_DOCKERFILE = ""

    provider = DaytonaSandboxRuntimeProvider()
    provider.create(_spec(unrestricted_egress=True), timeout=300)

    params, _ = _DaytonaClient.created
    assert not hasattr(params, "network_allow_list")
    assert params.domain_allow_list is None
    assert params.network_block_all is False


def test_daytona_direct_image_preserves_extended_create_timeout(settings, monkeypatch):
    monkeypatch.setitem(sys.modules, "daytona", _fake_daytona_module())
    settings.HOSTED_SANDBOX_PROVIDER = "daytona"
    settings.DAYTONA_API_KEY = "key"
    settings.ALK_DAYTONA_SNAPSHOT = "alk-hosted-v1"
    settings.ALK_DAYTONA_DOCKERFILE = "/tmp/Dockerfile.hosted"
    settings.ALK_DAYTONA_SNAPSHOT_DIGEST = "sha256:snapshot-digest"

    provider = DaytonaSandboxRuntimeProvider()
    provider.create(_spec(), timeout=provider.create_timeout_seconds)

    params, timeout = _DaytonaClient.created
    assert params.image == "/tmp/Dockerfile.hosted"
    assert timeout == 1200
    assert provider.runtime_name == "direct-image-adjustments-v1"
    assert provider.runtime_digest == "sha256:snapshot-digest"
    assert sandbox_runtime_reference() == (
        "direct-image-adjustments-v1",
        "sha256:snapshot-digest",
    )


class _E2BFiles:
    def __init__(self):
        self.values = {}

    def write(self, path, data):
        self.values[path] = data

    def read(self, path, **_kwargs):
        if path not in self.values:
            from e2b import FileNotFoundException

            raise FileNotFoundException(path)
        return self.values[path]


class _E2BCommands:
    bootstrap_exit_code = 0

    def __init__(self):
        self.calls = []

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command.startswith("install -d "):
            return SimpleNamespace(
                stdout="",
                stderr="",
                exit_code=type(self).bootstrap_exit_code,
            )
        if kwargs.get("background"):
            return SimpleNamespace(pid=73)
        marker = re.search(r"(__FUTUREAGI_EXIT_[0-9a-f]+__)", command).group(1)
        return SimpleNamespace(stdout=f"output\n{marker}7\n", stderr="problem")

    def list(self, **_kwargs):
        return [SimpleNamespace(pid=73)]


class _E2BSandbox:
    sandbox_id = "e2b-sandbox"

    def __init__(self):
        self.files = _E2BFiles()
        self.commands = _E2BCommands()
        self.killed = False

    def kill(self):
        self.killed = True
        return True

    def get_host(self, port):
        return f"{port}-e2b-sandbox.e2b.app"


class _E2BSandboxClass:
    created = None
    sandbox = _E2BSandbox()

    @classmethod
    def create(cls, **kwargs):
        cls.sandbox.killed = False
        cls.created = kwargs
        return cls.sandbox

    @classmethod
    def connect(cls, sandbox_id, **_kwargs):
        if cls.sandbox.killed:
            from e2b import SandboxNotFoundException

            raise SandboxNotFoundException(sandbox_id)
        return cls.sandbox


def _fake_e2b_module():
    error = type("SandboxException", (Exception,), {})
    return SimpleNamespace(
        Sandbox=_E2BSandboxClass,
        SandboxException=error,
        SandboxNotFoundException=type("SandboxNotFoundException", (error,), {}),
        FileNotFoundException=type("FileNotFoundException", (error,), {}),
    )


def test_e2b_adapter_combines_domain_and_cidr_egress(settings, monkeypatch):
    monkeypatch.setitem(sys.modules, "e2b", _fake_e2b_module())
    settings.E2B_API_KEY = "e2b-key"
    settings.ALK_E2B_TEMPLATE_REFERENCE = "alk-hosted-e2b:build-123"
    settings.ALK_E2B_TEMPLATE_BUILD_ID = "build-123"
    settings.ALK_E2B_TEMPLATE_CPU_UNITS = 4
    settings.ALK_E2B_TEMPLATE_MEMORY_MB = 8192
    settings.ALK_E2B_TEMPLATE_DISK_GB = 10
    settings.ALK_E2B_MAX_TTL_SECONDS = 86400

    provider = E2BSandboxRuntimeProvider()
    sandbox = provider.create(_spec(), timeout=300)

    assert sandbox.id == "e2b-sandbox"
    assert _E2BSandboxClass.created["template"] == "alk-hosted-e2b:build-123"
    assert _E2BSandboxClass.created["timeout"] == 7200
    assert _E2BSandboxClass.created["network"] == {
        "allow_public_traffic": False,
        "deny_out": ["0.0.0.0/0"],
        "allow_out": [
            "api.deepgram.com",
            "platform.example.com",
            "143.223.88.0/21",
        ],
    }
    bootstrap_command, bootstrap_options = _E2BSandboxClass.sandbox.commands.calls[0]
    assert bootstrap_command == (
        "install -d -o svc-control -g svc-control -m 0700 "
        "/run/futureagi /run/user/2000 && "
        "rm -f /usr/local/bin/python && "
        "printf '#!/bin/sh\\nexec /opt/alk-venv/bin/python \"$@\"\\n' "
        "> /usr/local/bin/python && chmod 0755 /usr/local/bin/python && "
        "ln -sfn /opt/alk-venv/bin/pip /usr/local/bin/pip && "
        "ln -sfn /opt/alk-venv/bin/uv /usr/local/bin/uv && "
        "ln -sfn /opt/alk-venv/bin/uvx /usr/local/bin/uvx"
    )
    assert bootstrap_options["user"] == "root"
    with pytest.raises(SandboxProviderError, match="bounded no-header callback"):
        provider.create_preview_url(sandbox, 8080, expires_in_seconds=600)

    assert provider.delete(sandbox, timeout=1, wait=True) is True
    assert sandbox._sandbox.killed is True


def test_e2b_adapter_kills_sandbox_when_runtime_bootstrap_fails(settings, monkeypatch):
    monkeypatch.setitem(sys.modules, "e2b", _fake_e2b_module())
    settings.E2B_API_KEY = "e2b-key"
    settings.ALK_E2B_TEMPLATE_REFERENCE = "alk-hosted-e2b:build-123"
    settings.ALK_E2B_TEMPLATE_BUILD_ID = "build-123"
    settings.ALK_E2B_TEMPLATE_CPU_UNITS = 4
    settings.ALK_E2B_TEMPLATE_MEMORY_MB = 8192
    settings.ALK_E2B_TEMPLATE_DISK_GB = 10
    settings.ALK_E2B_MAX_TTL_SECONDS = 86400
    _E2BCommands.bootstrap_exit_code = 1

    try:
        with pytest.raises(SandboxProviderError, match="initialization failed"):
            E2BSandboxRuntimeProvider().create(_spec(), timeout=300)
        assert _E2BSandboxClass.sandbox.killed is True
    finally:
        _E2BCommands.bootstrap_exit_code = 0


def test_e2b_process_preserves_exit_code_and_reconnectable_command(monkeypatch):
    monkeypatch.setitem(sys.modules, "e2b", _fake_e2b_module())
    raw = _E2BSandbox()
    process = E2BProcess(raw, "svc-control")

    result = process.exec("echo output")
    secret = "must-not-appear-in-command"
    command = process.execute_session_command(
        "alk-harness",
        SandboxCommandRequest(
            command="sleep 60",
            env={"PROVIDER_TOKEN": secret},
            run_async=True,
        ),
    )

    assert result.exit_code == 7
    assert result.stdout == "output"
    assert result.stderr == "problem"
    assert command.cmd_id.startswith("73:")
    assert (
        process.get_session_command("alk-harness", command.cmd_id).status == "running"
    )

    submitted_command, submitted_options = raw.commands.calls[-1]
    assert secret not in submitted_command
    assert submitted_options["envs"] == {"PROVIDER_TOKEN": secret}


def test_e2b_process_fails_when_command_disappears_without_exit_marker(monkeypatch):
    monkeypatch.setitem(sys.modules, "e2b", _fake_e2b_module())
    process = E2BProcess(_E2BSandbox(), "svc-control")

    with pytest.raises(
        SandboxProviderError, match="disappeared without an exit marker"
    ):
        process.get_session_command("alk-harness", "99:missing")


def test_e2b_template_capacity_fails_closed(settings):
    settings.ALK_E2B_TEMPLATE_REFERENCE = "alk-hosted-e2b:build-123"
    settings.ALK_E2B_TEMPLATE_CPU_UNITS = 4
    settings.ALK_E2B_TEMPLATE_MEMORY_MB = 8192
    settings.ALK_E2B_TEMPLATE_DISK_GB = 10
    settings.ALK_E2B_MAX_TTL_SECONDS = 86400

    with pytest.raises(SandboxProviderConfigurationError, match="requires 8 vCPU"):
        E2BSandboxRuntimeProvider.validate_requested_resources(8, 8192, 10)


def test_e2b_continuous_runtime_limit_fails_closed(settings):
    settings.ALK_E2B_TEMPLATE_REFERENCE = "alk-hosted-e2b:build-123"
    settings.ALK_E2B_TEMPLATE_CPU_UNITS = 4
    settings.ALK_E2B_TEMPLATE_MEMORY_MB = 8192
    settings.ALK_E2B_TEMPLATE_DISK_GB = 10
    settings.ALK_E2B_MAX_TTL_SECONDS = 3600

    with pytest.raises(
        SandboxProviderConfigurationError, match="requires a 7200-second"
    ):
        E2BSandboxRuntimeProvider.validate_requested_resources(4, 8192, 10, 7200)


def test_unknown_sandbox_provider_fails_closed(settings):
    settings.HOSTED_SANDBOX_PROVIDER = "unknown"

    with pytest.raises(SandboxProviderConfigurationError, match="unsupported"):
        sandbox_provider_name()
