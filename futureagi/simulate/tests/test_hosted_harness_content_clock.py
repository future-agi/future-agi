from __future__ import annotations

from types import SimpleNamespace

import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessJob
from simulate.services.hosted_harness import (
    create_hosted_job,
    register_attempt,
    update_execution_counts,
)
from simulate.services.hosted_harness_gateway import HostedHarnessGateway, _mark_stage

from .test_hosted_harness_channels import _headers, _payload

ATTEMPTS = "/simulate/api/harness/attempts"

pytestmark = pytest.mark.django_db


def _clock(job):
    return HostedHarnessJob.no_workspace_objects.get(id=job.id).content_updated_at


def _rewind(job):
    job.content_updated_at = job.created_at
    job.save(update_fields=["content_updated_at", "updated_at"])
    return job.created_at


def _fake_sandbox(files):
    return SimpleNamespace(
        fs=SimpleNamespace(download_file=lambda path, timeout=None: files[path]),
        process=SimpleNamespace(
            exec=lambda command, **kwargs: SimpleNamespace(exit_code=0, result="")
        ),
    )


def test_creation_stamps_the_clock(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="clock-new")

    assert job.content_updated_at is not None
    assert abs((job.content_updated_at - job.created_at).total_seconds()) < 1


def test_platform_stage_advance_moves_the_clock(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="clock-stage")
    before = _rewind(job)

    _mark_stage(job, "acquiring_source")

    assert job.current_stage == "acquiring_source"
    assert _clock(job) > before


def test_scenario_provisioning_moves_the_clock(user, workspace):
    job, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="clock-provision",
        workspace=workspace,
    )
    before = _rewind(job)
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")

    response = APIClient().post(
        f"{ATTEMPTS}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Refunds",
            "modality": "voice",
            "personas": [
                {
                    "scenario_key": "refund-request",
                    "name": "Customer",
                    "situation": "Asks for a refund",
                    "outcome": "Agent follows policy",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )

    assert response.status_code == 200, response.content
    assert _clock(job) > before


def test_heartbeat_moves_the_clock_only_when_the_authoring_changed(
    organization, monkeypatch
):
    payload = _payload(
        metadata={"authoring_object_key": "harness/jobs/x/authoring.tar.gz"}
    )
    job, _ = create_hosted_job(organization, payload, idempotency_key="clock-heartbeat")
    before = _rewind(job)
    attempt = SimpleNamespace(id="attempt-1", job_id=job.id, attempt_number=1)
    files = {
        "/work/authoring/contract.json": b'{"modality":"voice"}',
        "/work/authoring/environment-bundle/environment-plan.json": b'{"runtime":{}}',
        "/work/authoring/scenarios.json": b'[{"scenario_key":"one"}]',
    }
    monkeypatch.setattr(
        "simulate.services.harness_usage.record_sandbox_runtime",
        lambda *args, **kwargs: None,
    )

    HostedHarnessGateway._sync_authoring_progress(attempt, _fake_sandbox(files))

    first = _clock(job)
    assert first > before
    job.refresh_from_db()
    assert {item["kind"] for item in job.stage_outputs} >= {"contract", "scenarios"}

    HostedHarnessGateway._sync_authoring_progress(attempt, _fake_sandbox(files))

    assert _clock(job) == first

    files["/work/authoring/scenarios.json"] = (
        b'[{"scenario_key":"one"},{"scenario_key":"two"}]'
    )
    HostedHarnessGateway._sync_authoring_progress(attempt, _fake_sandbox(files))

    assert _clock(job) > first


def test_execution_counts_leave_the_clock_alone_without_a_run(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="clock-counts")
    before = _rewind(job)

    update_execution_counts(job)

    assert _clock(job) == before
