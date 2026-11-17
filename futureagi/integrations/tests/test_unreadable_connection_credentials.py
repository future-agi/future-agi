"""A connection whose saved credentials cannot be read.

Its list row and its page answer 200 with a marker and one sentence and write
nothing. The credentials Edit reconnects it with every key it requires, for
its own platform and where it delivers (an S3 bucket's region and prefix, a
Datadog site), after which both read as any other connection. A connection
whose credentials Edit cannot replace answers the sentence. Every request that
needs the credentials answers the sentence instead of failing, a failed sync
records its own error type, and a readable connection reads as it does now.
"""

import json
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from django.db import connection as db_connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status as http_status

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)
from integrations.services import slack_oauth
from integrations.services.credentials import CredentialManager
from integrations.services.diagnostic_projection import public_status_message
from integrations.services.export_destinations import fixed_destination
from integrations.temporal.activities import sync_integration_connection
from integrations.views.integration_connection import INITIAL_DISPATCH_FAILED

SENTENCE = (
    "This connection's saved credentials can't be read. Reconnect it to use it again."
)
LIST_URL = "/integrations/connections/"
LIST_FIELDS = {
    "id",
    "platform",
    "display_name",
    "host_url",
    "status",
    "status_message",
    "external_project_name",
    "last_synced_at",
    "total_traces_synced",
    "total_spans_synced",
    "total_scores_synced",
    "backfill_completed",
    "backfill_progress",
    "sync_interval_seconds",
    "created_at",
}
DETAIL_FIELDS = {
    "id",
    "platform",
    "display_name",
    "host_url",
    "status",
    "status_message",
    "external_project_name",
    "project",
    "project_name",
    "public_key_display",
    "secret_key_display",
    "last_synced_at",
    "sync_cursor",
    "sync_interval_seconds",
    "last_error_notified_at",
    "backfill_from",
    "backfill_completed",
    "backfill_progress",
    "total_traces_synced",
    "total_spans_synced",
    "total_scores_synced",
    "created_at",
    "updated_at",
    "created_by",
}


def _detail_url(pk):
    return f"/integrations/connections/{pk}/"


def _result(response):
    data = response.json()
    return data.get("result", data)


def _sealed_with_another_key(credentials: dict) -> bytes:
    """Credentials sealed with a key other than the configured one."""
    return Fernet(Fernet.generate_key()).encrypt(json.dumps(credentials).encode())


def _stored(connection):
    row = IntegrationConnection.no_workspace_objects.get(pk=connection.pk)
    return (
        row.status,
        row.status_message,
        bytes(row.encrypted_credentials),
        row.updated_at,
    )


def _connection_updates(queries):
    table = IntegrationConnection._meta.db_table
    return [
        query["sql"]
        for query in queries.captured_queries
        if query["sql"].lstrip().upper().startswith("UPDATE") and table in query["sql"]
    ]


@pytest.fixture
def unreadable_connection(db, organization, workspace, user, int_project):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.LANGFUSE,
        display_name="Unreadable Langfuse",
        host_url="https://langfuse.example.com",
        encrypted_credentials=_sealed_with_another_key(
            {
                "public_key": "pk-lf-placeholder-old",
                "secret_key": "sk-lf-placeholder-old",
            }
        ),
        project=int_project,
        external_project_name="unreadable-project",
        status=ConnectionStatus.ACTIVE,
        sync_interval_seconds=300,
        backfill_completed=True,
    )


@pytest.fixture
def unreadable_slack(db, organization, workspace, user):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.SLACK,
        display_name="Engineering Slack",
        host_url="https://slack.com",
        encrypted_credentials=_sealed_with_another_key(
            {"bot_token": "xoxb-placeholder-old", "team_id": "T-SAME"}
        ),
        external_project_name="T-SAME",
        status=ConnectionStatus.ACTIVE,
        backfill_completed=True,
    )


def _valid_service(mock_get_svc):
    service = MagicMock()
    service.validate_credentials.return_value = {
        "valid": True,
        "projects": [],
        "total_traces": 0,
    }
    mock_get_svc.return_value = service
    return service


