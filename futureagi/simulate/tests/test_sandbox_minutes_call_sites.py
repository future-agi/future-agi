"""Where hosted sandboxes are created, renewed and deleted: each checks the plan
first, times the sandbox it makes, and records why it ended, so only legitimate time
is billed."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.test import APIClient

from simulate.models import (
    AgentFixRun,
    HarnessNudge,
    HostedHarnessAttempt,
    HostedHarnessCleanupReceipt,
    HostedHarnessConversation,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
    HostedSandboxLifetime,
)
from simulate.services import agent_fix_sandbox, harness_usage, sandbox_usage
from simulate.services.agent_fix_runs import close_fix_run, request_stop
from simulate.services.agent_fix_sweep import sweep_lost_fix_runs
from simulate.services.harness_versioning import nudges, rebuild
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    record_cleanup,
    register_attempt,
)
from simulate.services.hosted_harness_conversation import (
    RUNTIME_STALE_SECONDS,
    _abandon_outstanding,
    _unacked_commands,
    enqueue_message,
    ensure_conversation,
    issue_conversation_capability,
    recover_stalled_conversations,
    retire_without_saved_workspace,
    send_to_harness,
)
from simulate.services.hosted_harness_gateway import (
    HostedHarnessGateway,
    _retired_chat_ending,
)
from simulate.services.hosted_sandbox import SandboxNotFoundError
from simulate.temporal.types.agent_fix import Observation
from simulate.tests.test_agent_fix_sandbox import _E2B, _new_fix
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_nudges import _answer
from simulate.tests.test_harness_nudges import (  # noqa: F401 - fixture
    started as started,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_rebuild import _queued, _send
from simulate.tests.test_harness_versioning_rebuild import (  # noqa: F401 - fixture
    inline as inline,
)
from simulate.tests.test_hosted_harness_conversation import _headers
from simulate.tests.test_hosted_harness_gateway import _Daytona, _payload
from simulate.tests.test_hosted_harness_gateway import (  # noqa: F401 - fixture
    _isolate_platform_simulator_environment as _isolate_platform_simulator_environment,
)
from simulate.tests.test_hosted_harness_gateway import (  # noqa: F401 - fixture
    _no_conversation_runtime as _no_conversation_runtime,
)
from simulate.tests.test_hosted_harness_gateway import _Sandbox as _ChatSandbox
from simulate.tests.test_rerun_api import (  # noqa: F401 - fixture
    agent_definition as agent_definition,
)
from simulate.tests.test_rerun_api import (  # noqa: F401 - fixture
    call_execution as call_execution,
)
from simulate.tests.test_rerun_api import (  # noqa: F401 - fixture
    dataset_for_scenario as dataset_for_scenario,
)
from simulate.tests.test_rerun_api import (  # noqa: F401 - fixture
    run_test as run_test,
)
from simulate.tests.test_rerun_api import (  # noqa: F401 - fixture
    scenario as scenario,
)
from simulate.tests.test_rerun_api import (  # noqa: F401 - fixture
    simulator_agent as simulator_agent,
)
from simulate.tests.test_rerun_api import (  # noqa: F401 - fixture
    test_execution as test_execution,
)
from simulate.tests.test_rerun_calls_in_run import (  # noqa: F401 - fixture
    calls as calls,
)
from simulate.tests.test_rerun_calls_in_run import (  # noqa: F401 - fixture
    finished_run as finished_run,
)
from simulate.tests.test_rerun_calls_in_run import (  # noqa: F401 - fixture
    workflow as workflow,
)
from simulate.tests.test_sandbox_usage import _broken_database
from tfc.ee_loader import has_ee

pytestmark = pytest.mark.django_db

_GATEWAY = "simulate.services.hosted_harness_gateway"
SIMULATE = Path(__file__).resolve().parents[1]
CHAT_TTL = 1800
PLATFORM = "https://platform.example"
_REFUSAL = {
    "allowed": False,
    "reason": "Usage paused — you set a budget limit for sandbox_minutes",
    "error_code": "BUDGET_PAUSED",
    "dimension": "sandbox_minutes",
    "current_usage": 12.5,
    "limit": 10.0,
    "upgrade_cta": {"text": "Upgrade to Pay-as-you-go", "plan": "payg"},
}


# --- the clock, the plan check, the meter ---------------------------------------------


class _Clock:
    def __init__(self, start):
        self.now = start

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    """Every ``timezone.now()`` reads this clock; only the test moves it."""
    moments = _Clock(timezone.now().replace(microsecond=0))
    monkeypatch.setattr(timezone, "now", lambda: moments.now)
    return moments


@pytest.fixture
def create_checks(monkeypatch):
    """The create-time plan check, stood in for: it records each organization asked
    about, and refuses once told to."""
    checks = SimpleNamespace(asked=[], refuse=False)

    def check(organization_id, *, attempt=None):
        checks.asked.append(str(organization_id))
        if checks.refuse:
            raise HostedHarnessError("usage_limit", _REFUSAL["reason"], status_code=402)

    monkeypatch.setattr(harness_usage, "require_sandbox_create", check)
    monkeypatch.setattr(agent_fix_sandbox, "require_sandbox_create", check)
    return checks


@pytest.fixture
def refuse_sandbox(monkeypatch):
    """The organization's plan refuses sandbox minutes and allows everything else; the
    real checks run on that answer. Returns the actions asked about."""
    asked = []

    def check(organization_id, action):
        asked.append(action)
        return dict(_REFUSAL) if action == "sandbox_runtime" else {"allowed": True}

    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    monkeypatch.setattr(harness_usage, "check_harness_action", check)
    return asked


@pytest.fixture
def metering(monkeypatch):
    """Rows are written as in the cloud; each sandbox charge sent is recorded."""
    sent = []
    monkeypatch.setattr(sandbox_usage, "is_oss", lambda: False)
    monkeypatch.setattr(
        sandbox_usage,
        "_emit_lifetime",
        lambda row, minutes: sent.append((row.id, Decimal(minutes))),
    )
    return sent


def _row(provider_ref):
    return HostedSandboxLifetime.no_workspace_objects.get(provider_ref=provider_ref)


def _at(value):
    return parse_datetime(value) if isinstance(value, str) else value


# --- fakes ------------------------------------------------------------------------------


class _Provider(_Daytona):
    """A provider whose sandboxes each have their own id and can be gone. ``create`` and
    ``renew_ttl`` take the time they are given on the test's clock."""

    def __init__(
        self,
        clock=None,
        *,
        name="daytona",
        renews=None,
        create_seconds=0,
        renew_seconds=0,
        kill_returns_false=False,
        gone_after_kill=True,
        failing_upload=None,
    ):
        super().__init__()
        self.name = name
        self.clock = clock
        self.create_seconds = create_seconds
        self.renew_seconds = renew_seconds
        self.kill_returns_false = kill_returns_false
        self.gone_after_kill = gone_after_kill
        self.failing_upload = failing_upload
        self.live = {}
        self.created = []
        self.renewals = []
        if renews is not None:
            self.renews_ttl = renews

    def _advance(self, seconds):
        if self.clock is not None and seconds:
            self.clock.advance(seconds)

    def create(self, params, **kwargs):
        self.params = params
        self._advance(self.create_seconds)
        sandbox = _ChatSandbox()
        sandbox.id = f"sandbox-{len(self.created) + 1}"
        if self.failing_upload:
            failing = self.failing_upload
            upload = sandbox.fs.upload_file

            def refuse(content, path):
                if path == failing:
                    raise RuntimeError("upload failed")
                upload(content, path)

            sandbox.fs.upload_file = refuse
        self.created.append(sandbox.id)
        self.live[sandbox.id] = sandbox
        self.sandbox = sandbox
        return sandbox

    def get(self, sandbox_id, request_timeout=None):
        if sandbox_id not in self.live:
            raise SandboxNotFoundError("sandbox not found", status_code=404)
        return self.live[sandbox_id]

    def delete(self, sandbox, **kwargs):
        self.lifecycle.append("delete")
        if self.kill_returns_false:
            if self.gone_after_kill:
                self.live.pop(sandbox.id, None)
            return False
        self.live.pop(sandbox.id, None)
        return None

    def renew_ttl(self, sandbox, ttl_seconds):
        self.renewals.append((self.clock.now if self.clock else None, ttl_seconds))
        self._advance(self.renew_seconds)


class _FixProvider(_E2B):
    def __init__(
        self,
        clock=None,
        *,
        renews=None,
        renew_seconds=0,
        kill_returns_false=False,
        gone_after_kill=True,
    ):
        super().__init__()
        self.clock = clock
        self.renew_seconds = renew_seconds
        self.kill_returns_false = kill_returns_false
        self.gone_after_kill = gone_after_kill
        if renews is not None:
            self.renews_ttl = renews

    def delete(self, sandbox, *, timeout, wait):
        if not self.kill_returns_false:
            return super().delete(sandbox, timeout=timeout, wait=wait)
        self.deleted.append(sandbox.id)
        if self.gone_after_kill:
            self.live.pop(sandbox.id, None)
        return False

    def renew_ttl(self, sandbox, ttl_seconds):
        super().renew_ttl(sandbox, ttl_seconds)
        if self.clock is not None and self.renew_seconds:
            self.clock.advance(self.renew_seconds)


# --- run launch -------------------------------------------------------------------------


def _launch(organization, settings, monkeypatch, client):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    payload["retry"]["max_infrastructure_attempts"] = 3
    job, _ = create_hosted_job(organization, payload, idempotency_key=str(uuid.uuid4()))
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    settings.ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS = 3600
    settings.ALK_HOSTED_SANDBOX_TTL_SECONDS = 7200
    monkeypatch.setattr(
        f"{_GATEWAY}._platform_simulator_material",
        lambda: {
            "ALK_HARNESS": "vertex-gemini",
            "ALK_HARNESS_MODEL": "gemini-3.7-flash",
            "AGENTCC_BASE_URL": "https://gateway.example.test",
            "AGENTCC_API_KEY": "harness-virtual-key",
        },
    )
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = "sha256:" + "b" * 64
    with patch(
        f"{_GATEWAY}.HostedSourceAcquirer.acquire", return_value=(b"archive", "")
    ):
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    attempt.refresh_from_db()
    job.refresh_from_db()
    return job, attempt


def test_launch_refusal_creates_no_sandbox_and_is_not_retried(
    organization, settings, monkeypatch, refuse_sandbox
):
    client = _Provider()

    job, attempt = _launch(organization, settings, monkeypatch, client)

    assert client.created == []
    assert attempt.state == HostedHarnessAttempt.State.FAILED
    assert attempt.terminal_failure["domain"] == "environment"
    assert attempt.terminal_failure["code"] == "usage_limit"
    # The job allowed three infrastructure attempts: a refusal still takes none.
    assert job.state != HostedHarnessJob.State.RETRY_WAIT
    receipt = HostedHarnessCleanupReceipt.no_workspace_objects.get(attempt=attempt)
    assert receipt.details["sandbox_created"] is False
    assert job.payload["metadata"]["usage_limit"]["error_code"] == "BUDGET_PAUSED"


def test_launch_records_provider_and_lifetime(
    organization, settings, monkeypatch, clock, create_checks
):
    before_create = clock.now
    client = _Provider(clock, create_seconds=300)

    job, attempt = _launch(organization, settings, monkeypatch, client)

    observation = attempt.sandbox_runtime
    assert create_checks.asked == [str(job.organization_id)]
    assert observation["provider"] == "daytona"
    assert observation["lifetime_seconds"] == client.params.ttl_seconds
    # The provider's lifetime starts inside create; the shown timer after it.
    assert _at(observation["armed_at"]) == before_create
    assert _at(observation["started_at"]) == before_create + timedelta(seconds=300)


