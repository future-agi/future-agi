"""Real gateway ingestion leaves workspace null; exports resolve it from the key."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.services.log_ingestion import ingest_request_logs
from integrations.services.mixpanel_service import MixpanelService
from integrations.temporal.activities import _export_mixpanel
from integrations.tests.test_mixpanel_service import CREDENTIALS, acknowledged


@pytest.mark.django_db
def test_mixpanel_exports_ingested_logs_only_for_connections_workspace(
    integration_connection, user
):
    connection = integration_connection
    other_workspace = Workspace.objects.create(
        name="other-mixpanel-workspace",
        organization=connection.organization,
        created_by=user,
    )
    other_org = Organization.objects.create(name="other-mixpanel-organization")
    now = datetime.now(UTC)
    keys = [
        AgentccAPIKey.no_workspace_objects.create(
            organization=org,
            workspace=workspace,
            gateway_key_id=name,
            name=name,
        )
        for name, org, workspace in [
            ("mixpanel-own-key", connection.organization, connection.workspace),
            ("mixpanel-other-workspace-key", connection.organization, other_workspace),
            ("mixpanel-other-org-key", other_org, None),
        ]
    ]
    assert (
        ingest_request_logs(
            [
                {
                    "request_id": key.gateway_key_id,
                    "auth_key_id": key.gateway_key_id,
                    "timestamp": (now - timedelta(minutes=2)).isoformat(),
                    "status_code": 200,
                    # Caller metadata must never grant access to another workspace.
                    "metadata": {"workspace_id": str(connection.workspace_id)},
                }
                for key in keys
            ]
        )
        == 3
    )
    own = AgentccRequestLog.no_workspace_objects.get(request_id=keys[0].gateway_key_id)
    assert own.workspace_id is None  # the actual gateway webhook persistence shape
    explicit = AgentccRequestLog.no_workspace_objects.create(
        organization=connection.organization,
        workspace=connection.workspace,
        request_id="mixpanel-explicit-workspace",
        started_at=now - timedelta(minutes=1),
    )
    # A known workspace takes precedence over the key fallback.
    AgentccRequestLog.no_workspace_objects.create(
        organization=connection.organization,
        workspace=other_workspace,
        api_key_id=keys[0].gateway_key_id,
        request_id="mixpanel-explicit-other-workspace",
        started_at=now - timedelta(minutes=1),
    )
    AgentccRequestLog.no_workspace_objects.bulk_create(
        [
            AgentccRequestLog(
                organization=connection.organization,
                api_key_id="unknown-key",
                request_id="mixpanel-unresolved-workspace",
                started_at=now - timedelta(minutes=1),
            )
        ]
    )
    keys[0].status = AgentccAPIKey.REVOKED
    keys[0].save(update_fields=["status"])
    with patch(
        "integrations.services.mixpanel_service.requests.post",
        side_effect=lambda *args, **kwargs: acknowledged(len(kwargs["json"])),
    ) as post:
        result = _export_mixpanel(
            connection, CREDENTIALS, MixpanelService(), now - timedelta(hours=1), now
        )
    assert result == {"logs_sent": 2, "metrics_sent": 0}
    assert {
        event["properties"]["request_id"] for event in post.call_args.kwargs["json"]
    } == {own.request_id, explicit.request_id}
