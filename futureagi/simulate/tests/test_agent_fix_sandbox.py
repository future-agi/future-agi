"""The fix engine's sandbox: credentials, model key, launch, watch and release, against a
fake E2B and a fake AgentCC admin API."""

import hashlib
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from django.db import OperationalError
from django.test import RequestFactory
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.services.gateway_client import GatewayClientError
from simulate.authentication import AgentFixEngineAuthentication
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
from simulate.services.hosted_sandbox import (
    SandboxNotFoundError,
    SandboxProviderConfigurationError,
    SandboxProviderError,
)
from simulate.temporal.types.agent_fix import Observation
from simulate.tests.test_harness_versioning_backfill import _agent_version, _definition
from simulate.tests.test_hosted_harness_gateway import _payload

pytestmark = pytest.mark.django_db

TEMPLATE = "agent-fix:build-7"
# The platform's own gateway key: in the environment, never in a sandbox.
MODEL_KEY = "harness-virtual-key"
ENGINE_MODELS = ["deepseek/deepseek-v4.1-flash"]
GRACE_SECONDS = 600


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


class _AgentCC:
    """AgentCC's admin API: issues numbered keys and records what it revoked."""

    def __init__(self):
        self.created = []
        self.revoked = []
        self.down = False

    def create_key(self, name, owner="", models=None, providers=None, metadata=None):
        if self.down:
            raise GatewayClientError("Cannot connect to gateway")
        self.created.append({"name": name, "models": models, "metadata": metadata})
        number = len(self.created)
        return {
            "id": f"gw-key-{number}",
            "key": f"sk-agentcc-fix-{number}",
            "key_prefix": f"sk-agentcc-{number}",
            "models": models,
        }

    def revoke_key(self, key_id):
        self.revoked.append(key_id)
        return {}


@pytest.fixture(autouse=True)
def platform(settings, monkeypatch):
    settings.AGENT_FIX_E2B_TEMPLATE_REFERENCE = TEMPLATE
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example.test/"
    settings.AGENT_FIX_MAX_SANDBOX_ATTEMPTS = 2
    settings.AGENT_FIX_HEARTBEAT_TIMEOUT_SECONDS = 600
    settings.AGENT_FIX_TOKEN_GRACE_SECONDS = GRACE_SECONDS
    settings.AGENT_FIX_ENGINE_MODELS = ENGINE_MODELS
    for name in ("ALK_HOSTED_AGENTCC_BASE_URL", "AGENTCC_INTERNAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test/")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", MODEL_KEY)


@pytest.fixture(autouse=True)
def agentcc(monkeypatch):
    fake = _AgentCC()
    monkeypatch.setattr(auth_bridge, "get_gateway_client", lambda: fake)
    return fake


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


def _key_statuses():
    return dict(
        AgentccAPIKey.no_workspace_objects.values_list("gateway_key_id", "status")
    )


def _engine_request(fix_run, credentials):
    request = RequestFactory().post(
        "/",
        HTTP_AUTHORIZATION=f"Bearer {credentials.token}",
        HTTP_X_FIX_FENCE=credentials.fence,
    )
    request.resolver_match = SimpleNamespace(kwargs={"pk": str(fix_run.id)})
    return request


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
    # Rotating starts no engine, so it uses up none of the run's attempts.
    assert fix_run.sandbox_attempt == 0
    # The token lives as long as the run may plus a grace, not one sandbox lease.
    assert fix_run.token_expires_at == fix_run.started_at + timedelta(
        minutes=60, seconds=GRACE_SECONDS
    )
    assert second.expires_at == fix_run.token_expires_at


def test_rotation_refuses_a_run_that_is_not_running(fix_run):
    _running(fix_run, state=AgentFixRun.State.STOPPING)

    assert rotate_credentials(fix_run.id) is None
    fix_run.refresh_from_db()
    assert fix_run.sandbox_attempt == 0
    assert fix_run.token_hash == ""


def test_a_report_sent_just_after_the_deadline_is_still_accepted(fix_run):
    # The deadline passed 30 seconds ago; the engine's final report is on its way.
    _running(fix_run, started_at=timezone.now() - timedelta(minutes=60, seconds=30))
    credentials = rotate_credentials(fix_run.id)

    user, _ = AgentFixEngineAuthentication().authenticate(
        _engine_request(fix_run, credentials)
    )

    assert user.fix_run.id == fix_run.id


def test_a_report_sent_after_the_grace_is_refused(fix_run):
    _running(
        fix_run,
        started_at=timezone.now() - timedelta(minutes=60, seconds=GRACE_SECONDS + 1),
    )
    credentials = rotate_credentials(fix_run.id)

    with pytest.raises(HostedHarnessError) as refused:
        AgentFixEngineAuthentication().authenticate(
            _engine_request(fix_run, credentials)
        )
    assert refused.value.code == "authentication_expired"


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
        "models": {
            "base_url": "https://gateway.example.test/v1",
            "api_key": "sk-agentcc-fix-1",
        },
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
    for secret in (capability["token"], capability["fence"], "sk-agentcc-fix-1"):
        assert secret not in started_with
    spec = provider.specs[0]
    assert spec.runtime_name == TEMPLATE
    assert spec.ttl_seconds == LEASE_SECONDS
    assert spec.labels == {"futureagi.fix_run": str(fix_run.id)}


