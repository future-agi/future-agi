from __future__ import annotations

import copy
import hashlib
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from simulate.models import (
    CallExecution,
    CallTranscript,
    HostedHarnessConversationTranscript,
    HostedHarnessEvent,
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
from simulate.services.hosted_harness_ingestion import (
    _ingest_hosted_transcript,
    ingest_artifact,
    ingest_event_batch,
    ingest_result_receipt,
)
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
    entries = [
        {"type": "user", "uuid": "valid", "text": "hello"},
        {"type": "tool", **bad},
    ]
    original = copy.deepcopy(entries)
    if via_api:
        response = APIClient().generic(
            "POST",
            f"{BASE}/{conversation.id}/session-store/append/",
            json.dumps(
                {"project_key": "project", "session_id": "session", "entries": entries}
            ),
            content_type="application/json",
            **_headers(capability),
        )
        assert response.status_code == 400, response.content
        assert response.json()["retryable"] is False
        assert "private-" not in response.content.decode()
        assert "invalid_jsonb_string" in response.content.decode()
    else:
        with pytest.raises(HostedHarnessError) as error:
            append_provider_transcript(
                conversation,
                project_key="project",
                provider_session_id="session",
                subpath="",
                entries=entries,
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
            "POST",
            f"{BASE}/{conversation.id}/events/",
            json.dumps(
                {
                    "schema_version": events[0]["schema_version"],
                    "acknowledged_through": 1,
                    "events": events,
                }
            ),
            content_type="application/json",
            **_headers(capability),
        )
        assert response.status_code == 400, response.content
        assert response.json()["retryable"] is False
        assert "private-" not in response.content.decode()
    else:
        with pytest.raises(HostedHarnessError) as error:
            ingest_conversation_events(
                conversation, acknowledged_through=1, events=events
            )
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
    assert append_provider_transcript(conversation, **key, entries=[entry]) == {
        "appended": 1
    }
    assert append_provider_transcript(conversation, **key, entries=[entry]) == {
        "appended": 0
    }
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
        ingest_conversation_events(
            conversation, acknowledged_through=1, events=[changed]
        )
    assert error.value.code == "conversation_event_conflict"
    wrong_digest = {**_event(conversation, 2, payload), "digest": "sha256:" + "0" * 64}
    with pytest.raises(HostedHarnessError) as error:
        ingest_conversation_events(
            conversation, acknowledged_through=1, events=[wrong_digest]
        )
    assert error.value.code == "conversation_event_digest_mismatch"
    with pytest.raises(HostedHarnessError) as error:
        ingest_conversation_events(
            conversation,
            acknowledged_through=1,
            events=[_event(conversation, 3, payload)],
        )
    assert error.value.code == "conversation_event_out_of_order"


@pytest.mark.parametrize("limit", ["TRANSCRIPT_ENTRY_LIMIT", "TRANSCRIPT_BYTES_LIMIT"])
def test_transcript_retention_limit_is_still_atomic(
    conversation_channel, monkeypatch, limit
):
    conversation, _, _ = conversation_channel
    monkeypatch.setattr(f"simulate.services.hosted_harness_conversation.{limit}", 1)
    with pytest.raises(HostedHarnessError) as error:
        append_provider_transcript(
            conversation,
            project_key="project",
            provider_session_id="session",
            subpath="",
            entries=[{"type": "user"}, {"type": "assistant"}],
        )
    assert error.value.status_code == 413
    assert not HostedHarnessConversationTranscript.no_workspace_objects.filter(
        conversation=conversation
    ).exists()


@pytest.fixture
def receipt_channel(organization, workspace):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="receipt-validation",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    url = f"/simulate/api/harness/attempts/{capability.attempt.id}/scenarios/"
    provision = client.post(
        url,
        {
            "operation": "provision",
            "name": "Synthetic receipt",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "one",
                    "name": "Customer",
                    "situation": "Needs help",
                    "outcome": "Gets help",
                }
            ],
        },
        format="json",
        **attempt_headers(capability),
    )
    assert provision.status_code == 200, provision.content
    result = provision.json()["result"]
    begin = client.post(
        url,
        {
            "operation": "begin",
            "run_test_id": result["run_test_id"],
            "scenario_keys": ["one"],
        },
        format="json",
        **attempt_headers(capability),
    )
    assert begin.status_code == 200, begin.content
    body = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "one",
        "scenario_id": result["scenarios"][0]["scenario_id"],
        "scenario_attempt": 1,
        "world_index": 0,
        "status": "passed",
        "sub_goals": [],
        "evaluations": [],
        "failure": None,
        "call": {
            "started_at": "2026-01-02T03:04:05Z",
            "ended_at": "2026-01-02T03:04:15Z",
            "duration_ms": 10000,
            "turns": 2,
            "recording_artifacts": [],
        },
    }
    return job, capability, body


