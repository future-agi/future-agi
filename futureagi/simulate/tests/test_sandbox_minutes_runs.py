"""A run's sandbox is charged by the minute, once, after its end is confirmed."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4, uuid5

import pytest
from django.utils import timezone

from simulate.models import (
    AgentFixRun,
    HostedHarnessAttempt,
    HostedHarnessCleanupReceipt,
    HostedHarnessJob,
)
from simulate.pydantic_schemas.agent_fix import FixRequest, Surface
from simulate.serializers.harness_job import HarnessConsumptionSerializer
from simulate.serializers.hosted_harness import HARNESS_FAILURE_DOMAINS
from simulate.services import harness_usage
from simulate.services.hosted_harness import (
    HostedHarnessError,
    begin_scenarios,
    canonical_digest,
    create_hosted_job,
    record_cleanup,
    register_attempt,
)
from simulate.services.hosted_harness_ingestion import ingest_result_receipt
from simulate.services.hosted_sandbox.base import (
    SandboxNotFoundError,
    SandboxProviderError,
    SandboxRuntimeProvider,
    delete_confirmed,
)
from simulate.services.hosted_sandbox.daytona import DaytonaSandboxRuntimeProvider
from simulate.services.hosted_sandbox.e2b import E2BSandboxRuntimeProvider
from simulate.tasks.hosted_harness_usage import (
    seal_hosted_harness_usage,
    seal_unsealed_hosted_harness_usage,
)
from simulate.tests.test_harness_usage import _provision, _receipt, _record, _report
from simulate.tests.test_harness_usage import (  # noqa: F401 - fixtures
    hosted_attempt as hosted_attempt,
)
from simulate.tests.test_harness_usage import (  # noqa: F401 - fixtures
    metered_attempt as metered_attempt,
)
from simulate.tests.test_harness_versioning_backfill import _agent_version, _definition
from simulate.tests.test_hosted_harness_channels import _payload

# A bill is decided only by what was recorded, so every time below is fixed relative to
# one start well in the past.
LIFETIME = 7200


def _t0():
    return timezone.now().replace(microsecond=0) - timedelta(hours=6)


def _finish(
    attempt,
    *,
    provider="daytona",
    lifetime=LIFETIME,
    started,
    ended,
    armed=None,
    domain=None,
    code=None,
    heartbeat=None,
    retried=False,
    cleanup=True,
):
    """Write what launch, the poll and cleanup record for an attempt's sandbox."""
    observation = {
        "started_at": started.isoformat(),
        "provider": provider,
        "lifetime_seconds": lifetime,
        "armed_at": (armed or started).isoformat(),
        "seconds": 0.0,
    }
    if ended is not None:
        observation["ended_at"] = ended.isoformat()
        observation["observed_at"] = ended.isoformat()
        observation["seconds"] = (ended - started).total_seconds()
    if retried:
        observation["retried"] = True
    failure = (
        {"domain": domain, "code": code}
        if domain is not None or code is not None
        else None
    )
    HostedHarnessAttempt.no_workspace_objects.filter(id=attempt.id).update(
        sandbox_runtime=observation,
        terminal_failure=failure,
        heartbeat_at=heartbeat,
        cleanup_verified_at=timezone.now() if cleanup else None,
    )
    attempt.refresh_from_db()
    return attempt


def _seal(attempt):
    harness_usage.replay_harness_usage(attempt, seal=True)


def _sandbox(events):
    return [event for event in events if event.event_type == "sandbox_runtime"]


def _amounts(events):
    return [event.amount for event in _sandbox(events)]


def _billed(attempt):
    attempt.refresh_from_db()
    return attempt.sandbox_runtime.get("billed_minutes")


def _set_job(attempt, **fields):
    HostedHarnessJob.no_workspace_objects.filter(id=attempt.job_id).update(**fields)
    attempt.job.refresh_from_db()


def _check_run_of_a_fix(attempt, user, workspace, *, state, failure, cancel_reason):
    """Make the attempt's job a check run of a fix that ended as given."""
    environment, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key=str(uuid4()),
        workspace=workspace,
    )
    fix = AgentFixRun.no_workspace_objects.create(
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
        state=state,
        failure=failure,
    )
    _set_job(attempt, fix_run=fix, cancel_reason=cancel_reason)
    return fix