@pytest.mark.parametrize("renews", [True, None], ids=["renewing", "not_renewing"])
def test_launch_rearms_after_uploads_only_on_a_renewing_provider(
    organization, settings, monkeypatch, clock, create_checks, renews
):
    armed = clock.now
    client = _Provider(clock, renews=renews, create_seconds=300, renew_seconds=5)

    _job, attempt = _launch(organization, settings, monkeypatch, client)

    [(renewal_started, ttl)] = client.renewals
    observation = attempt.sandbox_runtime
    if renews:
        assert _at(observation["armed_at"]) == renewal_started
        assert observation["lifetime_seconds"] == ttl
    else:
        assert _at(observation["armed_at"]) == armed


def test_launch_allows_when_billing_cannot_load(organization, settings, monkeypatch):
    try:
        from ee.usage.services.config import BillingConfigError
    except ImportError:  # the open build has no billing config at all

        class BillingConfigError(Exception):
            pass

    def unavailable(*_args, **_kwargs):
        raise BillingConfigError("no billing config on this host")

    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    monkeypatch.setattr(harness_usage, "check_harness_action", unavailable)
    client = _Provider()

    _job, attempt = _launch(organization, settings, monkeypatch, client)

    assert client.created == ["sandbox-1"]
    assert attempt.state == HostedHarnessAttempt.State.RUNNING


# --- chat sandboxes ---------------------------------------------------------------------


@pytest.fixture
def chat_gateway(monkeypatch, settings):
    """A finished job's chat sandbox started against a fake provider."""
    settings.ALK_HOSTED_CHAT_TTL_SECONDS = CHAT_TTL
    settings.HARNESS_PUBLIC_BASE_URL = PLATFORM
    for name, value in (
        ("load_workspace_archive", lambda _conversation: None),
        ("_authoring_archive_for", lambda _job: None),
        ("_platform_simulator_material", lambda: {}),
        ("_resolved_egress_domains", lambda *_args: {"platform.example"}),
    ):
        monkeypatch.setattr(f"{_GATEWAY}.{name}", value)
    monkeypatch.setattr(
        f"{_GATEWAY}.HostedSourceAcquirer.acquire", lambda *_: (b"source", None)
    )

    def build(client):
        gateway = object.__new__(HostedHarnessGateway)
        gateway.client = client
        return gateway

    return build


def _finished_job(organization, workspace=None):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key=str(uuid.uuid4()), workspace=workspace
    )
    job.state = HostedHarnessJob.State.FAILED
    job.current_stage = "failed"
    job.save(update_fields=["state", "current_stage", "updated_at"])
    return job, ensure_conversation(job)


def _start_chat(gateway, job, conversation):
    return gateway.ensure_conversation_runtime(
        job, conversation=conversation, endpoint_base_url=PLATFORM
    )


def _report_alive(conversation, at):
    HostedHarnessConversationLease.no_workspace_objects.filter(
        conversation=conversation
    ).update(state=HostedHarnessConversationLease.State.ACTIVE, heartbeat_at=at)


def _open_chat_row(job, conversation, ref, *, provider="daytona"):
    sandbox_usage.open_sandbox_lifetime(
        kind=HostedSandboxLifetime.Kind.CHAT,
        provider_name=provider,
        provider_ref=ref,
        organization_id=job.organization_id,
        workspace_id=job.workspace_id,
        owner_id=conversation.id,
        lifetime_seconds=CHAT_TTL,
        armed_at=timezone.now(),
    )
    return _row(ref)


def test_chat_sandbox_opens_a_row(
    organization, workspace, clock, metering, create_checks, chat_gateway
):
    job, conversation = _finished_job(organization, workspace)
    armed = clock.now
    client = _Provider(clock, create_seconds=120)

    _start_chat(chat_gateway(client), job, conversation)

    row = _row("sandbox-1")
    assert create_checks.asked == [str(job.organization_id)]
    assert (row.kind, row.provider, row.owner_id) == (
        "chat",
        "daytona",
        conversation.id,
    )
    assert (row.organization_id, row.workspace_id) == (organization.id, workspace.id)
    assert row.lifetime_seconds == CHAT_TTL
    assert row.armed_at == armed
    assert row.started_at == armed + timedelta(seconds=120)
    assert row.lifetime_ends_at == armed + timedelta(seconds=CHAT_TTL)
    assert row.ended_at is None


def test_chat_check_refusal_creates_no_sandbox(
    organization, metering, refuse_sandbox, chat_gateway
):
    job, conversation = _finished_job(organization)
    client = _Provider()

    with pytest.raises(HostedHarnessError) as refused:
        _start_chat(chat_gateway(client), job, conversation)

    assert (refused.value.code, refused.value.status_code) == ("usage_limit", 402)
    assert client.created == []
    assert not HostedSandboxLifetime.no_workspace_objects.exists()


@pytest.mark.parametrize("person_message_too", [False, True])
def test_builder_check_sandbox_is_free(
    organization, clock, metering, create_checks, chat_gateway, person_message_too
):
    job, conversation = _finished_job(organization)
    attempt_id = str(uuid.uuid4())
    # The build message went to the run's own sandbox, which took it.
    send_to_harness(
        job,
        content="Build the test environment",
        client_request_id=f"build-{attempt_id}",
        endpoint_base_url=PLATFORM,
    )
    conversation.refresh_from_db()
    conversation.command_acked_through = conversation.next_message_sequence - 1
    conversation.save(update_fields=["command_acked_through", "updated_at"])
    from simulate.services.hosted_harness import _ask_builder_to_check

    _ask_builder_to_check(str(job.id), attempt_id)
    assert HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation=conversation, client_request_id=f"check-{attempt_id}"
    ).exists()
    if person_message_too:
        enqueue_message(job, content="And add a refund case", client_request_id="p-1")
    create_checks.refuse = True
    client = _Provider()

    if person_message_too:
        with pytest.raises(HostedHarnessError):
            _start_chat(chat_gateway(client), job, conversation)
        assert create_checks.asked == [str(job.organization_id)]
        assert client.created == []
        return

    _start_chat(chat_gateway(client), job, conversation)

    row = _row("sandbox-1")
    assert create_checks.asked == []
    assert row.kind == "platform_check"
    # Nobody deletes it: the sweep closes it once its lifetime is over.
    clock.advance(CHAT_TTL + 11 * 60)
    sandbox_usage.sweep_sandbox_lifetimes()
    row.refresh_from_db()
    assert row.ended_at == row.lifetime_ends_at
    assert row.settled_at is not None
    assert row.billed_minutes == 0
    assert metering == []


@pytest.mark.parametrize("renews", [True, None], ids=["renewing", "not_renewing"])
def test_chat_renewal_rearms_only_on_a_renewing_provider(
    organization, clock, metering, chat_gateway, renews
):
    job, conversation = _finished_job(organization)
    client = _Provider(clock, renews=renews, renew_seconds=5)
    client.live["sandbox-1"] = _ChatSandbox()
    armed = clock.now
    _open_chat_row(job, conversation, "sandbox-1")
    lease = issue_conversation_capability(
        conversation,
        endpoint_base_url=PLATFORM,
        provider_ref="sandbox-1",
        attempt=None,
        ttl_seconds=CHAT_TTL,
    ).lease
    clock.advance(10 * 60)
    renewed_at = clock.now

    chat_gateway(client)._renew_chat_idle_window(lease)

    row = _row("sandbox-1")
    if renews:
        assert row.armed_at == renewed_at
        assert row.lifetime_ends_at == renewed_at + timedelta(seconds=CHAT_TTL)
    else:
        assert row.armed_at == armed
        assert row.lifetime_ends_at == armed + timedelta(seconds=CHAT_TTL)


def test_chat_start_failure_is_free(
    organization, clock, metering, create_checks, chat_gateway
):
    job, conversation = _finished_job(organization)
    client = _Provider(clock, failing_upload="/work/source.tar.gz")

    with pytest.raises(HostedHarnessError):
        _start_chat(chat_gateway(client), job, conversation)

    row = _row("sandbox-1")
    assert row.ended_at is not None
    assert (row.end_reason, row.failure_domain) == ("start_failed", "infrastructure")
    assert sandbox_usage.seal_sandbox_lifetime(str(row.id)) == "settled"
    row.refresh_from_db()
    assert row.billed_minutes == 0
    assert metering == []


def test_claim_released_during_start_is_free(
    organization, metering, create_checks, chat_gateway, monkeypatch
):
    job, conversation = _finished_job(organization)
    client = _Provider()
    monkeypatch.setattr(
        f"{_GATEWAY}.issue_conversation_capability", lambda *_a, **_k: None
    )

    started_lease = chat_gateway(client)._start_chat_sandbox(
        job,
        conversation=conversation,
        endpoint_base_url=PLATFORM,
        claim_ref="pending:given-up",
    )

    assert started_lease is None
    row = _row("sandbox-1")
    assert row.ended_at is not None
    assert (row.end_reason, row.failure_domain) == ("claim_released", "infrastructure")


@pytest.mark.parametrize("gone", [True, False], ids=["gone", "still_there"])
@pytest.mark.parametrize("site", ["chat_retire", "chat_start_failed", "fix_release"])
def test_e2b_delete_of_a_gone_sandbox_closes_the_row(
    site,
    gone,
    organization,
    user,
    workspace,
    metering,
    create_checks,
    chat_gateway,
    fix_settings,
    monkeypatch,
):
    if site == "fix_release":
        provider = _FixProvider(kill_returns_false=True, gone_after_kill=gone)
        monkeypatch.setattr(agent_fix_sandbox, "get_sandbox_provider", lambda: provider)
        fix_run = _new_fix(user, workspace)
        agent_fix_sandbox.launch(str(fix_run.id))
        agent_fix_sandbox.release(str(fix_run.id))
    else:
        job, conversation = _finished_job(organization)
        client = _Provider(
            name="e2b",
            kill_returns_false=True,
            gone_after_kill=gone,
            failing_upload="/work/source.tar.gz"
            if site == "chat_start_failed"
            else None,
        )
        gateway = chat_gateway(client)
        if site == "chat_retire":
            client.live["sandbox-1"] = _ChatSandbox()
            _open_chat_row(job, conversation, "sandbox-1", provider="e2b")
            gateway._delete_chat_sandbox("sandbox-1", conversation.id)
        else:
            with pytest.raises(RuntimeError):
                gateway._start_chat_sandbox(
                    job, conversation=conversation, endpoint_base_url=PLATFORM
                )

    row = _row("sandbox-1")
    assert (row.ended_at is not None) is gone


def _lose_the_first_lifetime_write(monkeypatch):
    """The first write to an open row fails, as a dropped connection would; the writes
    after it go through."""
    open_row = sandbox_usage._open_row
    calls = []

    def flaky(provider_ref):
        calls.append(provider_ref)
        if len(calls) == 1:
            raise RuntimeError("the connection was lost")
        return open_row(provider_ref)

    monkeypatch.setattr(sandbox_usage, "_open_row", flaky)
    return calls


