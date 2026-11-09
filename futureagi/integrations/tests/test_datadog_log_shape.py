"""The shared-activity type gate found a nonexistent request-log gateway field."""

from datetime import timedelta

import pytest
import requests
from django.utils import timezone

from agentcc.models import AgentccRequestLog
from integrations.services.datadog_service import DatadogService
from integrations.temporal.activities import _export_datadog


@pytest.mark.django_db
def test_export_uses_real_request_log_fields(integration_connection, monkeypatch):
    now = timezone.now()
    row = AgentccRequestLog.objects.create(
        organization=integration_connection.organization,
        workspace=integration_connection.workspace,
        request_id="synthetic-export",
        model="synthetic",
        provider="synthetic",
        started_at=now,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        latency_ms=20,
        status_code=200,
    )
    bodies = []

    def post(url, *, json, **kwargs):
        assert url in {
            "https://http-intake.logs.datadoghq.com/api/v2/logs",
            "https://api.datadoghq.com/api/v2/series",
        }
        bodies.append(json)
        response = requests.Response()
        response.status_code = 202
        return response

    monkeypatch.setattr("requests.post", post)
    result = _export_datadog(
        integration_connection,
        {"api_key": "synthetic-test", "site": "us1"},
        DatadogService(),
        now - timedelta(seconds=1),
        now + timedelta(seconds=1),
    )
    assert result["logs_sent"] == 1
    assert len(bodies) == 2
    assert bodies[0][0]["attributes"]["request_id"] == row.request_id
    assert "gateway:" not in bodies[0][0]["ddtags"]
    metrics = {item["metric"]: item for item in bodies[1]["series"]}
    assert metrics["agentcc.gateway.requests"]["points"][0]["value"] == 1
    assert metrics["agentcc.gateway.input_tokens"]["points"][0]["value"] == 10