def _skipped_receipt(attempt):
    registration = attempt.job.scenario_registrations.get()
    body = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(attempt.job_id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "scenario_key": "case-one",
        "scenario_id": str(registration.scenario_id),
        "scenario_attempt": 1,
        "world_index": None,
        "status": "skipped",
        "sub_goals": [],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    body["digest"] = canonical_digest(body)
    return body


def _refusing(dimension="sandbox_minutes", *, only=None):
    from ee.usage.schemas.events import CheckResult

    def check_usage(org_id, action, *args, **kwargs):
        if only is not None and action != only:
            return CheckResult(allowed=True)
        return CheckResult(
            allowed=False,
            error_code="BUDGET_PAUSED",
            dimension=dimension,
            reason=f"Usage paused — you set a budget limit for {dimension}",
        )

    return check_usage


# ── The minute rule ─────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(150, [2.5]), (59.99999, [0.9999]), (0.003, [])],
    ids=["two_and_a_half", "just_under_a_minute", "a_few_milliseconds"],
)
def test_billed_minutes_are_fractional_and_never_round_up(
    metered_attempt, seconds, expected
):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(
        capability.attempt, started=t0, ended=t0 + timedelta(seconds=seconds)
    )

    _seal(attempt)

    assert _amounts(events) == expected


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_ended_exactly_at_the_cap_bills_the_whole_lifetime(metered_attempt):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(
        capability.attempt, started=t0, ended=t0 + timedelta(seconds=LIFETIME)
    )

    _seal(attempt)

    assert _amounts(events) == [120.0]


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_ended_after_the_cap_bills_only_to_the_cap(metered_attempt):
    capability, events = metered_attempt
    t0 = _t0()
    cap = t0 + timedelta(seconds=LIFETIME)
    attempt = _finish(capability.attempt, started=t0, ended=cap + timedelta(hours=3))

    _seal(attempt)

    assert _amounts(events) == [120.0]
    # Charged to the month the sandbox could last have lived in.
    assert _sandbox(events)[0].timestamp == cap


# ── Recording the start, renewals and the end ───────────────────────────────


@pytest.fixture
def clock(monkeypatch):
    moment = [_t0()]
    monkeypatch.setattr(harness_usage.timezone, "now", lambda: moment[0])
    return moment


@pytest.mark.django_db
def test_start_records_provider_and_limit_once(hosted_attempt, clock):
    attempt = hosted_attempt.attempt
    t0 = clock[0]

    harness_usage.record_sandbox_runtime(
        attempt, started=True, provider="daytona", lifetime_seconds=600
    )
    clock[0] = t0 + timedelta(seconds=30)
    harness_usage.record_sandbox_runtime(
        attempt,
        started=True,
        provider="e2b",
        lifetime_seconds=60,
        armed_at=t0 + timedelta(seconds=30),
    )
    clock[0] = t0 + timedelta(seconds=90)
    harness_usage.record_sandbox_runtime(attempt, final=True)

    attempt.refresh_from_db()
    observation = attempt.sandbox_runtime
    assert observation["provider"] == "daytona"
    assert observation["lifetime_seconds"] == 600
    # No moment before the create was given: the limit runs from the start.
    assert observation["armed_at"] == observation["started_at"] == t0.isoformat()
    assert "retried" not in observation


@pytest.mark.django_db
def test_start_arms_at_the_moment_before_create(hosted_attempt, clock):
    attempt = hosted_attempt.attempt
    t0 = clock[0]
    requested = t0 - timedelta(seconds=40)

    harness_usage.record_sandbox_runtime(
        attempt,
        started=True,
        provider="daytona",
        lifetime_seconds=600,
        armed_at=requested,
    )

    attempt.refresh_from_db()
    assert attempt.sandbox_runtime["armed_at"] == requested.isoformat()
    assert attempt.sandbox_runtime["started_at"] == t0.isoformat()


@pytest.mark.django_db
@pytest.mark.parametrize("retried", [True, False])
def test_final_observation_records_a_platform_retry(hosted_attempt, clock, retried):
    attempt = hosted_attempt.attempt
    harness_usage.record_sandbox_runtime(
        attempt, started=True, provider="daytona", lifetime_seconds=600
    )
    harness_usage.record_sandbox_runtime(attempt, retried=retried)
    attempt.refresh_from_db()
    assert "retried" not in attempt.sandbox_runtime

    harness_usage.record_sandbox_runtime(attempt, final=True, retried=retried)

    attempt.refresh_from_db()
    assert attempt.sandbox_runtime.get("retried", False) is retried