@pytest.mark.parametrize(
    "site",
    [
        "chat_retire",
        "claim_released",
        "chat_start_failed",
        "fix_release",
        "fix_release-engine_dead_unseen",
    ],
)
def test_a_lost_reason_write_still_frees_our_failure(
    site,
    organization,
    user,
    workspace,
    clock,
    metering,
    create_checks,
    chat_gateway,
    fix_settings,
    monkeypatch,
):
    if site == "fix_release":
        _fix_provider(monkeypatch, _FixProvider(clock))
        fix_run = _new_fix(user, workspace)
        agent_fix_sandbox.launch(str(fix_run.id))
        _set_fix(fix_run, state=_FAILED, failure={"code": "fix_engine_died"})
        calls = _lose_the_first_lifetime_write(monkeypatch)
        clock.advance(10 * 60)
        agent_fix_sandbox.release(str(fix_run.id))
    elif site == "fix_release-engine_dead_unseen":
        # Silent since its launch, the engine is past its report timeout when the fix
        # reaches its deadline: the ending is ours only because it died unseen.
        _fix_provider(monkeypatch, _FixProvider(clock))
        fix_run = _new_fix(user, workspace)
        agent_fix_sandbox.launch(str(fix_run.id))
        clock.advance(15 * 60)
        _set_fix(
            fix_run,
            state=_FAILED,
            failure={"code": "fix_deadline"},
            finished_at=clock.now,
        )
        calls = _lose_the_first_lifetime_write(monkeypatch)
        agent_fix_sandbox.release(str(fix_run.id))
    else:
        job, conversation = _finished_job(organization)
        client = _Provider(
            clock,
            failing_upload=(
                "/work/source.tar.gz" if site == "chat_start_failed" else None
            ),
        )
        gateway = chat_gateway(client)
        calls = _lose_the_first_lifetime_write(monkeypatch)
        if site == "chat_retire":
            client.live["sandbox-1"] = _ChatSandbox()
            _open_chat_row(job, conversation, "sandbox-1")
            silent_since = clock.now
            clock.advance(10 * 60)
            gateway._delete_chat_sandbox(
                "sandbox-1",
                conversation.id,
                reason="chat_runtime_stopped",
                failure_domain="infrastructure",
                last_seen=silent_since,
            )
        elif site == "claim_released":
            monkeypatch.setattr(
                f"{_GATEWAY}.issue_conversation_capability", lambda *_a, **_k: None
            )
            gateway._start_chat_sandbox(
                job,
                conversation=conversation,
                endpoint_base_url=PLATFORM,
                claim_ref="pending:given-up",
            )
        else:
            with pytest.raises(RuntimeError):
                gateway._start_chat_sandbox(
                    job, conversation=conversation, endpoint_base_url=PLATFORM
                )

    # The reason written before the delete was lost; the close still records it.
    assert len(calls) == 2
    row = _row("sandbox-1")
    assert row.ended_at is not None
    assert row.failure_domain == "infrastructure"
    assert sandbox_usage.seal_sandbox_lifetime(str(row.id)) == "settled"
    assert metering == []


# The sandbox is no longer there when it is deleted. Seen alive a minute before, early
# in its life (lost on our side), or 30 s before its limit and found after it (expired).
@pytest.mark.parametrize(
    "site, found_at_minute, seen_at_minute, expected, minutes",
    [
        (
            "retire_agent_changed",
            10,
            9,
            ("agent_changed", "infrastructure"),
            None,
        ),
        (
            "retire_agent_changed",
            31,
            29.5,
            ("agent_changed", ""),
            "30.0000",
        ),
        # A fenced-out lease says nothing about when its sandbox went: as recorded.
        ("retire_lease_expired", 10, None, ("lease_expired", ""), "10.0000"),
        ("rebuild_stop", 10, 9, ("deleted", "infrastructure"), None),
        # A late stop: the fenced lease's heartbeat at 10 says nothing about a sandbox
        # found gone after its limit, so the recorded ending stands.
        ("rebuild_stop", 45, 10, ("deleted", ""), "30.0000"),
        ("run_cleanup", 10, 9, ("sandbox_gone", "infrastructure"), None),
        ("run_cleanup", 31, 29.5, ("lifetime_elapsed", ""), "30.0000"),
    ],
    ids=[
        "retire_seen_a_minute_before",
        "retire_seen_up_to_its_limit",
        "retire_fenced_out",
        "rebuild_stop_seen_a_minute_before",
        "rebuild_stop_found_after_its_limit",
        "run_cleanup_seen_a_minute_before",
        "run_cleanup_seen_up_to_its_limit",
    ],
)
def test_chat_sandbox_already_gone_when_deleted(
    site,
    found_at_minute,
    seen_at_minute,
    expected,
    minutes,
    organization,
    clock,
    metering,
    chat_gateway,
):
    job, conversation = _finished_job(organization)
    client = _Provider(clock)
    gateway = chat_gateway(client)
    t0 = clock.now
    _open_chat_row(job, conversation, "sandbox-1")
    seen = None if seen_at_minute is None else t0 + timedelta(minutes=seen_at_minute)
    issue_conversation_capability(
        conversation,
        endpoint_base_url=PLATFORM,
        provider_ref="sandbox-1",
        attempt=None,
        ttl_seconds=CHAT_TTL,
        control_only=site == "run_cleanup",
    )
    if seen is not None:
        _report_alive(conversation, seen)
    leases = HostedHarnessConversationLease.no_workspace_objects.filter(
        conversation=conversation
    )
    if site == "rebuild_stop":
        # The closed rebuild fenced the lease; it still carries the sandbox.
        leases.update(state=HostedHarnessConversationLease.State.EXPIRED)
    elif site.startswith("retire_"):
        # The retire has already handed the lease to the sandbox starting next.
        leases.update(provider_ref="pending:next")
    clock.now = t0 + timedelta(minutes=found_at_minute)

    if site == "run_cleanup":
        gateway._cleanup_conversation_runtime(job)
    elif site == "rebuild_stop":
        gateway._delete_chat_sandbox("sandbox-1", conversation.id)
    else:
        reason = site.removeprefix("retire_")
        gateway._delete_chat_sandbox(
            "sandbox-1", conversation.id, reason=reason, last_seen=seen
        )

    row = _row("sandbox-1")
    assert row.ended_at == clock.now
    assert (row.end_reason, row.failure_domain) == expected
    sandbox_usage.seal_sandbox_lifetime(str(row.id))
    assert metering == ([] if minutes is None else [(row.id, Decimal(minutes))])


def _lease(*, state, now, expires_in, heartbeat_ago):
    return SimpleNamespace(
        state=state,
        expires_at=now + timedelta(seconds=expires_in),
        heartbeat_at=now - timedelta(seconds=heartbeat_ago),
    )


@pytest.mark.parametrize(
    "state, expires_in, heartbeat_ago, expected",
    [
        ("expired", 1200, 5, ("lease_expired", "", None)),
        ("released", 1200, 5, ("lease_expired", "", None)),
        ("active", 60, 5, ("idle_window_over", "", "heartbeat")),
        ("active", 61, 5, ("agent_changed", "", "heartbeat")),
        (
            "active",
            1200,
            RUNTIME_STALE_SECONDS + 1,
            ("chat_runtime_stopped", "infrastructure", "heartbeat"),
        ),
        # Silent since minute 5 of a 30-minute window, retired at minute 30.
        (
            "active",
            0,
            25 * 60,
            ("chat_runtime_stopped", "infrastructure", "heartbeat"),
        ),
        # Issued with the chat's lifetime, its start never finished.
        (
            "starting",
            19 * 60,
            11 * 60,
            ("chat_runtime_stopped", "infrastructure", "heartbeat"),
        ),
        (
            "starting",
            30,
            11 * 60,
            ("chat_runtime_stopped", "infrastructure", "heartbeat"),
        ),
    ],
    ids=[
        "expired",
        "released",
        "idle exactly 60 s",
        "live 61 s",
        "silent",
        "silent_and_window_over",
        "never started",
        "never started, in its last minute",
    ],
)
def test_retired_chat_ending(state, expires_in, heartbeat_ago, expected):
    now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    lease = _lease(
        state=state, now=now, expires_in=expires_in, heartbeat_ago=heartbeat_ago
    )

    ending = _retired_chat_ending(lease, now)

    if expected[2] == "heartbeat":
        expected = (*expected[:2], lease.heartbeat_at)
    assert ending == expected


# No message after the start: its idle window and its lifetime both end at minute 30. Its
# process stops at minute 5 (ours) or 10 s before the limit (an expiry); the next message
# comes at minute 33, after the window.
@pytest.mark.parametrize(
    "stops_at_minute", [5, 29 + 50 / 60], ids=["died_at_5", "died_at_29_50"]
)
def test_daytona_chat_silent_when_its_idle_window_ended(
    organization, clock, metering, create_checks, chat_gateway, stops_at_minute
):
    job, conversation = _finished_job(organization)
    client = _Provider(clock)
    gateway = chat_gateway(client)
    t0 = clock.now
    _start_chat(gateway, job, conversation)
    _report_alive(conversation, t0 + timedelta(minutes=stops_at_minute))
    lease = HostedHarnessConversationLease.no_workspace_objects.get(
        conversation=conversation
    )
    assert lease.expires_at == t0 + timedelta(minutes=30)
    clock.advance(33 * 60)

    _start_chat(gateway, job, conversation)

    row = _row("sandbox-1")
    assert row.ended_at == t0 + timedelta(minutes=33)
    sandbox_usage.seal_sandbox_lifetime(str(row.id))
    if stops_at_minute > 29:
        assert (row.end_reason, row.failure_domain) == ("lifetime_elapsed", "")
        assert metering == [(row.id, Decimal("30.0000"))]
    else:
        assert (row.end_reason, row.failure_domain) == (
            "chat_runtime_stopped",
            "infrastructure",
        )
        assert metering == []


# Stopped 10 s before the limit (an expiry), or at minute 21, long before it (ours).
@pytest.mark.parametrize(
    "stops_at_minute", [29 + 50 / 60, 21], ids=["near_cap", "early"]
)
def test_daytona_chat_used_after_its_lifetime_is_charged_to_it(
    organization, clock, metering, create_checks, chat_gateway, stops_at_minute
):
    job, conversation = _finished_job(organization)
    client = _Provider(clock)
    gateway = chat_gateway(client)
    t0 = clock.now
    _start_chat(gateway, job, conversation)
    _report_alive(conversation, t0)
    # A message at minute 20 renews the idle window; Daytona's own lifetime stays.
    clock.advance(20 * 60)
    _report_alive(conversation, clock.now)
    _start_chat(gateway, job, conversation)
    lease = HostedHarnessConversationLease.no_workspace_objects.get(
        conversation=conversation
    )
    assert lease.expires_at == t0 + timedelta(minutes=50)
    assert _row("sandbox-1").lifetime_ends_at == t0 + timedelta(minutes=30)
    _report_alive(conversation, t0 + timedelta(minutes=stops_at_minute))
    # The next message, at minute 35, finds it gone.
    clock.advance(15 * 60)

    _start_chat(gateway, job, conversation)

    row = _row("sandbox-1")
    assert row.ended_at == t0 + timedelta(minutes=35)
    sandbox_usage.seal_sandbox_lifetime(str(row.id))
    if stops_at_minute > 29:
        assert (row.end_reason, row.failure_domain) == ("lifetime_elapsed", "")
        assert metering == [(row.id, Decimal("30.0000"))]
    else:
        assert (row.end_reason, row.failure_domain) == (
            "chat_runtime_stopped",
            "infrastructure",
        )
        assert metering == []


def _waiting_chat(organization, clock, *, heartbeat_at, ref="sandbox-1"):
    job, conversation = _finished_job(organization)
    enqueue_message(job, content="hello", client_request_id=str(uuid.uuid4()))
    _open_chat_row(job, conversation, ref)
    issue_conversation_capability(
        conversation,
        endpoint_base_url=PLATFORM,
        provider_ref=ref,
        attempt=None,
        ttl_seconds=3 * CHAT_TTL,
    )
    _report_alive(conversation, heartbeat_at)
    return job, conversation


