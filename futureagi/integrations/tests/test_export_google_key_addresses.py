"""A Google Cloud Storage or Pub/Sub export accepts a service account key only
when the key names Google's own sign-in addresses.

The connection check answers any other key with one sentence. The scheduled
upload and publish check the stored key again each time they use it, and the
export stops. For a key that is not accepted nothing is sent and no Google
client is made. Every form of key Google Cloud creates still works: the
current and the older token address, both sign-in pages, and a key that names
googleapis.com as its Google cloud or names none.

Every Google call is patched: nothing is sent.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentcc.models import AgentccRequestLog
from integrations.models import ConnectionStatus, SyncLog, SyncStatus
from integrations.services.cloud_storage_service import CloudStorageService
from integrations.services.queue_service import QueueService
from integrations.temporal.activities import _sync_export_integration

NOT_ACCEPTED_SENTENCE = (
    "Service account JSON is accepted only when it names Google's own sign-in "
    "addresses. Use the key file exactly as Google Cloud created it."
)
USED = "a key that names an address that is not Google's was used to sign in"
WENT_ON = "the export went on with a key that is not accepted"

GCS = {"sub_platform": "gcs", "bucket": "acme-logs", "prefix": "gw"}
PUBSUB = {
    "sub_platform": "pubsub",
    "topic_path": "projects/acme-exports/topics/gw",
    "project_id": "acme-exports",
}
LOGS = [{"request_id": "first"}, {"request_id": "second"}]
# What an export's sentence calls each destination.
DESTINATIONS = {"cloud_storage": "Cloud storage", "message_queue": "Message Queue"}

# A field the key leaves out.
ABSENT = object()


def _key(**changes):
    """A service account key as Google Cloud creates it, as the JSON text a
    person pastes, with ``changes`` made (ABSENT leaves a field out)."""
    key = {
        "type": "service_account",
        "project_id": "acme-exports",
        "client_email": "exporter@acme-exports.iam.gserviceaccount.com",
        "client_id": "100000000000000000001",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "universe_domain": "googleapis.com",
    }
    for field, value in changes.items():
        if value is ABSENT:
            key.pop(field)
        else:
            key[field] = value
    return json.dumps(key)


# Values that are not Google's own, for each field a key gives one in.
TOKEN_ADDRESSES = {
    "over http": "http://oauth2.googleapis.com/token",
    "with an extra path": "https://oauth2.googleapis.com/token/extra",
    "with a port": "https://oauth2.googleapis.com:443/token",
    "with a user name": "https://exporter@oauth2.googleapis.com/token",
    "on a lookalike host": "https://oauth2.googleapis.com.example.test/token",
    "with a trailing slash": "https://oauth2.googleapis.com/token/",
    "with a query": "https://oauth2.googleapis.com/token?next=1",
    "with a fragment": "https://oauth2.googleapis.com/token#top",
    "in another case": "https://oauth2.googleapis.com/TOKEN",
    "on another host": "https://sign-in.example.test/token",
    "that is empty": None,
    "that is a number": 443,
}
SIGN_IN_PAGES = {
    "over http": "http://accounts.google.com/o/oauth2/auth",
    "on a lookalike host": "https://accounts.google.com.example.test/o/oauth2/auth",
}
GOOGLE_CLOUDS = {
    "named example.test": "example.test",
    "named like googleapis.com": "googleapis.com.example.test",
    "with no name": "",
}
NOT_ACCEPTED = [
    pytest.param(field, value, id=f"{name} {why}")
    for field, name, values in (
        ("token_uri", "token address", TOKEN_ADDRESSES),
        ("auth_uri", "sign-in page", SIGN_IN_PAGES),
        ("universe_domain", "Google cloud", GOOGLE_CLOUDS),
    )
    for why, value in values.items()
]

# Every form of key Google Cloud creates.
ACCEPTED = [
    pytest.param({}, id="a key as Google Cloud creates it"),
    pytest.param(
        {"token_uri": "https://accounts.google.com/o/oauth2/token"},
        id="the older token address",
    ),
    pytest.param({"token_uri": ABSENT}, id="no token address"),
    pytest.param(
        {"auth_uri": "https://accounts.google.com/o/oauth2/v2/auth"},
        id="the newer sign-in page",
    ),
    pytest.param({"auth_uri": ABSENT}, id="no sign-in page"),
    pytest.param({"universe_domain": ABSENT}, id="no Google cloud named"),
    pytest.param(
        {"token_uri": ABSENT, "auth_uri": ABSENT, "universe_domain": ABSENT},
        id="no address at all",
    ),
]


def _check_storage(key):
    credentials = {**GCS, "service_account_json": key}
    return CloudStorageService().validate_credentials("", credentials)


def _check_queue(key):
    credentials = {**PUBSUB, "service_account_json": key}
    return QueueService().validate_credentials("", credentials)


def _upload(key):
    credentials = {**GCS, "service_account_json": key}
    return CloudStorageService().export_logs(credentials, LOGS)


def _publish(key):
    credentials = {**PUBSUB, "service_account_json": key}
    return QueueService().publish_logs(credentials, LOGS)


# Each site, with the Google client it makes for a key that is accepted.
CONNECTION_CHECKS = [
    pytest.param(_check_storage, "storage", id="storage connection check"),
    pytest.param(_check_queue, "publisher", id="queue connection check"),
]
EXPORTS = [
    pytest.param(_upload, "storage", id="storage upload"),
    pytest.param(_publish, "publisher", id="queue publish"),
]
SCHEDULED_EXPORTS = [
    pytest.param("cloud_storage", GCS, CloudStorageService, id="storage upload"),
    pytest.param("message_queue", PUBSUB, QueueService, id="queue publish"),
]


@pytest.fixture
def google():
    """Google's sign-in and both clients, patched: nothing is sent."""
    signs_in = "google.oauth2.service_account.Credentials.from_service_account_info"
    with (
        patch(signs_in) as sign_in,
        patch("google.cloud.storage.Client") as storage,
        patch("google.cloud.pubsub_v1.PublisherClient") as publisher,
    ):
        yield SimpleNamespace(
            sign_in=sign_in,
            storage=storage,
            publisher=publisher,
            used=lambda: sign_in.called or storage.called or publisher.called,
        )


