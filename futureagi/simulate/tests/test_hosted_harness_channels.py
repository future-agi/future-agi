from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient

from simulate.models import CallExecution, HostedHarnessJob, HostedHarnessReceipt
from simulate.models.chat_message import ChatMessageModel
from simulate.services.hosted_harness import (
    HostedHarnessError,
    canonical_digest,
    create_hosted_job,
    record_cleanup,
    register_attempt,
)
from simulate.services.hosted_harness_ingestion import (
    _apply_receipt_to_call,
    _call_lifecycle_status,
    _ingest_hosted_transcript,
    _normalized_artifact_content_type,
    _read_hosted_tool_trace,
    _receipt_evaluation_coverage,
    _receipt_evaluations,
    ingest_result_receipt,
)

BASE = "/simulate/api/harness/attempts"


@pytest.mark.django_db
def test_hosted_personas_share_one_dataset_and_map_to_distinct_calls(organization):
    job, _ = create_hosted_job(
        organization,
        _payload(scenario_count=2),
        idempotency_key="grouped-scenario-dataset",
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    headers = _headers(capability)
    personas = [
        {
            "scenario_key": "late-refund",
            "name": "Sam",
            "situation": "My refund is late",
            "outcome": "Explain the status",
        },
        {
            "scenario_key": "duplicate-charge",
            "name": "Avery",
            "situation": "I was charged twice",
            "outcome": "Reverse the duplicate",
        },
    ]
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Billing support suite",
            "modality": "text",
            "personas": personas,
        },
        format="json",
        **headers,
    )
    assert provision.status_code == 200, provision.content
    provisioned = provision.json()["result"]
    assert len({item["scenario_id"] for item in provisioned["scenarios"]}) == 1

    registrations = list(
        job.scenario_registrations.select_related("scenario", "dataset_row").order_by(
            "created_at"
        )
    )
    assert len(registrations) == 2
    assert len({item.scenario_id for item in registrations}) == 1
    assert len({item.dataset_row_id for item in registrations}) == 2
    scenario = registrations[0].scenario
    assert scenario.dataset.row_set.filter(deleted=False).count() == 2

    begin = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": [item["scenario_key"] for item in personas],
        },
        format="json",
        **headers,
    )
    assert begin.status_code == 200, begin.content
    registrations = list(
        job.scenario_registrations.select_related("call_execution").order_by(
            "created_at"
        )
    )
    assert len({item.call_execution_id for item in registrations}) == 2
    assert {item.call_execution.row_id for item in registrations} == {
        item.dataset_row_id for item in registrations
    }


@pytest.mark.django_db
def test_chat_added_scenario_appends_a_row_without_replacing_existing_call(
    organization,
):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="append-grouped-scenario-row",
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    headers = _headers(capability)
    first = {
        "scenario_key": "late-refund",
        "name": "Sam",
        "situation": "My refund is late",
        "outcome": "Explain the status",
    }
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Billing support suite",
            "modality": "text",
            "personas": [first],
        },
        format="json",
        **headers,
    )
    assert provision.status_code == 200, provision.content
    provisioned = provision.json()["result"]
    begin = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": [first["scenario_key"]],
        },
        format="json",
        **headers,
    )
    assert begin.status_code == 200, begin.content
    original = job.scenario_registrations.select_related("call_execution").get()
    original_call_id = original.call_execution_id
    dataset_id = original.scenario.dataset_id

    second = {
        "scenario_key": "duplicate-charge",
        "name": "Avery",
        "situation": "I was charged twice",
        "outcome": "Reverse the duplicate",
    }
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(scenario_count=2)
    job.refresh_from_db()
    extended = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Billing support suite",
            "modality": "text",
            "personas": [first, second],
        },
        format="json",
        **headers,
    )
    assert extended.status_code == 200, extended.content
    resumed = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": [first["scenario_key"], second["scenario_key"]],
        },
        format="json",
        **headers,
    )
    assert resumed.status_code == 200, resumed.content

    registrations = list(
        job.scenario_registrations.select_related(
            "scenario", "call_execution"
        ).order_by("created_at")
    )
    assert len(registrations) == 2
    assert {item.scenario.dataset_id for item in registrations} == {dataset_id}
    assert registrations[0].call_execution_id == original_call_id
    assert registrations[1].call_execution_id != original_call_id
    assert registrations[1].call_execution.row_id == registrations[1].dataset_row_id
    assert registrations[0].scenario.dataset.row_set.filter(deleted=False).count() == 2