@pytest.mark.parametrize(
    "last_seen_before_cap, expected",
    [
        (30, ("lifetime_elapsed", "")),
        (10 * 60, ("chat_stopped_responding", "infrastructure")),
    ],
    ids=["seen_30_s_before", "seen_10_min_before"],
)
def test_abandoned_chat_after_its_lifetime_is_charged(
    organization, clock, metering, last_seen_before_cap, expected
):
    cap = clock.now + timedelta(seconds=CHAT_TTL)
    _job, conversation = _waiting_chat(
        organization,
        clock,
        heartbeat_at=cap - timedelta(seconds=last_seen_before_cap),
    )
    # Long enough after its last poll that its waiting message is given up.
    clock.now = cap + timedelta(minutes=16)

    assert _abandon_outstanding(conversation.id) == 1

    row = _row("sandbox-1")
    assert (row.end_reason, row.failure_domain) == expected


def test_abandoned_chat_is_free(organization, clock, metering):
    t0 = clock.now
    _job, conversation = _waiting_chat(
        organization, clock, heartbeat_at=t0 + timedelta(minutes=1)
    )
    clock.advance(17 * 60)

    assert _abandon_outstanding(conversation.id) == 1

    row = _row("sandbox-1")
    assert (row.end_reason, row.failure_domain) == (
        "chat_stopped_responding",
        "infrastructure",
    )
    sandbox_usage.close_sandbox_lifetime(provider_ref="sandbox-1")
    sandbox_usage.seal_sandbox_lifetime(str(row.id))
    assert metering == []


def test_abandon_whose_ending_was_not_recorded_waits_for_the_next_pass(
    organization, clock, metering, monkeypatch
):
    t0 = clock.now
    refs = ("sandbox-1", "sandbox-2")
    for ref in refs:
        _waiting_chat(
            organization, clock, heartbeat_at=t0 + timedelta(minutes=1), ref=ref
        )
    clock.advance(17 * 60)
    calls = _lose_the_first_lifetime_write(monkeypatch)
    scheduled = []

    first = recover_stalled_conversations(scheduled.append)

    # One conversation's ending was lost: its abandon rolled back whole, and the other
    # conversation was still abandoned.
    assert len(calls) == 2
    assert (first["abandoned"], first["errors"]) == (1, 1)
    leases = {
        lease.provider_ref: lease
        for lease in HostedHarnessConversationLease.no_workspace_objects.filter(
            provider_ref__in=refs
        ).select_related("conversation")
    }
    [kept] = [
        lease
        for lease in leases.values()
        if lease.state == HostedHarnessConversationLease.State.ACTIVE
    ]
    assert (_row(kept.provider_ref).end_reason, _row(kept.provider_ref).ended_at) == (
        "",
        None,
    )
    assert _unacked_commands(kept.conversation).exists()

    second = recover_stalled_conversations(scheduled.append)

    assert (second["abandoned"], second["errors"]) == (1, 0)
    for ref in refs:
        assert (_row(ref).end_reason, _row(ref).failure_domain) == (
            "chat_stopped_responding",
            "infrastructure",
        )
    assert scheduled == []


def test_chat_whose_saved_files_are_gone_is_billed(organization, clock, metering):
    t0 = clock.now
    _job, conversation = _waiting_chat(organization, clock, heartbeat_at=t0)
    clock.advance(5 * 60)

    retire_without_saved_workspace(conversation.id)

    row = _row("sandbox-1")
    assert (row.end_reason, row.failure_domain) == ("saved_workspace_gone", "")
    sandbox_usage.close_sandbox_lifetime(provider_ref="sandbox-1")
    sandbox_usage.seal_sandbox_lifetime(str(row.id))
    assert metering == [(row.id, Decimal("5.0000"))]


def test_failed_chat_delete_leaves_the_row_for_the_sweep(
    organization, metering, chat_gateway
):
    job, conversation = _finished_job(organization)
    client = _Provider()
    client.live["sandbox-1"] = _ChatSandbox()
    _open_chat_row(job, conversation, "sandbox-1")

    def refuse(_sandbox, **_kwargs):
        raise RuntimeError("provider unavailable")

    client.delete = refuse

    chat_gateway(client)._delete_chat_sandbox(
        "sandbox-1", conversation.id, reason="lease_expired"
    )

    row = _row("sandbox-1")
    assert row.ended_at is None
    assert row.end_reason == "lease_expired"


def test_run_cleanup_closes_the_chat_row(organization, metering, chat_gateway):
    job, conversation = _finished_job(organization)
    client = _Provider()
    client.live["sandbox-1"] = _ChatSandbox()
    _open_chat_row(job, conversation, "sandbox-1")
    issue_conversation_capability(
        conversation,
        endpoint_base_url=PLATFORM,
        provider_ref="sandbox-1",
        attempt=None,
        ttl_seconds=CHAT_TTL,
        control_only=True,
    )

    chat_gateway(client)._cleanup_conversation_runtime(job)

    row = _row("sandbox-1")
    assert row.ended_at is not None
    assert (row.end_reason, row.failure_domain) == ("deleted", "")


# --- rebuilds ---------------------------------------------------------------------------


@pytest.fixture
def rebuilds_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_VERSIONING_NUDGES_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    settings.HARNESS_PUBLIC_BASE_URL = PLATFORM


def _running_rebuild(user, workspace):
    """An environment whose rebuild's builder message is in its chat, and the chat's own
    sandbox, open and silent."""
    environment, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key=str(uuid.uuid4()),
        workspace=workspace,
    )
    conversation, command, _ = enqueue_message(
        environment, content="Rebuild it", client_request_id=str(uuid.uuid4())
    )
    nudge = HarnessNudge.no_workspace_objects.create(
        job=environment,
        organization=user.organization,
        workspace=workspace,
        target=HarnessNudge.Target.ENVIRONMENT,
        state=HarnessNudge.State.QUEUED,
        command_message=command,
    )
    _open_chat_row(environment, conversation, "sandbox-1")
    issue_conversation_capability(
        conversation,
        endpoint_base_url=PLATFORM,
        provider_ref="sandbox-1",
        attempt=None,
        ttl_seconds=CHAT_TTL,
    )
    _report_alive(conversation, timezone.now() - timedelta(minutes=5))
    return environment, nudge


@pytest.mark.parametrize(
    "code, expected",
    [
        ("turn_failed", ("", "")),
        ("turn_interrupted", ("", "")),
        ("no_report", ("", "")),
        ("landing_failed", ("", "")),
        ("stopped_by_user", ("", "")),
        ("stopped_by_newer", ("", "")),
        ("checks_failed", ("", "")),
        ("not_runnable", ("", "")),
        ("draft_changed", ("", "")),
        ("scenario_key_taken", ("", "")),
        ("", ("", "")),
        (None, ("", "")),
        ("rebuild_not_started", ("rebuild_not_started", "infrastructure")),
        ("rebuild_stuck", ("rebuild_stuck", "infrastructure")),
    ],
)
def test_rebuild_ending(
    code,
    expected,
    user,
    workspace,
    metering,
    rebuilds_on,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from simulate.tasks import harness_nudges as nudge_tasks

    stops = []
    monkeypatch.setattr(
        nudge_tasks, "schedule_sandbox_stop", lambda *args: stops.append(args)
    )
    monkeypatch.setattr(nudge_tasks, "schedule_nudge_check_task", lambda *_args: None)
    environment, nudge = _running_rebuild(user, workspace)

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            locked = nudges._head(environment.id, lock=True)
            rebuild.close_nudge(
                rebuild._lock_nudge(locked, nudge.id),
                locked=locked,
                state=(
                    HarnessNudge.State.APPLIED
                    if code is None
                    else HarnessNudge.State.FAILED
                ),
                error=None if code is None else {"code": code},
            )

    row = _row("sandbox-1")
    assert (row.end_reason, row.failure_domain) == expected
    # The stop task is the one that deletes it, and is not run here.
    assert [ref for _conversation, ref in stops] == ["sandbox-1"]
    assert row.ended_at is None


def _attempt_entry(nudge, command):
    nudge.refresh_from_db()
    return next(
        entry
        for entry in (nudge.usage or {}).get("attempts") or []
        if entry["command_message_id"] == str(command.id)
    )


def test_turn_start_records_the_sandbox_it_ran_in(
    user,
    workspace,
    storage,
    scheduled,
    started,
    inline,
    rebuilds_on,
    auth_client,
    django_capture_on_commit_callbacks,
):
    environment, nudge, _a1, _a2 = _queued(
        user,
        workspace,
        storage,
        "r-sandbox-refs",
        auth_client,
        django_capture_on_commit_callbacks,
    )
    assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
    nudge.refresh_from_db()
    command = nudge.command_message
    conversation = command.conversation

    def sandbox(ref):
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url=PLATFORM,
            provider_ref=ref,
            attempt=None,
            ttl_seconds=600,
        )
        return APIClient(), _headers(capability)

    interrupt = enqueue_message(
        environment, content="", client_request_id="stop-it", kind="interrupt"
    )[1]
    ran = {"command_message_id": str(command.id)}
    client, headers = sandbox("sandbox-a")
    with django_capture_on_commit_callbacks(execute=True):
        _send(
            client,
            conversation,
            headers,
            0,
            (1, "turn_started", ran),
            (2, "turn_interrupted", {**ran, "interrupt_message_id": str(interrupt.id)}),
        )
    nudge.refresh_from_db()
    taken_at = nudge.draft_snapshot["taken_at"]
    assert _attempt_entry(nudge, command)["sandbox_refs"] == ["sandbox-a"]

    client, headers = sandbox("sandbox-b")
    with django_capture_on_commit_callbacks(execute=True):
        _send(client, conversation, headers, 0, (3, "turn_started", ran))
    with django_capture_on_commit_callbacks(execute=True):
        _send(client, conversation, headers, 0, (4, "turn_started", ran))

    assert _attempt_entry(nudge, command)["sandbox_refs"] == ["sandbox-a", "sandbox-b"]
    nudge.refresh_from_db()
    assert nudge.draft_snapshot["taken_at"] == taken_at


def test_a_turn_start_after_the_rebuild_closed_records_no_sandbox(
    user,
    workspace,
    storage,
    scheduled,
    started,
    inline,
    rebuilds_on,
    auth_client,
    django_capture_on_commit_callbacks,
):
    environment, nudge, _a1, _a2 = _queued(
        user,
        workspace,
        storage,
        "r-late-start",
        auth_client,
        django_capture_on_commit_callbacks,
    )
    assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
    nudge.refresh_from_db()
    command = nudge.command_message
    conversation = command.conversation
    with transaction.atomic():
        locked = nudges._head(environment.id, lock=True)
        rebuild.close_nudge(
            rebuild._lock_nudge(locked, nudge.id),
            locked=locked,
            state=HarnessNudge.State.FAILED,
            error={"code": "rebuild_not_started"},
        )
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url=PLATFORM,
        provider_ref="sandbox-late",
        attempt=None,
        ttl_seconds=600,
    )

    with django_capture_on_commit_callbacks(execute=True):
        _send(
            APIClient(),
            conversation,
            _headers(capability),
            0,
            (1, "turn_started", {"command_message_id": str(command.id)}),
        )

    nudge.refresh_from_db()
    for entry in (nudge.usage or {}).get("attempts") or []:
        assert not entry.get("sandbox_refs")


def _finish_command(message_id) -> None:
    """The chat answered this builder message, so it is idle for the next one."""
    message = HostedHarnessConversationMessage.no_workspace_objects.get(id=message_id)
    message.state = "completed"
    message.save(update_fields=["state"])
    HostedHarnessConversation.no_workspace_objects.filter(
        id=message.conversation_id
    ).update(command_acked_through=message.sequence, state="warm_idle")