S3_HOST = "https://acme-gateway-logs.s3.eu-west-1.amazonaws.com/agentcc/production"
S3_SAVED = {
    "sub_platform": "s3",
    "bucket": "acme-gateway-logs",
    "region": "eu-west-1",
    "access_key_id": "placeholder-access-key-old",
    "secret_access_key": "placeholder-old-secret",
    "prefix": "agentcc/production",
}
S3_NEW_KEYS = {
    "access_key_id": "placeholder-access-key-new",
    "secret_access_key": "placeholder-new-secret",
}
DATADOG_EU_HOST = "https://api.datadoghq.eu"
DATADOG_EU_VALIDATE = "https://api.datadoghq.eu/api/v1/validate"
GCS_HOST = "https://storage.googleapis.com/acme-gateway-logs/agentcc"


def _unreadable_export(organization, workspace, user, platform, host_url, saved):
    """An export in error whose saved credentials were sealed with another key."""
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=platform,
        display_name=IntegrationPlatform(platform).label,
        host_url=host_url,
        encrypted_credentials=_sealed_with_another_key(saved),
        external_project_name=f"unreadable-{platform}",
        status=ConnectionStatus.ERROR,
        status_message="Stored note",
        backfill_completed=True,
    )


def _edit_credentials(auth_client, connection, body):
    return auth_client.patch(
        _detail_url(connection.id),
        data=json.dumps(body),
        content_type="application/json",
    )


@pytest.mark.django_db
@pytest.mark.api
class TestReadingAConnectionWithUnreadableCredentials:
    def test_list_marks_it_and_says_the_sentence(
        self, auth_client, unreadable_connection
    ):
        resp = auth_client.get(LIST_URL)

        assert resp.status_code == http_status.HTTP_200_OK
        rows = _result(resp)["connections"]
        assert len(rows) == 1
        assert rows[0]["id"] == str(unreadable_connection.id)
        assert rows[0].get("credentials_unreadable") is True
        assert rows[0]["status_message"] == SENTENCE

    def test_its_page_marks_it_and_says_the_sentence(
        self, auth_client, unreadable_connection
    ):
        resp = auth_client.get(_detail_url(unreadable_connection.id))

        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result.get("credentials_unreadable") is True
        assert result["status_message"] == SENTENCE
        assert result["public_key_display"] == "****"
        assert result["secret_key_display"] == "****"

    def test_list_and_page_write_nothing(self, auth_client, unreadable_connection):
        before = _stored(unreadable_connection)

        with CaptureQueriesContext(db_connection) as queries:
            listed = auth_client.get(LIST_URL)
            page = auth_client.get(_detail_url(unreadable_connection.id))

        assert listed.status_code == http_status.HTTP_200_OK
        assert page.status_code == http_status.HTTP_200_OK
        assert _result(listed)["connections"][0].get("credentials_unreadable") is True
        assert _result(page).get("credentials_unreadable") is True
        assert _connection_updates(queries) == []
        assert _stored(unreadable_connection) == before

    def test_a_stored_error_note_gives_way_to_the_sentence_and_stays_stored(
        self, auth_client, unreadable_connection
    ):
        IntegrationConnection.no_workspace_objects.filter(
            pk=unreadable_connection.pk
        ).update(status=ConnectionStatus.ERROR, status_message=INITIAL_DISPATCH_FAILED)
        before = _stored(unreadable_connection)

        with CaptureQueriesContext(db_connection) as queries:
            listed = _result(auth_client.get(LIST_URL))["connections"]
            page = _result(auth_client.get(_detail_url(unreadable_connection.id)))

        for body in (listed[0], page):
            assert body["status"] == "error"
            assert body["status_message"] == SENTENCE
            assert body.get("credentials_unreadable") is True
        assert _connection_updates(queries) == []
        assert _stored(unreadable_connection) == before


