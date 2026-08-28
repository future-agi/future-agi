"""Tests for the consolidated harness read DTO and P0 fixes.

Covers:
  - serialize_job returns the complete shared DTO shape
  - terminal failure overlay onto job
  - bundle provenance mismatch / missing rejection
  - egress domain cap (20/21)
  - scenario cardinality enforcement
  - secret_ref validation at admission
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from simulate.models import (
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessStageOutput,
)
from simulate.services.harness_provider import serialize_job
from simulate.services.hosted_harness import (
    HostedHarnessError,
    _resolve_scenario_modality,
    create_hosted_job,
    provision_scenarios,
    record_cleanup,
    register_attempt,
)
from simulate.services.hosted_harness_gateway import (
    _SCENARIO_DIRECTORY_COUNT_COMMAND,
    _bundle_archive_for,
    _hosted_scenario_repair_script,
    _validate_egress_domains,
    authoring_stage_outputs_from_archive,
)


def _v1_payload(**overrides):
    payload = {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": "future-agi/ride-voice-agent",
            "ref": "main",
            "commit_sha": "a" * 40,
            "visibility": "public",
        },
        "agent": {"connector": "livekit", "config": {}, "secret_refs": {}},
        "scenario_count": 2,
        "seed": 42,
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 4,
            "memory_mb": 8192,
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
            "allowed_egress_domains": ["api.example.com"],
        },
        "retry": {
            "max_infrastructure_attempts": 1,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 15,
            "retryable_domains": ["infrastructure"],
        },
        "artifacts": {
            "level": "full",
            "retention_days": 30,
            "allow_bundle_download": False,
            "max_artifact_bytes": 1024,
        },
        "metadata": {"voice_case": "2.1.2"},
    }
    payload.update(overrides)
    return payload


# ── Read DTO shape ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_serialize_job_returns_full_dto_shape(organization):
    job, _ = create_hosted_job(organization, _v1_payload(), idempotency_key="dto-shape")
    result = serialize_job(job)
    # Top-level keys
    assert set(result.keys()) == {
        "job",
        "status",
        "events",
        "stage_outputs",
        "scenarios",
        "receipts",
        "platform",
    }
    # Job sub-keys
    assert "job_id" in result["job"]
    assert "run_id" in result["job"]
    assert "run_test_id" in result["job"]
    assert "test_execution_id" in result["job"]
    assert "source" in result["job"]
    assert "metadata" in result["job"]
    # Status sub-keys
    assert "state" in result["status"]
    assert "stage" in result["status"]
    assert "failure" in result["status"]
    assert "deadline_at" in result["status"]
    # Empty before any work
    assert result["stage_outputs"] == []
    assert result["scenarios"] == []
    assert result["receipts"] == []
    assert result["events"] == []
    assert result["platform"] == {
        "run_test_id": None,
        "test_execution_id": None,
        "url": None,
    }


@pytest.mark.django_db
def test_serialize_job_includes_stage_outputs(organization):
    job, _ = create_hosted_job(
        organization, _v1_payload(), idempotency_key="dto-stages"
    )
    HostedHarnessStageOutput.no_workspace_objects.create(
        job=job,
        title="Contract",
        summary="ride-voice agent",
        kind="contract",
        data={"agent": "ride-voice"},
    )
    result = serialize_job(job)
    assert len(result["stage_outputs"]) == 1
    assert result["stage_outputs"][0]["kind"] == "contract"
    assert result["stage_outputs"][0]["data"]["agent"] == "ride-voice"


@pytest.mark.django_db
def test_serialize_job_includes_run_ids(organization):
    job, _ = create_hosted_job(
        organization, _v1_payload(), idempotency_key="dto-runids"
    )
    result = serialize_job(job)
    assert result["job"]["run_test_id"] is None
    assert result["job"]["test_execution_id"] is None
    assert result["job"]["run_id"] is not None


def test_authoring_archive_exposes_full_documents_without_secret_values():
    body = io.BytesIO()
    documents = {
        "contract.json": {
            "one_liner": "Books rides",
            "modality": "voice",
            "tools": [{"name": "book_ride", "description": "Create a booking"}],
        },
        "environment.json": {
            "services": ["agent", "tools-api"],
            "commands": {"agent": "python -m agent"},
            "LIVEKIT_API_SECRET": "must-not-leak",
            "secret_purposes": ["target_provider"],
        },
        "scenarios.json": [
            {
                "name": "airport",
                "instruction": "Book an airport ride",
                "use_case": "booking",
                "api_key": "must-not-leak",
            }
        ],
    }
    with tarfile.open(fileobj=body, mode="w:gz") as archive:
        for name, document in documents.items():
            data = json.dumps(document).encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    outputs = authoring_stage_outputs_from_archive(body.getvalue())

    assert [item["kind"] for item in outputs] == [
        "contract",
        "environment",
        "scenarios",
    ]
    assert outputs[0]["data"]["tools"][0]["description"] == "Create a booking"
    assert outputs[1]["data"]["commands"]["agent"] == "python -m agent"
    assert outputs[1]["data"]["LIVEKIT_API_SECRET"] == "[redacted]"
    assert outputs[1]["data"]["secret_purposes"] == ["target_provider"]
    assert outputs[2]["data"][0]["api_key"] == "[redacted]"


def test_authoring_archive_reads_per_scenario_files_and_respects_run_count():
    body = io.BytesIO()
    documents = {
        "contract.json": {"modality": "voice", "tools": []},
        "scenarios/zeta/scenario.json": {
            "name": "Zeta",
            "instruction": "Second alphabetically",
        },
        "scenarios/alpha/scenario.json": {
            "name": "Alpha",
            "instruction": "First alphabetically",
        },
    }
    with tarfile.open(fileobj=body, mode="w:gz") as archive:
        for name, document in documents.items():
            data = json.dumps(document).encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    outputs = authoring_stage_outputs_from_archive(body.getvalue(), scenario_limit=1)

    assert [item["kind"] for item in outputs] == ["contract", "scenarios"]
    assert outputs[1]["summary"] == "1 grounded scenarios"
    assert outputs[1]["data"] == [
        {
            "name": "Alpha",
            "instruction": "First alphabetically",
            "scenario_key": "alpha",
        }
    ]


def test_hosted_scenario_repair_script_requests_exact_missing_count_safely():
    script = _hosted_scenario_repair_script(
        name="hotel'); raise RuntimeError('unsafe",
        expected=3,
        actual=2,
    )

    compile(script, "repair-scenarios.py", "exec")
    assert "Add exactly 1 distinct validated scenario" in script
    assert "count=3" in script
    assert "name=\"hotel'); raise RuntimeError('unsafe\"" in script
    assert "-type d" in _SCENARIO_DIRECTORY_COUNT_COMMAND
    assert "ls " not in _SCENARIO_DIRECTORY_COUNT_COMMAND


# ── Terminal failure overlay ────────────────────────────────────────────


@pytest.mark.django_db
def test_terminal_failure_copied_to_job(organization):
    job, _ = create_hosted_job(
        organization, _v1_payload(), idempotency_key="terminal-overlay"
    )
    cap = register_attempt(job.id, endpoint_base_url="https://platform.example.com")
    attempt = cap.attempt
    attempt.provider_ref = "sandbox-1"
    attempt.terminal_stage = "failed"
    attempt.terminal_failure = {
        "domain": "infrastructure",
        "stage": "running",
        "code": "guest_crashed",
        "message": "entrypoint exited 1",
    }
    attempt.state = HostedHarnessAttempt.State.FAILED
    attempt.save()

    returned_job = record_cleanup(
        attempt.id,
        provider_ref="sandbox-1",
        verified_absent=True,
        details={"provider": "test"},
    )
    assert returned_job.state == HostedHarnessJob.State.FAILED
    assert returned_job.failure is not None
    assert returned_job.failure["code"] == "guest_crashed"
    assert returned_job.current_stage == "failed"

    # The read DTO reflects the failure.
    dto = serialize_job(returned_job)
    assert dto["status"]["failure"]["code"] == "guest_crashed"
    assert dto["status"]["stage"] == "failed"


@pytest.mark.django_db
def test_terminal_completed_clears_failure(organization):
    job, _ = create_hosted_job(
        organization, _v1_payload(), idempotency_key="terminal-ok"
    )
    cap = register_attempt(job.id, endpoint_base_url="https://platform.example.com")
    attempt = cap.attempt
    attempt.provider_ref = "sandbox-2"
    attempt.terminal_stage = "completed"
    attempt.terminal_event_received = True
    attempt.manifest_acked = True
    attempt.state = HostedHarnessAttempt.State.COMPLETED
    attempt.save()

    returned_job = record_cleanup(
        attempt.id,
        provider_ref="sandbox-2",
        verified_absent=True,
    )
    assert returned_job.state == HostedHarnessJob.State.COMPLETED
    assert returned_job.failure is None


# ── Egress domain validation ───────────────────────────────────────────


def test_egress_20_domains_accepted():
    _validate_egress_domains([f"d{i}.example.com" for i in range(20)])


def test_egress_21_domains_rejected():
    with pytest.raises(HostedHarnessError) as exc:
        _validate_egress_domains([f"d{i}.example.com" for i in range(21)])
    assert exc.value.code == "egress_domain_limit_exceeded"


def test_egress_private_host_rejected():
    with pytest.raises(HostedHarnessError) as exc:
        _validate_egress_domains(["192.168.1.1"])
    assert exc.value.code == "egress_domain_private"


def test_egress_localhost_rejected():
    with pytest.raises(HostedHarnessError) as exc:
        _validate_egress_domains(["localhost"])
    assert exc.value.code == "egress_domain_private"


def test_egress_loopback_rejected():
    with pytest.raises(HostedHarnessError) as exc:
        _validate_egress_domains(["127.0.0.1"])
    assert exc.value.code == "egress_domain_private"


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="daytona")
def test_create_rejects_21_egress_domains(user):
    client = APIClient()
    client.force_authenticate(user=user)
    payload = _v1_payload()
    payload["security"]["allowed_egress_domains"] = [
        f"d{i}.example.com" for i in range(21)
    ]

    with (
        patch("simulate.services.hosted_harness.create_hosted_job"),
        patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"),
    ):
        response = client.post(
            "/simulate/api/harness-jobs/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="egress-21",
        )

    assert response.status_code == 400
    assert response.json()["error"] == "egress_domain_limit_exceeded"


# ── Bundle verification ────────────────────────────────────────────────


@pytest.mark.django_db
def test_bundle_provenance_mismatch_rejected(organization):
    job, _ = create_hosted_job(
        organization, _v1_payload(), idempotency_key="bundle-mismatch"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_dir = Path(tmpdir) / "future-agi__ride-voice-agent"
        bundle_dir.mkdir()
        manifest = {
            "schema_version": "futureagi.environment-bundle.v2",
            "digest": "sha256:" + "0" * 64,
            "provenance": {
                "repository": "wrong-org/wrong-repo",
                "commit": "a" * 40,
            },
            "files": [],
        }
        (bundle_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        with override_settings(ALK_HOSTED_BUNDLE_DIR=tmpdir):
            with pytest.raises(HostedHarnessError) as exc:
                _bundle_archive_for(job)
            assert exc.value.code == "bundle_provenance_repository_mismatch"


@pytest.mark.django_db
def test_bundle_file_hash_mismatch_rejected(organization):
    job, _ = create_hosted_job(
        organization, _v1_payload(), idempotency_key="bundle-hash-bad"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_dir = Path(tmpdir) / "future-agi__ride-voice-agent"
        bundle_dir.mkdir()
        (bundle_dir / "contract.json").write_text('{"agent":"test"}', encoding="utf-8")
        manifest = {
            "schema_version": "futureagi.environment-bundle.v2",
            "digest": "sha256:" + "0" * 64,
            "provenance": {
                "repository": "future-agi/ride-voice-agent",
            },
            "files": [
                {"path": "contract.json", "sha256": "0" * 64, "size": 16},
            ],
        }
        (bundle_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        with override_settings(ALK_HOSTED_BUNDLE_DIR=tmpdir):
            with pytest.raises(HostedHarnessError) as exc:
                _bundle_archive_for(job)
            assert exc.value.code == "bundle_file_hash_mismatch"


@pytest.mark.django_db
def test_bundle_missing_returns_none(organization):
    job, _ = create_hosted_job(
        organization, _v1_payload(), idempotency_key="bundle-missing"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        with override_settings(ALK_HOSTED_BUNDLE_DIR=tmpdir):
            archive, manifest = _bundle_archive_for(job)
    assert archive is None
    assert manifest is None


@pytest.mark.django_db
def test_bundle_verified_returns_archive_and_manifest(organization):
    job, _ = create_hosted_job(organization, _v1_payload(), idempotency_key="bundle-ok")
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_dir = Path(tmpdir) / "future-agi__ride-voice-agent"
        bundle_dir.mkdir()
        content = b'{"agent":"test"}'
        content_hash = hashlib.sha256(content).hexdigest()
        (bundle_dir / "contract.json").write_bytes(content)
        manifest = {
            "schema_version": "futureagi.environment-bundle.v2",
            "digest": "sha256:" + "0" * 64,
            "provenance": {
                "repository": "future-agi/ride-voice-agent",
                "commit": "a" * 40,
            },
            "files": [
                {"path": "contract.json", "sha256": content_hash, "size": len(content)},
            ],
        }
        (bundle_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        with override_settings(ALK_HOSTED_BUNDLE_DIR=tmpdir):
            archive, loaded = _bundle_archive_for(job)
    assert archive is not None
    assert loaded is not None
    assert loaded["digest"] == "sha256:" + "0" * 64


# ── Scenario cardinality ───────────────────────────────────────────────


def test_scenario_modality_uses_authored_contract_for_legacy_guest_payload():
    job = SimpleNamespace(
        stage_outputs=[
            {"kind": "contract", "data": {"modality": "voice"}},
        ]
    )

    assert _resolve_scenario_modality(job, {"operation": "provision"}) == "voice"
    assert _resolve_scenario_modality(job, {"modality": "text"}) == "text"


@pytest.mark.django_db
def test_provision_rejects_wrong_count(organization):
    job, _ = create_hosted_job(
        organization, _v1_payload(scenario_count=2), idempotency_key="card-bad"
    )
    cap = register_attempt(job.id, endpoint_base_url="https://p.example.com")
    with pytest.raises(HostedHarnessError) as exc:
        provision_scenarios(
            cap.attempt,
            {
                "operation": "provision",
                "name": "test",
                "personas": [
                    {"scenario_key": "a", "name": "A"},
                ],
            },
        )
    assert exc.value.code == "scenario_count_mismatch"


@pytest.mark.django_db
def test_provision_rejects_duplicate_keys(organization):
    job, _ = create_hosted_job(
        organization, _v1_payload(scenario_count=2), idempotency_key="card-dup"
    )
    cap = register_attempt(job.id, endpoint_base_url="https://p.example.com")
    with pytest.raises(HostedHarnessError) as exc:
        provision_scenarios(
            cap.attempt,
            {
                "operation": "provision",
                "name": "test",
                "personas": [
                    {"scenario_key": "a", "name": "A"},
                    {"scenario_key": "a", "name": "B"},
                ],
            },
        )
    assert exc.value.code == "scenario_key_duplicate"


# ── Secret ref validation ──────────────────────────────────────────────


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="daytona")
def test_create_rejects_non_platform_vault_secret_ref(user):
    """Even if the serializer allowed it, admission rejects non-platform-vault."""
    client = APIClient()
    client.force_authenticate(user=user)
    payload = _v1_payload()
    payload["agent"]["secret_refs"] = {
        "GOOGLE_CREDS": {
            "manager": "platform-vault",
            "key": "gcp-sa",
            "purpose": "target_provider",
        }
    }

    # Valid platform-vault ref should not hit the secret_manager_unsupported error
    with (
        patch(
            "simulate.services.hosted_harness.create_hosted_job",
            return_value=(
                SimpleNamespace(
                    id="11111111-1111-1111-1111-111111111111",
                    payload={
                        "retry": {
                            "max_infrastructure_attempts": 1,
                            "initial_backoff_seconds": 1,
                            "max_backoff_seconds": 15,
                        }
                    },
                ),
                True,
            ),
        ),
        patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"),
        patch(
            "simulate.services.harness_provider.serialize_job",
            return_value={"job": {}, "status": {}},
        ),
    ):
        response = client.post(
            "/simulate/api/harness-jobs/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="secret-ok",
        )
    assert response.status_code == 202