def test_each_attempt_keeps_its_own_ending(
    user,
    workspace,
    storage,
    scheduled,
    started,
    inline,
    rebuilds_on,
    auth_client,
    django_capture_on_commit_callbacks,
):
    environment, nudge, _a1, _a2 = _queued(
        user,
        workspace,
        storage,
        "r-endings",
        auth_client,
        django_capture_on_commit_callbacks,
    )
    commands = []
    for state, error in (
        (HarnessNudge.State.FAILED, {"code": "turn_failed"}),
        (HarnessNudge.State.APPLIED, None),
    ):
        if commands:
            # "Rebuild again" queues the same rebuild with a new builder message.
            _finish_command(commands[-1])
            response = _answer(auth_client, environment, nudge, "yes")
            assert response.status_code == 200, response.content
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        commands.append(nudge.command_message_id)
        rebuild.start_nudge_turn(
            nudge.id, message_id=nudge.command_message_id, started_at=None
        )
        with transaction.atomic():
            locked = nudges._head(environment.id, lock=True)
            rebuild.close_nudge(
                rebuild._lock_nudge(locked, nudge.id),
                locked=locked,
                state=state,
                error=error,
            )

    nudge.refresh_from_db()
    endings = {
        entry["command_message_id"]: entry.get("ending")
        for entry in nudge.usage["attempts"]
    }
    assert [endings[str(command)] for command in commands] == ["turn_failed", "applied"]
    assert nudge.error is None


def test_a_late_turn_start_of_an_earlier_attempt_records_no_sandbox(
    user,
    workspace,
    storage,
    scheduled,
    started,
    inline,
    rebuilds_on,
    auth_client,
    django_capture_on_commit_callbacks,
):
    environment, nudge, _a1, _a2 = _queued(
        user,
        workspace,
        storage,
        "r-late-earlier",
        auth_client,
        django_capture_on_commit_callbacks,
    )
    assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
    nudge.refresh_from_db()
    first = nudge.command_message
    conversation = first.conversation

    def lease_on(ref):
        issue_conversation_capability(
            conversation,
            endpoint_base_url=PLATFORM,
            provider_ref=ref,
            attempt=None,
            ttl_seconds=600,
        )

    lease_on("sandbox-a")
    rebuild._note_turn_sandbox(nudge.id, str(conversation.id), first.id)
    with transaction.atomic():
        locked = nudges._head(environment.id, lock=True)
        rebuild.close_nudge(
            rebuild._lock_nudge(locked, nudge.id),
            locked=locked,
            state=HarnessNudge.State.FAILED,
            error={"code": "turn_failed"},
        )
    # "Rebuild again": a new builder message, and chat moves to another sandbox.
    _finish_command(first.id)
    response = _answer(auth_client, environment, nudge, "yes")
    assert response.status_code == 200, response.content
    assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
    lease_on("sandbox-b")

    rebuild._note_turn_sandbox(nudge.id, str(conversation.id), first.id)

    assert _attempt_entry(nudge, first)["sandbox_refs"] == ["sandbox-a"]


def _nudge_with(environment, attempts, *, state=HarnessNudge.State.FAILED):
    return HarnessNudge.no_workspace_objects.create(
        job=environment,
        organization=environment.organization,
        workspace=environment.workspace,
        target=HarnessNudge.Target.ENVIRONMENT,
        state=state,
        usage={"attempts": attempts},
    )


_T = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def _entry(ending, refs, *, start=0, end=10):
    entry = {
        "command_message_id": str(uuid.uuid4()),
        "sandbox_refs": refs,
        "turn_started_at": (_T + timedelta(minutes=start)).isoformat(),
    }
    if end is not None:
        entry["turn_ended_at"] = (_T + timedelta(minutes=end)).isoformat()
    if ending is not None:
        entry["ending"] = ending
    return entry


@pytest.mark.parametrize(
    "entry, expected",
    [
        (
            _entry("turn_failed", ["sandbox-1"]),
            ([(_T, _T + timedelta(minutes=10))], False, None),
        ),
        (_entry("turn_failed", ["sandbox-2"]), ([], False, None)),
        (_entry("stopped_by_user", ["sandbox-1"]), ([], False, None)),
        (_entry("applied", ["sandbox-1"]), ([], False, None)),
        (_entry(None, ["sandbox-1"], end=None), ([], True, None)),
        (_entry("turn_failed", ["sandbox-1"], end=None), ([], False, None)),
        (
            _entry("rebuild_stuck", ["sandbox-1"]),
            ([], False, _T + timedelta(minutes=10)),
        ),
        # Stuck in the sandbox it moved to; this earlier one keeps its own ending.
        (_entry("rebuild_stuck", ["sandbox-1", "sandbox-2"]), ([], False, None)),
    ],
    ids=[
        "failed_here",
        "failed_elsewhere",
        "stopped_by_user",
        "applied",
        "not_ended",
        "failed_without_an_end",
        "rebuild_stuck",
        "stuck_in_a_later_sandbox",
    ],
)
def test_failed_turn_intervals(entry, expected, organization, caplog):
    job, conversation = _finished_job(organization)
    _nudge_with(job, [entry])

    with caplog.at_level("WARNING", logger=rebuild.logger.name):
        turns = rebuild.failed_turn_intervals(
            conversation_id=conversation.id, provider_ref="sandbox-1"
        )

    assert (turns.intervals, turns.pending, turns.sandbox_failed_at) == expected
    warned = [r for r in caplog.records if "no time bounds" in r.getMessage()]
    unbounded_failure = "turn_ended_at" not in entry and entry.get("ending")
    assert len(warned) == (1 if unbounded_failure else 0)


# --- fix engine -------------------------------------------------------------------------


@pytest.fixture
def fix_settings(settings, monkeypatch):
    settings.AGENT_FIX_E2B_TEMPLATE_REFERENCE = "agent-fix:build-7"
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example.test/"
    settings.AGENT_FIX_MAX_SANDBOX_ATTEMPTS = 2
    settings.AGENT_FIX_HEARTBEAT_TIMEOUT_SECONDS = 600
    settings.AGENT_FIX_TOKEN_GRACE_SECONDS = 600
    for name in ("ALK_HOSTED_AGENTCC_BASE_URL", "AGENTCC_INTERNAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test/")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "harness-virtual-key")


def _fix_provider(monkeypatch, provider):
    monkeypatch.setattr(agent_fix_sandbox, "get_sandbox_provider", lambda: provider)
    return provider


def _set_fix(fix_run, **fields):
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(**fields)


_OURS = sorted(harness_usage.OUR_FIX_FAILURES)
_FAILED = AgentFixRun.State.FAILED


# A fix's sandbox released after the fix ended (charged unless the end was ours), and,
# with ``found``, after the watch found its engine gone at (found, last seen) minutes.
_STATE = AgentFixRun.State
_FIX_ENDINGS = [
    ("RUNNING", _STATE.RUNNING, None, None, ("engine_replaced", "infrastructure")),
    *[
        (f"FAILED-{code}", _FAILED, code, None, (code, "infrastructure"))
        for code in _OURS
    ],
    ("FAILED-fix_deadline", _FAILED, "fix_deadline", None, ("fix_deadline", "")),
    (
        "FAILED-agent_source_unreadable",
        _FAILED,
        "agent_source_unreadable",
        None,
        ("agent_source_unreadable", ""),
    ),
    ("STOPPING", _STATE.STOPPING, None, None, ("stopping", "")),
    ("STOPPED", _STATE.STOPPED, None, None, ("stopped", "")),
    ("READY", _STATE.READY, None, None, ("ready", "")),
    ("OUT_OF_BUDGET", _STATE.OUT_OF_BUDGET, None, None, ("out_of_budget", "")),
    ("NO_IMPROVEMENT", _STATE.NO_IMPROVEMENT, None, None, ("no_improvement", "")),
    ("NOT_REPRODUCED", _STATE.NOT_REPRODUCED, None, None, ("not_reproduced", "")),
    (
        "ENGINE_GONE-then-STOPPING",
        _STATE.STOPPING,
        None,
        (10, 10 - 10 / 60),
        ("engine_gone", "infrastructure"),
    ),
    (
        "ENGINE_GONE-then-fix_deadline",
        _FAILED,
        "fix_deadline",
        (10, 10 - 10 / 60),
        ("engine_gone", "infrastructure"),
    ),
    (
        "ENGINE_GONE-at-the-cap-then-relaunched",
        _STATE.RUNNING,
        None,
        (20.5, 19.5),
        ("lifetime_elapsed", "infrastructure"),
    ),
    (
        "ENGINE_GONE-at-the-cap-then-fix_deadline",
        _FAILED,
        "fix_deadline",
        (20.5, 19.5),
        ("lifetime_elapsed", ""),
    ),
    # Its engine died at the limit and the fix then failed on our side with no relaunch.
    *[
        (
            f"ENGINE_GONE-at-the-cap-then-FAILED-{code}",
            _FAILED,
            code,
            (20.5, 19.5),
            ("lifetime_elapsed", "infrastructure"),
        )
        for code in _OURS
    ],
]


@pytest.mark.parametrize(
    "state, code, found, expected",
    [case[1:] for case in _FIX_ENDINGS],
    ids=[case[0] for case in _FIX_ENDINGS],
)
def test_fix_sandbox_ending(
    state,
    code,
    found,
    expected,
    user,
    workspace,
    clock,
    metering,
    create_checks,
    fix_settings,
    monkeypatch,
):
    provider = _fix_provider(monkeypatch, _FixProvider(clock))
    fix_run = _new_fix(user, workspace)
    t0 = clock.now
    agent_fix_sandbox.launch(str(fix_run.id))
    if found is None:
        # Well inside the engine's report timeout: its launch counts as its last report.
        clock.advance(5 * 60)
    else:
        found_at, last_seen = found
        clock.now = t0 + timedelta(minutes=found_at)
        _set_fix(fix_run, heartbeat_at=t0 + timedelta(minutes=last_seen))
        provider.live.pop("sandbox-1")
        assert agent_fix_sandbox._look(str(fix_run.id), "4242:engine") == (
            Observation.ENGINE_GONE
        )
    _set_fix(
        fix_run,
        state=state,
        failure={"code": code} if code else None,
        finished_at=None if state in AgentFixRun.ACTIVE_STATES else clock.now,
    )

    if state == _STATE.RUNNING:
        # The platform starts the engine again; the launch releases the old sandbox.
        agent_fix_sandbox.launch(str(fix_run.id))
    else:
        agent_fix_sandbox.release(str(fix_run.id))

    row = _row("sandbox-1")
    assert (row.end_reason, row.failure_domain) == expected
    assert row.ended_at == clock.now
    sandbox_usage.seal_sandbox_lifetime(str(row.id))
    if expected[1]:
        assert metering == []
    else:
        # Billed to the release, never past the sandbox's lifetime limit.
        billed = min(clock.now, row.lifetime_ends_at) - row.started_at
        assert metering == [(row.id, Decimal(billed.total_seconds()) / 60)]


