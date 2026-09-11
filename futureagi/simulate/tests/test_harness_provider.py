from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.response import Response
from rest_framework.test import APIClient

from simulate.models import RunTest, TestExecution
from simulate.serializers.harness_job import (
    HarnessJobCreateSerializer,
    HarnessPreflightSerializer,
)
from simulate.services.harness_provider import (
    DaytonaHarnessProvider,
    SandboxHarnessProvider,
    _validate_known_daytona_egress,
    _validate_required_credential_files,
    get_harness_provider,
)
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job

_LIVEKIT_REFS = {
    "LIVEKIT_URL": {
        "key": "harness-livekit_url",
        "manager": "platform-vault",
        "purpose": "target_provider",
        "version": "1",
    },
    "LIVEKIT_API_KEY": {
        "key": "harness-livekit_api_key",
        "manager": "platform-vault",
        "purpose": "target_provider",
        "version": "1",
    },
    "LIVEKIT_API_SECRET": {
        "key": "harness-livekit_api_secret",
        "manager": "platform-vault",
        "purpose": "target_provider",
        "version": "1",
    },
}


def _v1_payload(**overrides):
    payload = {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": "acme/agent",
            "ref": "main",
            "visibility": "public",
        },
        "agent": {
            "connector": "auto",
            "config": {},
            "secret_refs": _LIVEKIT_REFS,
        },
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
    assert accepted.validated_data["runtime"]["max_duration_seconds"] == 72_000

    rejected = HarnessJobCreateSerializer(data=_v1_payload(scenario_count=201))
    assert not rejected.is_valid()
    assert "scenario_count" in rejected.errors


def test_large_hosted_job_gets_a_per_scenario_runtime_budget():
    serializer = HarnessJobCreateSerializer(data=_v1_payload(scenario_count=50))

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["runtime"]["max_duration_seconds"] == 18_000


def test_small_hosted_job_preserves_the_requested_runtime_budget():
    serializer = HarnessJobCreateSerializer(data=_v1_payload(scenario_count=10))

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["runtime"]["max_duration_seconds"] == 600


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
        _validate_known_daytona_egress(_v1_payload(), "https://harness.example.test/")


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


def test_daytona_preflight_requires_only_the_provider_the_source_uses(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}
    payload = _v1_payload()
    payload["agent"] = {"connector": "auto", "config": {}, "secret_refs": {}}
    request = SimpleNamespace(
        validated_data=payload,
        build_absolute_uri=lambda _path: "https://harness.example.test/",
    )

    def _status_by_name(response):
        return {
            item["environment_name"]: item["status"]
            for item in response.data["credentials"]["requirements"]
        }

    with patch(
        "simulate.services.harness_provider._preflight_source_connectors",
        return_value=(["livekit"], 12),
    ):
        response = DaytonaHarnessProvider().preflight(request)

    assert response.status_code == 200
    assert response.data["ready_to_submit"] is False
    assert response.data["credentials"]["detected_connectors"] == ["livekit"]
    assert response.data["credentials"]["scanned_files"] == 12
    statuses = _status_by_name(response)
    assert statuses["LIVEKIT_URL"] == "missing"
    assert statuses["VAPI_API_KEY"] == "optional"
    assert statuses["RETELL_API_KEY"] == "optional"
    # One family is not a choice; the picker only appears for an ambiguous scan.
    assert response.data["credentials"]["credential_choices"] == []

    payload["agent"]["secret_refs"] = _LIVEKIT_REFS
    with patch(
        "simulate.services.harness_provider._preflight_source_connectors",
        return_value=(["livekit"], 12),
    ):
        ready = DaytonaHarnessProvider().preflight(request)
    assert ready.data["ready_to_submit"] is True
    assert _status_by_name(ready)["LIVEKIT_URL"] == "configured"


def test_daytona_preflight_leaves_credentials_optional_when_source_is_unclassified(
    settings,
):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}
    payload = _v1_payload()
    payload["agent"] = {"connector": "auto", "config": {}, "secret_refs": {}}
    request = SimpleNamespace(
        validated_data=payload,
        build_absolute_uri=lambda _path: "https://harness.example.test/",
    )

    with patch(
        "simulate.services.harness_provider._preflight_source_connectors",
        return_value=([], 3),
    ):
        response = DaytonaHarnessProvider().preflight(request)

    assert response.data["ready_to_submit"] is True
    assert {
        item["status"] for item in response.data["credentials"]["requirements"]
    } == {"optional"}
    assert response.data["credentials"]["credential_choices"] == []

    with patch(
        "simulate.services.harness_provider._preflight_source_connectors",
        return_value=(["livekit", "retell"], 3),
    ):
        ambiguous = DaytonaHarnessProvider().preflight(request)
    assert ambiguous.data["ready_to_submit"] is False
    choice = ambiguous.data["credentials"]["credential_choices"][0]
    assert choice["satisfied"] is False
    assert choice["options"] == [
        ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"],
        ["RETELL_API_KEY"],
    ]


