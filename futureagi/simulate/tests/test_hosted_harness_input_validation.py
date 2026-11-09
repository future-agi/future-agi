from __future__ import annotations

import copy
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from simulate.models import (
    HostedHarnessConversationTranscript,
    HostedHarnessJob,
    HostedHarnessReceipt,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.serializers.hosted_harness import HarnessResultReceiptSerializer
from simulate.services.hosted_harness import (
    HostedHarnessError,
    canonical_digest,
    create_hosted_job,
    register_attempt,
    request_cancellation,
)
from simulate.services.hosted_harness_conversation import (
    append_provider_transcript,
    enqueue_message,
    ingest_conversation_events,
    issue_conversation_capability,
    load_provider_transcript,
)
from simulate.services.hosted_harness_gateway import HostedHarnessGateway
from simulate.services.hosted_harness_ingestion import ingest_artifact, ingest_result_receipt
from simulate.tests.test_hosted_harness_channels import _headers as attempt_headers
from simulate.tests.test_hosted_harness_conversation import BASE, _headers, _payload

pytestmark = pytest.mark.django_db


@pytest.fixture
def conversation_channel(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="input-validation"
    )
    conversation, command, _ = enqueue_message(
        job, content="Synthetic request", client_request_id="input-validation"
    )
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="synthetic-sandbox",
        attempt=None,
        ttl_seconds=600,
    )
    return conversation, command, capability


def _event(conversation, sequence, payload):
    event = {
        "schema_version": "futureagi.harness-conversation-event.v1",
        "event_id": f"validation-event-{sequence}",
        "conversation_id": str(conversation.id),
        "sequence": sequence,
        "kind": "tool_result",
        "emitted_at": "2026-01-02T03:04:05Z",
        "payload": payload,
    }
    try:
        event["digest"] = canonical_digest(event)
    except UnicodeEncodeError:
        # Invalid surrogate input must be rejected before attempting UTF-8 hashing.
        event["digest"] = "sha256:" + "0" * 64
    return event


BAD_JSON = [
    {"nested": [{"text": "private-transcript\x00"}]},
    {"nested": [{"private-key\x00": "text"}]},
    {"nested": [{"text": "private-transcript\ud800"}]},
    {"nested": [{"private-key\udfff": "text"}]},
]


@pytest.mark.parametrize("bad", BAD_JSON)
@pytest.mark.parametrize("via_api", [False, True])
def test_transcript_rejects_unstorable_json_before_writes(
    conversation_channel, bad, via_api
):
    conversation, _, capability = conversation_channel
    entries = [{"type": "user", "uuid": "valid", "text": "hello"},
               {"type": "tool", **bad}]
    original = copy.deepcopy(entries)
    if via_api:
        response = APIClient().generic(
            "POST", f"{BASE}/{conversation.id}/session-store/append/",
            json.dumps({"project_key": "project", "session_id": "session", "entries": entries}),
            content_type="application/json", **_headers(capability),
        )
        assert response.status_code == 400, response.content
        assert response.json()["retryable"] is False
        assert "private-" not in response.content.decode()
        assert "invalid_jsonb_string" in response.content.decode()
    else:
        with pytest.raises(HostedHarnessError) as error:
            append_provider_transcript(
                conversation, project_key="project", provider_session_id="session",
                subpath="", entries=entries,
            )
        assert error.value.status_code == 400
        assert error.value.code == "invalid_jsonb_string"
    assert entries == original
    assert not HostedHarnessConversationTranscript.no_workspace_objects.filter(
        conversation=conversation
    ).exists()


@pytest.mark.parametrize("bad", BAD_JSON)
@pytest.mark.parametrize("via_api", [False, True])
def test_event_batch_rejects_unstorable_json_without_acknowledging(
    conversation_channel, bad, via_api
):
    conversation, command, capability = conversation_channel
    events = [_event(conversation, 1, {"text": "hello"}), _event(conversation, 2, bad)]
    original = copy.deepcopy(events)
    if via_api:
        response = APIClient().generic(
            "POST", f"{BASE}/{conversation.id}/events/",
            json.dumps({"schema_version": events[0]["schema_version"],
                        "acknowledged_through": 1, "events": events}),
            content_type="application/json", **_headers(capability),
        )
        assert response.status_code == 400, response.content
        assert response.json()["retryable"] is False
        assert "private-" not in response.content.decode()
    else:
        with pytest.raises(HostedHarnessError) as error:
            ingest_conversation_events(conversation, acknowledged_through=1, events=events)
        assert error.value.status_code == 400
    conversation.refresh_from_db()
    command.refresh_from_db()
    capability.lease.refresh_from_db()
    assert conversation.event_acked_through == 0
    assert conversation.command_acked_through == 0
    assert capability.lease.event_watermark == 0
    assert command.state == "queued"
    assert not conversation.events.exists()
    assert events == original
    corrected = _event(conversation, 1, {"text": "binary output encoded as AA=="})
    assert ingest_conversation_events(
        conversation, acknowledged_through=1, events=[corrected]
    ) == {"acked_through_sequence": 1}