# A 30-minute fix whose last watch saw its engine report at ``last_report`` minutes. The
# fix then ends without a watch noticing the engine had stopped: at its deadline (the watch
# reads the deadline first), by the lost-fix sweep once deadline and grace are past, or by
# a person's stop at 20, read at the stop however late the release or the close.
@pytest.mark.parametrize(
    "last_report, ended_by, expected",
    [
        (2, "deadline", ("engine_gone", "infrastructure")),
        (2, "lost_fix_sweep", ("engine_gone", "infrastructure")),
        (2, "stop", ("engine_gone", "infrastructure")),
        (19.5, "stop", ("stopping", "")),
        (2, "stop-closed_late", ("engine_gone", "infrastructure")),
        (19.5, "stop-closed_late", ("stopped", "")),
        (19.5, "stop-lost_fix_sweep", ("stopped", "")),
        (29.9, "deadline", ("fix_deadline", "")),
        (5, "ready", ("ready", "")),
    ],
    ids=[
        "engine_dead_unseen-then-fix_deadline",
        "engine_dead_unseen-then-fix_deadline-via_lost_fix_sweep",
        "engine_dead_unseen-then-STOPPING",
        "engine_alive-then-STOPPING-released_late",
        "engine_dead_unseen-then-STOPPED-closed_late",
        "engine_alive-then-STOPPED-closed_late",
        "engine_alive-then-STOPPED-via_lost_fix_sweep",
        "engine_alive-then-fix_deadline",
        "READY-released_late",
    ],
)
def test_fix_release_finds_an_engine_that_died_unseen(
    last_report,
    ended_by,
    expected,
    user,
    workspace,
    clock,
    metering,
    create_checks,
    fix_settings,
    monkeypatch,
):
    provider = _fix_provider(monkeypatch, _FixProvider(clock, renews=True))
    fix_run = _new_fix(user, workspace, max_minutes=30)
    t0 = clock.now
    agent_fix_sandbox.launch(str(fix_run.id))
    clock.now = t0 + timedelta(minutes=last_report)
    _set_fix(fix_run, heartbeat_at=clock.now)
    assert agent_fix_sandbox._look(str(fix_run.id), "4242:engine") == (
        Observation.RUNNING
    )

    if ended_by == "deadline":
        clock.now = t0 + timedelta(minutes=30)
        assert agent_fix_sandbox._look(str(fix_run.id), "4242:engine") == (
            Observation.ENDED
        )
        clock.now = t0 + timedelta(minutes=31)
        agent_fix_sandbox.release(str(fix_run.id))
    elif ended_by == "lost_fix_sweep":
        # E2B removed the sandbox at its limit; no workflow drives the fix any more.
        provider.live.pop("sandbox-1")
        monkeypatch.setattr(
            "simulate.services.agent_fix_sweep.agent_fix_workflow_running",
            lambda _fix_run_id: True,
        )
        clock.now = t0 + timedelta(minutes=41)
        assert sweep_lost_fix_runs() == {"checked": 1, "closed": 1}
        fix_run.refresh_from_db()
        assert fix_run.failure["code"] == "fix_deadline"
    elif ended_by == "ready":
        # The engine's last report was its result; the release comes 25 min later.
        close_fix_run(fix_run.id, _STATE.READY)
        clock.now = t0 + timedelta(minutes=30)
        agent_fix_sandbox.release(str(fix_run.id))
    else:
        monkeypatch.setattr(
            "simulate.services.agent_fix_runs.cancel_agent_fix_workflow",
            lambda _fix_run_id: None,
        )
        clock.now = t0 + timedelta(minutes=20)
        assert request_stop(fix_run).state == _STATE.STOPPING
        if ended_by == "stop-lost_fix_sweep":
            # The workflow is stuck: the sweep stops the fix after deadline and grace.
            monkeypatch.setattr(
                "simulate.services.agent_fix_sweep.agent_fix_workflow_running",
                lambda _fix_run_id: True,
            )
            clock.now = t0 + timedelta(minutes=41)
            assert sweep_lost_fix_runs() == {"checked": 1, "closed": 1}
        else:
            # A late release: the runner was out, or the provider's delete was retried.
            clock.now = t0 + timedelta(minutes=31)
            if ended_by == "stop-closed_late":
                close_fix_run(fix_run.id, _STATE.STOPPED)
            agent_fix_sandbox.release(str(fix_run.id))

    row = _row("sandbox-1")
    assert (row.end_reason, row.failure_domain) == expected
    assert row.ended_at == clock.now
    sandbox_usage.seal_sandbox_lifetime(str(row.id))
    if expected[1]:
        assert metering == []
    else:
        billed = min(clock.now, row.lifetime_ends_at) - row.started_at
        assert metering == [(row.id, Decimal(billed.total_seconds()) / 60)]


# The previous engine died long before; a person stops the fix while the platform starts
# the engine again, during the new sandbox's create or just before its credentials.
@pytest.mark.parametrize("stopped", ["during_create", "before_credentials"])
def test_a_stop_during_a_relaunch_is_read_against_the_new_sandbox(
    stopped, user, workspace, clock, metering, create_checks, fix_settings, monkeypatch
):
    provider = _fix_provider(monkeypatch, _FixProvider(clock))
    fix_run = _new_fix(user, workspace)
    t0 = clock.now
    agent_fix_sandbox.launch(str(fix_run.id))
    clock.now = t0 + timedelta(minutes=11)
    assert agent_fix_sandbox._look(str(fix_run.id), "4242:engine") == (
        Observation.ENGINE_GONE
    )
    monkeypatch.setattr(
        "simulate.services.agent_fix_runs.cancel_agent_fix_workflow",
        lambda _fix_run_id: None,
    )
    if stopped == "during_create":
        create = provider.create

        def create_then_stop(spec, *, timeout):
            sandbox = create(spec, timeout=timeout)
            request_stop(fix_run)
            return sandbox

        monkeypatch.setattr(provider, "create", create_then_stop)
    else:
        rotate = agent_fix_sandbox.rotate_credentials

        def stop_then_rotate(fix_run_id):
            request_stop(fix_run)
            return rotate(fix_run_id)

        monkeypatch.setattr(agent_fix_sandbox, "rotate_credentials", stop_then_rotate)
    clock.now = t0 + timedelta(minutes=13)
    assert agent_fix_sandbox.launch(str(fix_run.id)).observation == (
        Observation.STOPPING
    )
    # A late release: 18 min after the new sandbox's start, still inside its limit.
    clock.now = t0 + timedelta(minutes=31)
    agent_fix_sandbox.release(str(fix_run.id))

    row = _row("sandbox-2")
    assert (row.end_reason, row.failure_domain) == ("stopping", "")
    sandbox_usage.seal_sandbox_lifetime(str(row.id))
    billed = row.ended_at - row.started_at
    assert metering == [(row.id, Decimal(billed.total_seconds()) / 60)]


# A fix whose sandbox was never renewed (its limit at 20) is stopped while its engine is
# silent past the report timeout, and released a minute later. The engine reported until
# 30 s before the limit and the stop came after it: an expiry. Or it died at 2 and the
# stop's write fails inside the database: the stop still lands, and is charged.
@pytest.mark.parametrize(
    "last_report, stopped_at, write_fails, expected, minutes",
    [
        (19.5, 31, False, ("lifetime_elapsed", ""), 20),
        (2, 15, True, ("stopping", ""), 16),
    ],
    ids=["reported_until_the_limit", "stop_write_failed"],
)
def test_a_stop_records_its_ending_before_the_release(
    last_report,
    stopped_at,
    write_fails,
    expected,
    minutes,
    user,
    workspace,
    clock,
    metering,
    create_checks,
    fix_settings,
    monkeypatch,
):
    _fix_provider(monkeypatch, _FixProvider(clock))
    fix_run = _new_fix(user, workspace)
    t0 = clock.now
    agent_fix_sandbox.launch(str(fix_run.id))
    _set_fix(fix_run, heartbeat_at=t0 + timedelta(minutes=last_report))
    monkeypatch.setattr(
        "simulate.services.agent_fix_runs.cancel_agent_fix_workflow",
        lambda _fix_run_id: None,
    )
    clock.now = t0 + timedelta(minutes=stopped_at)
    if write_fails:
        with patch.object(sandbox_usage, "_open_row", _broken_database):
            request_stop(fix_run)
    else:
        request_stop(fix_run)
    fix_run.refresh_from_db()
    assert fix_run.state == _STATE.STOPPING
    clock.advance(60)
    agent_fix_sandbox.release(str(fix_run.id))

    row = _row("sandbox-1")
    assert (row.end_reason, row.failure_domain) == expected
    sandbox_usage.seal_sandbox_lifetime(str(row.id))
    assert metering == [(row.id, Decimal(minutes))]


# The fix's watch was down past the sandbox's limit (the runner's workers were out), so
# the lifetime sweep reaches the sandbox before the fix's own release does.
@pytest.mark.parametrize(
    "then",
    [
        "relaunched",
        "fix_deadline",
        "fix_deadline-engine_dead_unseen",
        "STOPPED-engine_dead_unseen",
        *[f"FAILED-{code}" for code in _OURS],
    ],
)
def test_sweep_leaves_a_running_fix_sandbox_to_its_release(
    then, user, workspace, clock, metering, create_checks, fix_settings, monkeypatch
):
    provider = _fix_provider(monkeypatch, _FixProvider(clock))
    fix_run = _new_fix(user, workspace)
    t0 = clock.now
    agent_fix_sandbox.launch(str(fix_run.id))
    # The engine reported until 30 s before the 20-minute limit, where E2B ended it; or
    # its last report came at 2 min and it died unseen.
    last_report = 2 if then.endswith("engine_dead_unseen") else 19.5
    _set_fix(fix_run, heartbeat_at=t0 + timedelta(minutes=last_report))
    provider.live.pop("sandbox-1")
    if then.startswith("STOPPED"):
        # A person stops the fix at 20, 18 min after its engine's last report.
        monkeypatch.setattr(
            "simulate.services.agent_fix_runs.cancel_agent_fix_workflow",
            lambda _fix_run_id: None,
        )
        clock.now = t0 + timedelta(minutes=20)
        assert request_stop(fix_run).state == _STATE.STOPPING
    clock.now = t0 + timedelta(minutes=31)

    assert sandbox_usage.sweep_sandbox_lifetimes()["expired"] == 0

    row = _row("sandbox-1")
    assert row.ended_at is None
    if then.startswith(("fix_deadline", "STOPPED")):
        # The fix ended with no relaunch and nothing released the sandbox.
        if then.startswith("STOPPED"):
            close_fix_run(fix_run.id, _STATE.STOPPED)
        else:
            _set_fix(
                fix_run,
                state=_FAILED,
                failure={"code": "fix_deadline"},
                finished_at=clock.now,
            )
        assert sandbox_usage.sweep_sandbox_lifetimes()["expired"] == 1
        # A later release changes nothing.
        agent_fix_sandbox.release(str(fix_run.id))
        row.refresh_from_db()
        assert row.ended_at == row.lifetime_ends_at
        if then == "fix_deadline":
            # Reporting until its limit: an expiry, charged.
            assert (row.end_reason, row.failure_domain) == ("lifetime_elapsed", "")
            assert metering == [(row.id, Decimal("20.0000"))]
        else:
            assert (row.end_reason, row.failure_domain) == (
                "engine_gone",
                "infrastructure",
            )
            assert metering == []
        return
    # The watch is back and finds the engine gone at the limit.
    assert agent_fix_sandbox._look(str(fix_run.id), "4242:engine") == (
        Observation.ENGINE_GONE
    )
    if then == "relaunched":
        # The platform starts the engine again.
        agent_fix_sandbox.launch(str(fix_run.id))
        row.refresh_from_db()
        assert row.ended_at == clock.now
        assert (row.end_reason, row.failure_domain) == (
            "lifetime_elapsed",
            "infrastructure",
        )
        sandbox_usage.seal_sandbox_lifetime(str(row.id))
        assert metering == []
        return
    # The fix fails on our side with no relaunch, and the sweep closes the sandbox
    # before the fix's own release.
    code = then.removeprefix("FAILED-")
    if code == "fix_engine_died":
        # Both of its engine attempts are used up, so the relaunch is refused.
        _set_fix(fix_run, sandbox_attempt=2)
        assert agent_fix_sandbox.launch(str(fix_run.id)).observation == (
            Observation.ENDED
        )
        fix_run.refresh_from_db()
        assert fix_run.failure["code"] == code
    else:
        _set_fix(fix_run, state=_FAILED, failure={"code": code}, finished_at=clock.now)
    assert sandbox_usage.sweep_sandbox_lifetimes()["expired"] == 1
    agent_fix_sandbox.release(str(fix_run.id))
    row.refresh_from_db()
    assert row.ended_at == row.lifetime_ends_at
    assert (row.end_reason, row.failure_domain) == (
        "lifetime_elapsed",
        "infrastructure",
    )
    assert metering == []