@pytest.mark.parametrize("timestamp_mode", ["utc", "offset", "serializer"])
def test_receipt_replay_normalizes_timestamps_before_chat_arithmetic(
    receipt_channel, timestamp_mode
):
    job, capability, body = receipt_channel
    content = json.dumps(
        {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Thanks"},
                {"role": "assistant", "content": "Welcome"},
            ]
        }
    ).encode()
    sha = hashlib.sha256(content).hexdigest()
    storage = MagicMock()
    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=storage,
    ):
        ingest_artifact(
            capability.attempt,
            digest=sha,
            kind="transcript",
            size=len(content),
            stream=io.BytesIO(content),
            content_type="application/json",
            scenario_key="one",
        )
    body["call"]["transcript_artifact"] = f"sha256:{sha}"
    if timestamp_mode == "offset":
        body["call"].update(
            started_at="2026-01-02T05:04:05+02:00", ended_at="2026-01-02T05:04:15+02:00"
        )
    body["digest"] = canonical_digest(body)
    original = copy.deepcopy(body)
    projected = body
    if timestamp_mode == "serializer":
        serializer = HarnessResultReceiptSerializer(data=body)
        serializer.is_valid(raise_exception=True)
        projected = serializer.validated_data
    storage.get_object.return_value.read.return_value = content
    scheduled_times = []
    bulk_create = ChatMessageModel.objects.bulk_create

    def store_rows(rows, **kwargs):
        # Observe the arithmetic before BaseModel's auto_now_add stamps inserts;
        # the real PostgreSQL insert still runs on both receipt deliveries.
        scheduled_times.append([row.created_at for row in rows])
        return bulk_create(rows, **kwargs)

    with (
        patch(
            "simulate.services.hosted_harness_ingestion.get_storage_client",
            return_value=storage,
        ),
        patch.object(ChatMessageModel.objects, "bulk_create", side_effect=store_rows),
    ):
        receipt, created = ingest_result_receipt(
            capability.attempt, projected, digest_body=body, replay_usage=False
        )
        assert created
        replay, created = ingest_result_receipt(
            capability.attempt, projected, digest_body=body, replay_usage=False
        )
        assert not created
    assert replay.id == receipt.id
    assert receipt.digest == original["digest"]
    assert body == original
    call = job.scenario_registrations.get().call_execution
    assert call.started_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert (
        call.completed_at == call.ended_at == datetime(2026, 1, 2, 3, 4, 15, tzinfo=UTC)
    )
    rows = list(
        ChatMessageModel.objects.filter(call_execution=call).order_by("created_at")
    )
    assert len(rows) == 4
    assert scheduled_times[0] == scheduled_times[1]
    first, reply, second, second_reply = scheduled_times[0]
    assert first == reply == call.started_at
    assert second == second_reply
    assert (second - first).total_seconds() == 1


@pytest.mark.parametrize("field", ["started_at", "ended_at"])
@pytest.mark.parametrize(
    "invalid",
    [None, "invalid-private-value", "2026-01-02T03:04:05", "2026-99-99T00:00:00Z", 123],
)
def test_receipt_rejects_invalid_timestamps_before_writes(
    receipt_channel, field, invalid
):
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
    assert not ChatMessageModel.objects.filter(
        call_execution=job.scenario_registrations.get().call_execution
    ).exists()


@pytest.mark.parametrize("call_data", [[], "invalid-private-value", False, {}])
def test_receipt_rejects_malformed_call_before_writes(receipt_channel, call_data):
    job, capability, body = receipt_channel
    body["call"] = call_data
    body["digest"] = canonical_digest(body)
    with pytest.raises(HostedHarnessError) as error:
        ingest_result_receipt(capability.attempt, body, replay_usage=False)
    assert error.value.status_code == 400
    assert error.value.code == "invalid_receipt_timestamp"
    assert not HostedHarnessReceipt.no_workspace_objects.filter(job=job).exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_transcript_rejects_nonfinite_numbers(conversation_channel, value):
    conversation, _, _ = conversation_channel
    with pytest.raises(HostedHarnessError) as error:
        append_provider_transcript(
            conversation,
            project_key="project",
            provider_session_id="session",
            subpath="",
            entries=[{"type": "tool", "value": value}],
        )
    assert error.value.status_code == 400
    assert error.value.code == "invalid_jsonb_number"
    assert not HostedHarnessConversationTranscript.no_workspace_objects.filter(
        conversation=conversation
    ).exists()