def test_valid_unicode_and_literal_escape_round_trip_and_replay(conversation_channel):
    conversation, _, _ = conversation_channel
    payload = {"nested": [{"文😀": "café 😀 \\u0000"}]}
    entry = {"type": "tool", "uuid": "unicode-entry", **payload}
    key = {"project_key": "project", "provider_session_id": "session", "subpath": ""}
    assert append_provider_transcript(conversation, **key, entries=[entry]) == {"appended": 1}
    assert append_provider_transcript(conversation, **key, entries=[entry]) == {"appended": 0}
    assert load_provider_transcript(conversation, **key)["entries"] == [entry]
    event = _event(conversation, 1, payload)
    original = copy.deepcopy(event)
    for _ in range(2):
        assert ingest_conversation_events(
            conversation, acknowledged_through=1, events=[event]
        ) == {"acked_through_sequence": 1}
    assert event == original
    assert conversation.events.get().payload == payload
    changed = _event(conversation, 1, {"text": "different"})
    with pytest.raises(HostedHarnessError) as error:
        ingest_conversation_events(conversation, acknowledged_through=1, events=[changed])
    assert error.value.code == "conversation_event_conflict"
    wrong_digest = {**_event(conversation, 2, payload), "digest": "sha256:" + "0" * 64}
    with pytest.raises(HostedHarnessError) as error:
        ingest_conversation_events(conversation, acknowledged_through=1, events=[wrong_digest])
    assert error.value.code == "conversation_event_digest_mismatch"
    with pytest.raises(HostedHarnessError) as error:
        ingest_conversation_events(conversation, acknowledged_through=1,
                                   events=[_event(conversation, 3, payload)])
    assert error.value.code == "conversation_event_out_of_order"


def test_transcript_retention_limit_is_still_atomic(conversation_channel, monkeypatch):
    conversation, _, _ = conversation_channel
    monkeypatch.setattr("simulate.services.hosted_harness_conversation.TRANSCRIPT_ENTRY_LIMIT", 1)
    with pytest.raises(HostedHarnessError) as error:
        append_provider_transcript(
            conversation, project_key="project", provider_session_id="session", subpath="",
            entries=[{"type": "user"}, {"type": "assistant"}],
        )
    assert error.value.status_code == 413
    assert not HostedHarnessConversationTranscript.no_workspace_objects.filter(
        conversation=conversation
    ).exists()


@pytest.fixture
def receipt_channel(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="receipt-validation")
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    url = f"/simulate/api/harness/attempts/{capability.attempt.id}/scenarios/"
    provision = client.post(url, {
        "operation": "provision", "name": "Synthetic receipt", "modality": "text",
        "personas": [{"scenario_key": "one", "name": "Customer",
                      "situation": "Needs help", "outcome": "Gets help"}],
    }, format="json", **attempt_headers(capability))
    assert provision.status_code == 200, provision.content
    result = provision.json()["result"]
    begin = client.post(url, {"operation": "begin", "run_test_id": result["run_test_id"],
                             "scenario_keys": ["one"]},
                        format="json", **attempt_headers(capability))
    assert begin.status_code == 200, begin.content
    body = {
        "schema_version": "futureagi.harness-result.v1", "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id), "attempt_number": 1,
        "scenario_key": "one", "scenario_id": result["scenarios"][0]["scenario_id"],
        "scenario_attempt": 1, "world_index": 0, "status": "passed",
        "sub_goals": [], "evaluations": [], "failure": None,
        "call": {"started_at": "2026-01-02T03:04:05Z", "ended_at": "2026-01-02T03:04:15Z",
                 "duration_ms": 10000, "turns": 2, "recording_artifacts": []},
    }
    return job, capability, body


