from unittest.mock import patch

import pytest
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
