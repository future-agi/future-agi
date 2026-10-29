"""The backend seal replays an attempt's usage after its sandbox is gone."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4, uuid5

import pytest
from django.utils import timezone
from temporalio.common import WorkflowIDConflictPolicy

from simulate.models import HostedHarnessCleanupReceipt, HostedHarnessJob
from simulate.services import harness_usage
from simulate.services.hosted_harness import (
    DELETE_CANCEL_REASON,
    create_hosted_job,
    finish_deferred_delete,
    register_attempt,
)
from simulate.tasks import hosted_harness_usage as usage_tasks
from simulate.tests.test_harness_usage import (
    _provision,
    _receipt,
    _record,
    _report,
    requires_cloud_billing,
)
from simulate.tests.test_hosted_harness_channels import _payload

AUTHORING_SPEND = {
    "stages": [
        {
            "stage": "build-environment",
            "models": ["gemini-3.7-flash"],
            "tokens_in": 1_000_000,
            "tokens_out": 100_000,
            "tokens_cached": 800_000,
        }
    ]
}


def _fresh_attempt(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key=str(uuid4()))
    return register_attempt(
        job.id, endpoint_base_url="https://platform.example"
    ).attempt


@pytest.fixture
def metered(organization, monkeypatch):
    from ee.usage.services import emitter

    events = []
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    monkeypatch.setattr(emitter, "emit", events.append)
    return _fresh_attempt(organization), events


@pytest.fixture
def seal_requests(monkeypatch):
    """Record seal enqueues at the Temporal boundary instead of connecting to it."""
    requests = []
    monkeypatch.setattr(
        usage_tasks.seal_hosted_harness_usage,
        "apply_async",
        lambda args, **options: requests.append((list(args), options)),
    )
    return requests


def _cleanup_receipt(attempt, *, age=timedelta(0), sealed=False):
    """What record_cleanup leaves behind, without running the cleanup itself."""
    moment = timezone.now() - age
    attempt.cleanup_verified_at = moment
    attempt.save(update_fields=["cleanup_verified_at", "updated_at"])
    receipt = HostedHarnessCleanupReceipt.no_workspace_objects.create(
        attempt=attempt,
        provider_ref="",
        verified_absent=True,
        details={"usage_sealed_at": "2026-01-01T00:00:00+00:00"} if sealed else {},
    )
    HostedHarnessCleanupReceipt.no_workspace_objects.filter(id=receipt.id).update(
        created_at=moment
    )
    return receipt


def _measured(attempt, records, django_capture_on_commit_callbacks):
    """Store a guest usage report and the receipt that makes its records billable."""
    _provision(attempt)
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(attempt, _report(records))
    _receipt(attempt)


def _seal(attempt):
    return usage_tasks.seal_hosted_harness_usage._original_func(str(attempt.id))


def _event_id(attempt, record):
    return str(uuid5(UUID(str(attempt.id)), record["id"]))


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_seal_replays_measured_calls_like_the_inline_path_did(
    metered, django_capture_on_commit_callbacks
):
    attempt, events = metered
    records = [
        _record("text_call", amount=200, funding="customer"),
        _record("text_call", amount=35),
        _record("voice_call", amount=0.25, funding="customer"),
        _record("voice_call", amount=2),
    ]
    _measured(attempt, records, django_capture_on_commit_callbacks)
    assert events == []
    receipt = _cleanup_receipt(attempt)

    with django_capture_on_commit_callbacks(execute=True):
        result = _seal(attempt)

    assert result == {"attempt_id": str(attempt.id), "sealed": True}
    assert {(event.event_type, float(event.amount)) for event in events} == {
        ("text_call", 200.0),
        ("text_call", 35.0),
        ("voice_call", 0.25),
        ("voice_call", 2.0),
    }
    assert {event.event_id for event in events} == {
        _event_id(attempt, record) for record in records
    }
    attempt.refresh_from_db()
    assert all(event.timestamp <= attempt.cleanup_verified_at for event in events)
    receipt.refresh_from_db()
    assert receipt.details["usage_sealed_at"]


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_seal_finalizes_authoring_credits_from_the_cleanup_spend(
    metered, django_capture_on_commit_callbacks
):
    attempt, events = metered
    job = attempt.job
    job.payload["metadata"] = {"harness_spend": {"attempts": {"1": AUTHORING_SPEND}}}
    job.save(update_fields=["payload", "updated_at"])
    _cleanup_receipt(attempt)

    with django_capture_on_commit_callbacks(execute=True):
        _seal(attempt)

    attempt.refresh_from_db()
    assert attempt.authoring_usage_report["records"][0]["credits"] == pytest.approx(
        70.2
    )
    assert len(events) == 1
    assert events[0].event_type == "harness_authoring"
    assert events[0].amount == pytest.approx(70.2)
    assert events[0].timestamp == attempt.created_at
    assert events[0].event_id == str(
        uuid5(UUID(str(attempt.id)), "authoring:build-environment")
    )


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_sealing_twice_re_emits_the_same_event_ids_and_rewrites_the_marker(
    metered, monkeypatch, django_capture_on_commit_callbacks
):
    attempt, events = metered
    record = _record("voice_call", amount=2)
    _measured(attempt, [record], django_capture_on_commit_callbacks)
    receipt = _cleanup_receipt(attempt)
    moment = [datetime(2026, 1, 2, tzinfo=UTC)]
    monkeypatch.setattr(usage_tasks, "timezone", SimpleNamespace(now=lambda: moment[0]))

    with django_capture_on_commit_callbacks(execute=True):
        _seal(attempt)
    receipt.refresh_from_db()
    first_marker = receipt.details["usage_sealed_at"]
    moment[0] = datetime(2026, 1, 2, 0, 5, tzinfo=UTC)
    with django_capture_on_commit_callbacks(execute=True):
        _seal(attempt)
    receipt.refresh_from_db()

    assert first_marker == "2026-01-02T00:00:00+00:00"
    assert receipt.details["usage_sealed_at"] == "2026-01-02T00:05:00+00:00"
    assert [event.event_id for event in events] == [_event_id(attempt, record)] * 2


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_seal_bills_a_job_deleted_while_it_ran(
    metered, django_capture_on_commit_callbacks
):
    attempt, events = metered
    record = _record("voice_call", amount=2)
    _measured(attempt, [record], django_capture_on_commit_callbacks)
    _cleanup_receipt(attempt)
    job = attempt.job
    job.payload["metadata"] = {"harness_spend": {"attempts": {"1": AUTHORING_SPEND}}}
    job.cancel_reason = DELETE_CANCEL_REASON
    job.save(
        update_fields=[
            "payload",
            "cancel_reason",
            "updated_at",
            *finish_deferred_delete(job),
        ]
    )
    assert not HostedHarnessJob.no_workspace_objects.filter(id=job.id).exists()

    with django_capture_on_commit_callbacks(execute=True):
        _seal(attempt)

    by_type = {event.event_type: event for event in events}
    assert set(by_type) == {"voice_call", "harness_authoring"}
    assert by_type["voice_call"].amount == pytest.approx(2.0)
    assert by_type["harness_authoring"].amount == pytest.approx(70.2)


def _delete_while_running(job):
    job.cancel_reason = DELETE_CANCEL_REASON
    job.save(
        update_fields=["cancel_reason", "updated_at", *finish_deferred_delete(job)]
    )
    assert not HostedHarnessJob.no_workspace_objects.filter(id=job.id).exists()


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_seal_bills_measured_calls_of_a_job_deleted_while_it_ran(
    metered, django_capture_on_commit_callbacks
):
    attempt, events = metered
    record = _record("voice_call", amount=2)
    _measured(attempt, [record], django_capture_on_commit_callbacks)
    _cleanup_receipt(attempt)
    _delete_while_running(attempt.job)

    with django_capture_on_commit_callbacks(execute=True):
        _seal(attempt)

    assert [event.event_type for event in events] == ["voice_call"]
    assert events[0].event_id == _event_id(attempt, record)
    assert events[0].amount == pytest.approx(2.0)


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_seal_finalizes_authoring_of_a_job_deleted_while_it_ran(
    metered, monkeypatch, django_capture_on_commit_callbacks
):
    from ee.usage.services.config import BillingConfig

    attempt, events = metered
    monkeypatch.setattr(
        BillingConfig,
        "get",
        lambda: SimpleNamespace(calculate_ai_credits=lambda cost_usd: 1.0),
    )
    job = attempt.job
    job.payload["metadata"] = {"harness_spend": {"attempts": {"1": AUTHORING_SPEND}}}
    job.save(update_fields=["payload", "updated_at"])
    _cleanup_receipt(attempt)
    _delete_while_running(job)

    with django_capture_on_commit_callbacks(execute=True):
        _seal(attempt)

    assert [event.event_type for event in events] == ["harness_authoring"]
    assert events[0].amount == pytest.approx(1.0)
    assert events[0].event_id == str(
        uuid5(UUID(str(attempt.id)), "authoring:build-environment")
    )


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_seal_leaves_the_receipt_unmarked_when_replay_fails(
    metered, monkeypatch, django_capture_on_commit_callbacks
):
    from ee.usage.services import emitter

    attempt, _ = metered
    _measured(
        attempt, [_record("voice_call", amount=2)], django_capture_on_commit_callbacks
    )
    receipt = _cleanup_receipt(attempt)

    def unavailable(event):
        raise RuntimeError("usage rail unavailable")

    monkeypatch.setattr(emitter, "emit", unavailable)

    with pytest.raises(RuntimeError, match="usage rail unavailable"):
        _seal(attempt)

    receipt.refresh_from_db()
    assert "usage_sealed_at" not in receipt.details


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_seal_refuses_an_attempt_without_a_cleanup_receipt(metered):
    attempt, events = metered

    with pytest.raises(HostedHarnessCleanupReceipt.DoesNotExist):
        _seal(attempt)

    assert events == []


@pytest.mark.django_db
def test_sweep_reschedules_only_stale_unsealed_receipts(organization, seal_requests):
    stale = _fresh_attempt(organization)
    staler = _fresh_attempt(organization)
    sealed = _fresh_attempt(organization)
    fresh = _fresh_attempt(organization)
    ancient = _fresh_attempt(organization)
    _cleanup_receipt(stale, age=timedelta(minutes=11))
    _cleanup_receipt(staler, age=timedelta(hours=1))
    _cleanup_receipt(sealed, age=timedelta(minutes=11), sealed=True)
    _cleanup_receipt(fresh, age=timedelta(minutes=9))
    _cleanup_receipt(ancient, age=timedelta(days=8))

    counts = usage_tasks.seal_unsealed_hosted_harness_usage._original_func()

    assert counts == {"scanned": 2, "scheduled": 2}
    assert [args for args, _ in seal_requests] == [[str(staler.id)], [str(stale.id)]]
    assert [options["task_id"] for _, options in seal_requests] == [
        f"hosted-harness-usage-seal-{staler.id}",
        f"hosted-harness-usage-seal-{stale.id}",
    ]
    for _, options in seal_requests:
        assert options["id_conflict_policy"] is WorkflowIDConflictPolicy.USE_EXISTING
        assert options["dispatch_timeout_seconds"] == 30


@pytest.mark.django_db
def test_sweep_caps_a_run_and_counts_only_accepted_enqueues(organization, monkeypatch):
    attempts = [_fresh_attempt(organization) for _ in range(3)]
    for minutes, attempt in zip((30, 20, 11), attempts, strict=True):
        _cleanup_receipt(attempt, age=timedelta(minutes=minutes))
    monkeypatch.setattr(usage_tasks, "_SWEEP_BATCH_LIMIT", 2)
    log = MagicMock()
    monkeypatch.setattr(usage_tasks, "logger", log)
    outcomes = iter([RuntimeError("temporal unreachable"), None])
    enqueued = []

    def apply_async(args, **options):
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome
        enqueued.append(list(args))

    monkeypatch.setattr(
        usage_tasks.seal_hosted_harness_usage, "apply_async", apply_async
    )

    counts = usage_tasks.seal_unsealed_hosted_harness_usage._original_func()

    assert counts == {"scanned": 2, "scheduled": 1}
    assert enqueued == [[str(attempts[1].id)]]
    log.error.assert_called_once()
    assert log.error.call_args.kwargs["attempt_id"] == str(attempts[0].id)


def test_enqueue_failure_never_raises_even_when_temporal_cannot_be_imported(
    monkeypatch,
):
    log = MagicMock()
    monkeypatch.setattr(usage_tasks, "logger", log)

    def unreachable(args, **options):
        raise RuntimeError("temporal unreachable")

    monkeypatch.setattr(
        usage_tasks.seal_hosted_harness_usage, "apply_async", unreachable
    )
    monkeypatch.setitem(sys.modules, "temporalio.common", None)

    assert usage_tasks.schedule_usage_seal("attempt-1") is False
    log.error.assert_called_once()
    assert log.error.call_args.kwargs == {"attempt_id": "attempt-1"}


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_record_harness_usage_can_store_without_emitting(
    metered, django_capture_on_commit_callbacks
):
    attempt, _ = metered
    report = _report([_record("voice_call", amount=2)])

    with django_capture_on_commit_callbacks() as silent:
        harness_usage.record_harness_usage(attempt, report, emit=False)
    with django_capture_on_commit_callbacks() as emitting:
        harness_usage.record_harness_usage(attempt, report)

    attempt.refresh_from_db()
    assert attempt.usage_report == report
    assert silent == []
    assert len(emitting) == 1


def test_seal_tasks_live_on_the_default_queue_with_the_stated_schedule():
    from simulate.temporal.constants import QUEUE_RUNNER
    from tfc.temporal.common import registry
    from tfc.temporal.drop_in.decorator import _ACTIVITY_WRAPPERS
    from tfc.temporal.schedules.simulate import SIMULATE_SCHEDULES

    seal = usage_tasks.seal_hosted_harness_usage
    sweep = usage_tasks.seal_unsealed_hosted_harness_usage
    assert "simulate.tasks.hosted_harness_usage" in registry.TEMPORAL_ACTIVITY_MODULES
    assert (
        seal._metadata["time_limit"],
        seal._metadata["max_retries"],
        seal._metadata["retry_delay"],
        seal._metadata["queue"],
    ) == (300, 10, 30, "default")
    assert (
        sweep._metadata["time_limit"],
        sweep._metadata["max_retries"],
        sweep._metadata["queue"],
    ) == (300, 0, "default")
    default_activities = registry.get_activities_for_queue("default")
    runner_activities = registry.get_activities_for_queue(QUEUE_RUNNER)
    for task in (seal, sweep):
        wrapper = _ACTIVITY_WRAPPERS[task.name]
        assert wrapper in default_activities
        assert wrapper not in runner_activities
    [schedule] = [
        item
        for item in SIMULATE_SCHEDULES
        if item.schedule_id == "seal-unsealed-hosted-harness-usage"
    ]
    assert schedule.activity_name == "seal_unsealed_hosted_harness_usage"
    assert schedule.interval_seconds == 600
    assert schedule.queue == "default"
    assert usage_tasks._SWEEP_BATCH_LIMIT == 200
    assert usage_tasks._SWEEP_GRACE == timedelta(minutes=10)
    assert usage_tasks._SWEEP_LOOKBACK == timedelta(days=7)