def _start_then_renew(attempt, clock, *, provider):
    t0 = clock[0]
    harness_usage.record_sandbox_runtime(
        attempt,
        started=True,
        provider="e2b",
        lifetime_seconds=LIFETIME,
        armed_at=t0,
    )
    harness_usage.rearm_sandbox_runtime(
        attempt,
        provider=provider,
        lifetime_seconds=3600,
        renewed_at=t0 + timedelta(seconds=600),
    )
    clock[0] = t0 + timedelta(hours=5)
    harness_usage.record_sandbox_runtime(attempt, final=True)
    HostedHarnessAttempt.no_workspace_objects.filter(id=attempt.id).update(
        cleanup_verified_at=clock[0]
    )
    attempt.refresh_from_db()
    return t0


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_a_renewal_moves_the_cap(metered_attempt, clock):
    capability, events = metered_attempt
    attempt = capability.attempt

    t0 = _start_then_renew(attempt, clock, provider=SimpleNamespace(renews_ttl=True))
    _seal(attempt)

    # Renewed at minute 10 for 60 minutes: the limit is minute 70.
    assert _amounts(events) == [70.0]
    renewed_at = (t0 + timedelta(seconds=600)).isoformat()
    assert attempt.sandbox_runtime["armed_at"] == renewed_at


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_a_renewal_on_a_provider_that_does_not_renew_is_ignored(metered_attempt, clock):
    capability, events = metered_attempt
    attempt = capability.attempt

    t0 = _start_then_renew(attempt, clock, provider=SimpleNamespace())
    _seal(attempt)

    assert _amounts(events) == [120.0]
    assert attempt.sandbox_runtime["armed_at"] == t0.isoformat()


@pytest.mark.django_db
def test_a_renewal_after_the_end_changes_nothing(hosted_attempt, clock):
    attempt = hosted_attempt.attempt
    harness_usage.record_sandbox_runtime(
        attempt, started=True, provider="e2b", lifetime_seconds=600
    )
    harness_usage.record_sandbox_runtime(attempt, final=True)
    attempt.refresh_from_db()
    before = dict(attempt.sandbox_runtime)

    harness_usage.rearm_sandbox_runtime(
        attempt,
        provider=SimpleNamespace(renews_ttl=True),
        lifetime_seconds=3600,
        renewed_at=clock[0] + timedelta(minutes=5),
    )

    attempt.refresh_from_db()
    assert attempt.sandbox_runtime == before


def test_only_a_provider_whose_renewal_restarts_the_lifetime_says_so():
    assert SandboxRuntimeProvider.renews_ttl is False
    assert DaytonaSandboxRuntimeProvider.renews_ttl is False
    assert E2BSandboxRuntimeProvider.renews_ttl is True


# ── What is free ────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("daytona-byo", []),
        ("daytona", [2.5]),
        ("e2b", [2.5]),
        ("Daytona-BYO", [2.5]),
    ],
)
def test_bring_your_own_sandbox_is_free(metered_attempt, provider, expected):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(
        capability.attempt,
        provider=provider,
        started=t0,
        ended=t0 + timedelta(seconds=150),
    )

    _seal(attempt)

    assert _amounts(events) == expected


_DOMAIN_CHARGES = {
    "infrastructure": [],
    "connectivity": [],
    "platform_sync": [],
    "agent": [2.5],
    "simulator": [2.5],
    "environment": [2.5],
    "grading": [2.5],
    None: [2.5],
}


