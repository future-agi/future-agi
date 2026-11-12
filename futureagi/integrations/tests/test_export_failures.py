"""A failed export says what happened, where, and what to do next.

The connection's page, card and Sync History show one plain sentence, never
the raw exception; the sync log's error_details, in storage and through the
sync-logs API, holds the failure's kind, type and HTTP status or vendor code,
never the exception's text or traceback. A failure is sorted by the
exception's type and HTTP status, never by digits in its text, so a Mixpanel
batch of 401 or 429 events refused by /track reads as Mixpanel's refusal.
Every vendor call is patched: nothing is sent.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)
from integrations.services.credentials import CredentialManager


def describe_export_failure(*args, **kwargs):
    from integrations.services.export_failures import (
        describe_export_failure as describe,
    )

    return describe(*args, **kwargs)


RETRY = "It is retried automatically; Retry tries again now."


def _conn(platform, host_url):
    return SimpleNamespace(
        platform=platform,
        host_url=host_url,
        get_platform_display=lambda: IntegrationPlatform(platform).label,
    )


def _http_error(code, body=None, reason="", url="http://x"):
    response = requests.Response()
    response.status_code = code
    response.reason = reason
    response._content = body.encode() if body else b""
    return requests.exceptions.HTTPError(
        f"{code} Error for url: {url}", response=response
    )


POSTHOG = _conn("posthog", "http://posthog.sink.test")
DATADOG = _conn("datadog", "https://api.datadoghq.eu")
S3 = _conn("cloud_storage", "https://logs.s3.eu-west-1.amazonaws.com/agentcc")


class TestTheSentence:
    @pytest.mark.parametrize(
        ("connection", "exc", "message"),
        [
            (
                POSTHOG,
                requests.exceptions.ConnectionError("HTTPConnectionPool(...) reset"),
                f"Could not reach PostHog. {RETRY}",
            ),
            (
                POSTHOG,
                requests.exceptions.ReadTimeout("read timed out"),
                f"PostHog did not answer in time. {RETRY}",
            ),
            (
                DATADOG,
                _http_error(403),
                "Datadog refused the API key (HTTP 403). Replace it in Edit, then Sync Now.",
            ),
            (
                POSTHOG,
                _http_error(401),
                "PostHog refused the project API key (HTTP 401). Replace it in Edit, "
                "then Sync Now.",
            ),
            (
                DATADOG,
                _http_error(503),
                f"Datadog answered HTTP 503. {RETRY}",
            ),
            (
                POSTHOG,
                _http_error(400, '{"error": "Invalid batch"}'),
                f"PostHog refused the request logs (HTTP 400). {RETRY}",
            ),
            (
                POSTHOG,
                RuntimeError("boom"),
                f"The export to PostHog stopped on an unexpected error. {RETRY}",
            ),
        ],
    )
    def test_names_what_happened_where_and_what_next(self, connection, exc, message):
        failure = describe_export_failure(connection, exc)
        assert (failure.message, failure.rate_limited) == (message, False)

    def test_a_rate_limit_keeps_the_connection_active(self):
        failure = describe_export_failure(DATADOG, _http_error(429))
        assert failure.rate_limited is True
        assert failure.message.startswith("Datadog asked to slow down (HTTP 429).")

    def test_amazon_s3_refusals(self):
        from botocore.exceptions import ClientError, EndpointConnectionError

        def refused(code):
            return ClientError({"Error": {"Code": code, "Message": "x"}}, "PutObject")

        assert describe_export_failure(S3, refused("InvalidAccessKeyId")).message == (
            "Amazon S3 refused the access keys (InvalidAccessKeyId). Replace them in "
            "Edit, then Sync Now."
        )
        assert describe_export_failure(S3, refused("NoSuchBucket")).message == (
            "Amazon S3 has no such bucket. Check the bucket, then Retry."
        )
        unreachable = EndpointConnectionError(endpoint_url="http://storage.test")
        assert describe_export_failure(S3, unreachable).message == (
            f"Could not reach Amazon S3. {RETRY}"
        )


# An endpoint override as the exception text of a failed call names it.
OVERRIDE = "http://override.sink.test:8080/api/v2/logs"


def _s3_refusal(code, status):
    from botocore.exceptions import ClientError

    return ClientError(
        {
            "Error": {"Code": code, "Message": f"refused at {OVERRIDE}"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutObject",
    )


def _details(kind, type_, http_status=None, vendor_code=None):
    return {
        "kind": kind,
        "type": type_,
        "http_status": http_status,
        "vendor_code": vendor_code,
    }


def _error_detail_cases():
    from botocore.exceptions import EndpointConnectionError

    return [
        (
            DATADOG,
            _http_error(403, url=OVERRIDE),
            _details("credentials_refused", "HTTPError", 403),
        ),
        (
            DATADOG,
            _http_error(503, url=OVERRIDE),
            _details("server_error", "HTTPError", 503),
        ),
        (
            POSTHOG,
            requests.exceptions.ConnectionError(f"Max retries with url: {OVERRIDE}"),
            _details("unreachable", "ConnectionError"),
        ),
        (
            S3,
            EndpointConnectionError(endpoint_url=OVERRIDE),
            _details("unreachable", "EndpointConnectionError"),
        ),
        (
            S3,
            _s3_refusal("SignatureDoesNotMatch", 403),
            _details(
                "credentials_refused", "ClientError", 403, "SignatureDoesNotMatch"
            ),
        ),
        (
            S3,
            _s3_refusal(f"Refused {OVERRIDE}", 400),
            _details("upload_refused", "ClientError", 400),
        ),
        (
            POSTHOG,
            RuntimeError(f"stopped at {OVERRIDE}"),
            _details("unexpected", "RuntimeError"),
        ),
    ]


class TestErrorDetails:
    @pytest.mark.parametrize(("connection", "exc", "details"), _error_detail_cases())
    def test_hold_the_kind_and_none_of_the_exception_text(
        self, connection, exc, details
    ):
        stored = describe_export_failure(connection, exc).details(exc)
        assert stored == details
        assert "override.sink.test" not in json.dumps(stored)


def _track_answer(text):
    response = MagicMock(status_code=200, text=text)
    response.raise_for_status.return_value = None
    return response


@pytest.mark.django_db(transaction=True)
class TestAFailedSync:
    def _export(self, organization, workspace, user, platform, host_url, credentials):
        return IntegrationConnection.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            created_by=user,
            platform=platform,
            display_name=IntegrationPlatform(platform).label,
            host_url=host_url,
            encrypted_credentials=CredentialManager.encrypt(credentials),
            external_project_name=platform,
            status=ConnectionStatus.ACTIVE,
            backfill_completed=True,
            last_synced_at=datetime.now(UTC) - timedelta(minutes=10),
        )

    def _logs(self, organization, count):
        from agentcc.models import AgentccRequestLog

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
                for i in range(count)
            ]
        )

    def _sync(self, connection, target, **patched):
        from integrations.temporal.activities import sync_integration_connection

        with patch(target, **patched):
            sync_integration_connection(str(connection.id))
        connection.refresh_from_db()
        return SyncLog.objects.get(connection=connection)

    def test_an_unreachable_destination_reads_plainly(
        self, organization, workspace, user
    ):
        self._logs(organization, 1)
        connection = self._export(
            organization,
            workspace,
            user,
            "posthog",
            "http://posthog.sink.test",
            {"api_key": "phc_key"},
        )
        raw = "HTTPConnectionPool(host='posthog.sink.test', port=80): Max retries"
        log = self._sync(
            connection,
            "integrations.services.posthog_service.requests.post",
            side_effect=requests.exceptions.ConnectionError(raw),
        )
        sentence = f"Could not reach PostHog. {RETRY}"
        assert (connection.status, connection.status_message) == (
            ConnectionStatus.ERROR,
            sentence,
        )
        assert (log.status, log.error_message) == (SyncStatus.FAILED, sentence)
        assert log.error_details == {
            "kind": "unreachable",
            "type": "ConnectionError",
            "http_status": None,
            "vendor_code": None,
        }

    @pytest.mark.parametrize("batch", [401, 429])
    def test_a_mixpanel_refusal_reads_as_mixpanels_whatever_the_batch_size(
        self, batch, organization, workspace, user
    ):
        self._logs(organization, batch)
        connection = self._export(
            organization,
            workspace,
            user,
            "mixpanel",
            "https://api.mixpanel.com",
            {"project_token": "t"},
        )
        log = self._sync(
            connection,
            "integrations.services.mixpanel_service.requests.post",
            return_value=_track_answer("0"),
        )
        sentence = (
            "Mixpanel accepted none of the request logs in a batch (check the "
            "project token). Replace it in Edit, then Sync Now."
        )
        assert (log.status, log.error_message) == (SyncStatus.FAILED, sentence)
        assert (connection.status, connection.status_message) == (
            ConnectionStatus.ERROR,
            sentence,
        )


@pytest.mark.django_db
class TestTheSyncLogsApi:
    def test_answers_no_exception_text_from_a_stored_sync_log(
        self, auth_client, integration_connection
    ):
        # A sync log stored with the exception's text and traceback, as sync
        # logs were before error_details held only its kind.
        log = SyncLog.objects.create(
            connection=integration_connection,
            status=SyncStatus.FAILED,
            started_at=datetime.now(UTC) - timedelta(minutes=1),
            completed_at=datetime.now(UTC),
            error_message="The sync stopped.",
            error_details={
                "error": f"Max retries exceeded with url: {OVERRIDE}",
                "type": "ConnectionError",
                "traceback": f"Traceback (most recent call last):\n{OVERRIDE}",
            },
        )

        detail = auth_client.get(f"/integrations/sync-logs/{log.id}/").json()
        listed = auth_client.get(
            "/integrations/sync-logs/",
            {"connection_id": str(integration_connection.id)},
        ).json()

        detail = detail.get("result", detail)
        (row,) = listed["result"]["sync_logs"]
        assert detail["error_details"] == {"type": "ConnectionError"}
        assert row["error_details"] == {"type": "ConnectionError"}
        assert "override.sink.test" not in json.dumps([detail, row])
