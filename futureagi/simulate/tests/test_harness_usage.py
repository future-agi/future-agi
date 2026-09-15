from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import fakeredis
import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessReceipt
from simulate.services import harness_usage
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    provision_scenarios,
    register_attempt,
)
from simulate.tests.test_hosted_harness_channels import BASE, _headers, _payload


@pytest.fixture
def metered_attempt(organization, monkeypatch, settings):
    from ee.usage.services import emitter

    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    monkeypatch.setattr(emitter, "_consumer_started", True)
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(emitter, "_redis_client", redis)
    job, _ = create_hosted_job(organization, _payload(), idempotency_key=str(uuid4()))
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    return capability, redis


def _record(
    action="scenario_generation", *, amount=1, funding="platform", infra_failed=False
):
    return {
        "id": str(uuid4()),
        "action": action,
        "scenario_key": "case-one",
        "amount": amount,
        "funding": funding,
        "infra_failed": infra_failed,
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def _report(records):
    return {
        "schema_version": "futureagi.harness-usage.v1",
        "operation": "report",
        "records": records,
        "totals": {
            "text_sim_tokens": sum(
                item["amount"]
                for item in records
                if item["action"] == "text_call"
                and item["funding"] == "platform"
                and not item["infra_failed"]
            ),
            "voice_sim_minutes": sum(
                item["amount"]
                for item in records
                if item["action"] == "voice_call" and not item["infra_failed"]
            ),
        },
        "sandbox_seconds": 12.5,
    }


@pytest.mark.django_db
def test_repeated_generation_reports_reconcile_with_existing_billing_ledger(
    metered_attempt, monkeypatch, django_capture_on_commit_callbacks
):
    consumer = pytest.importorskip("ee.cloud.billing.consumer")
    from ee.usage.models.usage import UsageEventLog, UsageSummary

    capability, redis = metered_attempt
    monkeypatch.setattr(consumer, "get_redis", lambda: redis)
    consumer._ensure_consumer_group()
    # The same snapshot can arrive from HTTP and the final pre-delete poll.
    report = _report([_record() for _ in range(31)])
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(capability.attempt, report)
        harness_usage.record_harness_usage(capability.attempt, report)
    consumer.process_batch()
    assert (
        UsageEventLog.objects.filter(
            organization=capability.attempt.job.organization
        ).count()
        == 31
    )
    summary = UsageSummary.objects.get(
        organization=capability.attempt.job.organization, dimension="ai_credits"
    )
    assert summary.total_usage_raw == 31
    capability.attempt.job.refresh_from_db()
    assert harness_usage.harness_consumption(capability.attempt.job)["ai_credits"] == 31


@pytest.mark.django_db
def test_stale_snapshot_cannot_erase_or_change_finalized_usage(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, _ = metered_attempt
    first, second = _record(), _record()
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(capability.attempt, _report([first, second]))
        harness_usage.record_harness_usage(capability.attempt, _report([first]))
    capability.attempt.job.refresh_from_db()
    stored = capability.attempt.job.payload["metadata"]["usage_reports"][
        str(capability.attempt.id)
    ]
    assert stored["records"] == [first, second]
    with pytest.raises(HostedHarnessError, match="cannot change"):
        harness_usage.record_harness_usage(
            capability.attempt, _report([{**first, "amount": 2}, second])
        )


@pytest.mark.django_db
def test_run_usage_waits_for_receipt_and_exempts_customer_tokens_not_voice(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, redis = metered_attempt
    attempt = capability.attempt
    provision_scenarios(
        attempt,
        {
            "name": "Usage suite",
            "modality": "voice",
            "personas": [
                {
                    "scenario_key": "case-one",
                    "name": "Caller",
                    "situation": "Ask",
                    "outcome": "Answer",
                }
            ],
        },
    )
    records = [
        _record("text_call", amount=200, funding="customer"),
        _record("text_call", amount=35),
        _record("voice_call", amount=0.25, funding="customer"),
        _record("voice_call", amount=2, infra_failed=True),
    ]
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(attempt, _report(records))
    assert redis.xlen("usage:events") == 0
    registration = attempt.job.scenario_registrations.get()
    HostedHarnessReceipt.objects.create(
        job=attempt.job,
        attempt=attempt,
        scenario=registration,
        attempt_number=attempt.attempt_number,
        status="passed",
        digest="sha256:" + "0" * 64,
        body={"call": {"duration_ms": 15000}, "failure": None},
    )
    harness_usage.replay_harness_usage(attempt)
    events = [data for _, data in redis.xrange("usage:events")]
    assert {(event["event_type"], float(event["amount"])) for event in events} == {
        ("text_call", 35.0),
        ("voice_call", 0.25),
    }


@pytest.mark.django_db
def test_usage_refusal_preserves_budget_dimension_without_starting_work(
    metered_attempt, monkeypatch
):
    from ee.usage.schemas.events import CheckResult
    from ee.usage.services import metering

    capability, redis = metered_attempt
    monkeypatch.setattr(
        metering,
        "check_usage",
        lambda *args: CheckResult(
            allowed=False,
            error_code="BUDGET_PAUSED",
            dimension="voice_sim_minutes",
            reason="Usage paused — you set a budget limit for voice_sim_minutes",
        ),
    )
    response = APIClient().post(
        f"{BASE}/{capability.attempt.id}/usage/",
        {"operation": "check", "action": "voice_call"},
        format="json",
        **_headers(capability),
    )
    assert response.status_code == 402
    body = response.json()
    assert body["error_code"] == "BUDGET_PAUSED"
    assert body["dimension"] == "voice_sim_minutes"
    assert redis.xlen("usage:events") == 0
    assert not capability.attempt.job.scenario_registrations.exists()


@pytest.mark.django_db
def test_successful_admission_clears_persisted_budget_refusal(
    metered_attempt, monkeypatch
):
    from ee.usage.schemas.events import CheckResult
    from ee.usage.services import metering

    capability, _ = metered_attempt
    decisions = iter(
        [
            CheckResult(
                allowed=False,
                error_code="BUDGET_PAUSED",
                dimension="text_sim_tokens",
                reason="Paused",
            ),
            CheckResult(allowed=True, dimension="text_sim_tokens"),
        ]
    )
    monkeypatch.setattr(metering, "check_usage", lambda *args: next(decisions))
    assert not harness_usage.check_harness_usage(capability.attempt, "text_call")[
        "allowed"
    ]
    capability.attempt.job.refresh_from_db()
    assert (
        capability.attempt.job.payload["metadata"]["usage_limit"]["error_code"]
        == "BUDGET_PAUSED"
    )
    assert harness_usage.check_harness_usage(capability.attempt, "text_call")["allowed"]
    capability.attempt.job.refresh_from_db()
    assert "usage_limit" not in capability.attempt.job.payload["metadata"]


@pytest.mark.django_db
def test_sandbox_runtime_stops_at_verified_teardown_without_billing(
    metered_attempt, monkeypatch
):
    capability, redis = metered_attempt
    started = datetime.now(UTC)
    moment = [started]
    monkeypatch.setattr(harness_usage.timezone, "now", lambda: moment[0])
    harness_usage.record_sandbox_runtime(capability.attempt, started=True)
    moment[0] = started + timedelta(seconds=30)
    harness_usage.record_sandbox_runtime(capability.attempt)
    moment[0] = started + timedelta(seconds=60)
    harness_usage.record_sandbox_runtime(capability.attempt, final=True)
    # A later cleanup/poll cannot extend a finalized observation.
    moment[0] = started + timedelta(seconds=90)
    harness_usage.record_sandbox_runtime(capability.attempt)
    capability.attempt.job.refresh_from_db()
    assert (
        harness_usage.harness_consumption(capability.attempt.job)["sandbox_seconds"]
        == 60
    )
    assert redis.xlen("usage:events") == 0