@pytest.mark.django_db
@pytest.mark.api
class TestReadableConnectionsReadAsBefore:
    def test_a_readable_connection_reads_as_before(
        self, auth_client, integration_connection
    ):
        integration_connection.refresh_from_db()
        listed = _result(auth_client.get(LIST_URL))["connections"]
        page = _result(auth_client.get(_detail_url(integration_connection.id)))

        assert len(listed) == 1
        for body, fields in ((listed[0], LIST_FIELDS), (page, DETAIL_FIELDS)):
            assert body.get("credentials_unreadable") is False
            assert set(body) == fields | {"credentials_unreadable"}
            assert body["status"] == "active"
            assert body["status_message"] == public_status_message(
                integration_connection
            )
        assert page["public_key_display"] == "pk-lf-****7890"
        assert page["secret_key_display"] == "sk-lf-****3210"

    def test_a_mixed_list_marks_only_the_unreadable_connection(
        self, auth_client, integration_connection, unreadable_connection
    ):
        listed = _result(auth_client.get(LIST_URL))["connections"]

        marks = {row["id"]: row.get("credentials_unreadable") for row in listed}
        assert marks == {
            str(integration_connection.id): False,
            str(unreadable_connection.id): True,
        }
        by_id = {row["id"]: row for row in listed}
        assert by_id[str(unreadable_connection.id)]["status_message"] == SENTENCE
        assert by_id[str(integration_connection.id)]["status_message"] != SENTENCE


@pytest.mark.django_db
@pytest.mark.api
class TestReconnectingAConnectionWithUnreadableCredentials:
    @patch("integrations.views.integration_connection.get_integration_service")
    def test_the_credentials_edit_reconnects_it_and_clears_the_sentence(
        self, mock_get_svc, auth_client, unreadable_connection
    ):
        service = _valid_service(mock_get_svc)
        new_credentials = {
            "public_key": "pk-lf-placeholder-new",
            "secret_key": "sk-lf-placeholder-new",
        }

        resp = auth_client.patch(
            _detail_url(unreadable_connection.id),
            data=json.dumps(new_credentials),
            content_type="application/json",
        )

        assert resp.status_code == http_status.HTTP_200_OK
        service.validate_credentials.assert_called_once()
        checked = service.validate_credentials.call_args.kwargs["credentials"]
        assert checked == new_credentials
        unreadable_connection.refresh_from_db()
        stored = bytes(unreadable_connection.encrypted_credentials)
        assert CredentialManager.decrypt(stored) == new_credentials

        listed = _result(auth_client.get(LIST_URL))["connections"]
        assert [row.get("credentials_unreadable") for row in listed] == [False]
        assert listed[0]["status_message"] != SENTENCE
        page = _result(auth_client.get(_detail_url(unreadable_connection.id)))
        assert page.get("credentials_unreadable") is False
        assert page["status_message"] != SENTENCE
        assert page["public_key_display"] == "pk-lf-****-new"

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_reconnecting_a_connection_in_error_makes_it_active(
        self, mock_get_svc, auth_client, unreadable_connection
    ):
        _valid_service(mock_get_svc)
        IntegrationConnection.no_workspace_objects.filter(
            pk=unreadable_connection.pk
        ).update(status=ConnectionStatus.ERROR, status_message="Stored note")

        resp = auth_client.patch(
            _detail_url(unreadable_connection.id),
            data=json.dumps(
                {
                    "public_key": "pk-lf-placeholder-new",
                    "secret_key": "sk-lf-placeholder-new",
                }
            ),
            content_type="application/json",
        )

        assert resp.status_code == http_status.HTTP_200_OK
        result = _result(resp)
        assert result["status"] == "active"
        assert result.get("credentials_unreadable") is False

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_new_credentials_that_do_not_validate_change_nothing(
        self, mock_get_svc, auth_client, unreadable_connection
    ):
        service = MagicMock()
        service.validate_credentials.return_value = {
            "valid": False,
            "error": "Invalid credentials.",
        }
        mock_get_svc.return_value = service
        before = _stored(unreadable_connection)

        resp = auth_client.patch(
            _detail_url(unreadable_connection.id),
            data=json.dumps(
                {
                    "public_key": "pk-lf-placeholder-bad",
                    "secret_key": "sk-lf-placeholder-bad",
                }
            ),
            content_type="application/json",
        )

        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert _result(resp) == "Invalid credentials."
        assert _stored(unreadable_connection) == before


