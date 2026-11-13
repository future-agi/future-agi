"""Datadog sync exports persisted request logs without inventing model fields."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from agentcc.models import AgentccRequestLog
from integrations.models import (
    ConnectionStatus,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)
from integrations.services.credentials import CredentialManager
from integrations.temporal.activities import sync_integration_connection


@pytest.fixture
def datadog_connection(integration_connection):
    connection = integration_connection
    connection.platform = IntegrationPlatform.DATADOG
    connection.project = None
    connection.last_synced_at = datetime.now(UTC) - timedelta(minutes=5)
    connection.encrypted_credentials = CredentialManager.encrypt(
        {"api_key": "test-datadog-key", "site": "us1"}
    )
    connection.save(
        update_fields=["platform", "project", "last_synced_at", "encrypted_credentials"]
    )
    return connection


@pytest.mark.django_db(transaction=True)
def test_datadog_sync_exports_request_log(datadog_connection):
    connection = datadog_connection
    checkpoint = connection.last_synced_at
    log = AgentccRequestLog.no_workspace_objects.create(
        organization=connection.organization,
        workspace=connection.workspace,
        request_id="datadog-request-1",
        model="gpt-4o-mini",
        provider="openai",
        resolved_model="gpt-4o-mini",
        started_at=checkpoint + timedelta(minutes=1),
        latency_ms=123,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cost=Decimal("0.001500"),
        status_code=200,
    )

    with patch("integrations.services.datadog_service.requests.post") as post:
        sync_integration_connection(str(connection.id))

    sync_log = SyncLog.objects.get(connection=connection)
    assert sync_log.error_message is None
    assert sync_log.status == SyncStatus.SUCCESS
    assert sync_log.traces_fetched == 1
    assert sync_log.traces_created == 1
    assert sync_log.spans_synced == 5
    connection.refresh_from_db()
    assert connection.status == ConnectionStatus.ACTIVE
    assert connection.status_message == ""
    assert connection.last_synced_at > checkpoint

    assert post.call_count == 2
    logs_call, metrics_call = post.call_args_list
    assert logs_call.args == ("https://http-intake.logs.datadoghq.com/api/v2/logs",)
    assert logs_call.kwargs["headers"]["DD-API-KEY"] == "test-datadog-key"
    (payload,) = logs_call.kwargs["json"]
    assert payload["ddtags"] == "model:gpt-4o-mini,provider:openai,status_code:200"
    assert payload["status"] == "info"
    assert payload["date"] == log.started_at.isoformat()
    assert payload["attributes"]["request_id"] == log.request_id
    assert payload["attributes"]["total_tokens"] == 15
    assert payload["attributes"]["cost"] == 0.0015
    assert metrics_call.args == ("https://api.datadoghq.com/api/v2/series",)
    assert {
        series["metric"]: series["points"][0]["value"]
        for series in metrics_call.kwargs["json"]["series"]
    } == {
        "agentcc.gateway.requests": 1,
        "agentcc.gateway.latency_ms": 123.0,
        "agentcc.gateway.input_tokens": 10,
        "agentcc.gateway.output_tokens": 5,
        "agentcc.gateway.cost": 0.0015,
    }


@pytest.mark.django_db(transaction=True)
def test_datadog_sync_empty_window_still_advances_checkpoint(datadog_connection):
    connection = datadog_connection
    checkpoint = connection.last_synced_at

    with patch("integrations.services.datadog_service.requests.post") as post:
        sync_integration_connection(str(connection.id))

    post.assert_not_called()
    sync_log = SyncLog.objects.get(connection=connection)
    assert sync_log.status == SyncStatus.NO_NEW_DATA
    assert sync_log.error_message is None
    assert sync_log.traces_fetched == 0
    assert sync_log.spans_synced == 0
    connection.refresh_from_db()
    assert connection.status == ConnectionStatus.ACTIVE
    assert connection.last_synced_at > checkpoint
