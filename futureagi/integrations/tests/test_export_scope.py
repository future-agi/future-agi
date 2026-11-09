"""Which request logs an export connection delivers.

Gateway request logs carry no workspace: they belong to the organization. A
connection in the default workspace exports them, with the default
workspace's own logs, as Request Logs shows them there; a connection in
another workspace exports that workspace's logs; no export ever carries
another organization's logs. Runs the sync activity itself, with the vendor
call patched, so nothing is sent.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from agentcc.models import AgentccRequestLog
from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)
from integrations.services.credentials import CredentialManager
from integrations.temporal.activities import sync_integration_connection


def _log(organization, workspace, request_id):
    # bulk_create, as log ingestion stores them: no save signal assigns a
    # workspace to a gateway row.
    (row,) = AgentccRequestLog.no_workspace_objects.bulk_create(
        [_row(organization, workspace, request_id)]
    )
    return row


def _row(organization, workspace, request_id):
    return AgentccRequestLog(
        organization=organization,
        workspace=workspace,
        request_id=request_id,
        model="gpt-4o-mini",
        provider="openai",
        status_code=200,
        latency_ms=120,
        total_tokens=42,
        started_at=datetime.now(UTC) - timedelta(minutes=5),
    )


def _posthog_connection(organization, workspace, user):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.POSTHOG,
        display_name="posthog",
        host_url="http://posthog.sink.test",
        encrypted_credentials=CredentialManager.encrypt({"api_key": "phc_e2e"}),
        status=ConnectionStatus.BACKFILLING,
        backfill_completed=False,
    )


def _exported_request_ids(connection):
    """Runs the connection's sync and returns the request ids PostHog was sent."""
    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    with patch(
        "integrations.services.posthog_service.requests.post", return_value=response
    ) as post:
        sync_integration_connection(str(connection.id))
    sent = []
    for call in post.call_args_list:
        assert call.args[0] == "http://posthog.sink.test/batch/"
        sent += [e["properties"]["request_id"] for e in call.kwargs["json"]["batch"]]
    return sorted(sent)


@pytest.fixture
def scoped_logs(db, organization, workspace, user):
    other = Workspace.objects.create(
        organization=organization, name="Other", is_default=False, created_by=user
    )
    foreign_org = Organization.objects.create(name="Foreign Organization")
    _log(organization, None, "r-gateway")
    _log(organization, workspace, "r-default")
    _log(organization, other, "r-other")
    _log(foreign_org, None, "r-foreign")
    return other


@pytest.mark.django_db(transaction=True)
class TestExportScope:
    def test_default_workspace_exports_the_org_gateway_logs(
        self, organization, workspace, user, scoped_logs
    ):
        connection = _posthog_connection(organization, workspace, user)

        assert _exported_request_ids(connection) == ["r-default", "r-gateway"]
        log = SyncLog.objects.get(connection=connection)
        assert log.status == SyncStatus.SUCCESS
        assert log.traces_fetched == 2

    def test_other_workspace_exports_its_own_logs(
        self, organization, user, scoped_logs
    ):
        connection = _posthog_connection(organization, scoped_logs, user)

        assert _exported_request_ids(connection) == ["r-other"]


@pytest.mark.django_db(transaction=True)
class TestDatadogExport:
    def test_sends_each_log_with_its_tags_and_the_window_series(
        self, organization, workspace, user, scoped_logs
    ):
        connection = IntegrationConnection.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            created_by=user,
            platform=IntegrationPlatform.DATADOG,
            display_name="datadog",
            host_url="https://api.datadoghq.com",
            encrypted_credentials=CredentialManager.encrypt(
                {"api_key": "dd-e2e", "site": "us1"}
            ),
            status=ConnectionStatus.BACKFILLING,
            backfill_completed=False,
        )
        response = MagicMock(status_code=202)
        response.raise_for_status.return_value = None
        with patch(
            "integrations.services.datadog_service.requests.post",
            return_value=response,
        ) as post:
            sync_integration_connection(str(connection.id))

        log = SyncLog.objects.get(connection=connection)
        assert (log.status, log.error_message) == (SyncStatus.SUCCESS, None)
        logs_call, series_call = post.call_args_list
        assert logs_call.args[0] == "https://http-intake.logs.datadoghq.com/api/v2/logs"
        sent = {
            entry["attributes"]["request_id"]: entry
            for entry in logs_call.kwargs["json"]
        }
        assert sorted(sent) == ["r-default", "r-gateway"]
        assert sent["r-gateway"]["ddtags"].split(",") == [
            "model:gpt-4o-mini",
            "provider:openai",
            "status_code:200",
        ]
        assert series_call.args[0] == "https://api.datadoghq.com/api/v2/series"
        requests_metric = next(
            s
            for s in series_call.kwargs["json"]["series"]
            if s["metric"] == "agentcc.gateway.requests"
        )
        assert requests_metric["points"][0]["value"] == 2
        assert requests_metric["tags"] == [f"org:{organization.id}"]