@pytest.mark.parametrize("timestamp_mode", ["utc", "offset", "serializer"])
def test_receipt_replay_normalizes_timestamps_before_chat_arithmetic(receipt_channel, timestamp_mode):
    job, capability, body = receipt_channel
    content = json.dumps({"messages": [
        {"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Thanks"}, {"role": "assistant", "content": "Welcome"},
    ]}).encode()
    import hashlib

    sha = hashlib.sha256(content).hexdigest()
    storage = MagicMock()
    with patch("simulate.services.hosted_harness_ingestion.get_storage_client", return_value=storage):
        ingest_artifact(capability.attempt, digest=sha, kind="transcript", size=len(content),
                        stream=io.BytesIO(content), content_type="application/json", scenario_key="one")
    body["call"]["transcript_artifact"] = f"sha256:{sha}"
    if timestamp_mode == "offset":
        body["call"].update(started_at="2026-01-02T05:04:05+02:00", ended_at="2026-01-02T05:04:15+02:00")
    body["digest"] = canonical_digest(body)
    original = copy.deepcopy(body)
    projected = body
    if timestamp_mode == "serializer":
        serializer = HarnessResultReceiptSerializer(data=body)
        serializer.is_valid(raise_exception=True)
        projected = serializer.validated_data
    storage.get_object.return_value.read.return_value = content
    with patch("simulate.services.hosted_harness_ingestion.get_storage_client", return_value=storage):
        receipt, created = ingest_result_receipt(capability.attempt, projected, digest_body=body, replay_usage=False)
        assert created
        replay, created = ingest_result_receipt(capability.attempt, projected, digest_body=body, replay_usage=False)
        assert not created
    assert replay.id == receipt.id
    assert receipt.digest == original["digest"]
    assert body == original
    call = job.scenario_registrations.get().call_execution
    assert call.started_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert call.completed_at == call.ended_at == datetime(2026, 1, 2, 3, 4, 15, tzinfo=UTC)
    rows = list(ChatMessageModel.objects.filter(call_execution=call).order_by("created_at"))
    assert len(rows) == 4
    assert rows[0].created_at == rows[1].created_at == call.started_at
    assert rows[2].created_at == rows[3].created_at
    assert (rows[2].created_at - rows[0].created_at).total_seconds() == 1


@pytest.mark.parametrize("field", ["started_at", "ended_at"])
@pytest.mark.parametrize("invalid", [None, "invalid-private-value", "2026-01-02T03:04:05", "2026-99-99T00:00:00Z", 123])
def test_receipt_rejects_invalid_timestamps_before_writes(receipt_channel, field, invalid):
    job, capability, body = receipt_channel
    if invalid is None:
        del body["call"][field]
    else:
        body["call"][field] = invalid
    body["digest"] = canonical_digest(body)
    with pytest.raises(HostedHarnessError) as error:
        ingest_result_receipt(capability.attempt, body, replay_usage=False)
    assert error.value.status_code == 400
    assert error.value.code == "invalid_receipt_timestamp"
    assert "private-value" not in str(error.value)
    assert not HostedHarnessReceipt.no_workspace_objects.filter(job=job).exists()
    assert not ChatMessageModel.objects.filter(call_execution=job.scenario_registrations.get().call_execution).exists()


@pytest.mark.parametrize("reason", ["unknown", "x" * 32, "x" * 33, "", None, ["user_canceled"]])
@pytest.mark.parametrize("terminal", [False, True])
def test_invalid_cancellation_is_rejected_without_state_changes(organization, reason, terminal):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="cancel-validation")
    if terminal:
        job.state = HostedHarnessJob.State.COMPLETED
        job.save(update_fields=["state"])
    initial_state = job.state
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = SimpleNamespace(name="synthetic")
    for cancel in (lambda: request_cancellation(job, reason), lambda: gateway.cancel(job, reason=reason)):
        with pytest.raises(HostedHarnessError) as error:
            cancel()
        assert error.value.status_code == 400
        assert error.value.code == "invalid_cancellation_reason"
        job.refresh_from_db()
        assert job.state == initial_state
        assert job.cancel_requested_at is None
        assert job.cancel_reason is None


@pytest.mark.parametrize("reason", ["user_canceled", "ttl_exceeded", "environment_deleted", "fix_ended"])
def test_supported_cancellation_persists_job_and_attempt_reason(organization, reason):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="valid-cancel")
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = SimpleNamespace(name="synthetic")
    with patch("simulate.services.phone_telephony.cleanup_hosted_phone_rooms"):
        gateway.cancel(job, reason=reason)
        gateway.cancel(job, reason=reason)
    job.refresh_from_db()
    capability.attempt.refresh_from_db()
    assert job.cancel_reason == capability.attempt.terminal_reason == reason
    assert job.state == HostedHarnessJob.State.CANCELED