@pytest.mark.parametrize(
    "reason", ["unknown", "x" * 32, "x" * 33, "", None, ["user_canceled"]]
)
@pytest.mark.parametrize("terminal", [False, True])
def test_invalid_cancellation_is_rejected_without_state_changes(
    organization, reason, terminal
):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="cancel-validation"
    )
    if terminal:
        job.state = HostedHarnessJob.State.COMPLETED
        job.save(update_fields=["state"])
    initial_state = job.state
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = SimpleNamespace(name="synthetic")
    for cancel in (
        lambda: request_cancellation(job, reason),
        lambda: gateway.cancel(job, reason=reason),
    ):
        with pytest.raises(HostedHarnessError) as error:
            cancel()
        assert error.value.status_code == 400
        assert error.value.code == "invalid_cancellation_reason"
        job.refresh_from_db()
        assert job.state == initial_state
        assert job.cancel_requested_at is None
        assert job.cancel_reason is None


@pytest.mark.parametrize(
    "reason", ["user_canceled", "ttl_exceeded", "environment_deleted", "fix_ended"]
)
def test_supported_cancellation_persists_job_and_attempt_reason(organization, reason):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="valid-cancel")
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = SimpleNamespace(name="synthetic")
    # Deletion cancellation hides the job once cleanup completes. Repeating the
    # request before that transition must retain the supported machine code.
    request_cancellation(job, reason)
    request_cancellation(job, reason)
    with patch("simulate.services.phone_telephony.cleanup_hosted_phone_rooms"):
        gateway.cancel(job, reason=reason)
    job.refresh_from_db()
    capability.attempt.refresh_from_db()
    assert job.cancel_reason == capability.attempt.terminal_reason == reason
    assert job.state == HostedHarnessJob.State.CANCELED


ATTEMPTS = "/simulate/api/harness/attempts"


