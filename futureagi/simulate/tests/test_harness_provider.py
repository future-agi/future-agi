from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.response import Response
from rest_framework.test import APIClient

from simulate.models import RunTest
from simulate.serializers.harness_job import HarnessJobCreateSerializer
from simulate.services.harness_provider import (
    DaytonaHarnessProvider,
    SandboxHarnessProvider,
    _validate_known_daytona_egress,
    get_harness_provider,
)
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job


def _v1_payload(**overrides):
    payload = {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": "acme/agent",
            "ref": "main",
            "visibility": "public",
        },
        "agent": {"connector": "auto", "config": {}, "secret_refs": {}},
        "scenario_count": 10,
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
            "max_infrastructure_attempts": 2,
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
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def test_default_provider_is_daytona():
    assert isinstance(get_harness_provider(), DaytonaHarnessProvider)


def test_hosted_job_scenario_count_is_bounded_at_two_hundred():
    accepted = HarnessJobCreateSerializer(data=_v1_payload(scenario_count=200))
    assert accepted.is_valid(), accepted.errors

    rejected = HarnessJobCreateSerializer(data=_v1_payload(scenario_count=201))
    assert not rejected.is_valid()
    assert "scenario_count" in rejected.errors


def test_customer_cannot_submit_platform_simulator_secret_purpose():
    payload = _v1_payload()
    payload["agent"]["secret_refs"] = {
        "DEEPGRAM_API_KEY": {
            "manager": "platform-vault",
            "key": "DEEPGRAM_API_KEY",
            "purpose": "simulator_provider",
        }
    }

    serializer = HarnessJobCreateSerializer(data=payload)

    assert not serializer.is_valid()
    assert "target_provider" in str(serializer.errors)


def test_customer_cannot_submit_platform_config_secret_manager():
    payload = _v1_payload()
    payload["agent"]["secret_refs"] = {
        "DEEPGRAM_API_KEY": {
            "manager": "platform-config",
            "key": "DEEPGRAM_API_KEY",
            "purpose": "target_provider",
        }
    }

    serializer = HarnessJobCreateSerializer(data=payload)

    assert not serializer.is_valid()
    assert "platform-vault" in str(serializer.errors)


def test_customer_cannot_use_reserved_simulator_alias_for_agent_secret():
    payload = _v1_payload()
    payload["agent"]["secret_refs"] = {
        "SIMULATOR_DEEPGRAM_API_KEY": {
            "manager": "platform-vault",
            "key": "customer-secret",
            "purpose": "target_provider",
        }
    }

    serializer = HarnessJobCreateSerializer(data=payload)

    assert not serializer.is_valid()
    assert "reserved" in str(serializer.errors)


def test_known_daytona_egress_rejects_overflow_without_vault_resolution(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = [
        f"base-{index}.example.com" for index in range(19)
    ]
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}

    with pytest.raises(HostedHarnessError, match="Daytona supports at most 20"):
        _validate_known_daytona_egress(
            _v1_payload(), "https://harness.example.test/"
        )


def test_daytona_preflight_rejects_known_egress_overflow(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = [
        f"base-{index}.example.com" for index in range(19)
    ]
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}
    request = SimpleNamespace(
        validated_data=_v1_payload(),
        build_absolute_uri=lambda _path: "https://harness.example.test/",
    )

    response = DaytonaHarnessProvider().preflight(request)

    assert response.status_code == 400
    assert response.data["error"] == "egress_domain_limit_exceeded"