def test_the_engine_gets_its_own_model_key_never_the_platform_key(
    fix_run, provider, agentcc
):
    launch(str(fix_run.id))

    fix_run.refresh_from_db()
    assert (
        MODEL_KEY.encode() not in provider.live["sandbox-1"].fs.uploads[CAPABILITY_PATH]
    )
    assert agentcc.created == [
        {
            "name": f"agent-fix-{fix_run.id}",
            "models": ENGINE_MODELS,
            "metadata": {
                "fix_run_id": str(fix_run.id),
                "org_id": str(fix_run.organization_id),
            },
        }
    ]
    key = AgentccAPIKey.no_workspace_objects.get(gateway_key_id="gw-key-1")
    assert fix_run.model_key_id == "gw-key-1"
    assert key.organization_id == fix_run.organization_id
    assert key.status == AgentccAPIKey.ACTIVE
    assert key.allowed_models == ENGINE_MODELS
    assert key.key_hash == hashlib.sha256(b"sk-agentcc-fix-1").hexdigest()
    # The key lives exactly as long as the engine's token.
    assert key.expires_at == fix_run.token_expires_at


def test_launch_without_a_model_key_fails_the_run_and_creates_no_sandbox(
    fix_run, provider, agentcc
):
    agentcc.down = True

    launched = launch(str(fix_run.id))

    fix_run.refresh_from_db()
    assert launched.observation == Observation.ENDED
    assert fix_run.state == AgentFixRun.State.FAILED
    assert fix_run.failure["code"] == "fix_model_key_unavailable"
    assert provider.specs == []
    assert fix_run.model_key_id == ""


def test_launch_lets_the_engine_reach_only_the_platform_and_the_model_gateway(
    fix_run, provider
):
    launch(str(fix_run.id))

    spec = provider.specs[0]
    assert spec.allowed_domains == ("gateway.example.test", "platform.example.test")
    assert spec.allowed_cidrs == ()
    assert spec.unrestricted_egress is False


def test_relaunch_deletes_the_previous_sandbox_and_fences_its_engine(
    fix_run, provider, agentcc
):
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
    # The old engine's model key is revoked with its sandbox; the new engine has its own.
    assert agentcc.revoked == ["gw-key-1"]
    assert second["models"]["api_key"] == "sk-agentcc-fix-2"
    assert fix_run.model_key_id == "gw-key-2"
    assert _key_statuses() == {
        "gw-key-1": AgentccAPIKey.REVOKED,
        "gw-key-2": AgentccAPIKey.ACTIVE,
    }


def test_a_relaunch_fences_the_old_engine_even_when_the_new_sandbox_fails(
    fix_run, provider, agentcc
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
    # The key minted for the sandbox that never came is recorded, so release revokes it.
    assert fix_run.model_key_id == "gw-key-2"
    release(str(fix_run.id))
    assert agentcc.revoked == ["gw-key-1", "gw-key-2"]


def test_only_a_started_engine_uses_up_an_attempt(
    fix_run, provider, agentcc, monkeypatch
):
    # Three launches fail after their sandbox exists but before the engine starts; the run
    # allows two engines, and the fourth launch still starts its first.
    start = _Process.execute_session_command

    def unavailable(self, session_id, request):
        raise SandboxProviderError("E2B API timed out")

    monkeypatch.setattr(_Process, "execute_session_command", unavailable)
    for _ in range(3):
        with pytest.raises(SandboxProviderError):
            launch(str(fix_run.id))
    monkeypatch.setattr(_Process, "execute_session_command", start)

    launched = launch(str(fix_run.id))

    fix_run.refresh_from_db()
    assert launched.observation == Observation.RUNNING
    assert fix_run.state == AgentFixRun.State.RUNNING
    assert fix_run.sandbox_attempt == 1
    # Every failed launch's key was revoked by the next launch.
    assert agentcc.revoked == ["gw-key-1", "gw-key-2", "gw-key-3"]


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


def test_launch_refuses_a_provider_other_than_e2b(fix_run, provider, agentcc):
    provider.name = "daytona"

    with pytest.raises(HostedHarnessError, match="E2B sandbox"):
        launch(str(fix_run.id))
    assert provider.specs == []
    assert agentcc.created == []


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


@pytest.mark.parametrize(
    "outage",
    [
        SandboxProviderError("E2B API timed out"),
        httpx.ConnectError("connection refused"),
        OperationalError("server closed the connection unexpectedly"),
    ],
)
def test_an_outage_is_raised_for_a_later_look_and_leaves_the_run_running(
    fix_run, provider, outage
):
    launched = launch(str(fix_run.id))

    def unavailable(sandbox_id, *, request_timeout=None):
        raise outage

    provider.get = unavailable
    with pytest.raises(type(outage)):
        observe(str(fix_run.id), launched.command_id)

    fix_run.refresh_from_db()
    assert fix_run.state == AgentFixRun.State.RUNNING


@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid E2B command identifier"),
        SandboxProviderConfigurationError("E2B_API_KEY is not set"),
    ],
)
def test_an_error_a_later_look_would_hit_again_fails_the_run(fix_run, provider, error):
    launched = launch(str(fix_run.id))

    def broken(sandbox_id, *, request_timeout=None):
        raise error

    provider.get = broken

    assert observe(str(fix_run.id), launched.command_id) == Observation.ENDED
    fix_run.refresh_from_db()
    assert fix_run.state == AgentFixRun.State.FAILED
    assert fix_run.failure["code"] == "fix_watch_failed"


def test_release_deletes_the_sandbox_and_revokes_the_model_key_once(
    fix_run, provider, agentcc
):
    launch(str(fix_run.id))

    release(str(fix_run.id))
    release(str(fix_run.id))

    fix_run.refresh_from_db()
    assert provider.deleted == ["sandbox-1"]
    assert fix_run.sandbox_ref == ""
    assert agentcc.revoked == ["gw-key-1"]
    assert fix_run.model_key_id == ""
    assert _key_statuses() == {"gw-key-1": AgentccAPIKey.REVOKED}