def test_recording_content_type_uses_wave_signature_over_bad_sender_default():
    header = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE"
    assert (
        _normalized_artifact_content_type(
            kind="recording_combined", supplied="video/mp4", header=header
        )
        == "audio/wav"
    )


def test_receipt_evaluations_preserve_deterministic_and_judged_checks():
    body = {
        "sub_goals": [
            {
                "name": "booking_created",
                "held": True,
                "judged": False,
                "reason": None,
            },
            {
                "name": "friendly_tone",
                "held": False,
                "judged": True,
                "reason": "The response was terse",
            },
        ],
        "evaluations": [{"name": "cs_policy", "passed": True, "kind": "eval"}],
    }

    assert _receipt_evaluations(body) == [
        {
            "name": "booking_created",
            "kind": "checkpoint",
            "passed": True,
            "reason": "",
        },
        {
            "name": "friendly_tone",
            "kind": "judge",
            "passed": False,
            "reason": "The response was terse",
        },
        {"name": "cs_policy", "passed": True, "kind": "eval"},
    ]


def test_receipt_evaluations_omit_a_sub_goal_nothing_decided():
    body = {
        "sub_goals": [
            {"name": "booking_created", "held": True, "judged": False, "reason": None},
            {"name": "explained_refusal", "held": None, "judged": True, "reason": None},
        ],
        "evaluations": [],
    }

    assert _receipt_evaluations(body) == [
        {
            "name": "booking_created",
            "kind": "checkpoint",
            "passed": True,
            "reason": "",
        },
    ]


def test_receipt_coverage_distinguishes_missing_grading_from_agent_failure():
    assert _receipt_evaluation_coverage(
        {
            "sub_goals": [
                {"name": "booked", "held": False, "judged": False},
                {"name": "friendly", "held": None, "judged": True},
            ],
            "evaluations": [],
        }
    ) == {"expected": 2, "executed": 1, "failed": 1, "complete": False}


def test_errored_scenario_with_completed_call_keeps_completed_lifecycle():
    assert (
        _call_lifecycle_status(
            {
                "status": "errored",
                "call": {
                    "started_at": "2026-08-27T10:00:00Z",
                    "ended_at": "2026-08-27T10:05:00Z",
                },
            }
        )
        == CallExecution.CallStatus.COMPLETED
    )
    assert (
        _call_lifecycle_status({"status": "errored", "call": None})
        == CallExecution.CallStatus.FAILED
    )


def test_receipt_projects_actual_call_end_time_and_duration():
    registration = MagicMock()
    call = registration.call_execution
    call.call_metadata = {}
    call.error_message = "chat_target_failed: old attempt could not connect"
    body = {
        "status": "errored",
        "call": {
            "started_at": "2026-08-27T10:00:00Z",
            "ended_at": "2026-08-27T10:01:22Z",
            "duration_ms": 82_000,
            "recording_artifacts": [],
            "stop_reason": "simulator_end_call",
        },
        "sub_goals": [],
        "evaluations": [],
    }

    with (
        patch(
            "simulate.services.hosted_harness_ingestion."
            "HostedHarnessArtifact.no_workspace_objects"
        ) as artifacts,
        patch("simulate.services.hosted_harness_ingestion.transaction.on_commit"),
    ):
        artifacts.filter.return_value.order_by.return_value.last.return_value = None
        _apply_receipt_to_call(registration, body)

    assert call.started_at == "2026-08-27T10:00:00Z"
    assert call.ended_at == "2026-08-27T10:01:22Z"
    assert call.completed_at == "2026-08-27T10:01:22Z"
    assert call.duration_seconds == 82
    assert call.ended_reason == "simulator_end_call"
    assert call.error_message == ""
    call.save.assert_called_once()


def test_read_hosted_tool_trace_ignores_blank_and_malformed_lines():
    response = MagicMock()
    response.read.return_value = (
        b'{"name":"lookup","ok":true}\n\nnot-json\n{"name":"book","ok":false}\n'
    )
    artifact = MagicMock(object_key="alk-harness/job/tool-trace")
    storage = MagicMock()
    storage.get_object.return_value = response

    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=storage,
    ):
        calls = _read_hosted_tool_trace(artifact)

    assert calls == [
        {"name": "lookup", "ok": True},
        {"name": "book", "ok": False},
    ]
    response.close.assert_called_once_with()
    response.release_conn.assert_called_once_with()