@pytest.mark.django_db
@pytest.mark.api
class TestReconnectingAnExportWithUnreadableCredentials:
    @patch("integrations.views.integration_connection.get_integration_service")
    def test_s3_takes_its_keys_and_keeps_its_bucket_region_and_prefix(
        self, mock_get_svc, auth_client, organization, workspace, user
    ):
        service = _valid_service(mock_get_svc)
        connection = _unreadable_export(
            organization, workspace, user, "cloud_storage", S3_HOST, S3_SAVED
        )

        resp = _edit_credentials(auth_client, connection, {"credentials": S3_NEW_KEYS})

        assert resp.status_code == http_status.HTTP_200_OK, resp.json()
        expected = {**S3_SAVED, **S3_NEW_KEYS}
        checked = service.validate_credentials.call_args.kwargs["credentials"]
        assert checked == expected
        connection.refresh_from_db()
        stored = CredentialManager.decrypt(bytes(connection.encrypted_credentials))
        assert stored == expected
        assert fixed_destination("cloud_storage", stored) == S3_HOST
        assert connection.host_url == S3_HOST
        assert connection.status == ConnectionStatus.ACTIVE
        page = _result(auth_client.get(_detail_url(connection.id)))
        assert page.get("credentials_unreadable") is False
        assert page["status_message"] != SENTENCE

    def test_datadog_on_another_site_is_checked_and_kept_on_that_site(
        self, monkeypatch, auth_client, organization, workspace, user
    ):
        monkeypatch.delenv("INTEGRATIONS_DATADOG_URL", raising=False)
        connection = _unreadable_export(
            organization,
            workspace,
            user,
            "datadog",
            DATADOG_EU_HOST,
            {"api_key": "dd-placeholder-old", "site": "eu1"},
        )

        def datadog_answer(url, **kwargs):
            # The new key is valid on the EU site only.
            return MagicMock(status_code=200 if url == DATADOG_EU_VALIDATE else 403)

        with patch(
            "integrations.services.datadog_service.requests.get",
            side_effect=datadog_answer,
        ) as validated:
            resp = _edit_credentials(
                auth_client,
                connection,
                {"credentials": {"api_key": "dd-placeholder-new"}},
            )

        assert resp.status_code == http_status.HTTP_200_OK, resp.json()
        assert [call.args[0] for call in validated.call_args_list] == [
            DATADOG_EU_VALIDATE
        ]
        connection.refresh_from_db()
        stored = CredentialManager.decrypt(bytes(connection.encrypted_credentials))
        assert stored == {"site": "eu1", "api_key": "dd-placeholder-new"}
        assert connection.host_url == DATADOG_EU_HOST
        assert connection.status == ConnectionStatus.ACTIVE
        page = _result(auth_client.get(_detail_url(connection.id)))
        assert page.get("credentials_unreadable") is False
        assert page["status_message"] != SENTENCE

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_a_partial_key_pair_is_refused_by_field_and_changes_nothing(
        self, mock_get_svc, auth_client, organization, workspace, user
    ):
        service = _valid_service(mock_get_svc)
        connection = _unreadable_export(
            organization, workspace, user, "cloud_storage", S3_HOST, S3_SAVED
        )
        before = _stored(connection)

        resp = _edit_credentials(
            auth_client,
            connection,
            {"credentials": {"access_key_id": "placeholder-access-key-new"}},
        )

        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert _result(resp) == (
            "credentials: Enter access_key_id and secret_access_key to "
            "reconnect this connection."
        )
        service.validate_credentials.assert_not_called()
        assert _stored(connection) == before

    @patch("integrations.views.integration_connection.get_integration_service")
    def test_google_cloud_storage_answers_the_sentence_and_changes_nothing(
        self, mock_get_svc, auth_client, organization, workspace, user
    ):
        service = _valid_service(mock_get_svc)
        connection = _unreadable_export(
            organization,
            workspace,
            user,
            "cloud_storage",
            GCS_HOST,
            {
                "sub_platform": "gcs",
                "bucket": "acme-gateway-logs",
                "service_account_json": "{}",
                "prefix": "agentcc",
            },
        )
        before = _stored(connection)

        resp = _edit_credentials(auth_client, connection, {"credentials": S3_NEW_KEYS})

        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert _result(resp) == SENTENCE
        service.validate_credentials.assert_not_called()
        assert _stored(connection) == before


@pytest.mark.django_db(transaction=True)
class TestSyncHistoryOfAConnectionWithUnreadableCredentials:
    def test_a_failed_sync_records_its_own_error_type(self, unreadable_connection):
        sync_integration_connection(str(unreadable_connection.id))

        log = SyncLog.objects.get(connection=unreadable_connection)
        assert log.status == SyncStatus.FAILED
        assert log.error_details["type"] == "UnreadableCredentials"