def test_every_failure_domain_has_a_verdict():
    assert set(_DOMAIN_CHARGES) - {None} == set(HARNESS_FAILURE_DOMAINS)


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        pytest.param(domain, expected, id=str(domain))
        for domain, expected in _DOMAIN_CHARGES.items()
    ],
)
def test_our_failure_is_free(metered_attempt, domain, expected):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(
        capability.attempt,
        started=t0,
        ended=t0 + timedelta(seconds=150),
        domain=domain,
        code=None if domain is None else "run_failed",
    )

    _seal(attempt)

    assert _amounts(events) == expected


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_job_failure_of_the_current_attempt_is_free(metered_attempt):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(capability.attempt, started=t0, ended=t0 + timedelta(seconds=150))
    _set_job(
        attempt,
        failure={"domain": "platform_sync", "code": "authoring_snapshot_missing"},
    )

    _seal(attempt)

    assert _amounts(events) == []


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_job_failure_does_not_free_an_earlier_attempt(metered_attempt):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(capability.attempt, started=t0, ended=t0 + timedelta(seconds=150))
    _set_job(
        attempt,
        failure={"domain": "platform_sync", "code": "authoring_snapshot_missing"},
        current_attempt_number=attempt.attempt_number + 1,
    )

    _seal(attempt)

    assert _amounts(events) == [2.5]


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("attempt_capability_activation_invalid", []),
        ("conversation_idempotency_conflict", []),
        # The customer's source would not unpack: their fault, charged.
        ("sandbox_upload_prepare_failed", [2.5]),
    ],
)
def test_our_launch_errors_are_free(metered_attempt, code, expected):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(
        capability.attempt,
        started=t0,
        ended=t0 + timedelta(seconds=150),
        domain="environment",
        code=code,
    )

    _seal(attempt)

    assert _amounts(events) == expected


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("state", "failure_code", "cancel_reason", "expected"),
    [
        *[
            pytest.param(AgentFixRun.State.FAILED, code, "fix_ended", [], id=code)
            for code in sorted(harness_usage.OUR_FIX_FAILURES)
        ],
        pytest.param(
            AgentFixRun.State.FAILED,
            "fix_deadline",
            "fix_ended",
            [2.5],
            id="fix_deadline",
        ),
        pytest.param(AgentFixRun.State.READY, None, "fix_ended", [2.5], id="ready"),
        pytest.param(
            AgentFixRun.State.FAILED,
            "fix_engine_failed",
            "user",
            [2.5],
            id="person_cancelled",
        ),
    ],
)
def test_check_run_cancelled_by_our_fix_failure_is_free(
    metered_attempt, user, workspace, state, failure_code, cancel_reason, expected
):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(capability.attempt, started=t0, ended=t0 + timedelta(seconds=150))
    _check_run_of_a_fix(
        attempt,
        user,
        workspace,
        state=state,
        failure=None if failure_code is None else {"code": failure_code},
        cancel_reason=cancel_reason,
    )

    _seal(attempt)

    assert _amounts(events) == expected


# ── When it is sent, and only once ──────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("cleanup", "ended"),
    [(False, True), (True, False)],
    ids=["cleanup_not_confirmed", "end_not_recorded"],
)
def test_no_event_before_cleanup_is_confirmed(metered_attempt, cleanup, ended):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(
        capability.attempt,
        started=t0,
        ended=t0 + timedelta(seconds=150) if ended else None,
        cleanup=cleanup,
    )

    _seal(attempt)

    assert _sandbox(events) == []
    assert _billed(attempt) is None

    record_cleanup(attempt.id, provider_ref="", verified_absent=True)
    _seal(attempt)

    assert len(_sandbox(events)) == 1


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "cleaned_up", [False, True], ids=["before_cleanup", "after_cleanup"]
)
def test_ingestion_never_sends_the_sandbox_charge(
    metered_attempt, django_capture_on_commit_callbacks, cleaned_up
):
    capability, events = metered_attempt
    attempt = capability.attempt
    _provision(attempt)
    attempt.job.refresh_from_db()
    begin_scenarios(
        attempt,
        {"run_test_id": str(attempt.job.run_test_id), "scenario_keys": ["case-one"]},
    )
    t0 = _t0()
    _finish(
        attempt,
        started=t0,
        ended=t0 + timedelta(seconds=150),
        cleanup=cleaned_up,
    )
    if cleaned_up:
        harness_usage.freeze_run_sandbox_charge(attempt, attempt.job)
        assert _billed(attempt) == "2.5000"

    with django_capture_on_commit_callbacks(execute=True) as replays:
        _, created = ingest_result_receipt(attempt, _skipped_receipt(attempt))

    assert created is True
    assert len(replays) == 1
    assert _sandbox(events) == []

    if not cleaned_up:
        HostedHarnessAttempt.no_workspace_objects.filter(id=attempt.id).update(
            cleanup_verified_at=timezone.now()
        )
    _seal(attempt)

    assert _amounts(events) == [2.5]


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_attempt_timed_before_this_change_is_never_billed(metered_attempt):
    capability, events = metered_attempt
    attempt = capability.attempt
    t0 = _t0()
    ended = t0 + timedelta(seconds=150)
    HostedHarnessAttempt.no_workspace_objects.filter(id=attempt.id).update(
        sandbox_runtime={
            "started_at": t0.isoformat(),
            "seconds": 150.0,
            "ended_at": ended.isoformat(),
        },
        cleanup_verified_at=ended,
    )
    attempt.refresh_from_db()

    _seal(attempt)
    _seal(attempt)
    record_cleanup(attempt.id, provider_ref="", verified_absent=True)
    # Old enough for the sweep to pick it up again.
    HostedHarnessCleanupReceipt.no_workspace_objects.filter(
        attempt_id=attempt.id
    ).update(created_at=timezone.now() - timedelta(minutes=30))
    swept = seal_unsealed_hosted_harness_usage._original_func()

    assert swept["scheduled"] == 1
    assert _sandbox(events) == []
    assert _billed(attempt) is None
    attempt.job.refresh_from_db()
    assert harness_usage.harness_consumption(attempt.job)["sandbox_minutes"] == 0


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_replay_reuses_one_event_id(metered_attempt):
    capability, events = metered_attempt
    t0 = _t0()
    ended = t0 + timedelta(seconds=150)
    attempt = _finish(capability.attempt, started=t0, ended=ended)

    for _ in range(3):
        _seal(attempt)

    sent = _sandbox(events)
    job = attempt.job
    assert len(sent) == 3
    assert {event.event_id for event in sent} == {
        str(uuid5(attempt.id, "sandbox_runtime"))
    }
    assert {event.amount for event in sent} == {2.5}
    assert sent[0].org_id == str(job.organization_id)
    assert sent[0].timestamp == ended
    assert sent[0].properties == {
        "source": "rl_environment",
        "source_id": str(job.id),
        "harness_job_id": str(job.id),
        "attempt_id": str(attempt.id),
        "workspace_id": str(job.workspace_id or ""),
        "test_execution_id": str(job.test_execution_id or ""),
        "sandbox_kind": "run",
        "provider": "daytona",
        "failure_domain": None,
    }