def _payload(**overrides):
    value = {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "remote",
            "endpoint": "https://agent.example.com",
            "visibility": "public",
        },
        "agent": {"connector": "vapi", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "seed": 7,
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
            "allow_bundle_download": False,
            "max_artifact_bytes": 1024,
        },
        "metadata": {},
    }
    value.update(overrides)
    return value


def _headers(capability):
    return {
        "HTTP_AUTHORIZATION": f"Bearer {capability.token}",
        "HTTP_X_HARNESS_FENCE": capability.fence,
    }


@pytest.mark.django_db
def test_hosted_text_transcript_is_materialized_for_chat_ui(organization, workspace):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="hosted-chat-transcript",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Chat transcript",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "chat-one",
                    "name": "Customer",
                    "situation": "Needs billing help",
                    "outcome": "Gets an answer",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    result = provision.json()["result"]
    client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": result["run_test_id"],
            "scenario_keys": ["chat-one"],
        },
        format="json",
        **_headers(capability),
    )
    call = CallExecution.objects.get(hosted_registration__scenario_key="chat-one")
    call.simulation_call_type = CallExecution.SimulationCallType.TEXT
    call.save(update_fields=["simulation_call_type"])
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "messages": [
                {"role": "user", "content": "Where is my invoice?"},
                {"role": "assistant", "content": "I can look that up."},
            ]
        }
    ).encode()
    storage = MagicMock()
    storage.get_object.return_value = response

    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=storage,
    ):
        _ingest_hosted_transcript(call, SimpleNamespace(object_key="transcript.json"))

    assert list(
        ChatMessageModel.objects.filter(call_execution=call)
        .order_by("created_at", "role")
        .values_list("role", flat=True)
    ) == ["user", "assistant"]
    response.close.assert_called_once_with()
    response.release_conn.assert_called_once_with()


def _upload_required_artifacts(client, capability, headers):
    entries = []
    storage = MagicMock()
    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=storage,
    ):
        for kind in ("build", "result", "log"):
            content = f"{kind}-artifact".encode()
            digest = hashlib.sha256(content).hexdigest()
            response = client.generic(
                "PUT",
                f"{BASE}/{capability.attempt.id}/artifacts/{digest}/",
                content,
                content_type="application/json",
                HTTP_X_ARTIFACT_KIND=kind,
                HTTP_X_ARTIFACT_SIZE=str(len(content)),
                **headers,
            )
            assert response.status_code == 201
            entries.append(
                {
                    "artifact_id": f"sha256:{digest}",
                    "kind": kind,
                    "size": len(content),
                    "scenario_key": None,
                }
            )
    return entries, storage


@pytest.mark.django_db
def test_job_creation_is_tenant_idempotent(organization):
    first, created = create_hosted_job(
        organization, _payload(), idempotency_key="stable-key"
    )

    second, duplicate = create_hosted_job(
        organization, _payload(), idempotency_key="stable-key"
    )

    assert created is True
    assert duplicate is False
    assert first.id == second.id
    assert first.payload["seed"] == 7

    with pytest.raises(HostedHarnessError, match="different request"):
        create_hosted_job(
            organization,
            _payload(scenario_count=2),
            idempotency_key="stable-key",
        )


@pytest.mark.django_db
def test_job_idempotency_cannot_cross_workspaces(organization, workspace):
    create_hosted_job(
        organization,
        _payload(),
        idempotency_key="workspace-stable-key",
        workspace=workspace,
    )

    with pytest.raises(HostedHarnessError, match="different workspace"):
        create_hosted_job(
            organization,
            _payload(),
            idempotency_key="workspace-stable-key",
            workspace=None,
        )


