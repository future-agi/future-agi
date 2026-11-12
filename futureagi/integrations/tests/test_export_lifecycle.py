"""An export connection's life without a FutureAGI project, and its count.

An export sends the gateway's request logs out, so creating one creates no
FutureAGI project (an import still creates the project it imports into), a
paused export resumes without one, and every successful sync adds the request
logs it exported to the connection's count. Every vendor call is patched:
nothing is sent.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status as http_status

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)
from integrations.services.credentials import CredentialManager
from tracer.models.project import Project

URL = "/integrations/connections/"
S3 = {
    "sub_platform": "s3",
    "bucket": "acme-gateway-logs",
    "region": "eu-west-1",
    "access_key_id": "AKIAEXAMPLE",
    "secret_access_key": "example-secret",
    "prefix": "/agentcc/production/",
}


def _result(response):
    data = response.json()
    return data.get("result", data)


def _valid_service():
    service = MagicMock()
    service.validate_credentials.return_value = {
        "valid": True,
        "projects": [],
        "total_traces": 0,
    }
    return service


def _create(auth_client, payload):
    with (
        patch(
            "integrations.views.integration_connection.get_integration_service",
            return_value=_valid_service(),
        ) as get_service,
        patch("integrations.temporal.activities.start_backfill_workflow"),
    ):
        response = auth_client.post(
            URL, data=json.dumps(payload), content_type="application/json"
        )
    return response, get_service.return_value


def _export(platform, credentials, **extra):
    return {
        "platform": platform,
        "credentials": credentials,
        "backfill_option": "all",
        "sync_interval_seconds": 300,
        **extra,
    }


@pytest.mark.django_db
class TestExportsCreateNoProject:
    @pytest.mark.parametrize(
        "platform, credentials",
        [
            ("datadog", {"api_key": "dd-key", "site": "us1"}),
            ("mixpanel", {"project_token": "mp-token"}),
            ("cloud_storage", S3),
            ("posthog", {"api_key": "phc_key"}),
        ],
    )
    def test_an_export_creates_no_project(self, auth_client, platform, credentials):
        before = Project.objects.count()
        extra = (
            {"host_url": "https://posthog.acme.test"} if platform == "posthog" else {}
        )
        response, _ = _create(auth_client, _export(platform, credentials, **extra))
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
        assert Project.objects.count() == before
        stored = IntegrationConnection.no_workspace_objects.get(
            id=_result(response)["id"]
        )
        assert stored.project_id is None
        assert not Project.objects.filter(name=platform).exists()

    def test_an_import_still_creates_its_project(self, auth_client):
        response, _ = _create(
            auth_client,
            {
                "platform": "langfuse",
                "host_url": "https://langfuse.acme.test",
                "public_key": "pk-lf",
                "secret_key": "sk-lf",
                "external_project_name": "lf-project",
                "new_project_name": "Imported from Langfuse",
                "backfill_option": "new_only",
            },
        )
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
        stored = IntegrationConnection.no_workspace_objects.get(
            id=_result(response)["id"]
        )
        assert stored.project is not None
        assert stored.project.name == "Imported from Langfuse"


def _connection(organization, workspace, user, platform, host_url, credentials):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=platform,
        display_name=IntegrationPlatform(platform).label,
        host_url=host_url,
        encrypted_credentials=CredentialManager.encrypt(credentials),
        external_project_name=platform,
        status=ConnectionStatus.PAUSED,
        backfill_completed=True,
    )


@pytest.mark.django_db
class TestExportPauseResume:
    def test_a_paused_export_resumes_without_a_project(
        self, auth_client, organization, workspace, user
    ):
        connection = _connection(
            organization,
            workspace,
            user,
            "datadog",
            "https://api.datadoghq.com",
            {"api_key": "dd-key", "site": "us1"},
        )
        response = auth_client.post(
            f"{URL}{connection.id}/resume/",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == http_status.HTTP_200_OK, response.json()
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.ACTIVE

    def test_a_paused_import_without_its_project_still_cannot_resume(
        self, auth_client, organization, workspace, user
    ):
        connection = _connection(
            organization,
            workspace,
            user,
            "langfuse",
            "https://langfuse.acme.test",
            {"public_key": "pk", "secret_key": "sk"},
        )
        response = auth_client.post(
            f"{URL}{connection.id}/resume/",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.PAUSED


@pytest.mark.django_db(transaction=True)
class TestExportCountsWhatItSent:
    def test_each_sync_adds_the_request_logs_it_exported(
        self, organization, workspace, user
    ):
        from datetime import UTC, datetime, timedelta

        from agentcc.models import AgentccRequestLog
        from integrations.temporal.activities import sync_integration_connection

        AgentccRequestLog.no_workspace_objects.bulk_create(
            [
                AgentccRequestLog(
                    organization=organization,
                    workspace=None,
                    request_id=f"r-{i}",
                    model="gpt-4o-mini",
                    provider="openai",
                    status_code=200,
                    started_at=datetime.now(UTC) - timedelta(minutes=5),
                )
                for i in range(3)
            ]
        )
        connection = _connection(
            organization,
            workspace,
            user,
            "posthog",
            "http://posthog.sink.test",
            {"api_key": "phc_key"},
        )
        connection.status = ConnectionStatus.BACKFILLING
        connection.total_traces_synced = 4
        connection.save(update_fields=["status", "total_traces_synced"])
        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        with patch(
            "integrations.services.posthog_service.requests.post",
            return_value=response,
        ):
            sync_integration_connection(str(connection.id))

        log = SyncLog.objects.get(connection=connection)
        assert (log.status, log.traces_fetched) == (SyncStatus.SUCCESS, 3)
        connection.refresh_from_db()
        assert connection.total_traces_synced == 7