@pytest.mark.django_db
def test_oss_emits_nothing(hosted_attempt, monkeypatch):
    monkeypatch.setattr(harness_usage, "is_oss", lambda: True)
    asked = []
    monkeypatch.setattr(
        harness_usage,
        "check_harness_action",
        lambda *args: asked.append(args) or {"allowed": False},
    )
    t0 = _t0()
    attempt = _finish(
        hosted_attempt.attempt, started=t0, ended=t0 + timedelta(seconds=150)
    )

    _seal(attempt)
    # What a re-run does before it resets the job.
    harness_usage.freeze_run_sandbox_charge(attempt, attempt.job)
    harness_usage.require_sandbox_create(
        str(attempt.job.organization_id), attempt=attempt
    )

    assert _billed(attempt) is None
    assert asked == []


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize("billed", [False, True], ids=["free", "billed"])
def test_charge_is_frozen_once(metered_attempt, billed):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(capability.attempt, started=t0, ended=t0 + timedelta(seconds=150))
    our_failure = {"domain": "platform_sync", "code": "sync_failed"}

    if billed:
        _seal(attempt)
        assert _billed(attempt) == "2.5000"
        # A later failure written on the job does not take back what was billed.
        _set_job(attempt, failure=our_failure)
        _seal(attempt)

        sent = _sandbox(events)
        assert [event.amount for event in sent] == [2.5, 2.5]
        assert sent[0].event_id == sent[1].event_id
        return

    _set_job(attempt, failure=our_failure)
    _seal(attempt)
    assert _billed(attempt) == "0.0000"

    # What a re-run does to the job afterwards must not turn the attempt billable.
    _set_job(attempt, failure=None, current_attempt_number=attempt.attempt_number + 1)
    assert harness_usage.harness_consumption(attempt.job)["sandbox_minutes"] == 0
    _seal(attempt)

    assert _sandbox(events) == []


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_seal_retries_when_the_stream_write_fails(
    hosted_attempt, monkeypatch, settings
):
    import redis

    from ee.usage.services import emitter

    class Stream:
        def __init__(self):
            self.writes = 0
            self.entries = []

        def xadd(self, key, data, maxlen=None):
            self.writes += 1
            if self.writes == 1:
                raise redis.ConnectionError("stream unavailable")
            self.entries.append(data)

    stream = Stream()
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    settings.USAGE_EVENTS_ENABLED = True
    monkeypatch.setattr(emitter, "_consumer_started", True)
    monkeypatch.setattr(emitter, "get_redis", lambda: stream)
    monkeypatch.setattr(seal_hosted_harness_usage, "apply_async", lambda *a, **k: None)
    t0 = _t0()
    attempt = _finish(
        hosted_attempt.attempt, started=t0, ended=t0 + timedelta(seconds=150)
    )
    record_cleanup(attempt.id, provider_ref="", verified_absent=True)

    with pytest.raises(redis.ConnectionError):
        seal_hosted_harness_usage._original_func(str(attempt.id))

    receipt = HostedHarnessCleanupReceipt.no_workspace_objects.get(
        attempt_id=attempt.id
    )
    assert "usage_sealed_at" not in receipt.details
    assert _billed(attempt) is None

    assert seal_hosted_harness_usage._original_func(str(attempt.id))["sealed"] is True
    sandbox_entries = [
        entry for entry in stream.entries if entry["event_type"] == "sandbox_runtime"
    ]
    assert len(sandbox_entries) == 1
    assert sandbox_entries[0]["event_id"] == str(uuid5(attempt.id, "sandbox_runtime"))
    assert float(sandbox_entries[0]["amount"]) == 2.5