@pytest.mark.django_db
def test_failed_scenario_is_completed_call_in_the_submitting_workspace(
    organization, workspace
):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="workspace-outcome-key",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    headers = _headers(capability)
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Workspace outcome",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "discount-request",
                    "name": "Customer",
                    "situation": "Requests a discount",
                    "outcome": "Agent follows policy",
                }
            ],
        },
        format="json",
        **headers,
    )
    assert provision.status_code == 200, provision.content
    provisioned = provision.json()["result"]
    begin = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": ["discount-request"],
        },
        format="json",
        **headers,
    )
    assert begin.status_code == 200, begin.content
    scenario = provisioned["scenarios"][0]
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "discount-request",
        "scenario_id": scenario["scenario_id"],
        "scenario_attempt": 1,
        "world_index": 0,
        "status": "failed",
        "sub_goals": [
            {
                "name": "discount_policy_followed",
                "held": False,
                # DRF trims surrounding whitespace from CharFields. The digest
                # covers the guest's wire JSON, before that normalization.
                "reason": "The agent offered an unsupported discount ",
                "judged": False,
            }
        ],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)
    result = client.post(
        f"{BASE}/{capability.attempt.id}/results/",
        receipt,
        format="json",
        **headers,
    )
    assert result.status_code == 200, result.content

    job.refresh_from_db()
    assert job.run_test.workspace == workspace
    assert job.completed_count == 0
    assert job.failed_count == 1
    job.test_execution.refresh_from_db()
    assert job.test_execution.completed_calls == 1
    assert job.test_execution.failed_calls == 0
    registration = job.scenario_registrations.select_related(
        "scenario", "call_execution"
    ).get()
    assert registration.scenario.workspace == workspace
    assert registration.scenario.dataset.workspace == workspace
    assert registration.call_execution.status == CallExecution.CallStatus.COMPLETED
    assert (
        registration.call_execution.call_metadata["harness_outcome_status"] == "failed"
    )
    assert registration.call_execution.call_metadata["harness_eval_coverage"] == {
        "expected": 1,
        "executed": 1,
        "failed": 0,
        "complete": True,
    }


@pytest.mark.django_db
def test_registering_attempt_supersedes_old_capability(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="attempt-key")
    first = register_attempt(job.id, endpoint_base_url="https://platform.example")
    job.current_stage = "failed"
    job.failure = {"code": "sandbox_launch_failed"}
    job.terminal_at = django_timezone.now()
    job.save(update_fields=["current_stage", "failure", "terminal_at", "updated_at"])
    second = register_attempt(job.id, endpoint_base_url="https://platform.example")

    first.attempt.refresh_from_db()
    assert first.attempt.state == "superseded"
    assert second.attempt.attempt_number == 2
    assert second.document["endpoints"]["events"].endswith("/events/")
    assert second.document["endpoints"]["ingress"].endswith("/ingress/")
    job.refresh_from_db()
    assert (second.attempt.expires_at - job.deadline_at).total_seconds() == 420
    assert job.current_stage == "queued"
    assert job.failure is None
    assert job.terminal_at is None


@pytest.mark.django_db
def test_attempt_can_mint_bounded_signed_ingress_url(organization, settings):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="attempt-ingress-key"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    capability.attempt.provider_ref = "sandbox-provider-id"
    capability.attempt.save(update_fields=["provider_ref", "updated_at"])

    sandbox = MagicMock()
    sandbox.create_signed_preview_url.return_value = SimpleNamespace(
        url="https://signed-preview.example/provider-callback"
    )
    daytona_client = MagicMock()
    daytona_client.get.return_value = sandbox
    daytona_class = MagicMock(return_value=daytona_client)
    fake_daytona = SimpleNamespace(
        Daytona=daytona_class,
        DaytonaConfig=MagicMock(return_value=object()),
    )
    settings.DAYTONA_API_KEY = "configured"

    with patch.dict(sys.modules, {"daytona": fake_daytona}):
        response = APIClient().post(
            f"{BASE}/{capability.attempt.id}/ingress/",
            {"port": 8080, "expires_in_seconds": 7200},
            format="json",
            **_headers(capability),
        )

    assert response.status_code == 200, response.content
    assert response.json()["url"] == (
        "https://signed-preview.example/provider-callback"
    )
    daytona_client.get.assert_called_once_with("sandbox-provider-id")
    sandbox.create_signed_preview_url.assert_called_once()


