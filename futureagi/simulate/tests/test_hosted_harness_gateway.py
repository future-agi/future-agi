from __future__ import annotations

from contextlib import nullcontext
import io
import tarfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models import HostedHarnessAttempt
from simulate.services.hosted_harness import create_hosted_job
from simulate.services.hosted_harness_gateway import (
    DaytonaHostedGateway,
    HostedSourceAcquirer,
)


def _payload():
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": "future-agi/reference-agent",
            "ref": "main",
            "commit_sha": "a" * 40,
            "visibility": "private",
            "installation_id": "installation-1",
        },
        "agent": {"connector": "vapi", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "seed": 1,
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 2,
            "memory_mb": 4096,
            "parallelism": 1,
            "concurrency_weight": 1,
            "max_duration_seconds": 600,
            "network_policy": "live",
        },
        "security": {
            "untrusted_source": True,
            "read_only_source": True,
            "allow_privileged": False,
            "allow_host_runtime_control": False,
            "allowed_egress_domains": ["agent.example.com"],
        },
        "retry": {
            "max_infrastructure_attempts": 2,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 15,
            "retryable_domains": ["infrastructure", "connectivity"],
        },
        "artifacts": {
            "level": "full",
            "retention_days": 30,
            "allow_bundle_download": True,
            "max_artifact_bytes": 1024,
        },
        "metadata": {},
    }


@pytest.mark.django_db
def test_gateway_clones_exact_commit_without_archiving_git_credentials(
    organization, monkeypatch
):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="source-gateway"
    )
    observed_commands = []
    observed_token = []

    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.GitHubAppTokenProvider.from_settings",
        lambda: SimpleNamespace(
            credential=lambda _: nullcontext("installation-secret-token")
        ),
    )

    def run(command, **kwargs):
        observed_commands.append(command)
        if kwargs.get("env", {}).get("GIT_CONFIG_VALUE_0"):
            observed_token.append(kwargs["env"]["GIT_CONFIG_VALUE_0"])
        if command[:2] == ["git", "init"]:
            checkout = command[-1]
            from pathlib import Path

            path = Path(checkout)
            path.mkdir(parents=True)
            (path / ".git").mkdir()
            (path / "agent.py").write_text("print('agent')", encoding="utf-8")
        if "rev-parse" in command:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("simulate.services.hosted_harness_gateway.subprocess.run", run)
    archive, commit_sha = HostedSourceAcquirer().acquire(job)

    assert commit_sha == "a" * 40
    assert all(
        "installation-secret-token" not in " ".join(cmd) for cmd in observed_commands
    )
    assert observed_token == ["Authorization: Bearer installation-secret-token"] * 4
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert "source/agent.py" in names
    assert all(".git" not in name for name in names)


class _Filesystem:
    def __init__(self):
        self.uploads = {}

    def upload_file(self, content, path):
        self.uploads[path] = content

    def download_file(self, path):
        return b"command-1"


class _Process:
    def __init__(self):
        self.exec_calls = []
        self.sessions = []

    def exec(self, command, **kwargs):
        self.exec_calls.append(command)
        return SimpleNamespace(exit_code=0, result="")

    def create_session(self, session_id):
        self.sessions.append(session_id)

    def execute_session_command(self, session_id, request):
        self.session_request = request
        return SimpleNamespace(cmd_id="command-1")

    def get_session_command(self, session_id, command_id):
        return SimpleNamespace(exit_code=None, status="running")


class _Sandbox:
    def __init__(self):
        self.id = "sandbox-1"
        self.fs = _Filesystem()
        self.process = _Process()


class _Daytona:
    def __init__(self):
        self.sandbox = _Sandbox()
        self.params = None
        self.deleted = False

    def create(self, params, **kwargs):
        self.params = params
        return self.sandbox

    def get(self, sandbox_id):
        return self.sandbox

    def delete(self, sandbox, **kwargs):
        self.deleted = True


@pytest.mark.django_db
def test_daytona_launch_uploads_contract_files_and_starts_one_session(
    organization, settings
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="launch-gateway")
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = "sha256:" + "b" * 64
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]

    attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")

    assert attempt.state == HostedHarnessAttempt.State.RUNNING
    assert attempt.provider_ref == "sandbox-1"
    assert set(client.sandbox.fs.uploads) >= {
        "/work/source.tar.gz",
        "/work/job.json",
        "/run/futureagi/secrets.json",
        "/run/futureagi/capabilities.json",
        "/run/futureagi/entrypoint-command-id",
    }
    assert client.sandbox.process.sessions == ["alk-harness"]
    assert client.params.network_block_all is True
    assert set(client.params.domain_allow_list.split(",")) == {
        "agent.example.com",
        "ingest.example.com",
        "platform.example.com",
    }


@pytest.mark.django_db
def test_cancel_writes_reason_signals_guest_and_holds_before_delete(
    organization, monkeypatch
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="cancel-gateway")
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")
    observations = iter(({"exit_code": None}, {"exit_code": 0}))
    monkeypatch.setattr(gateway, "inspect", lambda _: next(observations))
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.time.sleep", lambda _: None
    )
    monkeypatch.setattr(gateway, "_delete_and_record", lambda _: job)

    gateway.cancel(job, reason="user_canceled")

    assert (
        b'"reason":"user_canceled"'
        in client.sandbox.fs.uploads["/run/futureagi/cancel.json"]
    )
    assert any(
        "pkill -TERM" in command for command in client.sandbox.process.exec_calls
    )