def test_daytona_preflight_requires_detected_vertex_credential_file(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}
    payload = _v1_payload()
    payload["agent"] = {"connector": "auto", "config": {}, "secret_refs": {}}
    request = SimpleNamespace(
        validated_data=payload,
        build_absolute_uri=lambda _path: "https://harness.example.test/",
    )

    with patch(
        "simulate.services.harness_provider._preflight_source_connectors",
        return_value=([], ["GOOGLE_APPLICATION_CREDENTIALS_JSON"], 7),
    ):
        response = DaytonaHarnessProvider().preflight(request)

    assert response.status_code == 200
    assert response.data["ready_to_submit"] is False
    requirement = next(
        item
        for item in response.data["credentials"]["requirements"]
        if item["environment_name"] == "GOOGLE_APPLICATION_CREDENTIALS_JSON"
    )
    assert requirement == {
        "id": "credential-file:GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "environment_name": "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "provider": "Google Vertex AI",
        "purpose": "Agent service-account credential file",
        "kind": "file",
        "required": True,
        "status": "missing",
    }

    payload["agent"]["secret_refs"] = {
        "GOOGLE_APPLICATION_CREDENTIALS_JSON": {
            "manager": "platform-vault",
            "key": "uploaded-google-adc",
            "version": "1",
            "purpose": "target_provider",
        }
    }
    with patch(
        "simulate.services.harness_provider._preflight_source_connectors",
        return_value=([], ["GOOGLE_APPLICATION_CREDENTIALS_JSON"], 7),
    ):
        ready = DaytonaHarnessProvider().preflight(request)

    assert ready.data["ready_to_submit"] is True


def test_daytona_launch_guard_rejects_missing_detected_vertex_file():
    payload = _v1_payload()
    payload["agent"] = {"connector": "auto", "config": {}, "secret_refs": {}}

    with (
        patch(
            "simulate.services.harness_provider._preflight_source_connectors",
            return_value=([], ["GOOGLE_APPLICATION_CREDENTIALS_JSON"], 7),
        ),
        pytest.raises(HostedHarnessError) as exc_info,
    ):
        _validate_required_credential_files(SimpleNamespace(), payload)

    assert exc_info.value.code == "credential_file_required"
    assert exc_info.value.status_code == 422
    assert "upload GOOGLE_APPLICATION_CREDENTIALS JSON" in str(exc_info.value)


def test_auto_source_submission_does_not_require_unrelated_voice_credentials():
    payload = _v1_payload()
    payload["agent"] = {"connector": "auto", "config": {}, "secret_refs": {}}

    serializer = HarnessJobCreateSerializer(data=payload)

    assert serializer.is_valid(), serializer.errors


def test_explicit_livekit_submission_still_requires_target_credentials():
    payload = _v1_payload()
    payload["agent"] = {"connector": "livekit", "config": {}, "secret_refs": {}}

    serializer = HarnessJobCreateSerializer(data=payload)

    assert not serializer.is_valid()
    assert "LIVEKIT_URL" in str(serializer.errors)


def test_connected_provider_agent_is_a_valid_source_without_repository_upload():
    payload = _v1_payload()
    payload.pop("source")
    payload["agent"] = {
        "connector": "retell",
        "mode": "provider_import",
        "config": {"agent_id": "agent-123"},
        "secret_refs": {
            "RETELL_API_KEY": {
                "manager": "platform-vault",
                "key": "retell-run-secret",
                "version": "1",
                "purpose": "target_provider",
            }
        },
    }

    serializer = HarnessJobCreateSerializer(data=payload)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["source"] == {
        "kind": "provider",
        "visibility": "public",
    }