@pytest.mark.django_db
def test_ingress_without_daytona_sdk_is_a_typed_502(organization, settings):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="attempt-ingress-no-sdk-key"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    capability.attempt.provider_ref = "sandbox-provider-id"
    capability.attempt.save(update_fields=["provider_ref", "updated_at"])
    settings.DAYTONA_API_KEY = "configured"

    with patch.dict(sys.modules, {"daytona": None}):
        response = APIClient().post(
            f"{BASE}/{capability.attempt.id}/ingress/",
            {"port": 8080, "expires_in_seconds": 7200},
            format="json",
            **_headers(capability),
        )

    assert response.status_code == 502, response.content
    assert response.json()["error"] == "ingress_unavailable"
    assert response.json()["retryable"] is True


@pytest.mark.django_db
def test_new_attempt_atomically_replaces_prior_scenario_receipt(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="receipt-rerun-key"
    )
    first = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    provision = client.post(
        f"{BASE}/{first.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Receipt rerun",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "same-scenario",
                    "name": "Customer",
                    "situation": "Needs help",
                    "outcome": "Receives help",
                }
            ],
        },
        format="json",
        **_headers(first),
    )
    assert provision.status_code == 200
    provisioned = provision.json()["result"]
    scenario = provisioned["scenarios"][0]
    begin = client.post(
        f"{BASE}/{first.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": ["same-scenario"],
        },
        format="json",
        **_headers(first),
    )
    assert begin.status_code == 200

    def receipt(capability, *, scenario_attempt):
        body = {
            "schema_version": "futureagi.harness-result.v1",
            "job_id": str(job.id),
            "attempt_id": str(capability.attempt.id),
            "attempt_number": capability.attempt.attempt_number,
            "scenario_key": "same-scenario",
            "scenario_id": scenario["scenario_id"],
            "scenario_attempt": scenario_attempt,
            "world_index": None,
            "status": "skipped",
            "sub_goals": [],
            "evaluations": [],
            "call": None,
            "failure": None,
        }
        body["digest"] = canonical_digest(body)
        return body

    original, created = ingest_result_receipt(
        first.attempt, receipt(first, scenario_attempt=1)
    )
    assert created is True
    second = register_attempt(job.id, endpoint_base_url="https://platform.example")

    replacement, created = ingest_result_receipt(
        second.attempt, receipt(second, scenario_attempt=2)
    )

    assert created is True
    assert replacement.id == original.id
    assert replacement.attempt_id == second.attempt.id
    assert replacement.attempt_number == 2
    assert HostedHarnessReceipt.no_workspace_objects.filter(job=job).count() == 1


@pytest.mark.django_db
def test_idempotent_receipt_repairs_late_voice_modality(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="late-modality-repair-key"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Late voice modality",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "late-voice",
                    "name": "Caller",
                    "situation": "Needs help",
                    "outcome": "Receives help",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    provisioned = provision.json()["result"]
    scenario = provisioned["scenarios"][0]
    client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": ["late-voice"],
        },
        format="json",
        **_headers(capability),
    )
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "late-voice",
        "scenario_id": scenario["scenario_id"],
        "scenario_attempt": 1,
        "world_index": None,
        "status": "skipped",
        "sub_goals": [],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)
    _, created = ingest_result_receipt(capability.attempt, receipt)
    assert created is True

    job.stage_outputs = [{"kind": "contract", "data": {"modality": "voice"}}]
    job.save(update_fields=["stage_outputs", "updated_at"])
    _, created = ingest_result_receipt(capability.attempt, receipt)

    assert created is False
    call = CallExecution.objects.get(
        hosted_registration__job=job,
        hosted_registration__scenario_key="late-voice",
    )
    assert call.simulation_call_type == CallExecution.SimulationCallType.VOICE


@pytest.mark.django_db
def test_event_channel_auth_digest_watermark_and_rejection(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="event-key")
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    payload = {"from": None, "to": "running"}
    event = {
        "event_id": "event-1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "sequence": 1,
        "emitted_at": "2026-08-25T10:14:03.412Z",
        "stage": "running",
        "type": "stage_changed",
        "payload": payload,
        "digest": canonical_digest(payload),
    }
    response = client.post(
        f"{BASE}/{capability.attempt.id}/events/",
        {"schema_version": "futureagi.harness-event.v1", "events": [event]},
        format="json",
        **_headers(capability),
    )

    assert response.status_code == 200
    assert response.json() == {"acked_through_sequence": 1, "rejected": []}

    invalid = dict(event)
    invalid.update(
        event_id="event-2",
        sequence=2,
        type="made_up",
        payload={},
        digest=canonical_digest({}),
    )
    response = client.post(
        f"{BASE}/{capability.attempt.id}/events/",
        {"schema_version": "futureagi.harness-event.v1", "events": [invalid]},
        format="json",
        **_headers(capability),
    )
    assert response.status_code == 200
    assert response.json()["acked_through_sequence"] == 2
    assert response.json()["rejected"][0]["code"] == "event_type_unknown"


