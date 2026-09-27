from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessReceipt
from simulate.services import harness_usage
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    provision_scenarios,
    record_cleanup,
    register_attempt,
)
from simulate.tests.test_hosted_harness_channels import BASE, _headers, _payload
from tfc.ee_loader import has_ee

# Credit amounts come from billing.yaml, which ships only with the private
# cloud overlay (ee/cloud); without it BillingConfig falls open to empty
# defaults and prices differently.
requires_cloud_billing = pytest.mark.skipif(
    not has_ee("ee.cloud"), reason="requires ee/cloud billing.yaml (OSS lane)"
)


@pytest.fixture
def hosted_attempt(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key=str(uuid4()))
    return register_attempt(job.id, endpoint_base_url="https://platform.example")


@pytest.fixture
def metered_attempt(hosted_attempt, monkeypatch):
    from ee.usage.services import emitter

    events = []
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    monkeypatch.setattr(emitter, "emit", events.append)
    return hosted_attempt, events


@pytest.mark.django_db
def test_oss_hosted_authoring_usage_is_free(hosted_attempt, monkeypatch):
    monkeypatch.setattr(harness_usage, "is_oss", lambda: True)

    assert harness_usage.check_harness_action(
        str(hosted_attempt.attempt.job.organization_id), "harness_authoring"
    ) == {"allowed": True}
    harness_usage.record_harness_authoring_usage(
        hosted_attempt.attempt,
        {
            "stages": [
                {
                    "stage": "understand-agent",
                    "models": ["gemini-3.7-flash"],
                    "tokens_in": 1,
                    "tokens_out": 1,
                }
            ]
        },
    )

    hosted_attempt.attempt.refresh_from_db()
    assert hosted_attempt.attempt.authoring_usage_report is None


def _record(
    action="text_call",
    *,
    amount=1,
    funding="platform",
    outcome="completed",
    failure_domain=None,
    occurred_at=None,
):
    record = {
        "id": str(uuid4()),
        "action": action,
        "scenario_key": "case-one",
        "amount": amount,
        "funding": funding,
        "occurred_at": occurred_at or datetime.now(UTC).isoformat(),
        "outcome": outcome,
    }
    if failure_domain is not None:
        record["failure_domain"] = failure_domain
    return record


def _report(records):
    return {
        "schema_version": "futureagi.harness-usage.v1",
        "operation": "report",
        "records": records,
    }


def _receipt(attempt, *, failure_domain="simulator"):
    registration = attempt.job.scenario_registrations.get()
    return HostedHarnessReceipt.objects.create(
        job=attempt.job,
        attempt=attempt,
        scenario=registration,
        attempt_number=attempt.attempt_number,
        status="errored",
        digest="sha256:" + "0" * 64,
        body={
            "call": {"duration_ms": 15000},
            "failure": {"domain": failure_domain, "code": "run_failed"},
        },
    )


