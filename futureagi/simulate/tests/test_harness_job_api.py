from unittest.mock import patch

import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_harness_job_create_proxies_typed_request(user):
    client = APIClient()
    client.force_authenticate(user=user)
    expected = {"job": {"job_id": "job-1"}, "status": {"stage": "queued"}}

    with patch("simulate.views.harness_job.HarnessSandboxClient.submit", return_value=expected) as submit:
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
