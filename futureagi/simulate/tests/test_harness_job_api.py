from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_harness_job_create_proxies_typed_request(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {"job": {"job_id": "job-1"}, "status": {"stage": "queued"}}

    with patch(
        "simulate.views.harness_job.HarnessSandboxClient.submit", return_value=expected
    ) as submit:
        response = client.post(
            "/simulate/api/harness-jobs/",
            {"source_path": "/workspace/agent", "scenario_count": 10},
            format="json",
        )

    assert response.status_code == 202
    assert response.json() == expected
    assert submit.call_args.args[0]["source_path"] == "/workspace/agent"


@pytest.mark.django_db
def test_harness_source_folder_upload_is_forwarded_to_sandbox(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {
        "source_id": "63ef3598-a84d-4ce0-a7a1-53c4e27f69f7",
        "name": "agent",
        "file_count": 2,
        "total_bytes": 18,
    }
    files = [
        SimpleUploadedFile("agent.py", b"print('ready')\n"),
        SimpleUploadedFile("requirements.txt", b"fastapi\n"),
    ]

    with patch(
        "simulate.views.harness_job.HarnessSandboxClient.upload_source",
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
    assert upload.call_args.args[2] == "agent"


@pytest.mark.django_db
def test_harness_job_accepts_uploaded_source_id(user):
    client = APIClient()
    client.force_authenticate(user=user)
    source_id = "63ef3598-a84d-4ce0-a7a1-53c4e27f69f7"
    expected = {"job": {"job_id": "job-1"}, "status": {"stage": "queued"}}

    with patch(
        "simulate.views.harness_job.HarnessSandboxClient.submit", return_value=expected
    ) as submit:
        response = client.post(
            "/simulate/api/harness-jobs/",
            {"source_id": source_id, "scenario_count": 10},
            format="json",
        )

    assert response.status_code == 202
    assert str(submit.call_args.args[0]["source_id"]) == source_id


@pytest.mark.django_db
def test_harness_job_create_rejects_inline_credentials(user):
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/simulate/api/harness-jobs/",
        {
            "source_path": "/workspace/agent",
            "connector_config": {"api_key": "must-not-cross-control-plane"},
        },
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_harness_job_forwards_ephemeral_environment_without_echoing_it(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {"job": {"job_id": "job-1"}, "status": {"stage": "queued"}}

    with patch(
        "simulate.views.harness_job.HarnessSandboxClient.submit", return_value=expected
    ) as submit:
        response = client.post(
            "/simulate/api/harness-jobs/",
            {
                "source_path": "/workspace/agent",
                "environment_values": {"OPENAI_API_KEY": "ephemeral-value"},
            },
            format="json",
        )

    assert response.status_code == 202
    assert "ephemeral-value" not in response.content.decode()
    assert submit.call_args.args[0]["environment_values"] == {
        "OPENAI_API_KEY": "ephemeral-value"
    }


@pytest.mark.django_db
def test_harness_job_rejects_invalid_or_conflicting_environment_values(user):
    client = APIClient()
    client.force_authenticate(user=user)

    invalid = client.post(
        "/simulate/api/harness-jobs/",
        {
            "source_path": "/workspace/agent",
            "environment_values": {"NOT-AN-ENV": "value"},
        },
        format="json",
    )
    conflict = client.post(
        "/simulate/api/harness-jobs/",
        {
            "source_path": "/workspace/agent",
            "environment_values": {"OPENAI_API_KEY": "value"},
            "secret_refs": {
                "OPENAI_API_KEY": {
                    "manager": "futureagi",
                    "key": "existing-secret",
                    "purpose": "existing reference",
                }
            },
        },
        format="json",
    )
    reserved = client.post(
        "/simulate/api/harness-jobs/",
        {
            "source_path": "/workspace/agent",
            "environment_values": {"DOCKER_HOST": "tcp://untrusted:2375"},
        },
        format="json",
    )

    assert invalid.status_code == 400
    assert conflict.status_code == 400
    assert reserved.status_code == 400


@pytest.mark.django_db
def test_harness_preflight_proxies_public_github_source(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {
        "source_kind": "github",
        "ready_to_submit": True,
        "credentials": {"requirements": []},
    }

    with patch(
        "simulate.views.harness_job.HarnessSandboxClient.preflight",
        return_value=expected,
    ) as preflight:
        response = client.post(
            "/simulate/api/harness-jobs/preflight/",
            {"github_repository": "future-agi/public-agent"},
            format="json",
        )

    assert response.status_code == 200
    assert response.json() == expected
    assert preflight.call_args.args[0]["github_visibility"] == "public"


@pytest.mark.django_db
def test_harness_job_forwards_public_github_branch_url_unchanged(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {"job": {"job_id": "job-1"}, "status": {"stage": "queued"}}
    url = "https://github.com/future-agi/future-agi/tree/feat/harness"

    with patch(
        "simulate.views.harness_job.HarnessSandboxClient.submit", return_value=expected
    ) as submit:
        response = client.post(
            "/simulate/api/harness-jobs/",
            {"github_repository": url},
            format="json",
        )

    assert response.status_code == 202
    assert submit.call_args.args[0]["github_repository"] == url
    assert "github_ref" not in submit.call_args.args[0]


@pytest.mark.django_db
def test_harness_preflight_forwards_non_secret_configuration(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {"source_kind": "local_repository", "ready_to_submit": True}

    with patch(
        "simulate.views.harness_job.HarnessSandboxClient.preflight",
        return_value=expected,
    ) as preflight:
        response = client.post(
            "/simulate/api/harness-jobs/preflight/",
            {
                "source_path": "/workspace/agent",
                "connector_config": {"REMOTE_AGENT_ID": "agent-42"},
            },
            format="json",
        )

    assert response.status_code == 200
    assert preflight.call_args.args[0]["connector_config"] == {
        "REMOTE_AGENT_ID": "agent-42"
    }


@pytest.mark.django_db
def test_harness_preflight_rejects_secret_like_configuration_keys(user):
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/simulate/api/harness-jobs/preflight/",
        {
            "source_path": "/workspace/agent",
            "connector_config": {"GEMINI_API_KEY": "inline-secret"},
        },
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_private_github_source_requires_app_installation(user):
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/simulate/api/harness-jobs/",
        {
            "github_repository": "customer/private-agent",
            "github_visibility": "private",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "github_installation_id" in response.json()["detail"]


@pytest.mark.django_db
def test_harness_job_rejects_ambiguous_source(user):
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/simulate/api/harness-jobs/",
        {
            "source_path": "/workspace/agent",
            "github_repository": "customer/agent",
        },
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_harness_job_cancel_accepts_optional_audit_reason(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {"job": {"job_id": "job-1"}, "status": {"stage": "canceled"}}

    with patch(
        "simulate.views.harness_job.HarnessSandboxClient.cancel",
        return_value=expected,
    ) as cancel:
        response = client.post(
            "/simulate/api/harness-jobs/job-1/cancel/",
            {"reason": "operator requested stop"},
            format="json",
        )

    assert response.status_code == 200
    assert response.json() == expected
    cancel.assert_called_once_with("job-1")