def test_provider_connection_value_satisfies_preflight_without_extra_env_source():
    payload = _v1_payload()
    payload.pop("source")
    payload["agent"] = {
        "connector": "retell_chat",
        "mode": "connect_only",
        "config": {"agent_id": "agent-123"},
        "secret_refs": {
            "RETELL_API_KEY": {
                "manager": "platform-vault",
                "key": "pending:retell_api_key",
                "version": "pending",
                "purpose": "target_provider",
            }
        },
    }
    payload["credential_values"] = {"RETELL_API_KEY": "test-provider-value"}

    serializer = HarnessPreflightSerializer(data=payload)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["credential_values"] == {
        "RETELL_API_KEY": "test-provider-value"
    }
    assert serializer.validated_data["source"]["kind"] == "provider"


def test_preflight_rejects_unknown_provider_target_before_run(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}
    payload = _v1_payload()
    payload["source"] = {"kind": "provider", "visibility": "public"}
    payload["agent"] = {
        "connector": "retell",
        "mode": "connect_only",
        "config": {"agent_id": "not-a-real-agent"},
        "secret_refs": {
            "RETELL_API_KEY": {
                "manager": "platform-vault",
                "key": "pending-retell-api-key",
                "version": "1",
                "purpose": "target_provider",
            }
        },
    }
    payload["credential_values"] = {"RETELL_API_KEY": "valid-key"}
    request = SimpleNamespace(
        validated_data=payload,
        build_absolute_uri=lambda _path: "https://harness.example.test/",
    )
    failed_target = SimpleNamespace(
        as_dict=lambda: {
            "provider": "retell_target",
            "label": "Retell voice agent",
            "aliases": ["RETELL_API_KEY"],
            "ok": False,
            "message": (
                "Retell voice agent ID was not found or is not accessible with "
                "RETELL_API_KEY"
            ),
        }
    )

    with (
        patch(
            "simulate.services.harness_provider._preflight_source_connectors",
            return_value=([], [], 0),
        ),
        patch(
            "simulate.services.harness_credential_probes.probe_all",
            return_value=[],
        ),
        patch(
            "simulate.services.harness_credential_probes.probe_provider_target",
            return_value=failed_target,
        ),
    ):
        response = DaytonaHarnessProvider().preflight(request)

    assert response.data["ready_to_submit"] is False
    assert response.data["credentials"]["probe"][-1]["provider"] == "retell_target"
    assert "ID was not found" in response.data["credentials"]["probe"][-1]["message"]


def test_repository_source_remains_required_for_environment_backed_provider():
    payload = _v1_payload()
    payload.pop("source")
    payload["agent"] = {
        "connector": "retell",
        "mode": "environment_backed",
        "config": {"lifecycle_manifest": "alk.yaml"},
        "secret_refs": {},
    }

    serializer = HarnessJobCreateSerializer(data=payload)

    assert not serializer.is_valid()
    assert "existing provider agent ID" in str(serializer.errors)


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
        patch("simulate.services.harness_provider._validate_required_credential_files"),
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
    run_test = RunTest.objects.create(
        name="Saved hosted run",
        organization=user.organization,
        workspace=workspace,
    )
    test_execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=2,
        total_calls=2,
        completed_calls=2,
        failed_calls=0,
        completed_at=job.created_at,
    )
    job.run_test = run_test
    job.test_execution = test_execution
    payload = dict(job.payload)
    payload["scenario_count"] = 1
    payload.setdefault("metadata", {})["authoring_object_key"] = (
        "harness/jobs/saved/authoring.tar.gz"
    )
    job.payload = payload
    job.save(
        update_fields=[
            "payload",
            "scenario_count",
            "run_test",
            "test_execution",
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
    test_execution.refresh_from_db()
    assert test_execution.status == TestExecution.ExecutionStatus.RUNNING
    assert test_execution.completed_at is None
    assert test_execution.completed_calls == 0
    assert test_execution.failed_calls == 0
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
    raw = (
        b'{"type":"service_account","project_id":"test-project",'
        b'"client_email":"svc@test-project.iam.gserviceaccount.com",'
        b'"private_key":"must-not-be-echoed"}'
    )
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
def test_secret_file_upload_rejects_incomplete_google_service_account(user):
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/simulate/api/harness-jobs/secret-files/",
        {
            "file": SimpleUploadedFile(
                "customer-google.json",
                b'{"type":"service_account","private_key":"incomplete"}',
                content_type="application/json",
            ),
            "environment_name": "GOOGLE_APPLICATION_CREDENTIALS",
        },
        format="multipart",
    )

    assert response.status_code == 400
    assert "project_id, client_email, and private_key" in response.json()["detail"]

    from simulate.models import HostedHarnessSecret

    assert not HostedHarnessSecret.objects.filter(
        organization=user.organization,
        name__startswith="harness-google-adc-",
    ).exists()


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