@pytest.mark.django_db
@pytest.mark.api
class TestRequestsThatNeedUnreadableCredentials:
    def test_a_new_host_without_new_keys_answers_the_sentence(
        self, auth_client, unreadable_connection
    ):
        before = _stored(unreadable_connection)

        resp = auth_client.patch(
            _detail_url(unreadable_connection.id),
            data=json.dumps({"host_url": "https://langfuse-new.example.com"}),
            content_type="application/json",
        )

        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert _result(resp) == SENTENCE
        assert _stored(unreadable_connection) == before

    @patch("integrations.temporal.activities.sync_integration_connection")
    def test_sync_now_answers_the_sentence_and_starts_nothing(
        self, mock_sync, auth_client, unreadable_connection
    ):
        before = _stored(unreadable_connection)

        resp = auth_client.post(
            f"/integrations/connections/{unreadable_connection.id}/sync_now/"
        )

        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert _result(resp) == SENTENCE
        mock_sync.delay.assert_not_called()
        assert _stored(unreadable_connection) == before

    @patch("integrations.temporal.activities.sync_integration_connection")
    def test_retry_on_a_connection_in_error_answers_the_sentence(
        self, mock_sync, auth_client, unreadable_connection
    ):
        IntegrationConnection.no_workspace_objects.filter(
            pk=unreadable_connection.pk
        ).update(status=ConnectionStatus.ERROR, status_message="Stored note")

        resp = auth_client.post(
            f"/integrations/connections/{unreadable_connection.id}/sync_now/"
        )

        assert resp.status_code == http_status.HTTP_400_BAD_REQUEST
        assert _result(resp) == SENTENCE
        mock_sync.delay.assert_not_called()

    def test_listing_slack_channels_answers_the_sentence(
        self, auth_client, unreadable_slack
    ):
        with patch("integrations.views.slack.SlackService.get_channels") as channels:
            resp = auth_client.get(
                f"/integrations/connections/{unreadable_slack.id}/slack/channels/"
            )

        assert resp.status_code == http_status.HTTP_409_CONFLICT
        assert _result(resp) == SENTENCE
        channels.assert_not_called()


@pytest.mark.django_db
@pytest.mark.api
class TestSlackReconnectWithUnreadableCredentials:
    def test_slack_reconnects_only_to_the_same_workspace(
        self, settings, api_client, organization, workspace, user, unreadable_slack
    ):
        settings.SLACK_CLIENT_ID = "client-id"
        settings.SLACK_CLIENT_SECRET = "client-secret"
        settings.APP_BASE_URL = "https://app.example.com"
        slack = unreadable_slack
        sealed = bytes(slack.encrypted_credentials)

        def reconnect(team_id):
            started = slack_oauth.start_slack_install(
                organization=organization,
                workspace=workspace,
                user=user,
                connection_id=slack.id,
            )
            query = parse_qs(urlparse(started["authorization_url"]).query)
            installed = {
                "bot_token": f"xoxb-placeholder-{team_id.lower()}",
                "team_id": team_id,
                "team_name": "Engineering",
                "bot_user_id": "U-BOT",
                "scope": "channels:read,channels:join,groups:read,chat:write",
            }
            with (
                patch(
                    "integrations.views.slack.exchange_slack_code",
                    return_value=installed,
                ),
                patch(
                    "integrations.views.slack.SlackService.validate_credentials",
                    return_value={"valid": True},
                ),
            ):
                response = api_client.get(
                    "/integrations/slack/callback/",
                    {"state": query["state"][0], "code": "temporary-code"},
                )
            return response, installed

        other, _ = reconnect("T-OTHER")
        assert other.status_code == http_status.HTTP_302_FOUND
        assert "slack=error" in other["Location"]
        slack.refresh_from_db()
        assert bytes(slack.encrypted_credentials) == sealed

        same, installed = reconnect("T-SAME")
        assert same.status_code == http_status.HTTP_302_FOUND
        assert "slack=connected" in same["Location"]
        slack.refresh_from_db()
        stored = bytes(slack.encrypted_credentials)
        assert CredentialManager.decrypt(stored) == installed
        assert slack.status == ConnectionStatus.ACTIVE