@pytest.mark.parametrize("renews", [True, None], ids=["renewing", "not_renewing"])
def test_fix_launch_opens_a_row_and_watch_rearms(
    renews, user, workspace, clock, metering, create_checks, fix_settings, monkeypatch
):
    provider = _fix_provider(
        monkeypatch, _FixProvider(clock, renews=renews, renew_seconds=5)
    )
    fix_run = _new_fix(user, workspace)
    t0 = clock.now

    agent_fix_sandbox.launch(str(fix_run.id))

    row = _row("sandbox-1")
    assert create_checks.asked == [str(fix_run.organization_id)]
    assert (row.kind, row.provider, row.owner_id) == ("fix_engine", "e2b", fix_run.id)
    assert row.workspace_id == fix_run.workspace_id
    assert row.lifetime_seconds == agent_fix_sandbox.LEASE_SECONDS
    assert (row.armed_at, row.started_at) == (t0, t0)
    clock.advance(5 * 60)
    renewed_at = clock.now
    _set_fix(fix_run, heartbeat_at=renewed_at)

    assert agent_fix_sandbox._look(str(fix_run.id), "4242:engine") == (
        Observation.RUNNING
    )

    assert provider.renewed == [("sandbox-1", agent_fix_sandbox.LEASE_SECONDS)]
    row.refresh_from_db()
    assert row.armed_at == (renewed_at if renews else t0)


def test_fix_check_refusal_raises_before_create(
    user, workspace, metering, refuse_sandbox, fix_settings, monkeypatch
):
    provider = _fix_provider(monkeypatch, _FixProvider())
    fix_run = _new_fix(user, workspace)

    with pytest.raises(HostedHarnessError) as refused:
        agent_fix_sandbox.launch(str(fix_run.id))

    assert (refused.value.code, refused.value.status_code) == ("usage_limit", 402)
    assert provider.specs == []
    assert not HostedSandboxLifetime.no_workspace_objects.exists()


# --- submission checks ------------------------------------------------------------------


def _saved_job(user, workspace, *, state=HostedHarnessJob.State.COMPLETED):
    payload = _payload()
    payload["metadata"] = {"authoring_object_key": "harness/environments/a.tar.gz"}
    job, _ = create_hosted_job(
        user.organization,
        payload,
        idempotency_key=str(uuid.uuid4()),
        workspace=workspace,
    )
    job.state = state
    job.save(update_fields=["state", "updated_at"])
    return job


def _post_message(user, workspace, job):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.post(
        f"/simulate/api/harness-jobs/{job.id}/conversation/messages/",
        {"content": "hello", "client_request_id": "m-1"},
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


@pytest.mark.requires_ee
def test_send_message_to_a_finished_job_checks_sandbox_minutes(
    user, workspace, settings, refuse_sandbox
):
    settings.HARNESS_PUBLIC_BASE_URL = PLATFORM
    job = _saved_job(user, workspace)

    response = _post_message(user, workspace, job)

    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED, response.content
    body = response.json()
    assert (body["error_code"], body["dimension"]) == (
        "BUDGET_PAUSED",
        "sandbox_minutes",
    )
    assert body["upgrade_cta"] == _REFUSAL["upgrade_cta"]
    assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation__job=job
    ).exists()


def test_send_message_to_a_running_job_does_not(
    user, workspace, settings, monkeypatch
):
    settings.HARNESS_PUBLIC_BASE_URL = PLATFORM
    asked = []
    monkeypatch.setattr(
        harness_usage, "require_sandbox_usage", lambda *args: asked.append(args)
    )
    job = _saved_job(user, workspace, state=HostedHarnessJob.State.RUNNING)

    response = _post_message(user, workspace, job)

    assert response.status_code == status.HTTP_202_ACCEPTED, response.content
    assert asked == []


@pytest.mark.requires_ee
def test_answer_rebuild_checks_sandbox_minutes(
    user, workspace, rebuilds_on, refuse_sandbox
):
    from ee.usage.exceptions import UsageLimitExceeded

    environment, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key=str(uuid.uuid4()),
        workspace=workspace,
    )
    nudge = _nudge_with(environment, [], state=HarnessNudge.State.PENDING)

    with pytest.raises(UsageLimitExceeded) as refused:
        rebuild.answer_rebuild(environment, nudge.id, user=user)

    assert refused.value.check_result.dimension == "sandbox_minutes"
    assert refuse_sandbox == ["harness_authoring", "sandbox_runtime"]
    nudge.refresh_from_db()
    assert nudge.state == HarnessNudge.State.PENDING


@pytest.mark.requires_ee
def test_add_scenarios_checks_sandbox_minutes(
    user, workspace, settings, auth_client, refuse_sandbox
):
    settings.HARNESS_PUBLIC_BASE_URL = PLATFORM
    job = _saved_job(user, workspace)
    scenario_count = job.scenario_count

    response = auth_client.post(
        f"/simulate/api/harness-jobs/{job.id}/extend/",
        {"count": 2, "guidance": "refunds"},
        format="json",
    )

    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED, response.content
    assert response.json()["dimension"] == "sandbox_minutes"
    job.refresh_from_db()
    # Refused before the request was written onto the environment.
    assert job.scenario_count == scenario_count
    assert "scenario_extend" not in job.payload["metadata"]


@pytest.mark.parametrize("reason", [_REFUSAL["reason"], ""], ids=["reason", "fallback"])
def test_fix_launch_blocker_reports_the_sandbox_limit(monkeypatch, reason):
    from simulate.selectors import agent_fix as selectors

    asked = []

    def check(organization_id, action):
        asked.append(action)
        if action == "sandbox_runtime":
            return {**_REFUSAL, "reason": reason}
        return {"allowed": True}

    monkeypatch.setattr(selectors, "check_harness_action", check)
    monkeypatch.setattr(
        selectors,
        "agent_kind",
        lambda _environment: SimpleNamespace(require_available=lambda: None),
    )
    source_run = SimpleNamespace(
        state=next(iter(selectors.FINISHED_RUN_STATES)),
        environment=None,
        organization_id=uuid.uuid4(),
    )

    blocker = selectors.launch_blocker(source_run)

    assert asked == ["agent_fix", "sandbox_runtime"]
    assert (blocker.code, blocker.status_code) == ("usage_limit", 402)
    assert blocker.message == (reason or "sandbox minutes are used up")


# --- re-runs ----------------------------------------------------------------------------


def _sandbox_refusal():
    return HostedHarnessError(
        "usage_limit",
        _REFUSAL["reason"],
        status_code=402,
        details={
            key: _REFUSAL[key]
            for key in (
                "error_code",
                "dimension",
                "current_usage",
                "limit",
                "upgrade_cta",
            )
        },
    )


@pytest.mark.requires_ee
def test_rerun_saved_checks_sandbox_minutes(
    user, workspace, settings, refuse_sandbox, monkeypatch
):
    from simulate.services.harness_provider import HostedHarnessProvider

    settings.HARNESS_PUBLIC_BASE_URL = PLATFORM
    job = _saved_job(user, workspace)
    started_runs = []
    monkeypatch.setattr(
        "simulate.services.hosted_harness.create_selected_harness_run",
        lambda *a, **k: started_runs.append(a),
    )
    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        lambda *a, **k: started_runs.append(a),
    )

    with pytest.raises(HostedHarnessError) as refused:
        HostedHarnessProvider().rerun_saved(
            str(job.id),
            organization=user.organization,
            workspace=workspace,
            environment_values={},
        )

    body = refused.value.as_dict()
    assert refused.value.status_code == 402
    assert (body["error"], body["error_code"], body["dimension"]) == (
        "usage_limit",
        "BUDGET_PAUSED",
        "sandbox_minutes",
    )
    assert body["upgrade_cta"] == _REFUSAL["upgrade_cta"]
    assert started_runs == []


@pytest.mark.requires_ee
def test_chat_rerun_view_answers_the_sandbox_limit(
    user, workspace, settings, refuse_sandbox, monkeypatch
):
    settings.HARNESS_PUBLIC_BASE_URL = PLATFORM
    job = _saved_job(user, workspace)
    conversation = ensure_conversation(job)
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url=PLATFORM,
        provider_ref="sandbox-chat",
        attempt=None,
        ttl_seconds=600,
    )
    monkeypatch.setattr(
        "simulate.views.hosted_harness_conversation.promote_latest_checkpoint",
        lambda *_a, **_k: None,
    )

    response = APIClient().post(
        f"/simulate/api/harness/conversations/{conversation.id}/rerun/",
        {},
        format="json",
        **_headers(capability),
    )

    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED, response.content
    assert response.json()["dimension"] == "sandbox_minutes"


class _RefusingProvider:
    def rerun_saved(self, job_id, *, organization, workspace, environment_values):
        raise _sandbox_refusal()


@pytest.fixture
def refusing_rerun(monkeypatch, run_test, test_execution):
    """A repository run whose saved environment the plan refuses to restart."""
    run_test.agent_definition.agent_type = "text"
    run_test.agent_definition.save(update_fields=["agent_type"])
    monkeypatch.setattr(
        "simulate.services.harness_provider.get_harness_provider",
        lambda: _RefusingProvider(),
    )
    monkeypatch.setattr(
        "simulate.views.run_test._voice_sim_gate_response", lambda *a, **k: None
    )
    job_id = uuid.uuid4()
    test_execution.execution_metadata = {"harness_job_id": str(job_id)}
    test_execution.save(update_fields=["execution_metadata"])
    return job_id


def _assert_limit_answer(response):
    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED, response.content
    body = response.json()
    assert (body["error"], body["error_code"], body["dimension"]) == (
        "usage_limit",
        "BUDGET_PAUSED",
        "sandbox_minutes",
    )


def test_execute_answers_a_refused_restart_with_the_limit(
    auth_client, refusing_rerun, run_test, test_execution, scenario
):
    test_execution.scenario_ids = [str(scenario.id)]
    test_execution.save(update_fields=["scenario_ids"])

    response = auth_client.post(
        f"/simulate/run-tests/{run_test.id}/execute/",
        {"scenario_ids": [str(scenario.id)]},
        format="json",
    )

    _assert_limit_answer(response)


@patch(
    "simulate.services.harness_credentials.credentials_for_rerun",
    return_value=({}, {}),
)
def test_selected_rerun_answers_a_refused_restart_with_the_limit(
    _credentials, auth_client, refusing_rerun, test_execution, call_execution
):
    response = auth_client.post(
        f"/simulate/test-executions/{test_execution.id}/rerun-calls/",
        {"rerun_type": "call_and_eval", "select_all": True},
        format="json",
    )

    _assert_limit_answer(response)


def test_bulk_rerun_answers_a_refused_restart_with_the_limit(
    auth_client, refusing_rerun, run_test, test_execution, call_execution
):
    response = auth_client.post(
        f"/simulate/run-tests/{run_test.id}/rerun-test-executions/",
        {"rerun_type": "call_and_eval", "test_execution_ids": [str(test_execution.id)]},
        format="json",
    )

    _assert_limit_answer(response)


