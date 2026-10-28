"""The fix engine's sandbox: credentials, launch, watch and release, against a fake E2B."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from simulate.models import AgentFixRun
from simulate.pydantic_schemas.agent_fix import FixRequest, Surface
from simulate.services import agent_fix_sandbox
from simulate.services.agent_fix_sandbox import (
    CAPABILITY_PATH,
    LEASE_SECONDS,
    launch,
    observe,
    release,
    rotate_credentials,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    hash_secret,
)
from simulate.services.hosted_sandbox import SandboxNotFoundError
from simulate.temporal.types.agent_fix import Observation
from simulate.tests.test_harness_versioning_backfill import _agent_version, _definition
from simulate.tests.test_hosted_harness_gateway import _payload

pytestmark = pytest.mark.django_db

TEMPLATE = "agent-fix:build-7"
MODEL_KEY = "harness-virtual-key"


class _Filesystem:
    def __init__(self):
        self.uploads = {}

    def upload_file(self, data, path):
        self.uploads[path] = data


class _Process:
    def __init__(self):
        self.exec_calls = []
        self.started = []
        self.exit_code = None

    def exec(self, command, **_kwargs):
        self.exec_calls.append(command)
        return SimpleNamespace(exit_code=0, result="")

    def create_session(self, _session_id):
        return None

    def execute_session_command(self, session_id, request):
        self.started.append((session_id, request))
        return SimpleNamespace(cmd_id="4242:engine")

    def get_session_command(self, _session_id, command_id, request_timeout=None):
        assert command_id == "4242:engine"
        return SimpleNamespace(exit_code=self.exit_code)


class _Sandbox:
    def __init__(self, sandbox_id):
        self.id = sandbox_id
        self.fs = _Filesystem()
        self.process = _Process()


class _E2B:
    name = "e2b"
    max_egress_domains = None
    create_timeout_seconds = 300

    def __init__(self):
        self.specs = []
        self.live = {}
        self.deleted = []
        self.renewed = []

    def create(self, spec, *, timeout):
        self.specs.append(spec)
        sandbox = _Sandbox(f"sandbox-{len(self.specs)}")
        self.live[sandbox.id] = sandbox
        return sandbox

    def get(self, sandbox_id, *, request_timeout=None):
        if sandbox_id not in self.live:
            raise SandboxNotFoundError("sandbox not found", status_code=404)
        return self.live[sandbox_id]

    def delete(self, sandbox, *, timeout, wait):
        self.deleted.append(sandbox.id)
        del self.live[sandbox.id]
        return True

    def renew_ttl(self, sandbox, ttl_seconds):
        self.renewed.append((sandbox.id, ttl_seconds))


@pytest.fixture(autouse=True)
def platform(settings, monkeypatch):
    settings.AGENT_FIX_E2B_TEMPLATE_REFERENCE = TEMPLATE
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example.test/"
    settings.AGENT_FIX_MAX_SANDBOX_ATTEMPTS = 2
    settings.AGENT_FIX_HEARTBEAT_TIMEOUT_SECONDS = 600
    for name in ("ALK_HOSTED_AGENTCC_BASE_URL", "AGENTCC_INTERNAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test/")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", MODEL_KEY)


@pytest.fixture
def provider(monkeypatch):
    fake = _E2B()
    monkeypatch.setattr(agent_fix_sandbox, "get_sandbox_provider", lambda: fake)
    return fake


@pytest.fixture
def fix_run(user, workspace):
    environment, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key=str(uuid.uuid4()),
        workspace=workspace,
    )
    return AgentFixRun.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        environment=environment,
        base_agent_version=_agent_version(_definition(user, workspace)),
        request=FixRequest(
            target_scenario_keys=["refund-request"],
            failing_sub_goals=["refund_issued"],
            surfaces=[Surface.PROMPT],
            budget_usd=5,
            max_minutes=60,
        ).model_dump(mode="json"),
    )


def _running(fix_run, **fields):
    values = {
        "state": AgentFixRun.State.RUNNING,
        "started_at": timezone.now(),
        **fields,
    }
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(**values)
    fix_run.refresh_from_db()
    return fix_run


def _capability(sandbox):
    return json.loads(sandbox.fs.uploads[CAPABILITY_PATH])


def test_rotation_stores_only_hashes_and_a_second_rotation_fences_the_first(fix_run):
    _running(fix_run)

    first = rotate_credentials(fix_run.id)
    second = rotate_credentials(fix_run.id)

    fix_run.refresh_from_db()
    assert fix_run.token_hash == hash_secret(second.token)
    assert fix_run.fence_hash == hash_secret(second.fence)
    # The first sandbox's token and fence no longer match anything stored.
    assert hash_secret(first.token) not in (fix_run.token_hash, fix_run.fence_hash)
    assert hash_secret(first.fence) not in (fix_run.token_hash, fix_run.fence_hash)
    assert fix_run.sandbox_attempt == 2
    # The token lives as long as the run may, not as long as one sandbox lease.
    assert fix_run.token_expires_at == fix_run.started_at + timedelta(minutes=60)
    assert second.expires_at == fix_run.token_expires_at


def test_rotation_refuses_a_run_that_is_not_running(fix_run):
    _running(fix_run, state=AgentFixRun.State.STOPPING)

    assert rotate_credentials(fix_run.id) is None
    fix_run.refresh_from_db()
    assert fix_run.sandbox_attempt == 0
    assert fix_run.token_hash == ""


def test_launch_writes_the_capability_file_0600_and_never_the_environment(
    fix_run, provider
):
    launched = launch(str(fix_run.id))

    fix_run.refresh_from_db()
    sandbox = provider.live["sandbox-1"]
    capability = _capability(sandbox)
    assert launched.observation == Observation.RUNNING
    assert launched.command_id == "4242:engine"
    assert fix_run.state == AgentFixRun.State.RUNNING
    assert fix_run.started_at is not None
    assert fix_run.sandbox_ref == "sandbox-1"
    assert fix_run.sandbox_attempt == 1
    assert capability == {
        "fix_run_id": str(fix_run.id),
        "api_base": (
            f"https://platform.example.test/simulate/api/agent-fix-engine/{fix_run.id}/"
        ),
        "token": capability["token"],
        "fence": capability["fence"],
        "expires_at": fix_run.token_expires_at.isoformat(),
        "models": {"base_url": "https://gateway.example.test/v1", "api_key": MODEL_KEY},
    }
    assert hash_secret(capability["token"]) == fix_run.token_hash
    assert hash_secret(capability["fence"]) == fix_run.fence_hash
    assert sandbox.process.exec_calls == [f"chmod 0600 {CAPABILITY_PATH}"]
    [(_session, request)] = sandbox.process.started
    assert request.command == "node /opt/agent-improvement/platform-entry.mjs"
    assert request.run_async is True
    # The environment carries only the template's PATH, so node resolves.
    assert set(request.env) == {"PATH"}
    assert "/opt/node22/bin" in request.env["PATH"]
    started_with = request.command + json.dumps(request.env)
    for secret in (capability["token"], capability["fence"], MODEL_KEY):
        assert secret not in started_with
    spec = provider.specs[0]
    assert spec.runtime_name == TEMPLATE
    assert spec.ttl_seconds == LEASE_SECONDS
    assert spec.labels == {"futureagi.fix_run": str(fix_run.id)}


def test_launch_lets_the_engine_reach_only_the_platform_and_the_model_gateway(
    fix_run, provider
):
    launch(str(fix_run.id))

    spec = provider.specs[0]
    assert spec.allowed_domains == ("gateway.example.test", "platform.example.test")
    assert spec.allowed_cidrs == ()
    assert spec.unrestricted_egress is False


def test_relaunch_deletes_the_previous_sandbox_and_fences_its_engine(fix_run, provider):
    launch(str(fix_run.id))
    first = _capability(provider.live["sandbox-1"])

    launch(str(fix_run.id))

    fix_run.refresh_from_db()
    second = _capability(provider.live["sandbox-2"])
    assert provider.deleted == ["sandbox-1"]
    assert fix_run.sandbox_ref == "sandbox-2"
    assert fix_run.sandbox_attempt == 2
    assert fix_run.token_hash == hash_secret(second["token"])
    assert fix_run.token_hash != hash_secret(first["token"])
    assert fix_run.fence_hash != hash_secret(first["fence"])


def test_a_relaunch_fences_the_old_engine_even_when_the_new_sandbox_fails(
    fix_run, provider
):
    launch(str(fix_run.id))

    def unavailable(spec, *, timeout):
        raise TimeoutError("sandbox create timed out")

    provider.create = unavailable
    with pytest.raises(TimeoutError):
        launch(str(fix_run.id))

    fix_run.refresh_from_db()
    assert fix_run.token_hash == ""
    assert fix_run.fence_hash == ""
    # A create that never produced an engine does not use up one of the run's sandboxes.
    assert fix_run.sandbox_attempt == 1


def test_launch_after_the_last_sandbox_closes_the_run_as_engine_died(fix_run, provider):
    _running(fix_run, sandbox_attempt=2)

    launched = launch(str(fix_run.id))

    fix_run.refresh_from_db()
    assert launched.observation == Observation.ENDED
    assert fix_run.state == AgentFixRun.State.FAILED
    assert fix_run.failure["code"] == "fix_engine_died"
    assert provider.specs == []


def test_launch_past_the_deadline_closes_the_run(fix_run, provider):
    _running(fix_run, started_at=timezone.now() - timedelta(minutes=61))

    launched = launch(str(fix_run.id))

    fix_run.refresh_from_db()
    assert launched.observation == Observation.ENDED
    assert fix_run.failure["code"] == "fix_deadline"
    assert provider.specs == []


def test_launch_of_a_stopping_run_starts_nothing(fix_run, provider):
    _running(fix_run, state=AgentFixRun.State.STOPPING)

    launched = launch(str(fix_run.id))

    assert launched.observation == Observation.STOPPING
    assert provider.specs == []


def test_launch_refuses_a_provider_other_than_e2b(fix_run, provider):
    provider.name = "daytona"

    with pytest.raises(HostedHarnessError, match="E2B sandbox"):
        launch(str(fix_run.id))
    assert provider.specs == []


def test_a_running_engine_renews_its_sandbox_lease(fix_run, provider):
    launched = launch(str(fix_run.id))

    assert observe(str(fix_run.id), launched.command_id) == Observation.RUNNING
    assert provider.renewed == [("sandbox-1", LEASE_SECONDS)]


def test_an_exited_engine_is_gone(fix_run, provider):
    launched = launch(str(fix_run.id))
    provider.live["sandbox-1"].process.exit_code = 1

    assert observe(str(fix_run.id), launched.command_id) == Observation.ENGINE_GONE
    assert provider.renewed == []


def test_a_deleted_sandbox_means_the_engine_is_gone(fix_run, provider):
    launched = launch(str(fix_run.id))
    del provider.live["sandbox-1"]

    assert observe(str(fix_run.id), launched.command_id) == Observation.ENGINE_GONE


def test_an_engine_silent_past_the_report_timeout_is_gone(fix_run, provider):
    launched = launch(str(fix_run.id))
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        heartbeat_at=timezone.now() - timedelta(seconds=601)
    )

    assert observe(str(fix_run.id), launched.command_id) == Observation.ENGINE_GONE


def test_the_deadline_closes_the_run_as_failed(fix_run, provider):
    launched = launch(str(fix_run.id))
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        started_at=timezone.now() - timedelta(minutes=61)
    )

    assert observe(str(fix_run.id), launched.command_id) == Observation.ENDED
    fix_run.refresh_from_db()
    assert fix_run.state == AgentFixRun.State.FAILED
    assert fix_run.failure["code"] == "fix_deadline"
    assert fix_run.token_hash == ""


def test_a_stopping_run_is_seen_before_the_sandbox(fix_run, provider):
    launched = launch(str(fix_run.id))
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        state=AgentFixRun.State.STOPPING
    )

    assert observe(str(fix_run.id), launched.command_id) == Observation.STOPPING
    assert provider.renewed == []


def test_release_deletes_the_sandbox_once(fix_run, provider):
    launch(str(fix_run.id))

    release(str(fix_run.id))
    release(str(fix_run.id))

    fix_run.refresh_from_db()
    assert provider.deleted == ["sandbox-1"]
    assert fix_run.sandbox_ref == ""