@pytest.mark.django_db
def test_scenario_started_event_marks_preallocated_call_ongoing(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="scenario-started-key"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Lifecycle projection",
            "modality": "voice",
            "personas": [
                {
                    "scenario_key": "lifecycle-case",
                    "name": "Caller",
                    "situation": "Needs help",
                    "outcome": "Receives help",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    provisioned = provision.json()["result"]
    client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": ["lifecycle-case"],
        },
        format="json",
        **_headers(capability),
    )
    call = CallExecution.objects.get(
        hosted_registration__job=job,
        hosted_registration__scenario_key="lifecycle-case",
    )
    assert call.status == CallExecution.CallStatus.PENDING

    payload = {
        "scenario_key": "lifecycle-case",
        "world_index": 0,
        "scenario_attempt": 1,
    }
    event = {
        "event_id": "scenario-started-1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "sequence": 1,
        "emitted_at": "2026-08-28T10:14:03.412Z",
        "stage": "running",
        "type": "scenario_started",
        "payload": payload,
        "digest": canonical_digest(payload),
    }
    response = client.post(
        f"{BASE}/{capability.attempt.id}/events/",
        {"schema_version": "futureagi.harness-event.v1", "events": [event]},
        format="json",
        **_headers(capability),
    )

    assert response.status_code == 200
    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.ONGOING


@pytest.mark.django_db
def test_event_gap_is_released_and_recorded_after_sixty_seconds(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="event-gap-key"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    payload = {"level": "info", "message": "after gap"}
    event = {
        "event_id": "event-gap-3",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "sequence": 3,
        "emitted_at": datetime.now(UTC).isoformat(),
        "stage": "running",
        "type": "log",
        "payload": payload,
        "digest": canonical_digest(payload),
    }
    response = client.post(
        f"{BASE}/{capability.attempt.id}/events/",
        {"schema_version": "futureagi.harness-event.v1", "events": [event]},
        format="json",
        **_headers(capability),
    )
    assert response.json()["acked_through_sequence"] == 0
    capability.attempt.gap_started_at = django_timezone.now() - timedelta(seconds=61)
    capability.attempt.save(update_fields=["gap_started_at"])

    response = client.post(
        f"{BASE}/{capability.attempt.id}/events/",
        {"schema_version": "futureagi.harness-event.v1", "events": [event]},
        format="json",
        **_headers(capability),
    )
    assert response.json()["acked_through_sequence"] == 3
    capability.attempt.refresh_from_db()
    assert capability.attempt.released_event_gaps[0]["from"] == 1
    assert capability.attempt.released_event_gaps[0]["through"] == 2


@pytest.mark.django_db
def test_superseded_attempt_cannot_emit(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="fence-key")
    first = register_attempt(job.id, endpoint_base_url="https://platform.example")
    register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    response = client.post(
        f"{BASE}/{first.attempt.id}/events/",
        {"schema_version": "futureagi.harness-event.v1", "events": []},
        format="json",
        **_headers(first),
    )
    assert response.status_code == 409
    assert response.json()["error"] == "attempt_superseded"


@pytest.mark.django_db
def test_artifact_upload_is_content_addressed_and_manifest_is_acked(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="artifact-key")
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    required_entries, storage = _upload_required_artifacts(
        client, capability, _headers(capability)
    )
    assert storage.put_object.call_count == 3

    terminal_payload = {
        "stage": "completed",
        "reason": None,
        "failure": None,
        "scenario_counts": {"passed": 0, "failed": 0, "errored": 0, "skipped": 1},
    }
    terminal = {
        "event_id": "terminal-1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "sequence": 1,
        "emitted_at": datetime.now(UTC).isoformat(),
        "stage": "completed",
        "type": "terminal",
        "payload": terminal_payload,
        "digest": canonical_digest(terminal_payload),
    }
    response = client.post(
        f"{BASE}/{capability.attempt.id}/events/",
        {"schema_version": "futureagi.harness-event.v1", "events": [terminal]},
        format="json",
        **_headers(capability),
    )
    assert response.status_code == 200

    manifest = {
        "schema_version": "futureagi.harness-manifest.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "entries": required_entries,
        "complete": True,
    }
    manifest["digest"] = canonical_digest(manifest)
    response = client.post(
        f"{BASE}/{capability.attempt.id}/artifacts/manifest/",
        manifest,
        format="json",
        **_headers(capability),
    )
    assert response.status_code == 200
    capability.attempt.refresh_from_db()
    assert capability.attempt.manifest_acked is True
    job.refresh_from_db()
    assert job.state == HostedHarnessJob.State.CLEANING_UP


@pytest.mark.django_db
def test_public_job_api_persists_before_scheduling(user):
    client = APIClient()
    client.force_authenticate(user=user)
    payload = _payload()
    with patch(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        return_value="hosted-harness-job",
    ):
        response = client.post(
            "/simulate/api/harness-jobs/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="api-key",
        )
    assert response.status_code == 202
    job = HostedHarnessJob.no_workspace_objects.get(id=response.json()["job"]["job_id"])
    assert job.organization_id == user.organization_id
    assert job.scenario_count == 1


@pytest.mark.django_db
def test_job_api_rejects_voice_parallelism_above_cpu(user):
    client = APIClient()
    client.force_authenticate(user=user)
    payload = _payload()
    payload["runtime"]["parallelism"] = 3
    payload["runtime"]["cpu_units"] = 2
    response = client.post(
        "/simulate/api/harness-jobs/",
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY="parallel-key",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_scenario_receipt_manifest_and_cleanup_finalize_platform_rows(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="finalizer-key"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    headers = _headers(capability)
    provision = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Hosted finalizer",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "account-locked",
                    "name": "Customer",
                    "situation": "Account is locked",
                    "outcome": "Agent refuses unsafe access",
                }
            ],
        },
        format="json",
        **headers,
    )
    assert provision.status_code == 200
    provisioned = provision.json()["result"]
    scenario = provisioned["scenarios"][0]

    begin = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": ["account-locked"],
        },
        format="json",
        **headers,
    )
    assert begin.status_code == 200

    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "account-locked",
        "scenario_id": scenario["scenario_id"],
        "scenario_attempt": 1,
        "world_index": None,
        "status": "skipped",
        "sub_goals": [],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)
    response = client.post(
        f"{BASE}/{capability.attempt.id}/results/",
        receipt,
        format="json",
        **headers,
    )
    assert response.status_code == 200

    terminal_payload = {
        "stage": "completed",
        "reason": None,
        "failure": None,
        "scenario_counts": {
            "passed": 0,
            "failed": 0,
            "errored": 0,
            "skipped": 1,
        },
    }
    terminal = {
        "event_id": "terminal-finalizer",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "sequence": 1,
        "emitted_at": datetime.now(UTC).isoformat(),
        "stage": "completed",
        "type": "terminal",
        "payload": terminal_payload,
        "digest": canonical_digest(terminal_payload),
    }
    response = client.post(
        f"{BASE}/{capability.attempt.id}/events/",
        {"schema_version": "futureagi.harness-event.v1", "events": [terminal]},
        format="json",
        **headers,
    )
    assert response.status_code == 200
    required_entries, _ = _upload_required_artifacts(client, capability, headers)

    manifest = {
        "schema_version": "futureagi.harness-manifest.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "entries": required_entries,
        "complete": True,
    }
    manifest["digest"] = canonical_digest(manifest)
    response = client.post(
        f"{BASE}/{capability.attempt.id}/artifacts/manifest/",
        manifest,
        format="json",
        **headers,
    )
    assert response.status_code == 200

    finalized = record_cleanup(
        capability.attempt.id,
        provider_ref="sandbox-finalizer",
        verified_absent=True,
    )
    finalized.refresh_from_db()
    assert finalized.state == HostedHarnessJob.State.COMPLETED
    assert finalized.current_stage == "completed"
    finalized.test_execution.refresh_from_db()
    assert finalized.test_execution.status == "completed"