# ── The run page and the fix's cost ─────────────────────────────────────────


@pytest.mark.django_db
def test_consumption_reports_billed_minutes_beside_seconds(hosted_attempt):
    t0 = _t0()
    first = hosted_attempt.attempt
    job_id = first.job_id
    second = register_attempt(job_id, endpoint_base_url="https://platform.example")
    third = register_attempt(job_id, endpoint_base_url="https://platform.example")
    billed = _finish(first, started=t0, ended=t0 + timedelta(seconds=150))
    free = _finish(
        second.attempt,
        started=t0,
        ended=t0 + timedelta(seconds=180),
        domain="infrastructure",
        code="sandbox_create_failed",
    )
    # Final, but its charge is not frozen yet: it shows nothing until the seal runs.
    _finish(third.attempt, started=t0, ended=t0 + timedelta(seconds=60))
    for attempt, minutes in ((billed, "2.5000"), (free, "0.0000")):
        runtime = {**attempt.sandbox_runtime, "billed_minutes": minutes}
        HostedHarnessAttempt.no_workspace_objects.filter(id=attempt.id).update(
            sandbox_runtime=runtime
        )
    job = HostedHarnessJob.no_workspace_objects.get(id=job_id)

    consumption = harness_usage.harness_consumption(job)

    assert consumption["sandbox_minutes"] == 2.5
    assert consumption["sandbox_seconds"] == 390


def test_consumption_serializer_carries_sandbox_minutes():
    data = HarnessConsumptionSerializer(
        {
            "text_sim_tokens": 0,
            "voice_sim_minutes": 0,
            "ai_credits": 0,
            "sandbox_seconds": 150,
            "sandbox_minutes": 2.5,
        }
    ).data

    assert data["sandbox_minutes"] == 2.5


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_fix_check_run_prices_sandbox_minutes(
    hosted_attempt, user, workspace, monkeypatch
):
    from ee.usage.schemas.billing_config import PricingTier
    from ee.usage.services.config import BillingConfig

    tiers = {
        "sandbox_minutes": [PricingTier(up_to=None, price_per_unit=Decimal("0.01"))]
    }
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    monkeypatch.setattr(
        BillingConfig, "get_pricing_tiers", lambda self, key: tiers.get(key, [])
    )
    monkeypatch.setattr(
        harness_usage,
        "harness_consumption",
        lambda job: {
            "text_sim_tokens": 0,
            "voice_sim_minutes": 0,
            "ai_credits": 0,
            "sandbox_minutes": 30,
        },
    )
    attempt = hosted_attempt.attempt
    _check_run_of_a_fix(
        attempt,
        user,
        workspace,
        state=AgentFixRun.State.RUNNING,
        failure=None,
        cancel_reason=None,
    )

    harness_usage.price_fix_check_run(attempt.job_id)

    attempt.job.refresh_from_db()
    assert attempt.job.fix_cost_usd == Decimal("0.3000")