def _outcome(site, key):
    """(what the site returned, what it stopped on)."""
    try:
        return site(key), None
    except Exception as stopped:
        return None, stopped


@pytest.mark.parametrize("field,value", NOT_ACCEPTED)
@pytest.mark.parametrize("check,client", CONNECTION_CHECKS)
def test_a_connection_check_answers_a_key_with_another_address_with_one_sentence(
    google, check, client, field, value
):
    result = check(_key(**{field: value}))

    assert not google.used(), USED
    assert result == {"valid": False, "error": NOT_ACCEPTED_SENTENCE}


@pytest.mark.parametrize("field,value", NOT_ACCEPTED)
@pytest.mark.parametrize("export,client", EXPORTS)
def test_an_export_with_a_key_with_another_address_stops_and_sends_nothing(
    google, export, client, field, value
):
    result, stopped = _outcome(export, _key(**{field: value}))

    assert not google.used(), USED
    assert result is None, WENT_ON
    assert str(stopped) == NOT_ACCEPTED_SENTENCE


@pytest.mark.parametrize("changes", ACCEPTED)
@pytest.mark.parametrize("check,client", CONNECTION_CHECKS)
def test_a_connection_check_accepts_every_key_google_cloud_creates(
    google, check, client, changes
):
    key = _key(**changes)

    result = check(key)

    assert result == {"valid": True, "projects": [], "total_traces": 0}
    google.sign_in.assert_called_once_with(json.loads(key))
    made = getattr(google, client)
    made.assert_called_once_with(credentials=google.sign_in.return_value)


@pytest.mark.parametrize("changes", ACCEPTED)
@pytest.mark.parametrize("export,client", EXPORTS)
def test_an_export_sends_with_every_key_google_cloud_creates(
    google, export, client, changes
):
    key = _key(**changes)

    result = export(key)

    assert result["sent"] == len(LOGS)
    google.sign_in.assert_called_once_with(json.loads(key))
    made = getattr(google, client)
    made.assert_called_once_with(credentials=google.sign_in.return_value)


@pytest.mark.django_db
@pytest.mark.parametrize("platform,credentials,service", SCHEDULED_EXPORTS)
def test_a_scheduled_export_with_a_stored_key_with_another_address_stops_in_place(
    integration_connection, google, platform, credentials, service
):
    connection = integration_connection
    checkpoint = datetime.now(UTC) - timedelta(minutes=5)
    connection.platform = platform
    connection.last_synced_at = checkpoint
    connection.save(update_fields=["platform", "last_synced_at"])
    for i in range(2):
        AgentccRequestLog.no_workspace_objects.create(
            organization=connection.organization,
            workspace=connection.workspace,
            request_id=f"request-{i}",
            started_at=checkpoint + timedelta(minutes=1),
        )
    key = _key(token_uri="https://sign-in.example.test/token")
    stored = {**credentials, "service_account_json": key}
    with (
        patch(
            "integrations.services.credentials.CredentialManager.decrypt",
            return_value=stored,
        ),
        patch(
            "integrations.services.base.get_integration_service",
            return_value=service(),
        ),
    ):
        _sync_export_integration(connection)

    assert not google.used(), USED
    connection.refresh_from_db()
    assert connection.status == ConnectionStatus.ERROR
    assert connection.last_synced_at == checkpoint
    sync_log = SyncLog.objects.get(connection=connection)
    assert sync_log.status == SyncStatus.FAILED
    # The sentence the Integrations page and Sync History show for this stop.
    assert sync_log.error_message == (
        f"The export to {DESTINATIONS[platform]} stopped on an unexpected error. "
        "It is retried automatically; Retry tries again now."
    )