@pytest.mark.requires_ee
def test_rerun_calls_in_run_checks_sandbox_minutes(
    finished_run, calls, workflow, refuse_sandbox, monkeypatch, user
):
    from simulate.models import TestExecution
    from simulate.services.hosted_harness import rerun_calls_in_run, resume_run

    chosen, _other = calls
    cleared = []
    monkeypatch.setattr(
        "simulate.services.call_rerun.clear_call_for_rerun",
        lambda *a, **k: cleared.append(a),
    )
    execution = TestExecution.no_workspace_objects.get(
        id=finished_run.test_execution_id
    )

    with pytest.raises(HostedHarnessError) as refused:
        rerun_calls_in_run(execution, [chosen.id])

    assert refused.value.status_code == 402
    assert refused.value.as_dict()["dimension"] == "sandbox_minutes"
    assert cleared == []
    finished_run.refresh_from_db()
    assert finished_run.state == HostedHarnessJob.State.COMPLETED
    _running, start = workflow
    start.assert_not_called()

    monkeypatch.setattr(
        "simulate.selectors.run_resume.check_resume",
        lambda _execution: SimpleNamespace(
            job=finished_run,
            unfinished=[SimpleNamespace(call_execution_id=chosen.id)],
        ),
    )
    with pytest.raises(HostedHarnessError) as resumed:
        resume_run(execution, user=user)
    assert resumed.value.status_code == 402


@pytest.mark.requires_ee
@pytest.mark.parametrize("case", ["fix_ended", "scheduler_down"])
def test_rerun_freezes_the_charge_before_it_resets_the_job(
    case, finished_run, calls, workflow, user, workspace, monkeypatch
):
    from ee.usage.services import emitter
    from simulate.models import TestExecution
    from simulate.services.hosted_harness import rerun_calls_in_run

    sent = []
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    monkeypatch.setattr(emitter, "emit", lambda event, **_: sent.append(event))
    monkeypatch.setattr(
        harness_usage, "require_sandbox_start", lambda *_args, **_kwargs: None
    )
    attempt = HostedHarnessAttempt.no_workspace_objects.get(
        job=finished_run, attempt_number=finished_run.current_attempt_number
    )
    began = attempt.cleanup_verified_at - timedelta(seconds=150)
    attempt.sandbox_runtime = {
        "started_at": began.isoformat(),
        "armed_at": began.isoformat(),
        "ended_at": attempt.cleanup_verified_at.isoformat(),
        "provider": "daytona",
        "lifetime_seconds": 3600,
        "seconds": 150.0,
    }
    attempt.save(update_fields=["sandbox_runtime", "updated_at"])
    if case == "fix_ended":
        fix_run = _new_fix(user, workspace)
        _set_fix(fix_run, state=_FAILED, failure={"code": "fix_engine_died"})
        HostedHarnessJob.no_workspace_objects.filter(id=finished_run.id).update(
            cancel_reason="fix_ended", fix_run=fix_run
        )
    else:
        _running, start = workflow
        start.side_effect = RuntimeError("scheduler unavailable")
    chosen, _other = calls
    execution = TestExecution.no_workspace_objects.get(
        id=finished_run.test_execution_id
    )

    if case == "fix_ended":
        rerun_calls_in_run(execution, [chosen.id])
    else:
        with pytest.raises(HostedHarnessError):
            rerun_calls_in_run(execution, [chosen.id])

    attempt.refresh_from_db()
    expected = "0.0000" if case == "fix_ended" else "2.5000"
    assert attempt.sandbox_runtime["billed_minutes"] == expected
    harness_usage.replay_harness_usage(attempt, seal=True)
    charges = [
        float(event.amount)
        for event in sent
        if getattr(event.event_type, "value", event.event_type) == "sandbox_runtime"
    ]
    assert charges == ([] if case == "fix_ended" else [2.5])


def test_rerun_waits_when_the_sandbox_minutes_cannot_be_stored(
    finished_run, calls, workflow, monkeypatch
):
    from simulate.models import TestExecution
    from simulate.services.hosted_harness import rerun_calls_in_run

    monkeypatch.setattr(
        harness_usage, "require_sandbox_start", lambda *_args, **_kwargs: None
    )

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("the database refused the write")

    monkeypatch.setattr(harness_usage, "freeze_run_sandbox_charge", unavailable)
    HostedHarnessJob.no_workspace_objects.filter(id=finished_run.id).update(
        cancel_reason="fix_ended"
    )
    chosen, _other = calls
    execution = TestExecution.no_workspace_objects.get(
        id=finished_run.test_execution_id
    )

    with pytest.raises(HostedHarnessError) as refused:
        rerun_calls_in_run(execution, [chosen.id])

    assert (refused.value.code, refused.value.status_code) == ("rerun_unavailable", 503)
    assert refused.value.retryable is True
    # Nothing was reset: what the stored minutes are read from is intact.
    finished_run.refresh_from_db()
    assert finished_run.state == HostedHarnessJob.State.COMPLETED
    assert finished_run.cancel_reason == "fix_ended"
    _running, start = workflow
    start.assert_not_called()


@pytest.mark.parametrize("retry_pending", [True, False])
def test_cleanup_records_a_platform_retry(organization, retry_pending):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key=str(uuid.uuid4())
    )
    attempt = register_attempt(job.id, endpoint_base_url=PLATFORM).attempt
    harness_usage.record_sandbox_runtime(
        attempt, started=True, provider="daytona", lifetime_seconds=900
    )

    job = record_cleanup(
        attempt.id,
        provider_ref="",
        verified_absent=True,
        retry_pending=retry_pending,
    )

    attempt.refresh_from_db()
    if retry_pending:
        assert attempt.sandbox_runtime["retried"] is True
        assert job.state == HostedHarnessJob.State.RETRY_WAIT
    else:
        assert "retried" not in attempt.sandbox_runtime


@pytest.mark.requires_ee
@pytest.mark.skipif(
    not has_ee("ee.cloud"), reason="requires ee/cloud billing.yaml (OSS lane)"
)
def test_budget_pause_blocks_the_next_run_start(user, workspace, settings, monkeypatch):
    from ee.usage.services.emitter import get_redis
    from simulate.services.harness_provider import (
        HostedHarnessProvider,
        _usage_limit_response,
    )

    settings.HARNESS_PUBLIC_BASE_URL = PLATFORM
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    started_runs = []
    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        lambda *a, **k: started_runs.append(a),
    )
    job = _saved_job(user, workspace)
    organization_id = str(user.organization_id)
    redis = get_redis()
    pause_key = f"pause:{organization_id}:sandbox_minutes"
    plan_key = f"plan:{organization_id}"
    redis.set(plan_key, "payg")
    redis.set(pause_key, "1")
    try:
        with pytest.raises(HostedHarnessError) as refused:
            HostedHarnessProvider().rerun_saved(
                str(job.id),
                organization=user.organization,
                workspace=workspace,
                environment_values={},
            )
        body = refused.value.as_dict()
        assert refused.value.status_code == 402
        assert body["error_code"] == "BUDGET_PAUSED"
        assert "sandbox_minutes" in body["message"]
        assert started_runs == []

        with pytest.raises(Exception) as at_submission:
            harness_usage.require_harness_run_usage(organization_id, job.payload)
        response = _usage_limit_response(at_submission.value)
        assert response is not None
        assert response.data["dimension"] == "sandbox_minutes"
    finally:
        redis.delete(pause_key, plan_key)


# --- where sandboxes and their charges can come from ------------------------------------


def _sources():
    for path in sorted(SIMULATE.rglob("*.py")):
        relative = path.relative_to(SIMULATE)
        if relative.parts[0] in {"tests", "migrations"} or "tests" in relative.parts:
            continue
        yield relative.as_posix(), ast.parse(path.read_text())


def _owners(tree):
    """Each top-level function or class method with its node; calls in a nested def or a
    lambda belong to the function that encloses them."""
    for top in tree.body:
        if isinstance(top, ast.FunctionDef | ast.AsyncFunctionDef):
            yield top.name, top
        elif isinstance(top, ast.ClassDef):
            for item in top.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    yield item.name, item
                else:
                    yield "<class>", item
        else:
            yield "<module>", top


def _called(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                yield func.id, None
            elif isinstance(func, ast.Attribute):
                yield func.attr, ast.unparse(func.value)


def _callers(name, *, owners_of_attribute=None):
    found = set()
    for path, tree in _sources():
        for owner, node in _owners(tree):
            for called, on in _called(node):
                if called != name:
                    continue
                if owners_of_attribute is not None and on not in owners_of_attribute:
                    continue
                found.add(f"{path}::{owner}")
    return found


def _calls_of(path, function):
    """The names each definition of ``function`` in ``path`` calls."""
    tree = ast.parse((SIMULATE / path).read_text())
    return [
        {called for called, _on in _called(node)}
        for owner, node in _owners(tree)
        if owner == function
    ]


def test_only_four_functions_create_a_sandbox():
    """Matches the spellings ``self.client.create`` and ``provider.create`` only."""
    assert _callers("create", owners_of_attribute={"self.client", "provider"}) == {
        "services/hosted_harness_gateway.py::launch",
        "services/hosted_harness_gateway.py::_start_chat_sandbox",
        "services/hosted_harness_gateway.py::author",
        "services/agent_fix_sandbox.py::launch",
    }


_RUN_STARTS = {
    "services/hosted_harness.py::rerun_calls_in_run": (
        "services/hosted_harness.py",
        "rerun_calls_in_run",
        "require_sandbox_start",
    ),
    "services/harness_provider.py::rerun_saved": (
        "services/harness_provider.py",
        "rerun_saved",
        "require_sandbox_start",
    ),
    "services/harness_provider.py::create": (
        "services/harness_provider.py",
        "create",
        "require_harness_run_usage",
    ),
    "services/harness_provider.py::run": (
        "services/harness_provider.py",
        "run",
        "require_harness_call_usage",
    ),
    "services/hosted_harness_conversation.py::start_conversation_run": (
        "services/hosted_harness_conversation.py",
        "start_conversation_run",
        "require_harness_call_usage",
    ),
    "services/harness_verify.py::_dispatch": (
        "services/harness_verify.py",
        "start_verify",
        "require_harness_call_usage",
    ),
    "services/agent_fix_engine.py::_dispatch": (
        "services/agent_fix_engine.py",
        "start_check_run",
        "require_harness_call_usage",
    ),
}


def test_every_run_workflow_start_is_checked():
    assert _callers("start_hosted_harness_gateway_workflow") == set(_RUN_STARTS)
    for caller, (path, checker, check) in _RUN_STARTS.items():
        assert any(check in calls for calls in _calls_of(path, checker)), caller
        if checker != caller.split("::")[1]:
            # The start is reached only through the function that checks.
            assert {
                found for found in _callers("_dispatch") if found.startswith(path)
            } == {f"{path}::{checker}"}, caller


def test_every_chat_runtime_start_is_known():
    assert _callers("schedule_conversation_runtime") == {
        "services/harness_provider.py::send_message",
        "services/harness_provider.py::_extend_in_chat",
        "services/harness_versioning/rebuild.py::_start_runtime",
        "services/hosted_harness_conversation.py::send_to_harness",
        "tasks/hosted_harness_conversation.py::recover_hosted_harness_conversations",
    }
    assert _callers("send_to_harness") == {
        "services/hosted_harness_gateway.py::_ask_harness_to_build",
        "services/hosted_harness.py::_ask_builder_to_check",
    }


def test_sandbox_events_are_built_only_by_the_two_seals():
    found = set()
    for path, tree in _sources():
        for owner, node in _owners(tree):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Attribute)
                    and sub.attr == "SANDBOX_RUNTIME"
                    and ast.unparse(sub.value) == "BillingEventType"
                ):
                    found.add(f"{path}::{owner}")
    assert found == {
        "services/harness_usage.py::emit_sandbox_runtime",
        "services/sandbox_usage.py::_emit_lifetime",
    }


def test_lifetime_rows_are_opened_only_where_a_sandbox_is_created():
    assert _callers("open_sandbox_lifetime") == {
        "services/hosted_harness_gateway.py::_start_chat_sandbox",
        "services/agent_fix_sandbox.py::launch",
    }
