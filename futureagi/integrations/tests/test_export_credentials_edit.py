"""Edit replaces an export's credentials, checked before they are saved.

A Datadog API key, a PostHog project API key, a Mixpanel project token and API
secret, and an Amazon S3 access key pair can be replaced from the connection's
Edit dialog. The new credentials are checked with the platform first (as the
Add Integration wizard checks them): a refusal leaves the stored ones as they
were; a pass stores them, and a connection in Error is active again. What
decides where an export delivers (the Datadog site, the bucket, region and
prefix) stays as it was set. Every vendor call is patched: nothing is sent.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status as http_status

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services.credentials import CredentialManager

URL = "/integrations/connections/"
SERVICE = "integrations.views.integration_connection.get_integration_service"

S3 = {
    "sub_platform": "s3",
    "bucket": "acme-gateway-logs",
    "region": "eu-west-1",
    "access_key_id": "AKIAOLD",
    "secret_access_key": "old-secret",
    "prefix": "agentcc",
}
EXPORTS = {
    "datadog": (
        "https://api.datadoghq.eu",
        {"api_key": "dd-old", "site": "eu1"},
        {"api_key": "dd-new"},
    ),
    "posthog": (
        "http://posthog.sink.test",
        {"api_key": "phc_old"},
        {"api_key": "phc_new"},
    ),
    "mixpanel": (
        "https://api.mixpanel.com",
        {"project_token": "tok-old"},
        {"project_token": "tok-new", "api_secret": "secret-new"},
    ),
    "cloud_storage": (
        "https://acme-gateway-logs.s3.eu-west-1.amazonaws.com/agentcc",
        S3,
        {"access_key_id": "AKIANEW", "secret_access_key": "new-secret"},
    ),
}


def _connection(organization, workspace, user, platform):
    host_url, credentials, _ = EXPORTS[platform]
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=platform,
        display_name=IntegrationPlatform(platform).label,
        host_url=host_url,
        encrypted_credentials=CredentialManager.encrypt(credentials),
        external_project_name=platform,
        status=ConnectionStatus.ERROR,
        status_message=f"{IntegrationPlatform(platform).label} refused the key.",
        backfill_completed=True,
    )


def _service(valid=True):
    service = MagicMock()
    service.validate_credentials.return_value = (
        {"valid": True, "projects": [], "total_traces": 0}
        if valid
        else {"valid": False, "error": "Invalid API key."}
    )
    return service


def _patch(auth_client, connection, body, service):
    with patch(SERVICE, return_value=service):
        response = auth_client.patch(
            f"{URL}{connection.id}/",
            data=json.dumps(body),
            content_type="application/json",
        )
    connection.refresh_from_db()
    return response


def _stored(connection):
    return CredentialManager.decrypt(bytes(connection.encrypted_credentials))


@pytest.mark.django_db
class TestEditReplacesAnExportsCredentials:
    @pytest.mark.parametrize("platform", sorted(EXPORTS))
    def test_checked_then_stored_and_the_connection_is_active_again(
        self, platform, auth_client, organization, workspace, user
    ):
        connection = _connection(organization, workspace, user, platform)
        host_url, before, replaced = EXPORTS[platform]
        service = _service()

        response = _patch(auth_client, connection, {"credentials": replaced}, service)

        assert response.status_code == http_status.HTTP_200_OK, response.json()
        expected = {**before, **replaced}
        assert service.validate_credentials.call_args.kwargs["credentials"] == expected
        assert _stored(connection) == expected
        assert connection.host_url == host_url
        assert (connection.status, connection.status_message) == (
            ConnectionStatus.ACTIVE,
            "",
        )

    def test_a_refused_key_leaves_the_stored_one(
        self, auth_client, organization, workspace, user
    ):
        connection = _connection(organization, workspace, user, "datadog")

        response = _patch(
            auth_client,
            connection,
            {"credentials": {"api_key": "dd-wrong"}},
            _service(valid=False),
        )

        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        assert "Invalid API key." in json.dumps(response.json())
        assert _stored(connection) == EXPORTS["datadog"][1]
        assert connection.status == ConnectionStatus.ERROR

    @pytest.mark.parametrize(
        ("platform", "body"),
        [
            ("datadog", {"site": "us1"}),
            ("cloud_storage", {"bucket": "elsewhere"}),
            ("cloud_storage", {"region": "us-east-1"}),
            ("posthog", {"project_token": "x"}),
        ],
    )
    def test_where_it_delivers_is_not_changed_here(
        self, platform, body, auth_client, organization, workspace, user
    ):
        connection = _connection(organization, workspace, user, platform)
        service = _service()

        response = _patch(auth_client, connection, {"credentials": body}, service)

        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        service.validate_credentials.assert_not_called()
        assert _stored(connection) == EXPORTS[platform][1]