def _provision(attempt):
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


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_authoring_tokens_become_deterministic_ai_credit_events(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, events = metered_attempt
    spend = {
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
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_authoring_usage(capability.attempt, spend)
        harness_usage.record_harness_authoring_usage(capability.attempt, spend)

    assert len(events) == 2
    assert events[0].event_id == events[1].event_id
    assert events[0].event_type == "harness_authoring"
    assert events[0].amount == pytest.approx(70.2)
    assert events[0].properties["model"] == "gemini-3.7-flash"
    assert events[0].properties["cached_input_tokens"] == 800_000
    capability.attempt.job.refresh_from_db()
    assert harness_usage.harness_consumption(capability.attempt.job)[
        "ai_credits"
    ] == pytest.approx(70.2)


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_live_authoring_estimates_refresh_without_charging_or_double_counting(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, events = metered_attempt
    job = capability.attempt.job
    first_stage = {
        "stage": "understand-agent",
        "models": ["gemini-3.7-flash"],
        "tokens_in": 1_000_000,
        "tokens_out": 100_000,
        "tokens_cached": 800_000,
    }
    first_spend = {"stages": [first_stage]}
    job.payload["metadata"] = {
        "harness_spend": {"attempts": {"1": first_spend}},
    }
    job.save(update_fields=["payload"])

    consumption = harness_usage.harness_consumption(job)
    assert consumption["ai_credits"] == pytest.approx(70.2)
    assert events == []

    second = register_attempt(job.id, endpoint_base_url="https://platform.example")
    second_spend = {"stages": [{**first_stage, "tokens_out": 200_000}]}
    job.refresh_from_db()
    job.payload["metadata"]["harness_spend"]["attempts"]["2"] = second_spend
    job.save(update_fields=["payload"])
    assert harness_usage.harness_consumption(job)["ai_credits"] == pytest.approx(185.4)
    assert events == []

    with django_capture_on_commit_callbacks(execute=True):
        record_cleanup(capability.attempt.id, provider_ref="", verified_absent=True)
    job.refresh_from_db()
    consumption = harness_usage.harness_consumption(job)
    assert consumption["ai_credits"] == pytest.approx(185.4)

    with django_capture_on_commit_callbacks(execute=True):
        record_cleanup(second.attempt.id, provider_ref="", verified_absent=True)
    job.refresh_from_db()
    consumption = harness_usage.harness_consumption(job)
    assert consumption["ai_credits"] == pytest.approx(185.4)
    assert sum(event.amount for event in events) == pytest.approx(185.4)
    register_attempt(job.id, endpoint_base_url="https://platform.example")
    job.refresh_from_db()
    job.payload["metadata"]["harness_spend"]["attempts"]["3"] = {
        "stages": [
            {
                "stage": "understand-agent",
                "models": ["not-a-priced-model"],
                "tokens_in": 1000,
                "tokens_out": 100,
            }
        ]
    }
    job.save(update_fields=["payload"])
    assert harness_usage.harness_consumption(job)["ai_credits"] is None


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_unpriceable_live_authoring_is_unavailable_not_zero(metered_attempt):
    capability, events = metered_attempt
    job = capability.attempt.job
    job.payload["metadata"] = {
        "harness_spend": {
            "attempts": {
                "1": {
                    "stages": [
                        {
                            "stage": "understand-agent",
                            "models": ["not-a-priced-model"],
                            "tokens_in": 1000,
                            "tokens_out": 100,
                        }
                    ],
                },
            },
        },
    }
    consumption = harness_usage.harness_consumption(job)
    assert consumption["ai_credits"] is None
    assert events == []


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_cleanup_finalizes_when_live_authoring_is_unpriced(metered_attempt):
    capability, events = metered_attempt
    job = capability.attempt.job
    job.payload["metadata"] = {
        "harness_spend": {
            "attempts": {
                "1": {
                    "stages": [
                        {
                            "stage": "understand-agent",
                            "models": ["gemini-3.8-flash-unlisted"],
                            "tokens_in": 1000,
                            "tokens_out": 100,
                        }
                    ]
                }
            }
        }
    }
    job.save(update_fields=["payload"])

    record_cleanup(
        capability.attempt.id,
        provider_ref="",
        verified_absent=True,
    )

    capability.attempt.refresh_from_db()
    job.refresh_from_db()
    assert capability.attempt.cleanup_verified_at is not None
    assert job.state == job.State.FAILED
    assert events == []


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_stale_snapshot_cannot_erase_or_change_finalized_usage(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, _ = metered_attempt
    first, second = _record(), _record()
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(capability.attempt, _report([first, second]))
        harness_usage.record_harness_usage(capability.attempt, _report([first]))
    capability.attempt.refresh_from_db()
    stored = capability.attempt.usage_report
    assert stored["records"] == [first, second]
    with pytest.raises(HostedHarnessError, match="cannot change"):
        harness_usage.record_harness_usage(
            capability.attempt, _report([{**first, "amount": 2}, second])
        )


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_receipt_replay_bills_platform_text_and_all_voice_minutes(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, events = metered_attempt
    attempt = capability.attempt
    _provision(attempt)
    records = [
        _record("text_call", amount=200, funding="customer"),
        _record("text_call", amount=35),
        _record("voice_call", amount=0.25, funding="customer"),
        _record("voice_call", amount=2),
    ]
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(attempt, _report(records))
    assert events == []

    _receipt(attempt)
    harness_usage.replay_harness_usage(attempt)

    assert {(event.event_type, float(event.amount)) for event in events} == {
        ("text_call", 200.0),
        ("text_call", 35.0),
        ("voice_call", 0.25),
        ("voice_call", 2.0),
    }
    attempt.job.refresh_from_db()
    assert harness_usage.harness_consumption(attempt.job) == {
        "text_sim_tokens": 235,
        "voice_sim_minutes": 2.25,
        "ai_credits": 0,
        "sandbox_seconds": 0,
    }


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_infrastructure_receipt_does_not_bill_measured_call(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, events = metered_attempt
    attempt = capability.attempt
    _provision(attempt)
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(
            attempt,
            _report(
                [
                    _record(
                        "voice_call",
                        amount=0.25,
                        outcome="failed",
                        failure_domain="infrastructure",
                    )
                ]
            ),
        )
    _receipt(attempt, failure_domain="infrastructure")

    harness_usage.replay_harness_usage(attempt)

    assert events == []
    attempt.job.refresh_from_db()
    assert harness_usage.harness_consumption(attempt.job)["voice_sim_minutes"] == 0


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_infrastructure_receipt_does_not_bill_completed_record(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, events = metered_attempt
    attempt = capability.attempt
    _provision(attempt)
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(
            attempt,
            _report([_record("voice_call", amount=0.25, outcome="completed")]),
        )
    _receipt(attempt, failure_domain="infrastructure")

    harness_usage.replay_harness_usage(attempt)

    assert events == []
    attempt.job.refresh_from_db()
    assert harness_usage.harness_consumption(attempt.job)["voice_sim_minutes"] == 0


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_simulator_failure_remains_billable_as_measured_usage(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, events = metered_attempt
    attempt = capability.attempt
    _provision(attempt)
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(
            attempt,
            _report(
                [
                    _record(
                        "voice_call",
                        amount=0.25,
                        outcome="failed",
                        failure_domain="simulator",
                    )
                ]
            ),
        )
    _receipt(attempt, failure_domain="simulator")

    harness_usage.replay_harness_usage(attempt)

    assert {(event.event_type, float(event.amount)) for event in events} == {
        ("voice_call", 0.25)
    }


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_guest_usage_timestamp_is_clamped_to_attempt_window(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, events = metered_attempt
    attempt = capability.attempt
    _provision(attempt)
    occurred_at = (attempt.created_at - timedelta(days=30)).isoformat()
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(
            attempt,
            _report([_record("voice_call", amount=0.25, occurred_at=occurred_at)]),
        )
    _receipt(attempt)

    harness_usage.replay_harness_usage(attempt)

    assert len(events) == 1
    assert events[0].timestamp == attempt.created_at


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_retried_receipt_history_keeps_prior_attempt_usage_billable(
    metered_attempt, django_capture_on_commit_callbacks
):
    capability, events = metered_attempt
    first = capability.attempt
    _provision(first)
    record = _record("voice_call", amount=2)
    with django_capture_on_commit_callbacks(execute=True):
        harness_usage.record_harness_usage(first, _report([record]))
    receipt = _receipt(first)

    second = register_attempt(
        first.job_id, endpoint_base_url="https://platform.example"
    )
    first.refresh_from_db()
    first.receipt_history = {
        "case-one": {
            "status": receipt.status,
            "body": receipt.body,
            "attempt_number": receipt.attempt_number,
            "digest": receipt.digest,
        }
    }
    first.save(update_fields=["receipt_history", "updated_at"])
    receipt.attempt = second.attempt
    receipt.attempt_number = second.attempt.attempt_number
    receipt.save(update_fields=["attempt", "attempt_number", "updated_at"])

    harness_usage.replay_harness_usage(first)

    assert {(event.event_type, float(event.amount)) for event in events} == {
        ("voice_call", 2.0)
    }


@pytest.mark.django_db
def test_sandbox_runtime_stops_at_verified_teardown_without_billing(
    hosted_attempt, monkeypatch
):
    capability, events = hosted_attempt, []
    started = datetime.now(UTC)
    moment = [started]
    monkeypatch.setattr(harness_usage.timezone, "now", lambda: moment[0])
    harness_usage.record_sandbox_runtime(capability.attempt, started=True)
    moment[0] = started + timedelta(seconds=30)
    harness_usage.record_sandbox_runtime(capability.attempt)
    moment[0] = started + timedelta(seconds=60)
    harness_usage.record_sandbox_runtime(capability.attempt, final=True)
    moment[0] = started + timedelta(seconds=90)
    harness_usage.record_sandbox_runtime(capability.attempt)

    capability.attempt.job.refresh_from_db()
    assert (
        harness_usage.harness_consumption(capability.attempt.job)["sandbox_seconds"]
        == 60
    )
    assert events == []


@pytest.mark.django_db
@pytest.mark.requires_ee
def test_usage_refusal_preserves_budget_dimension_without_starting_work(
    metered_attempt, monkeypatch
):
    from ee.usage.schemas.events import CheckResult
    from ee.usage.services import metering

    capability, events = metered_attempt
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
    assert events == []
    assert not capability.attempt.job.scenario_registrations.exists()


@pytest.mark.django_db
@pytest.mark.requires_ee
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