@pytest.fixture
def attempt_channel(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="attempt-event-validation"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    return capability


def _attempt_event(capability, sequence, payload):
    attempt = capability.attempt
    event = {
        "event_id": f"attempt-validation-{sequence}",
        "job_id": str(attempt.job_id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "sequence": sequence,
        "emitted_at": "2026-01-02T03:04:05Z",
        "stage": "running",
        "type": "log",
        "payload": payload,
    }
    try:
        event["digest"] = canonical_digest(payload)
    except UnicodeEncodeError:
        # Invalid surrogate input must be rejected before attempting UTF-8 hashing.
        event["digest"] = "sha256:" + "0" * 64
    return event


def _post_attempt_events(capability, events):
    return APIClient().generic(
        "POST",
        f"{ATTEMPTS}/{capability.attempt.id}/events/",
        json.dumps({"schema_version": "futureagi.harness-event.v1", "events": events}),
        content_type="application/json",
        **attempt_headers(capability),
    )


@pytest.mark.parametrize("bad", [{"message": "private-tool \x00 output"}, *BAD_JSON])
@pytest.mark.parametrize("via_api", [False, True])
def test_attempt_event_batch_rejects_unstorable_json_without_acknowledging(
    attempt_channel, bad, via_api
):
    capability = attempt_channel
    attempt = capability.attempt
    control = _attempt_event(capability, 1, {"level": "info", "message": "control"})
    assert ingest_event_batch(attempt, [control]) == {
        "acked_through_sequence": 1,
        "rejected": [],
    }
    attempt.refresh_from_db()
    heartbeat_at = attempt.heartbeat_at
    events = [
        _attempt_event(capability, 2, {"level": "info", "message": "valid"}),
        _attempt_event(capability, 3, {"level": "info", "message": "tool", **bad}),
    ]
    original = copy.deepcopy(events)
    if via_api:
        response = _post_attempt_events(capability, events)
        assert response.status_code == 400, response.content
        assert response.json()["error"] == "invalid_jsonb_string"
        assert response.json()["retryable"] is False
        assert "private-" not in response.content.decode()
    else:
        with pytest.raises(HostedHarnessError) as error:
            ingest_event_batch(attempt, events)
        assert error.value.status_code == 400
        assert error.value.code == "invalid_jsonb_string"
    assert events == original
    attempt.refresh_from_db()
    assert attempt.event_watermark == 1
    assert attempt.heartbeat_at == heartbeat_at
    assert list(
        HostedHarnessEvent.no_workspace_objects.filter(attempt=attempt).values_list(
            "sequence", flat=True
        )
    ) == [1]
    corrected = _attempt_event(
        capability, 2, {"level": "info", "message": "binary output encoded as AA=="}
    )
    assert ingest_event_batch(attempt, [corrected]) == {
        "acked_through_sequence": 2,
        "rejected": [],
    }


def test_attempt_event_batch_stores_valid_unicode_verbatim(attempt_channel):
    capability = attempt_channel
    payload = {"level": "info", "message": "café 😀 \\u0000", "文😀": ["é"]}
    event = _attempt_event(capability, 1, payload)
    response = _post_attempt_events(capability, [event])
    assert response.status_code == 200, response.content
    assert response.json() == {"acked_through_sequence": 1, "rejected": []}
    stored = HostedHarnessEvent.no_workspace_objects.get(attempt=capability.attempt)
    assert stored.accepted is True
    assert stored.payload == payload


def _artifact_storage(objects):
    """Object storage double that serves each uploaded artifact by its digest."""
    storage = MagicMock()

    def get_object(_bucket, object_key):
        response = MagicMock()
        response.read.return_value = objects[object_key.rsplit("/", 1)[-1]]
        return response

    storage.get_object.side_effect = get_object
    return storage


def _upload(capability, objects, content, kind):
    digest = hashlib.sha256(content).hexdigest()
    objects[digest] = content
    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=_artifact_storage(objects),
    ):
        response = APIClient().generic(
            "PUT",
            f"{ATTEMPTS}/{capability.attempt.id}/artifacts/{digest}/",
            content,
            content_type="application/json",
            HTTP_X_ARTIFACT_KIND=kind,
            HTTP_X_ARTIFACT_SIZE=str(len(content)),
            HTTP_X_SCENARIO_KEY="one",
            **attempt_headers(capability),
        )
    assert response.status_code == 201, response.content
    return digest


def _transcript(*contents, tool_calls=None):
    messages = [
        {"role": ("user", "assistant")[index % 2], "content": content}
        for index, content in enumerate(contents)
    ]
    if tool_calls is not None:
        messages[-1]["tool_calls"] = tool_calls
    return json.dumps({"messages": messages}).encode()


UNSTORABLE_TRANSCRIPTS = [
    _transcript("Hello", "private-tool said \x00 here"),
    _transcript("Hello", "private-reply \ud800"),
    _transcript("Hello", "Hi", tool_calls=[{"private-key\x00": "lookup"}]),
    # Older guests upload rendered text rather than JSON.
    b"customer: Hello\nagent: private-raw \x00 reply",
]


def _post_receipt(capability, objects, body, *, via_api):
    body["digest"] = canonical_digest(body)
    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=_artifact_storage(objects),
    ):
        if via_api:
            return APIClient().generic(
                "POST",
                f"{ATTEMPTS}/{capability.attempt.id}/results/",
                json.dumps(body),
                content_type="application/json",
                **attempt_headers(capability),
            )
        return ingest_result_receipt(capability.attempt, body, replay_usage=False)


def _assert_receipt_rejected(capability, objects, body, *, via_api):
    if via_api:
        response = _post_receipt(capability, objects, body, via_api=True)
        assert response.status_code == 400, response.content
        assert response.json()["error"] == "invalid_jsonb_string"
        assert response.json()["retryable"] is False
        assert "private-" not in response.content.decode()
    else:
        with pytest.raises(HostedHarnessError) as error:
            _post_receipt(capability, objects, body, via_api=False)
        assert error.value.status_code == 400
        assert error.value.code == "invalid_jsonb_string"
        assert "private-" not in str(error.value)


def _call_state(call):
    call.refresh_from_db()
    return {
        "status": call.status,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "transcript_available": call.transcript_available,
        "call_metadata": call.call_metadata,
        "provider_call_data": call.provider_call_data,
        "chat_rows": ChatMessageModel.objects.filter(call_execution=call).count(),
        "transcript_rows": CallTranscript.objects.filter(call_execution=call).count(),
    }


