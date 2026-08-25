from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from simulate.services.harness_provider import (
    DaytonaHarnessProvider,
    SandboxHarnessProvider,
    get_harness_provider,
)


def _v1_payload(**overrides):
    payload = {
        "source": {
            "kind": "github",
            "repository": "acme/agent",
            "ref": "main",
            "visibility": "public",
        },
        "agent": {"connector": "auto"},
        "scenario_count": 10,
        "artifacts": {"level": "full"},
    }
    payload.update(overrides)
    return payload


def test_default_provider_is_daytona():
    assert isinstance(get_harness_provider(), DaytonaHarnessProvider)


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
            "agent": {"connector": "livekit", "config": {"room": "r1"}, "secret_refs": {}},
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
            {"files": files, "paths": ["agent.py", "requirements.txt"], "name": "agent"},
            format="multipart",
        )

    assert response.status_code == 201
    assert response.json() == expected
    assert upload.call_args.args[1] == ["agent.py", "requirements.txt"]


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="daytona")
def test_daytona_create_starts_gateway_workflow(user):
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
    with patch(
        "simulate.services.hosted_harness.create_hosted_job",
        return_value=(_Job(), True),
    ) as create, patch(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow"
    ) as start, patch(
        "simulate.services.harness_provider.serialize_job", return_value=serialized
    ):
        response = client.post(
            "/simulate/api/harness-jobs/",
            _v1_payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY="key-1",
        )

    assert response.status_code == 202
    assert response.json() == serialized
    assert create.call_args.kwargs["idempotency_key"] == "key-1"
    assert start.call_args.args[0] == str(_Job.id)
