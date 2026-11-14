"""SQS must acknowledge every entry before an export checkpoint can advance."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agentcc.models import AgentccRequestLog
from integrations.models import ConnectionStatus, SyncLog, SyncStatus
from integrations.services.queue_service import QueueService
from integrations.temporal.activities import _sync_export_integration

CREDENTIALS = {
    "sub_platform": "sqs",
    "queue_url": "https://sqs.example.test/queue",
    "access_key_id": "test-key",
    "secret_access_key": "test-secret",
}


def success(*ids):
    return {"Successful": [{"Id": str(i)} for i in ids], "Failed": []}


def failure(entry_id="1", sender_fault=False):
    return {
        "Id": entry_id,
        "Code": "InvalidMessageContents" if sender_fault else "InternalError",
        "SenderFault": sender_fault,
        "Message": "not included in the surfaced error",
    }


@pytest.fixture
def sqs():
    with patch("boto3.client") as client, patch("time.sleep") as sleep:
        yield client.return_value, sleep


def test_sqs_retries_only_failed_entries(sqs):
    client, sleep = sqs
    client.send_message_batch.side_effect = [
        {"Successful": [{"Id": "0"}], "Failed": [failure()]},
        success(1),
    ]
    logs = [{"request_id": "first"}, {"request_id": "second"}]

    assert QueueService().publish_logs(CREDENTIALS, logs) == {"sent": 2}
    calls = client.send_message_batch.call_args_list
    assert [e["Id"] for e in calls[0].kwargs["Entries"]] == ["0", "1"]
    assert calls[1].kwargs["Entries"] == [calls[0].kwargs["Entries"][1]]
    sleep.assert_called_once_with(0.25)


def test_sqs_exhausted_retries_raise(sqs):
    client, sleep = sqs
    client.send_message_batch.return_value = {
        "Successful": [],
        "Failed": [failure("0")],
    }
    with pytest.raises(RuntimeError, match="InternalError"):
        QueueService().publish_logs(CREDENTIALS, [{"request_id": "first"}])
    assert client.send_message_batch.call_count == 4
    assert [c.args[0] for c in sleep.call_args_list] == [0.25, 0.5, 1.0]


def test_sqs_sender_fault_is_not_retried(sqs):
    client, sleep = sqs
    client.send_message_batch.return_value = {
        "Successful": [{"Id": "0"}],
        "Failed": [failure(sender_fault=True)],
    }
    with pytest.raises(RuntimeError, match="InvalidMessageContents") as error:
        QueueService().publish_logs(
            CREDENTIALS, [{"request_id": str(i)} for i in range(2)]
        )
    assert "not included" not in str(error.value)
    client.send_message_batch.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"Successful": [{"Id": "0"}], "Failed": []},
        {"Successful": [{"Id": "0"}, {"Id": "0"}]},
        {"Successful": [{"Id": "unknown"}, {"Id": "1"}]},
        {"Successful": [{"Id": "0"}, {"Id": "1"}], "Failed": [failure()]},
    ],
)
def test_sqs_incomplete_or_inconsistent_acknowledgements_raise(sqs, response):
    client, sleep = sqs
    client.send_message_batch.return_value = response
    with pytest.raises(RuntimeError, match="acknowledgement"):
        QueueService().publish_logs(
            CREDENTIALS, [{"request_id": str(i)} for i in range(2)]
        )
    sleep.assert_not_called()


def test_sqs_batches_ten_entries_and_counts_all(sqs):
    client, sleep = sqs
    client.send_message_batch.side_effect = [success(*range(10)), success(0)]
    assert QueueService().publish_logs(
        CREDENTIALS, [{"request_id": str(i)} for i in range(11)]
    ) == {"sent": 11}
    assert [
        len(c.kwargs["Entries"]) for c in client.send_message_batch.call_args_list
    ] == [10, 1]
    sleep.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("sender_fault", [False, True])
def test_sqs_failed_sync_keeps_checkpoint(integration_connection, sqs, sender_fault):
    connection = integration_connection
    checkpoint = datetime.now(timezone.utc) - timedelta(minutes=5)
    connection.platform = "message_queue"
    connection.last_synced_at = checkpoint
    connection.save(update_fields=["platform", "last_synced_at"])
    for i in range(2):
        AgentccRequestLog.no_workspace_objects.create(
            organization=connection.organization,
            workspace=connection.workspace,
            request_id=f"request-{i}",
            started_at=checkpoint + timedelta(minutes=1),
        )
    client, _ = sqs
    client.send_message_batch.side_effect = [
        {"Successful": [{"Id": "0"}], "Failed": [failure(sender_fault=sender_fault)]},
        *[{"Successful": [], "Failed": [failure(sender_fault=sender_fault)]}] * 3,
    ]
    with (
        patch(
            "integrations.services.credentials.CredentialManager.decrypt",
            return_value=CREDENTIALS,
        ),
        patch(
            "integrations.services.base.get_integration_service",
            return_value=QueueService(),
        ),
    ):
        _sync_export_integration(connection)

    connection.refresh_from_db()
    assert connection.last_synced_at == checkpoint
    assert connection.status == ConnectionStatus.ERROR
    sync_log = SyncLog.objects.get(connection=connection)
    assert sync_log.status == SyncStatus.FAILED
    assert "SQS" in sync_log.error_message
