"""What a Mixpanel connection sends, and what its sync log says about it.

With a project token only, request logs go to /track, which answers 1 when it
took the batch and 0 when it took none (https://docs.mixpanel.com/reference/
track-event); a 0 fails the sync, so Sync History reads failed. /track takes
the last 5 days only, so a connection without an API secret cannot start from
an older date. With the API secret, the connection check and the export go to
/import, strict, authenticated by the secret (https://docs.mixpanel.com/
reference/import-events), and each event carries the $insert_id Mixpanel
requires and deduplicates on. Every HTTP call is patched: nothing is sent.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
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
from integrations.services.mixpanel_service import MixpanelService

TRACK = "https://api.mixpanel.com/track"


def _insert_id(request_id):
    from integrations.services.mixpanel_service import _insert_id as derive

    return derive(request_id)


def _rejected():
    from integrations.services.mixpanel_service import MixpanelRejected

    return MixpanelRejected


IMPORT = "https://api.mixpanel.com/import"


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("INTEGRATIONS_MIXPANEL_URL", raising=False)


def _answer(status=200, text="1"):
    response = MagicMock(status_code=status, text=text)
    response.raise_for_status.return_value = None
    return response


EVENTS = [
    {
        "event": "agentcc_request",
        "time": 1700000000,
        "properties": {"request_id": "r-1"},
    },
    {
        "event": "agentcc_request",
        "time": 1700000001,
        "properties": {"request_id": "r-2"},
    },
]


class TestTrack:
    @patch("integrations.services.mixpanel_service.requests.post")
    def test_a_batch_track_takes_is_sent(self, post):
        post.return_value = _answer(text="1")
        assert MixpanelService().export_events({"project_token": "t"}, EVENTS) == {
            "sent": 2
        }
        assert post.call_args.args[0] == TRACK
        sent = post.call_args.kwargs["json"]
        assert [e["properties"]["token"] for e in sent] == ["t", "t"]
        assert all("$insert_id" in e["properties"] for e in sent)
        assert [e["properties"]["$insert_id"] for e in sent] == [
            _insert_id("r-1"),
            _insert_id("r-2"),
        ]

    @pytest.mark.parametrize("body", ["0", " 0\n", ""])
    @patch("integrations.services.mixpanel_service.requests.post")
    def test_a_batch_track_takes_none_of_fails(self, post, body):
        post.return_value = _answer(text=body)
        with pytest.raises(
            _rejected(), match="accepted none of the events in a batch of 2"
        ):
            MixpanelService().export_events({"project_token": "t"}, EVENTS)


class TestImport:
    @patch("integrations.services.mixpanel_service.requests.post")
    def test_the_secret_sends_a_strict_import(self, post):
        post.return_value = _answer(
            text='{"code": 200, "num_records_imported": 2, "status": "OK"}'
        )
        MixpanelService().export_events(
            {"project_token": "t", "api_secret": "s3cret"}, EVENTS
        )
        assert post.call_args.args[0] == IMPORT
        assert "params" in post.call_args.kwargs
        assert post.call_args.kwargs["params"] == {"strict": "1"}
        auth = post.call_args.kwargs["headers"]["Authorization"]
        assert auth == "Basic " + base64.b64encode(b"s3cret:").decode()
        for event in post.call_args.kwargs["json"]:
            props = event["properties"]
            assert props["token"] == "t"
            assert props["distinct_id"] == "agentcc-gateway"
            assert isinstance(props["time"], int)
            assert len(props["$insert_id"]) <= 36
            assert props["$insert_id"].isalnum()

    def test_insert_id_is_the_same_for_the_same_request(self):
        assert _insert_id("r-1") == _insert_id("r-1")
        assert _insert_id("r-1") != _insert_id("r-2")

    @patch("integrations.services.mixpanel_service.requests.post")
    def test_the_check_with_a_secret_goes_to_import(self, post):
        post.return_value = _answer(200, text='{"code": 200, "status": "OK"}')
        result = MixpanelService().validate_credentials(
            "", {"project_token": "t", "api_secret": "s3cret"}
        )
        assert result["valid"] is True
        assert post.call_args.args[0] == IMPORT
        assert "params" in post.call_args.kwargs
        assert post.call_args.kwargs["params"] == {"strict": "1"}
        assert post.call_args.kwargs["json"][0]["event"] == "$agentcc_connection_test"

    @patch("integrations.services.mixpanel_service.requests.post")
    def test_a_wrong_secret_fails_the_check(self, post):
        post.return_value = _answer(401, text='{"error": "Invalid credentials"}')
        result = MixpanelService().validate_credentials(
            "", {"project_token": "t", "api_secret": "wrong"}
        )
        assert result == {
            "valid": False,
            "error": "Invalid API secret. Please check your Mixpanel project secret.",
        }


@pytest.mark.django_db(transaction=True)
class TestSyncLog:
    def _connection(self, organization, workspace, user, credentials):
        from agentcc.models import AgentccRequestLog

        AgentccRequestLog.no_workspace_objects.bulk_create(
            [
                AgentccRequestLog(
                    organization=organization,
                    workspace=None,
                    request_id="r-mp-1",
                    model="gpt-4o-mini",
                    provider="openai",
                    status_code=200,
                    started_at=datetime.now(UTC) - timedelta(minutes=5),
                )
            ]
        )
        return IntegrationConnection.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            created_by=user,
            platform=IntegrationPlatform.MIXPANEL,
            display_name="Mixpanel",
            host_url="https://api.mixpanel.com",
            encrypted_credentials=CredentialManager.encrypt(credentials),
            external_project_name="mixpanel",
            status=ConnectionStatus.BACKFILLING,
            backfill_completed=False,
        )

    def _sync(self, connection, response):
        from integrations.temporal.activities import sync_integration_connection

        with patch(
            "integrations.services.mixpanel_service.requests.post",
            return_value=response,
        ):
            sync_integration_connection(str(connection.id))
        return SyncLog.objects.get(connection=connection)

    def test_a_track_zero_reads_failed(self, organization, workspace, user):
        connection = self._connection(
            organization, workspace, user, {"project_token": "t"}
        )
        log = self._sync(connection, _answer(text="0"))
        assert log.status == SyncStatus.FAILED
        assert log.error_message == (
            "Mixpanel accepted none of the request logs in a batch (check the "
            "project token). Replace it in Edit, then Sync Now."
        )
        assert log.error_details == {
            "kind": "events_refused",
            "type": "MixpanelRejected",
            "http_status": None,
            "vendor_code": None,
        }
        assert log.traces_fetched == 0
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.ERROR
        assert connection.total_traces_synced == 0

    def test_a_track_one_reads_success(self, organization, workspace, user):
        connection = self._connection(
            organization, workspace, user, {"project_token": "t"}
        )
        log = self._sync(connection, _answer(text="1"))
        assert (log.status, log.traces_fetched) == (SyncStatus.SUCCESS, 1)


@pytest.mark.django_db
class TestTrackStartsWithinFiveDays:
    URL = "/integrations/connections/"

    def _create(self, auth_client, credentials, days_ago):
        service = MagicMock()
        service.validate_credentials.return_value = {"valid": True, "projects": []}
        payload = {
            "platform": "mixpanel",
            "credentials": credentials,
            "backfill_option": "from_date",
            "backfill_from_date": (
                datetime.now(UTC) - timedelta(days=days_ago)
            ).isoformat(),
        }
        with (
            patch(
                "integrations.views.integration_connection.get_integration_service",
                return_value=service,
            ),
            patch("integrations.temporal.activities.start_backfill_workflow"),
        ):
            return auth_client.post(
                self.URL, data=json.dumps(payload), content_type="application/json"
            )

    def test_an_older_start_without_the_secret_is_refused(self, auth_client):
        response = self._create(auth_client, {"project_token": "t"}, days_ago=6)
        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        assert "last 5 days" in json.dumps(response.json())
        assert not IntegrationConnection.no_workspace_objects.filter(
            platform="mixpanel"
        ).exists()

    def test_a_start_within_five_days_is_kept(self, auth_client):
        response = self._create(auth_client, {"project_token": "t"}, days_ago=2)
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()

    def test_any_start_with_the_secret_is_kept(self, auth_client):
        response = self._create(
            auth_client, {"project_token": "t", "api_secret": "s"}, days_ago=30
        )
        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