@pytest.mark.parametrize("content", UNSTORABLE_TRANSCRIPTS)
@pytest.mark.parametrize("via_api", [False, True])
def test_receipt_rejects_unstorable_transcript_artifact_without_writes(
    receipt_channel, content, via_api
):
    job, capability, body = receipt_channel
    call = job.scenario_registrations.get().call_execution
    before = _call_state(call)
    objects = {}
    rejected_body = copy.deepcopy(body)
    rejected_body["call"]["transcript_artifact"] = (
        f"sha256:{_upload(capability, objects, content, 'transcript')}"
    )
    _assert_receipt_rejected(capability, objects, rejected_body, via_api=via_api)
    assert not HostedHarnessReceipt.no_workspace_objects.filter(job=job).exists()
    assert _call_state(call) == before
    corrected = _transcript("Hello", "binary output encoded as AA==")
    body["call"]["transcript_artifact"] = (
        f"sha256:{_upload(capability, objects, corrected, 'transcript')}"
    )
    _, created = _post_receipt(capability, objects, body, via_api=False)
    assert created
    assert ChatMessageModel.objects.filter(call_execution=call).count() == 2


@pytest.mark.parametrize(
    "line",
    [
        {"name": "lookup", "result": "private-tool said \x00 here"},
        {"name": "lookup", "result": "private-\udfff"},
        {"name": "lookup", "private-key\x00": "result"},
    ],
)
@pytest.mark.parametrize("via_api", [False, True])
def test_receipt_rejects_unstorable_tool_trace_without_writes(
    receipt_channel, line, via_api
):
    job, capability, body = receipt_channel
    call = job.scenario_registrations.get().call_execution
    before = _call_state(call)
    objects = {}
    trace = json.dumps({"name": "lookup", "at": 1}) + "\n" + json.dumps(line) + "\n"
    _upload(capability, objects, trace.encode(), "tool_trace")
    _assert_receipt_rejected(capability, objects, body, via_api=via_api)
    assert not HostedHarnessReceipt.no_workspace_objects.filter(job=job).exists()
    assert _call_state(call) == before


def test_receipt_stores_valid_unicode_transcript_and_tool_trace(receipt_channel):
    job, capability, body = receipt_channel
    text = "café 😀 \\u0000"
    objects = {}
    digest = _upload(capability, objects, _transcript("Hello", text), "transcript")
    body["call"]["transcript_artifact"] = f"sha256:{digest}"
    trace = json.dumps({"name": "lookup", "result": text}).encode()
    _upload(capability, objects, trace, "tool_trace")
    response = _post_receipt(capability, objects, body, via_api=True)
    assert response.status_code == 200, response.content
    call = job.scenario_registrations.get().call_execution
    call.refresh_from_db()
    reply = ChatMessageModel.objects.get(call_execution=call, role="assistant")
    assert reply.messages == [text]
    assert call.provider_call_data["livekit"]["tool_calls"] == [
        {"name": "lookup", "result": text}
    ]


@pytest.mark.parametrize(
    "document",
    [
        {"messages": [{"role": "user", "content": "private-caller \x00 said"}]},
        {"messages": [{"role": "assistant", "content": "private-\ud800"}]},
        {"transcript": "private-raw \x00 text"},
    ],
)
def test_voice_transcript_rejects_unstorable_text_before_replacing_rows(
    receipt_channel, document
):
    job, _, _ = receipt_channel
    call = job.scenario_registrations.get().call_execution
    call.simulation_call_type = CallExecution.SimulationCallType.VOICE
    call.save(update_fields=["simulation_call_type"])
    CallTranscript.objects.create(
        call_execution=call, speaker_role="user", content="earlier transcript"
    )
    response = MagicMock()
    response.read.return_value = json.dumps(document).encode()
    storage = MagicMock()
    storage.get_object.return_value = response
    with (
        patch(
            "simulate.services.hosted_harness_ingestion.get_storage_client",
            return_value=storage,
        ),
        pytest.raises(HostedHarnessError) as error,
    ):
        _ingest_hosted_transcript(call, SimpleNamespace(object_key="transcript.json"))
    assert error.value.status_code == 400
    assert error.value.code == "invalid_jsonb_string"
    assert list(call.transcripts.values_list("content", flat=True)) == [
        "earlier transcript"
    ]
    response.close.assert_called_once_with()
    response.release_conn.assert_called_once_with()