@pytest.mark.django_db
def test_daytona_create_rejects_known_egress_overflow_before_persisting(
    user, workspace, settings
):
    settings.HARNESS_PROVIDER = "daytona"
    settings.HARNESS_PUBLIC_BASE_URL = "https://harness.example.test"
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = [
        f"base-{index}.example.com" for index in range(19)
    ]
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}
    client = APIClient()
    client.force_authenticate(user=user)

    with (
        patch("simulate.services.hosted_harness.create_hosted_job") as create,
        patch(
            "simulate.temporal.client.start_hosted_harness_gateway_workflow"
        ) as start,
    ):
        response = client.post(
            "/simulate/api/harness-jobs/",
            _v1_payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY="known-egress-overflow",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

    assert response.status_code == 400
    assert response.json()["error"] == "egress_domain_limit_exceeded"
    create.assert_not_called()
    start.assert_not_called()


def test_harness_create_cors_preflight_allows_idempotency_key():
    response = APIClient().options(
        "/simulate/api/harness-jobs/",
        HTTP_ORIGIN="http://localhost:3000",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS=(
            "authorization,content-type,idempotency-key,x-workspace-id"
        ),
    )

    assert response.status_code == 200
    allowed = {
        header.strip().lower()
        for header in response["Access-Control-Allow-Headers"].split(",")
    }
    assert "idempotency-key" in allowed


@override_settings(HARNESS_PROVIDER="sandbox")
def test_sandbox_provider_selected_by_setting():
    assert isinstance(get_harness_provider(), SandboxHarnessProvider)


def test_sandbox_flatten_maps_github_source():
    flat = SandboxHarnessProvider._flatten_source(
        {
            "source": {
                "kind": "github",
                "repository": "acme/agent",
                "ref": "main",
                "commit_sha": "a" * 40,
                "visibility": "public",
            },
            "agent": {
                "connector": "livekit",
                "config": {"room": "r1"},
                "secret_refs": {},
            },
            "scenario_count": 7,
            "seed": 42,
            "metadata": {"k": "v"},
        }
    )
    assert flat["github_repository"] == "acme/agent"
    assert flat["github_ref"] == "main"
    assert flat["github_commit_sha"] == "a" * 40
    assert flat["github_visibility"] == "public"
    assert flat["connector"] == "livekit"
    assert flat["connector_config"] == {"room": "r1"}
    assert flat["scenario_count"] == 7
    assert flat["seed"] == 42
    assert "source_path" not in flat and "source_id" not in flat


def test_sandbox_flatten_maps_archive_source():
    flat = SandboxHarnessProvider._flatten_source(
        {
            "source": {
                "kind": "archive",
                "archive_artifact_id": "63ef3598-a84d-4ce0-a7a1-53c4e27f69f7",
            },
            "agent": {"connector": "auto"},
        }
    )
    assert flat["source_id"] == "63ef3598-a84d-4ce0-a7a1-53c4e27f69f7"
    assert "github_repository" not in flat


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="sandbox")
def test_sandbox_create_forwards_mapped_flat_payload(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {"job": {"job_id": "job-1"}, "status": {"stage": "queued"}}

    with patch(
        "simulate.services.harness_sandbox.HarnessSandboxClient.submit",
        return_value=expected,
    ) as submit:
        response = client.post(
            "/simulate/api/harness-jobs/", _v1_payload(), format="json"
        )

    assert response.status_code == 202
    assert response.json() == expected
    forwarded = submit.call_args.args[0]
    assert forwarded["github_repository"] == "acme/agent"
    assert forwarded["connector"] == "auto"
    assert "source" not in forwarded  # v1.6 nesting was flattened


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="sandbox")
def test_sandbox_source_upload_is_forwarded(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {
        "source_id": "63ef3598-a84d-4ce0-a7a1-53c4e27f69f7",
        "name": "agent",
        "file_count": 2,
        "total_bytes": 23,
    }
    files = [
        SimpleUploadedFile("agent.py", b"print('ready')\n"),
        SimpleUploadedFile("requirements.txt", b"fastapi\n"),
    ]

    with patch(
        "simulate.services.harness_sandbox.HarnessSandboxClient.upload_source",
        return_value=expected,
    ) as upload:
        response = client.post(
            "/simulate/api/harness-jobs/sources/",
            {
                "files": files,
                "paths": ["agent.py", "requirements.txt"],
                "name": "agent",
            },
            format="multipart",
        )

    assert response.status_code == 201
    assert response.json() == expected
    assert upload.call_args.args[1] == ["agent.py", "requirements.txt"]


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="daytona")
def test_daytona_create_starts_gateway_workflow(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)

    class _Job:
        id = "11111111-1111-1111-1111-111111111111"
        payload = {
            "retry": {
                "max_infrastructure_attempts": 2,
                "initial_backoff_seconds": 1,
                "max_backoff_seconds": 15,
            }
        }

    serialized = {"job": {"job_id": str(_Job.id)}, "status": {"state": "queued"}}
    with (
        patch(
            "simulate.services.hosted_harness.create_hosted_job",
            return_value=(_Job(), True),
        ) as create,
        patch(
            "simulate.temporal.client.start_hosted_harness_gateway_workflow"
        ) as start,
        patch(
            "simulate.services.harness_provider.serialize_job", return_value=serialized
        ),
    ):
        response = client.post(
            "/simulate/api/harness-jobs/",
            _v1_payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY="key-1",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

    assert response.status_code == 202
    assert response.json() == serialized
    assert create.call_args.kwargs["idempotency_key"] == "key-1"
    assert create.call_args.kwargs["workspace"] == workspace
    assert start.call_args.args[0] == str(_Job.id)


@pytest.mark.django_db
@override_settings(
    HARNESS_PROVIDER="daytona",
    HARNESS_PUBLIC_BASE_URL="https://harness.example.test",
)
def test_daytona_saved_rerun_reuses_job_and_starts_fresh_attempt_cycle(user, workspace):
    job, _ = create_hosted_job(
        user.organization,
        _v1_payload(),
        idempotency_key="daytona-rerun",
        workspace=workspace,
    )
    job.state = job.State.COMPLETED
    job.current_stage = "completed"
    job.current_attempt_number = 3
    job.completed_count = 10
    job.terminal_at = job.created_at
    job.scenario_count = 2
    payload = dict(job.payload)
    payload["scenario_count"] = 1
    job.payload = payload
    job.save(
        update_fields=[
            "payload",
            "scenario_count",
            "state",
            "current_stage",
            "current_attempt_number",
            "completed_count",
            "terminal_at",
            "updated_at",
        ]
    )

    with patch(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow"
    ) as start:
        result = DaytonaHarnessProvider().rerun_saved(
            str(job.id),
            organization=user.organization,
            workspace=workspace,
            environment_values={"LIVEKIT_URL": "wss://customer.example.test"},
        )

    job.refresh_from_db()
    assert job.state == job.State.QUEUED
    assert job.current_stage == "queued"
    assert job.completed_count == 0
    assert job.terminal_at is None
    assert job.payload["metadata"]["attempt_cycle_start"] == 4
    assert job.payload["scenario_count"] == 2
    livekit_ref = job.payload["agent"]["secret_refs"]["LIVEKIT_URL"]
    assert livekit_ref["manager"] == "platform-vault"
    assert "customer.example.test" not in json.dumps(job.payload)
    assert result["job"]["job_id"] == str(job.id)
    start.assert_called_once()


@pytest.mark.django_db
@override_settings(
    HARNESS_PROVIDER="daytona",
    HARNESS_PUBLIC_BASE_URL="https://harness.example.test",
)
def test_daytona_saved_rerun_rejects_legacy_run_without_authoring_snapshot(
    user, workspace
):
    job, _ = create_hosted_job(
        user.organization,
        _v1_payload(),
        idempotency_key="daytona-legacy-rerun",
        workspace=workspace,
    )
    job.run_test = RunTest.objects.create(
        name="Legacy hosted run",
        organization=user.organization,
        workspace=workspace,
    )
    job.state = job.State.COMPLETED
    job.current_stage = "completed"
    job.save(update_fields=["run_test", "state", "current_stage", "updated_at"])

    with pytest.raises(HostedHarnessError) as exc_info:
        DaytonaHarnessProvider().rerun_saved(
            str(job.id),
            organization=user.organization,
            workspace=workspace,
            environment_values={},
        )

    assert exc_info.value.code == "rerun_authoring_snapshot_missing"
    assert exc_info.value.status_code == 409
    job.refresh_from_db()
    assert job.state == job.State.COMPLETED


@pytest.mark.django_db
def test_secret_file_upload_returns_only_opaque_reference(user):
    # Daytona receives a normal platform-vault ref. The guest recreates the
    # credential file from the JSON alias; no host-local path crosses the seam.
    client = APIClient()
    client.force_authenticate(user=user)
    raw = b'{"type":"service_account","private_key":"must-not-be-echoed"}'
    response = client.post(
        "/simulate/api/harness-jobs/secret-files/",
        {
            "file": SimpleUploadedFile(
                "customer-google.json", raw, content_type="application/json"
            ),
            "environment_name": "GOOGLE_APPLICATION_CREDENTIALS",
        },
        format="multipart",
    )

    assert response.status_code == 201
    result = response.json()
    assert result["environment_name"] == "GOOGLE_APPLICATION_CREDENTIALS_JSON"
    assert result["size"] == len(raw)
    assert result["secret_ref"]["manager"] == "platform-vault"
    assert result["secret_ref"]["purpose"] == "target_provider"
    assert result["secret_ref"]["key"]
    assert raw.decode() not in response.content.decode()

    from simulate.models import HostedHarnessSecret

    record = HostedHarnessSecret.objects.get(name=result["secret_ref"]["key"])
    assert raw.decode() not in record.encrypted_value
    assert json.loads(record.get_value()) == json.loads(raw)


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="sandbox")
def test_secret_file_upload_keeps_local_sandbox_file_reference(user):
    client = APIClient()
    client.force_authenticate(user=user)
    raw = b'{"type":"service_account"}'

    response = client.post(
        "/simulate/api/harness-jobs/secret-files/",
        {
            "file": SimpleUploadedFile("google.json", raw),
            "environment_name": "GOOGLE_APPLICATION_CREDENTIALS",
        },
        format="multipart",
    )

    assert response.status_code == 201
    result = response.json()
    assert result["environment_name"] == "GOOGLE_APPLICATION_CREDENTIALS"
    assert result["secret_ref"]["manager"] == "harness_environment_file"