# ── The limit checks ────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_require_sandbox_create_refusal_is_persisted_and_raises_402(
    metered_attempt, monkeypatch
):
    from ee.usage.services import metering

    capability, _ = metered_attempt
    attempt = capability.attempt
    monkeypatch.setattr(metering, "check_usage", _refusing())
    decision = _refusing()(str(attempt.job.organization_id), "sandbox_runtime")

    with pytest.raises(HostedHarnessError) as refused:
        harness_usage.require_sandbox_create(
            str(attempt.job.organization_id), attempt=attempt
        )

    assert refused.value.code == "usage_limit"
    assert refused.value.status_code == 402
    assert refused.value.retryable is False
    assert refused.value.message == decision.reason
    attempt.job.refresh_from_db()
    assert attempt.job.payload["metadata"]["usage_limit"] == decision.model_dump(
        mode="json"
    )


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("error", "level"),
    [("BillingConfigError", "info"), ("RuntimeError", "warning")],
    ids=["no_billing_config", "anything_else"],
)
def test_require_sandbox_create_allows_when_billing_cannot_load(
    metered_attempt, monkeypatch, error, level
):
    from ee.usage.services import metering
    from ee.usage.services.config import BillingConfigError

    capability, _ = metered_attempt
    attempt = capability.attempt
    raised = {"BillingConfigError": BillingConfigError, "RuntimeError": RuntimeError}[
        error
    ]

    def check_usage(*args, **kwargs):
        raise raised("billing is not available here")

    monkeypatch.setattr(metering, "check_usage", check_usage)
    log = MagicMock()
    monkeypatch.setattr(harness_usage, "logger", log)

    harness_usage.require_sandbox_create(
        str(attempt.job.organization_id), attempt=attempt
    )

    attempt.job.refresh_from_db()
    assert "usage_limit" not in (attempt.job.payload.get("metadata") or {})
    getattr(log, level).assert_called_once()
    other = "warning" if level == "info" else "info"
    getattr(log, other).assert_not_called()


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "check",
    [
        "require_harness_run_usage",
        "require_harness_call_usage",
        "require_agent_fix_usage",
    ],
)
def test_submission_checks_include_sandbox_minutes(metered_attempt, monkeypatch, check):
    from ee.usage.exceptions import UsageLimitExceeded
    from ee.usage.services import metering

    capability, _ = metered_attempt
    job = capability.attempt.job
    organization_id = str(job.organization_id)
    monkeypatch.setattr(metering, "check_usage", _refusing(only="sandbox_runtime"))
    arguments = {
        "require_harness_run_usage": (
            organization_id,
            {"agent": {"connector": "phone"}},
        ),
        "require_harness_call_usage": (job,),
        "require_agent_fix_usage": (organization_id,),
    }[check]

    with pytest.raises(UsageLimitExceeded) as refused:
        getattr(harness_usage, check)(*arguments)

    assert refused.value.check_result.dimension == "sandbox_minutes"


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_require_sandbox_start_answers_the_standard_limit_fields(
    metered_attempt, monkeypatch
):
    from ee.usage.services import metering

    capability, _ = metered_attempt
    organization_id = str(capability.attempt.job.organization_id)
    monkeypatch.setattr(metering, "check_usage", _refusing())

    with pytest.raises(HostedHarnessError) as refused:
        harness_usage.require_sandbox_start(organization_id)

    assert refused.value.code == "usage_limit"
    assert refused.value.status_code == 402
    body = refused.value.as_dict()
    assert body["error_code"] == "BUDGET_PAUSED"
    assert body["dimension"] == "sandbox_minutes"
    assert body["upgrade_cta"] is None
    assert {"current_usage", "limit"} <= body.keys()

    def unavailable(*args, **kwargs):
        raise OSError("usage store unreachable")

    monkeypatch.setattr(metering, "check_usage", unavailable)
    with pytest.raises(OSError):
        harness_usage.require_sandbox_start(organization_id)


# ── A delete that leaves the sandbox gone ───────────────────────────────────


