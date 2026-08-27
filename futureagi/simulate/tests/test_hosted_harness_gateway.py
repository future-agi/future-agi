from __future__ import annotations

import io
import json
import tarfile
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models import HostedHarnessAttempt, HostedHarnessJob
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job
from simulate.services.hosted_harness_gateway import (
    DaytonaHostedGateway,
    HostedSourceAcquirer,
    _authoring_archive_for,
    pack_authoring_archive,
    prepare_dispatch_payload,
    resolve_authored_connector,
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


def test_authoring_archive_resolves_uploaded_source_by_explicit_key(tmp_path, settings):
    root = tmp_path / "authoring"
    scenario = root / "uploaded-agent" / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    stale = root / "uploaded-agent" / "webrtc-runs" / "old" / "postgres"
    stale.mkdir(parents=True)
    (stale / "pg_wal").write_bytes(b"must-not-upload")
    settings.ALK_HOSTED_BUNDLE_DIR = str(root)
    job = SimpleNamespace(
        payload={
            "source": {
                "kind": "archive",
                "archive_artifact_id": "0f95b074-d3db-47a1-a3a1-2e37991aa946",
            },
            "metadata": {"authoring_key": "uploaded-agent"},
        }
    )

    archive = _authoring_archive_for(job)

    assert archive is not None
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        assert "scenarios/one/scenario.json" in tar.getnames()
        assert "webrtc-runs/old/postgres/pg_wal" not in tar.getnames()


def test_fresh_authoring_archive_contains_contract_and_scenarios_only(tmp_path):
    root = tmp_path / "authoring"
    scenario = root / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (root / "contract.json").write_text('{"agent":"ride"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    ignored = root / "webrtc-runs" / "old"
    ignored.mkdir(parents=True)
    (ignored / "recording.wav").write_bytes(b"not an authoring input")

    body = pack_authoring_archive(root)

    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        assert sorted(archive.getnames()) == [
            "contract.json",
            "scenarios/one/scenario.json",
        ]


def test_fresh_authoring_archive_rejects_missing_scenarios(tmp_path):
    (tmp_path / "contract.json").write_text("{}", encoding="utf-8")

    with pytest.raises(HostedHarnessError, match="without scenario artifacts"):
        pack_authoring_archive(tmp_path)


def test_voice_authoring_resolves_auto_connector_with_livekit_credentials(tmp_path):
    root = tmp_path / "authoring"
    scenario = root / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (root / "contract.json").write_text('{"modality":"voice"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    payload = _payload()
    payload["agent"] = {
        "connector": "auto",
        "config": {},
        "secret_refs": {
            "LIVEKIT_URL": {},
            "LIVEKIT_API_KEY": {},
            "LIVEKIT_API_SECRET": {},
        },
    }

    resolved = resolve_authored_connector(payload, pack_authoring_archive(root))

    assert resolved["agent"]["connector"] == "livekit"
    assert payload["agent"]["connector"] == "auto"


def test_authored_connector_never_overrides_explicit_or_ambiguous_input(tmp_path):
    root = tmp_path / "authoring"
    scenario = root / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (root / "contract.json").write_text('{"modality":"voice"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    body = pack_authoring_archive(root)
    explicit = _payload()
    explicit["agent"]["connector"] = "retell"
    ambiguous = _payload()
    ambiguous["agent"]["connector"] = "auto"

    assert resolve_authored_connector(explicit, body)["agent"]["connector"] == "retell"
    assert resolve_authored_connector(ambiguous, body)["agent"]["connector"] == "auto"


def test_dispatch_payload_mirrors_only_livekit_url():
    payload = {"agent": {"connector": "livekit", "config": {}}}

    dispatched = prepare_dispatch_payload(
        payload,
        {
            "LIVEKIT_URL": "wss://customer.livekit.cloud",
            "LIVEKIT_API_KEY": "must-not-be-copied",
            "LIVEKIT_API_SECRET": "must-not-be-copied",
        },
    )

    assert dispatched["agent"]["config"] == {
        "livekit_url": "wss://customer.livekit.cloud"
    }
    assert payload["agent"]["config"] == {}
    assert "must-not-be-copied" not in json.dumps(dispatched)


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
    # Egress domains present -> allowlist-only egress: block_all is False and the
    # domain_allow_list is the deny-by-default. block_all True is mutually
    # exclusive with an allow-list in Daytona (verified live), so the allow-list
    # itself enforces the boundary.
    assert client.params.network_block_all is False
    assert set(client.params.domain_allow_list.split(",")) == {
        "agent.example.com",
        "ingest.example.com",
        "platform.example.com",
    }


@pytest.mark.django_db
def test_daytona_livekit_launch_adds_explicit_webrtc_media_cidrs(
    organization, settings
):
    payload = _payload()
    payload["agent"]["connector"] = "livekit"
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="launch-livekit-webrtc"
    )
    client = _Daytona()
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = "sha256:" + "b" * 64
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    settings.ALK_HOSTED_WEBRTC_EGRESS_CIDRS = ["203.0.113.0/24", "2001:db8::/32"]

    gateway.launch(job, endpoint_base_url="https://platform.example.com")

    assert set(client.params.network_allow_list.split(",")) == {
        "203.0.113.0/24",
        "2001:db8::/32",
    }
    assert client.params.domain_allow_list is None


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


@pytest.mark.django_db
def test_reconcile_relaunches_infra_failure_until_budget_then_fails(
    organization, monkeypatch
):
    from simulate.services.hosted_harness import record_cleanup

    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="retry-gateway")
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = _Daytona()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""

    def _fake_delete(attempt, *, retry_pending=False):
        return record_cleanup(
            attempt.id,
            provider_ref=str(attempt.provider_ref),
            verified_absent=True,
            retry_pending=retry_pending,
            details={"provider": "test"},
        )

    monkeypatch.setattr(gateway, "_delete_and_record", _fake_delete)
    acquire = patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    )

    # Attempt 1 crashes (exit 2): infrastructure failure, 1 < 2 -> RETRY_WAIT.
    with acquire:
        attempt1 = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    assert attempt1.attempt_number == 1
    monkeypatch.setattr(gateway, "inspect", lambda _: {"exit_code": 2, "logs": "boom"})
    assert gateway.reconcile_completed(attempt1).state == (
        HostedHarnessJob.State.RETRY_WAIT
    )

    # Relaunch a fresh attempt; register_attempt supersedes attempt 1.
    with acquire:
        attempt2 = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    assert attempt2.attempt_number == 2
    # Attempt 2 crashes again: budget spent (2 not < 2) -> terminal FAILED.
    assert gateway.reconcile_completed(attempt2).state == HostedHarnessJob.State.FAILED


@pytest.mark.django_db
def test_reconcile_exit3_and_exit0_never_retry(organization, monkeypatch):
    from simulate.services.hosted_harness import record_cleanup

    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="noretry-gateway")
    gateway = object.__new__(DaytonaHostedGateway)
    gateway.client = _Daytona()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    captured = {}

    def _fake_delete(attempt, *, retry_pending=False):
        captured["retry_pending"] = retry_pending
        return record_cleanup(
            attempt.id,
            provider_ref=str(attempt.provider_ref),
            verified_absent=True,
            retry_pending=retry_pending,
            details={"provider": "test"},
        )

    monkeypatch.setattr(gateway, "_delete_and_record", _fake_delete)
    acquire = patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    )

    # Exit 3 (fenced/superseded): never retried.
    with acquire:
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    monkeypatch.setattr(gateway, "inspect", lambda _: {"exit_code": 3, "logs": ""})
    assert gateway.reconcile_completed(attempt).state != (
        HostedHarnessJob.State.RETRY_WAIT
    )
    assert captured["retry_pending"] is False

    # Exit 0 (terminal reached / verdict delivered): never retried.
    with acquire:
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    monkeypatch.setattr(gateway, "inspect", lambda _: {"exit_code": 0, "logs": ""})
    assert gateway.reconcile_completed(attempt).state != (
        HostedHarnessJob.State.RETRY_WAIT
    )
    assert captured["retry_pending"] is False