@pytest.mark.django_db
def test_secret_values_are_encrypted_and_return_only_platform_refs(user):
    client = APIClient()
    client.force_authenticate(user=user)
    raw = "must-not-be-echoed"

    response = client.post(
        "/simulate/api/harness-jobs/secret-values/",
        {"environment_values": {"DEEPGRAM_API_KEY": raw}},
        format="json",
    )

    assert response.status_code == 201
    reference = response.json()["secret_refs"]["DEEPGRAM_API_KEY"]
    assert reference["manager"] == "platform-vault"
    assert reference["purpose"] == "target_provider"
    assert raw not in response.content.decode()

    from simulate.models import HostedHarnessSecret

    record = HostedHarnessSecret.objects.get(
        organization=user.organization, name=reference["key"]
    )
    assert raw not in record.encrypted_value
    assert record.get_value() == raw


@pytest.mark.django_db
def test_secret_values_reject_runner_owned_names(user):
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/simulate/api/harness-jobs/secret-values/",
        {"environment_values": {"FI_API_KEY": "customer-value"}},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="sandbox")
def test_harness_job_adjustment_is_validated_and_forwarded(user):
    # Kept endpoint (base): a validated adjustment is forwarded to the sandbox
    # client verbatim.
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {
        "job": {"job_id": "job-1"},
        "status": {"stage": "generating_scenarios"},
        "adjustments": [{"status": "pending"}],
    }
    payload = {
        "instruction": "Add 10 more scenarios covering payment failures",
        "client_request_id": "browser-1",
    }

    with patch(
        "simulate.services.harness_sandbox.HarnessSandboxClient.adjust",
        return_value=expected,
    ) as adjust:
        response = client.post(
            "/simulate/api/harness-jobs/job-1/adjust/", payload, format="json"
        )

    assert response.status_code == 200
    assert response.json() == expected
    adjust.assert_called_once_with("job-1", payload)


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="daytona")
def test_harness_job_adjustment_routes_to_daytona_provider(user):
    client = APIClient()
    client.force_authenticate(user=user)
    job_id = "11111111-1111-1111-1111-111111111111"
    payload = {
        "instruction": "Create 1 more scenario covering discounts",
        "client_request_id": "browser-1",
    }
    expected = {"adjustments": [{"status": "pending"}]}

    with patch.object(
        DaytonaHarnessProvider, "adjust", return_value=Response(expected)
    ) as adjust:
        response = client.post(
            f"/simulate/api/harness-jobs/{job_id}/adjust/", payload, format="json"
        )

    assert response.status_code == 200
    assert response.json() == expected
    assert adjust.call_args.args[1] == job_id


@pytest.mark.django_db
def test_harness_job_adjustment_rejects_empty_instruction(user):
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/simulate/api/harness-jobs/job-1/adjust/",
        {"instruction": "   "},
        format="json",
    )

    assert response.status_code == 400
