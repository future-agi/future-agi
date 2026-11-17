"""Where a connection says it delivers.

A connection stores, and the Integrations page shows, its real destination:
the address the user entered for a Langfuse or PostHog instance, otherwise
the destination its credentials name (the Datadog site's API host, Mixpanel's
ingestion API, the bucket in its region under its key prefix). No connection
is stored with an address nobody chose, and a destination that follows from
the credentials cannot be overwritten with another host. Every vendor call is
patched: nothing is sent.
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


def fixed_destination(platform, credentials):
    from integrations.services.export_destinations import fixed_destination as derive

    return derive(platform, credentials)


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


class TestFixedDestination:
    @pytest.mark.parametrize(
        "site, host",
        [
            ("us1", "https://api.datadoghq.com"),
            ("us3", "https://api.us3.datadoghq.com"),
            ("us5", "https://api.us5.datadoghq.com"),
            ("eu1", "https://api.datadoghq.eu"),
            ("ap1", "https://api.ap1.datadoghq.com"),
            ("us1-fed", "https://api.ddog-gov.com"),
        ],
    )
    def test_datadog_is_the_sites_api_host(self, site, host):
        assert fixed_destination("datadog", {"api_key": "k", "site": site}) == host

    def test_mixpanel_is_its_ingestion_api(self):
        assert fixed_destination("mixpanel", {"project_token": "t"}) == (
            "https://api.mixpanel.com"
        )

    def test_s3_is_the_bucket_in_its_region_under_its_prefix(self):
        assert fixed_destination("cloud_storage", S3) == (
            "https://acme-gateway-logs.s3.eu-west-1.amazonaws.com/agentcc/production"
        )
        no_prefix = {**S3, "prefix": ""}
        assert fixed_destination("cloud_storage", no_prefix) == (
            "https://acme-gateway-logs.s3.eu-west-1.amazonaws.com/"
        )

    def test_other_storage_and_queues_name_their_container_or_queue(self):
        azure = {
            "sub_platform": "azure_blob",
            "container": "logs",
            "prefix": "gw",
            "connection_string": "DefaultEndpointsProtocol=https;AccountName=acme;AccountKey=x",
        }
        assert fixed_destination("cloud_storage", azure) == (
            "https://acme.blob.core.windows.net/logs/gw"
        )
        gcs = {"sub_platform": "gcs", "bucket": "acme-logs", "prefix": "gw"}
        assert fixed_destination("cloud_storage", gcs) == (
            "https://storage.googleapis.com/acme-logs/gw"
        )
        sqs = {
            "sub_platform": "sqs",
            "queue_url": "https://sqs.us-east-1.amazonaws.com/123/gw",
        }
        assert fixed_destination("message_queue", sqs) == sqs["queue_url"]
        pubsub = {"sub_platform": "pubsub", "topic_path": "projects/p/topics/gw"}
        assert fixed_destination("message_queue", pubsub) == (
            "https://pubsub.googleapis.com/v1/projects/p/topics/gw"
        )

    def test_alert_and_issue_platforms_name_their_api(self):
        assert fixed_destination("pagerduty", {}) == "https://events.pagerduty.com"
        assert fixed_destination("linear", {}) == "https://api.linear.app"

    def test_entered_address_platforms_have_none(self):
        assert fixed_destination("posthog", {"api_key": "phc"}) is None
        assert fixed_destination("langfuse", {}) is None


@pytest.mark.django_db
class TestCreateStoresTheDestination:
    def test_datadog_stores_the_chosen_sites_api_host(self, auth_client):
        response, service = _create(
            auth_client,
            _export(
                "datadog",
                {"api_key": "dd-key", "site": "eu1"},
                # A client that sends another host does not change where the
                # connection delivers.
                host_url="https://datadog.com",
            ),
        )
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
        result = _result(response)
        assert result["host_url"] == "https://api.datadoghq.eu"
        assert service.validate_credentials.call_args.kwargs["host_url"] == (
            "https://api.datadoghq.eu"
        )
        assert result["display_name"] == "Datadog"

    def test_mixpanel_stores_its_ingestion_api(self, auth_client):
        response, _ = _create(
            auth_client, _export("mixpanel", {"project_token": "mp-token"})
        )
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
        assert _result(response)["host_url"] == "https://api.mixpanel.com"

    def test_s3_stores_the_bucket_region_and_prefix(self, auth_client):
        response, _ = _create(
            auth_client, _export("cloud_storage", S3, display_name="Amazon S3")
        )
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
        result = _result(response)
        assert result["host_url"] == (
            "https://acme-gateway-logs.s3.eu-west-1.amazonaws.com/agentcc/production"
        )
        assert result["display_name"] == "Amazon S3"

    def test_without_a_display_name_the_platforms_name_is_used(self, auth_client):
        response, _ = _create(auth_client, _export("cloud_storage", S3))
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
        assert _result(response)["display_name"] == "Cloud Storage"

    @pytest.mark.parametrize(
        "platform, credentials",
        [
            ("datadog", {"api_key": "dd-key", "site": "us1"}),
            ("mixpanel", {"project_token": "mp-token"}),
            ("cloud_storage", S3),
        ],
    )
    def test_no_connection_stores_an_address_nobody_chose(
        self, auth_client, platform, credentials
    ):
        response, _ = _create(auth_client, _export(platform, credentials))
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
        stored = IntegrationConnection.no_workspace_objects.get(
            id=_result(response)["id"]
        )
        assert stored.host_url != f"https://{platform}.com"
        assert stored.host_url == fixed_destination(platform, credentials)

    def test_posthog_stores_the_address_entered(self, auth_client):
        response, _ = _create(
            auth_client,
            _export(
                "posthog",
                {"api_key": "phc_key"},
                host_url="https://posthog.acme.test",
            ),
        )
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
        assert _result(response)["host_url"] == "https://posthog.acme.test"

    @pytest.mark.parametrize("platform", ["posthog", "langfuse"])
    def test_an_entered_address_platform_without_one_is_refused(
        self, auth_client, platform
    ):
        credentials = (
            {"api_key": "phc_key"}
            if platform == "posthog"
            else {"public_key": "pk", "secret_key": "sk"}
        )
        response, service = _create(auth_client, _export(platform, credentials))
        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        assert "host_url" in json.dumps(response.json())
        service.validate_credentials.assert_not_called()
        assert not IntegrationConnection.no_workspace_objects.filter(
            platform=platform
        ).exists()


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
class TestEditTheDestination:
    def test_a_fixed_destination_has_no_host_to_change(
        self, auth_client, organization, workspace, user
    ):
        connection = _connection(
            organization,
            workspace,
            user,
            "datadog",
            "https://api.datadoghq.eu",
            {"api_key": "dd-key", "site": "eu1"},
        )
        response = auth_client.patch(
            f"{URL}{connection.id}/",
            data=json.dumps({"host_url": "https://elsewhere.example.com"}),
            content_type="application/json",
        )
        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        connection.refresh_from_db()
        assert connection.host_url == "https://api.datadoghq.eu"

    def _patch(self, auth_client, connection, payload, service):
        with patch(
            "integrations.views.integration_connection.get_integration_service",
            return_value=service,
        ):
            return auth_client.patch(
                f"{URL}{connection.id}/",
                data=json.dumps(payload),
                content_type="application/json",
            )

    def _posthog(self, organization, workspace, user):
        return _connection(
            organization,
            workspace,
            user,
            "posthog",
            "https://posthog.acme.test",
            {"api_key": "phc_key"},
        )

    def test_a_posthog_host_is_checked_then_saved(
        self, auth_client, organization, workspace, user
    ):
        connection = self._posthog(organization, workspace, user)
        service = _valid_service()
        response = self._patch(
            auth_client, connection, {"host_url": "https://eu.i.posthog.com"}, service
        )
        assert response.status_code == http_status.HTTP_200_OK, response.json()
        service.validate_credentials.assert_called_once_with(
            host_url="https://eu.i.posthog.com",
            credentials={"api_key": "phc_key"},
            ca_certificate=None,
        )
        connection.refresh_from_db()
        assert connection.host_url == "https://eu.i.posthog.com"

    def test_a_posthog_host_the_check_refuses_is_not_saved(
        self, auth_client, organization, workspace, user
    ):
        connection = self._posthog(organization, workspace, user)
        service = MagicMock()
        service.validate_credentials.return_value = {
            "valid": False,
            "error": "PostHog rejected the project API key.",
        }
        response = self._patch(
            auth_client,
            connection,
            {"host_url": "https://posthog.elsewhere.test", "display_name": "Renamed"},
            service,
        )
        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        assert "PostHog rejected the project API key." in json.dumps(response.json())
        connection.refresh_from_db()
        assert (connection.host_url, connection.display_name) == (
            "https://posthog.acme.test",
            "PostHog",
        )

    def test_an_edit_that_keeps_the_host_checks_nothing(
        self, auth_client, organization, workspace, user
    ):
        connection = self._posthog(organization, workspace, user)
        service = _valid_service()
        response = self._patch(
            auth_client,
            connection,
            {"host_url": "https://posthog.acme.test", "display_name": "Renamed"},
            service,
        )
        assert response.status_code == http_status.HTTP_200_OK, response.json()
        service.validate_credentials.assert_not_called()
        connection.refresh_from_db()
        assert connection.display_name == "Renamed"
