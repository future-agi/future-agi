"""Model constraints + shared hosted-job submission service (certification)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import IntegrityError

from simulate.models import (
    CertificationCampaign,
    CertificationCase,
    CertificationNotification,
)
from simulate.services.hosted_harness_submission import submit_hosted_harness_job

pytestmark = pytest.mark.django_db


def _campaign(organization, **overrides):
    fields = {
        "organization": organization,
        "deployment_id": "helm:futureagi:42@us-gcp",
        "deployment_identity": {"helm_revision": 42},
        "suite_key": "production_agents_v1",
        "suite_digest": "sha256:" + "a" * 64,
        "normalized_suite": {"cases": []},
        "idempotency_key": "certification:helm:futureagi:42@us-gcp:sha256:" + "a" * 64,
    }
    fields.update(overrides)
    return CertificationCampaign.no_workspace_objects.create(**fields)


def _case(campaign, case_key="ride_booking__veh002_single", **overrides):
    fields = {
        "campaign": campaign,
        "case_key": case_key,
        "sequence": 1,
        "idempotency_key": f"{campaign.idempotency_key}:{case_key}",
        "spec": {"agent": {"name": "Ride Booking"}},
    }
    fields.update(overrides)
    return CertificationCase.no_workspace_objects.create(**fields)


# ---------------------------------------------------------------------------
# model constraints
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_campaign_unique_per_org_deployment_suite(organization):
    _campaign(organization)
    with pytest.raises(IntegrityError):
        _campaign(organization)
    # A different deployment id is a different campaign.
    _campaign(
        organization,
        deployment_id="helm:futureagi:43@us-gcp",
        idempotency_key="certification:helm:futureagi:43@us-gcp:sha256:" + "a" * 64,
    )


@pytest.mark.django_db(transaction=True)
def test_case_unique_per_campaign_key(organization):
    campaign = _campaign(organization)
    _case(campaign)
    with pytest.raises(IntegrityError):
        _case(campaign)
    _case(campaign, case_key="front_desk__veh001_single", sequence=2)


@pytest.mark.django_db(transaction=True)
def test_notification_dedupes_campaign_and_case_scopes(organization):
    campaign = _campaign(organization)
    case = _case(campaign)
    digest = "sha256:" + "b" * 64
    kwargs = {
        "campaign": campaign,
        "event_kind": CertificationNotification.EventKind.CAMPAIGN_STARTED,
        "payload": {"a": 1},
        "payload_digest": digest,
    }
    CertificationNotification.no_workspace_objects.create(**kwargs)
    with pytest.raises(IntegrityError):
        CertificationNotification.no_workspace_objects.create(**kwargs)

    case_kwargs = {
        "campaign": campaign,
        "case": case,
        "event_kind": CertificationNotification.EventKind.BLOCKING_FAILURE,
        "payload": {"b": 2},
        "payload_digest": digest,
    }
    CertificationNotification.no_workspace_objects.create(**case_kwargs)
    with pytest.raises(IntegrityError):
        CertificationNotification.no_workspace_objects.create(**case_kwargs)


# ---------------------------------------------------------------------------
# shared submission service
# ---------------------------------------------------------------------------


def _payload():
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {"kind": "remote"},
        "agent": {"connector": "auto", "config": {}, "secret_refs": {}},
        "scenario_count": 2,
        "seed": 7,
        "runtime": {"max_duration_seconds": 600},
        "security": {},
        "retry": {
            "max_infrastructure_attempts": 2,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 15,
        },
        "artifacts": {"level": "full", "max_artifact_bytes": 1024},
        "metadata": {},
    }


@pytest.fixture
def submission_mocks():
    job = SimpleNamespace(
        id=uuid4(),
        payload={
            "retry": {
                "max_infrastructure_attempts": 2,
                "initial_backoff_seconds": 1,
                "max_backoff_seconds": 15,
            }
        },
    )
    with (
        patch(
            "simulate.services.harness_provider._validate_secret_refs_hosted"
        ) as v_secrets,
        patch(
            "simulate.services.harness_provider._validate_phone_connectivity"
        ) as v_phone,
        patch(
            "simulate.services.harness_provider._validate_known_hosted_egress"
        ) as v_egress,
        patch("simulate.services.harness_usage.require_harness_run_usage") as run_usage,
        patch(
            "simulate.services.harness_provider._validate_required_credential_files",
            return_value=([], [], 0),
        ) as creds,
        patch(
            "simulate.services.harness_usage.require_harness_source_call_usage"
        ) as call_usage,
        patch(
            "simulate.services.hosted_harness.create_hosted_job",
            return_value=(job, True),
        ) as create,
        patch(
            "simulate.temporal.client.start_hosted_harness_gateway_workflow"
        ) as start,
    ):
        yield SimpleNamespace(
            job=job,
            v_secrets=v_secrets,
            v_phone=v_phone,
            v_egress=v_egress,
            run_usage=run_usage,
            creds=creds,
            call_usage=call_usage,
            create=create,
            start=start,
        )


def test_submit_runs_full_admission_and_schedules(organization, submission_mocks):
    m = submission_mocks
    job, created = submit_hosted_harness_job(
        organization,
        None,
        _payload(),
        "certification:dep:digest:case",
        "https://p.example",
    )
    assert job is m.job and created is True
    m.v_secrets.assert_called_once()
    m.v_phone.assert_called_once()
    m.v_egress.assert_called_once()
    m.run_usage.assert_called_once()
    m.creds.assert_called_once()
    m.call_usage.assert_called_once()
    m.create.assert_called_once()
    m.start.assert_called_once_with(str(m.job.id), "https://p.example", 2, 1, 15)


def test_submit_maps_unexpected_scheduler_failure(organization, submission_mocks):
    submission_mocks.start.side_effect = RuntimeError("temporal down")
    from simulate.services.hosted_harness import HostedHarnessError

    with pytest.raises(HostedHarnessError) as ei:
        submit_hosted_harness_job(
            organization, None, _payload(), "k", "https://p.example"
        )
    assert ei.value.code == "scheduler_unavailable"
    assert ei.value.status_code == 503
    assert ei.value.retryable is True


def test_submit_propagates_typed_validation_errors(organization, submission_mocks):
    from simulate.services.hosted_harness import HostedHarnessError

    submission_mocks.v_secrets.side_effect = HostedHarnessError(
        "secret_manager_unsupported", "nope", status_code=422
    )
    with pytest.raises(HostedHarnessError) as ei:
        submit_hosted_harness_job(
            organization, None, _payload(), "k", "https://p.example"
        )
    assert ei.value.code == "secret_manager_unsupported"
    submission_mocks.create.assert_not_called()