class _Provider:
    def __init__(self, outcome):
        self.outcome = outcome
        self.asked = []

    def get(self, sandbox_id, *, request_timeout=None):
        self.asked.append((sandbox_id, request_timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize(
    ("deleted", "outcome", "expected", "asked"),
    [
        pytest.param(None, object(), True, False, id="None"),
        pytest.param(True, object(), True, False, id="True"),
        pytest.param(
            False,
            SandboxNotFoundError("sandbox not found", status_code=404),
            True,
            True,
            id="False-gone",
        ),
        pytest.param(False, object(), False, True, id="False-alive"),
        pytest.param(
            False,
            SandboxProviderError("provider unavailable", status_code=503),
            False,
            True,
            id="False-error",
        ),
    ],
)
def test_delete_confirmed(deleted, outcome, expected, asked):
    provider = _Provider(outcome)

    assert delete_confirmed(provider, "ref-1", deleted, request_timeout=7) is expected
    assert provider.asked == ([("ref-1", 7)] if asked else [])


# ── Whole runs, from launch to the bill ─────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("provider", "expected"),
    [("daytona", [12.0]), ("daytona-byo", [])],
    ids=["daytona", "daytona-byo"],
)
def test_twelve_minute_run_meters_twelve(metered_attempt, clock, provider, expected):
    capability, events = metered_attempt
    attempt = capability.attempt
    t0 = clock[0]
    harness_usage.record_sandbox_runtime(
        attempt,
        started=True,
        provider=provider,
        lifetime_seconds=LIFETIME,
        armed_at=t0,
    )
    clock[0] = t0 + timedelta(minutes=12)
    harness_usage.record_sandbox_runtime(attempt, final=True)
    HostedHarnessAttempt.no_workspace_objects.filter(id=attempt.id).update(
        cleanup_verified_at=clock[0]
    )
    attempt.refresh_from_db()

    _seal(attempt)

    assert _amounts(events) == expected
    assert attempt.sandbox_runtime["provider"] == provider
    attempt.job.refresh_from_db()
    assert harness_usage.harness_consumption(attempt.job)["sandbox_minutes"] == (
        expected[0] if expected else 0
    )


_SEVEN_MINUTES = 420


def _gone(attempt, t0, *, heartbeat, found, retried=False):
    return _finish(
        attempt,
        lifetime=_SEVEN_MINUTES,
        started=t0,
        ended=found,
        domain="infrastructure",
        code="sandbox_disappeared",
        heartbeat=heartbeat,
        retried=retried,
    )


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_run_sandbox_expired_at_minute_seven_meters_seven(metered_attempt):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _gone(
        capability.attempt,
        t0,
        heartbeat=t0 + timedelta(minutes=6, seconds=59),
        found=t0 + timedelta(minutes=7, seconds=15),
    )

    _seal(attempt)

    assert _amounts(events) == [7.0]


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("heartbeat", "found", "expected"),
    [
        pytest.param(410, 419, [], id="found_before_the_cap"),
        pytest.param(329, 7200, [], id="silent_long_before_the_cap"),
        pytest.param(330, 420, [7.0], id="last_seen_exactly_90_s_before"),
        pytest.param(419, 420, [7.0], id="found_exactly_at_the_cap"),
        pytest.param(None, 480, [], id="no_heartbeat"),
    ],
)
def test_run_sandbox_gone_before_its_cap_is_free(
    metered_attempt, heartbeat, found, expected
):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _gone(
        capability.attempt,
        t0,
        heartbeat=None if heartbeat is None else t0 + timedelta(seconds=heartbeat),
        found=t0 + timedelta(seconds=found),
    )

    _seal(attempt)

    assert _amounts(events) == expected


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_expired_run_attempt_retried_by_the_platform_is_free(metered_attempt):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _gone(
        capability.attempt,
        t0,
        heartbeat=t0 + timedelta(minutes=6, seconds=59),
        found=t0 + timedelta(minutes=7, seconds=15),
        retried=True,
    )

    _seal(attempt)

    assert _amounts(events) == []


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_superseded_attempt_is_charged(metered_attempt):
    capability, events = metered_attempt
    t0 = _t0()
    attempt = _finish(capability.attempt, started=t0, ended=t0 + timedelta(seconds=150))
    HostedHarnessAttempt.no_workspace_objects.filter(id=attempt.id).update(
        state=HostedHarnessAttempt.State.SUPERSEDED
    )
    attempt.refresh_from_db()

    _seal(attempt)

    assert _amounts(events) == [2.5]


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_dispatch_hang_attempt_is_free(metered_attempt):
    capability, events = metered_attempt
    t0 = _t0()
    # Only the domain decides; the dispatch that never answered is ours.
    attempt = _finish(
        capability.attempt,
        started=t0,
        ended=t0 + timedelta(minutes=12),
        domain="infrastructure",
        code="dispatch_unanswered",
    )

    _seal(attempt)

    assert _sandbox(events) == []


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_infra_failed_scenario_in_a_billed_attempt_charges_the_sandbox(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, events = metered_attempt
    attempt = capability.attempt
    _provision(attempt)
    ours = _record(
        "voice_call", amount=1, outcome="failed", failure_domain="infrastructure"
    )
    normal = _record("voice_call", amount=2)
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(attempt, _report([ours, normal]))
    _receipt(attempt)
    t0 = _t0()
    attempt = _finish(attempt, started=t0, ended=t0 + timedelta(seconds=150))

    _seal(attempt)

    assert sorted((event.event_type, event.amount) for event in events) == [
        ("sandbox_runtime", 2.5),
        ("voice_call", 2.0),
    ]


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_run_record_shows_four_figures(metered_attempt):
    capability, _ = metered_attempt
    t0 = _t0()
    attempt = _finish(capability.attempt, started=t0, ended=t0 + timedelta(seconds=150))
    _seal(attempt)
    attempt.job.refresh_from_db()

    consumption = harness_usage.harness_consumption(attempt.job)

    assert {
        "text_sim_tokens",
        "voice_sim_minutes",
        "ai_credits",
        "sandbox_minutes",
        "sandbox_seconds",
    } <= consumption.keys()
    assert consumption["sandbox_minutes"] == 2.5
