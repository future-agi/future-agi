import json
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone

from tracer.models.observability_provider import ObservabilityProvider
from tracer.models.trace_investigation import TraceInvestigationAttempt
from tracer.serializers.trace_investigation import (
    ConversationDossierSerializer,
    ConversationEvidenceResponseSerializer,
)
from tracer.services.clickhouse.v2.span_reader import CHSpan
from tracer.services.conversation_evidence import (
    conversation_dossier,
    conversation_evidence_rows,
)
from tracer.services.trace_investigation import (
    InvestigationConflict,
    claim_due_investigations,
    record_trace_notifications,
)
from tracer.tests.test_trace_investigation_control import _configure, _delivery

pytestmark = pytest.mark.django_db

RECORDING = "https://recordings.example.test/call.wav"
RAW_LOG = {
    "call_status": "ended",
    "direction": "outbound",
    "duration_ms": 42500,
    "disconnection_reason": "agent_hangup",
    "agent_id": "agent_1",
    "agent_version": 3,
    "agent_name": "Scheduler",
    "from_number": "+15550100",
    "to_number": "+15550101",
    "retell_llm_dynamic_variables": {
        "greeting_line": "Hello, this is the scheduler.",
        "transfer_phone": "+15550199",
        "callee_phone": "+15550101",
    },
    "collected_dynamic_variables": {"current_node": "voicemail"},
    "call_analysis": {
        "call_summary": "Reached voicemail.",
        "call_successful": False,
        "in_voicemail": True,
        "user_sentiment": "Neutral",
        "custom_analysis_data": {"wrong_person": False},
    },
    "latency": {
        "llm": {"p50": 800, "p90": 1200, "max": 1500, "num": 3, "values": [1, 2, 3]}
    },
    "transcript_with_tool_calls": [
        {
            "role": "node_transition",
            "former_node_name": "start",
            "new_node_name": "greeting",
            "time_sec": 0.1,
            "transition_type": "auto",
        },
        {
            "role": "agent",
            "content": "Hello, this is the scheduler.",
            "words": [
                {"word": "Hello,", "start": 0.5, "end": 0.9},
                {"word": "scheduler.", "start": 2.0, "end": 2.6},
            ],
        },
        {
            "role": "user",
            "content": "Please leave a message.",
            "words": [
                {"word": "Please", "start": 3.0, "end": 3.3},
                {"word": "message.", "start": 4.0, "end": 4.5},
            ],
        },
        {
            "role": "agent",
            "content": "[No response, waiting for the next utterance.]",
            "words": [
                {"word": "[No", "start": 5.0, "end": 5.0},
                {"word": "utterance.]", "start": 5.0, "end": 5.0},
            ],
        },
        {
            "role": "tool_call_invocation",
            "tool_call_id": "call_1",
            "name": "end_call",
            "arguments": "{}",
            "time_sec": 6.2,
            "type": "function",
        },
        {
            "role": "tool_call_result",
            "tool_call_id": "call_1",
            "successful": True,
            "content": "ok",
            "time_sec": 6.4,
        },
        "not a turn",
    ],
}


def _span(*, observation_type="conversation", attrs=None, **overrides) -> CHSpan:
    attrs_string = {
        "gen_ai.system": "retell",
        "raw_log": json.dumps(RAW_LOG),
        "llm.input_messages.0.message.role": "system",
        "llm.input_messages.0.message.content": "Answer in one sentence.",
        "conversation.recording.mono.combined": RECORDING,
        "provider_transcript": "the same transcript again",
    }
    attrs_string.update(attrs or {})
    values = {
        "id": "span-1",
        "project_id": "22222222-2222-2222-2222-222222222222",
        "trace_id": "33333333-3333-3333-3333-333333333333",
        "parent_span_id": "",
        "name": "Call Log",
        "observation_type": observation_type,
        "operation_name": "",
        "start_time": datetime(2026, 9, 11, 10, 0, 0, tzinfo=UTC),
        "end_time": datetime(2026, 9, 11, 10, 0, 42, tzinfo=UTC),
        "latency_ms": 42000,
        "model": "",
        "provider": "openai",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "status": "OK",
        "status_message": "",
        "org_id": None,
        "project_version_id": None,
        "end_user_id": None,
        "trace_session_id": None,
        "prompt_version_id": None,
        "prompt_label_id": None,
        "custom_eval_config_id": None,
        "input": "",
        "output": "",
        "tags": "[]",
        "span_events": "",
        "metadata": "{}",
        "resource_attrs": "{}",
        "attributes_extra": "{}",
        "attrs_string": {key: value for key, value in attrs_string.items() if value},
    }
    values.update(overrides)
    return CHSpan(**values)


def test_dossier_keeps_the_call_once_with_timed_turns_and_tool_activity():
    dossier = conversation_dossier(_span())

    assert dossier["provider"] == "retell"
    assert dossier["agent_instructions"] == "Answer in one sentence."
    assert dossier["call"] == {
        "status": "ended",
        "direction": "outbound",
        "duration_seconds": 42.5,
        "ended_reason": "agent_hangup",
        "agent": {"id": "agent_1", "version": 3, "name": "Scheduler"},
    }
    # A variable that holds the caller's or callee's own number is masked; a
    # configured line and a transfer target pass through.
    assert dossier["variables"] == {
        "configured": {
            "greeting_line": "Hello, this is the scheduler.",
            "transfer_phone": "+15550199",
            "callee_phone": "[number of a call participant]",
        },
        "collected": {"current_node": "voicemail"},
    }
    assert dossier["analysis"] == {
        "summary": "Reached voicemail.",
        "successful": False,
        "in_voicemail": True,
        "sentiment": "Neutral",
        "flags": {"wrong_person": False},
    }
    # Percentiles only: the per-request value list is left out.
    assert dossier["latency_ms"] == {
        "llm": {"p50": 800, "p90": 1200, "max": 1500, "num": 3}
    }
    assert dossier["turns"] == [
        {
            "i": 0,
            "role": "transition",
            "at": 0.1,
            "from": "start",
            "to": "greeting",
            "type": "auto",
        },
        {
            "i": 1,
            "role": "agent",
            "start": 0.5,
            "end": 2.6,
            "text": "Hello, this is the scheduler.",
            "spoken": True,
        },
        {
            "i": 2,
            "role": "user",
            "start": 3.0,
            "end": 4.5,
            "text": "Please leave a message.",
        },
        # Word timings that span no time: the caller never heard this text.
        {
            "i": 3,
            "role": "agent",
            "start": 5.0,
            "end": 5.0,
            "text": "[No response, waiting for the next utterance.]",
            "spoken": False,
        },
        {
            "i": 4,
            "role": "tool_call",
            "at": 6.2,
            "id": "call_1",
            "name": "end_call",
            "arguments": "{}",
        },
        {
            "i": 5,
            "role": "tool_result",
            "at": 6.4,
            "id": "call_1",
            "ok": True,
            "content": "ok",
        },
    ]
    assert dossier["not_included"] == ["provider_log", "recording_audio"]
    # The call's own from and to numbers appear nowhere in the record.
    assert not {"+15550100", "+15550101"} & set(json.dumps(dossier).split('"'))


def test_dossier_carries_the_provider_log_issues_when_the_log_was_read():
    issues = [{"at": 12.5, "level": "error", "message": "model response timed out"}]
    read = conversation_dossier(
        _span(attrs={"conversation.provider_log.issues": json.dumps(issues)})
    )
    clean = conversation_dossier(
        _span(attrs={"conversation.provider_log.issues": "[]"})
    )
    unreadable = conversation_dossier(
        _span(attrs={"conversation.provider_log.issues": "{not json"})
    )

    assert (read["provider_log_issues"], read["not_included"]) == (
        issues,
        ["recording_audio"],
    )
    # A log that was read and had nothing to report is not the same as no log.
    assert (clean["provider_log_issues"], clean["not_included"]) == (
        [],
        ["recording_audio"],
    )
    assert "provider_log_issues" not in unreadable
    assert unreadable["not_included"] == ["provider_log", "recording_audio"]


def test_agent_turn_without_word_timings_is_not_spoken():
    raw_log = {"transcript_with_tool_calls": [{"role": "agent", "content": "Hi"}]}

    dossier = conversation_dossier(_span(attrs={"raw_log": json.dumps(raw_log)}))

    assert dossier["turns"] == [
        {
            "i": 0,
            "role": "agent",
            "start": None,
            "end": None,
            "text": "Hi",
            "spoken": False,
        }
    ]


def test_dossier_keeps_the_events_that_are_not_agent_or_user_turns():
    transcript = [
        {
            "role": "agent",
            "content": "I am transferring you now.",
            "words": [{"word": "I", "start": 1.0, "end": 2.0}],
        },
        {
            "role": "transfer_target",
            "content": "We do not take this call.",
            "words": [
                {"word": "We", "start": 3.0, "end": 3.2},
                {"word": "call.", "start": 4.0, "end": 4.4},
            ],
        },
        {"role": "dtmf", "digit": "2"},
        {
            "role": "sms",
            "content": "Your code is 1234",
            "multimedia": [{"url": "https://media.example.test/a", "summary": "a map"}],
            "time_sec": 6.5,
        },
        {
            "role": "injected",
            "content": "The caller is a returning customer.",
            "time_sec": 7,
        },
    ]
    raw_log = {"transcript_with_tool_calls": transcript}

    dossier = conversation_dossier(_span(attrs={"raw_log": json.dumps(raw_log)}))

    assert dossier["turns"][1:] == [
        {
            "i": 1,
            "role": "transfer_target",
            "start": 3.0,
            "end": 4.4,
            "text": "We do not take this call.",
        },
        {"i": 2, "role": "dtmf", "at": None, "digit": "2"},
        {
            "i": 3,
            "role": "sms",
            "at": 6.5,
            "text": "Your code is 1234",
            "media": ["a map"],
        },
        {
            "i": 4,
            "role": "injected",
            "at": 7.0,
            "text": "The caller is a returning customer.",
        },
    ]
    # Each event alone is kept too: none is dropped without notice.
    for index, entry in enumerate(transcript):
        alone = {"transcript_with_tool_calls": [entry]}
        (turn,) = conversation_dossier(_span(attrs={"raw_log": json.dumps(alone)}))[
            "turns"
        ]
        assert turn["role"] == entry["role"], index


def test_declared_contract_accepts_a_record_with_every_event_and_missing_values():
    # The same record, as JSON, is checked against the generated validator in
    # frontend/src/api/contracts/__tests__/generated-conversation-evidence-contract.test.js
    raw_log = {
        **RAW_LOG,
        "duration_ms": None,
        "transcript_with_tool_calls": [
            *RAW_LOG["transcript_with_tool_calls"],
            {"role": "agent", "content": "Hi"},
            {"role": "transfer_target", "content": "No.", "words": []},
            {"role": "dtmf", "digit": "2"},
            {"role": "sms", "content": "Code 1234", "multimedia": [{"summary": None}]},
            {"role": "injected", "content": "A returning customer.", "time_sec": 7},
        ],
    }
    dossier = conversation_dossier(
        _span(
            attrs={
                "raw_log": json.dumps(raw_log),
                "llm.input_messages.0.message.role": "user",
                "conversation.provider_log.issues": json.dumps(
                    [{"at": 12.5, "level": "error", "message": "timed out"}]
                ),
            }
        )
    )

    assert dossier["agent_instructions"] is None
    assert dossier["call"]["duration_seconds"] is None
    serializer = ConversationDossierSerializer(data=dossier)
    assert serializer.is_valid(), serializer.errors
    # Every key of the record is declared: validation keeps all of them.
    assert json.loads(json.dumps(serializer.validated_data)) == dossier


def test_transcript_with_an_event_the_record_cannot_keep_has_no_dossier():
    # The investigator then reads the stored span, which holds the event.
    raw_log = {
        "transcript_with_tool_calls": [
            {"role": "agent", "content": "Hi"},
            {"role": "a_role_added_later", "content": "something happened"},
        ]
    }

    assert conversation_dossier(_span(attrs={"raw_log": json.dumps(raw_log)})) is None


@pytest.mark.parametrize(
    "span",
    [
        _span(observation_type="llm"),
        _span(attrs={"gen_ai.system": "another-provider"}),
        _span(attrs={"raw_log": ""}),
        _span(attrs={"raw_log": "{not json"}),
    ],
    ids=[
        "not a conversation",
        "unknown provider",
        "no call payload",
        "unreadable payload",
    ],
)
def test_span_that_is_not_a_readable_provider_call_has_no_dossier(span):
    assert conversation_dossier(span) is None


def test_first_message_that_is_not_a_system_prompt_is_not_taken_as_instructions():
    span = _span(attrs={"llm.input_messages.0.message.role": "user"})

    assert conversation_dossier(span)["agent_instructions"] is None


def _claim(project):
    _configure(project)
    with override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0):
        record_trace_notifications(deliveries=[_delivery(project)])
    (claim,) = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=1
    )["claims"]
    return claim


def _rows(claim, spans, **overrides):
    reader = mock.MagicMock()
    reader.roots_by_trace_ids.return_value = spans
    with mock.patch("tracer.services.conversation_evidence.get_reader") as get_reader:
        get_reader.return_value.__enter__.return_value = reader
        result = conversation_evidence_rows(
            **{
                "attempt_id": claim["attempt_id"],
                "lease_token": claim["lease_token"],
                **overrides,
            }
        )
    return result["rows"], reader


def test_claimed_provider_call_gets_one_evidence_row_in_the_stored_span_shape(
    observe_project,
):
    claim = _claim(observe_project)

    rows, reader = _rows(claim, [_span()])

    # The read stops at the attempt's cutoff: a call log rewritten after the
    # claim is not this attempt's evidence.
    reader.roots_by_trace_ids.assert_called_once_with(
        [str(claim["trace_id"])],
        project_id=str(claim["project_id"]),
        cutoff=TraceInvestigationAttempt.no_workspace_objects.get(
            id=claim["attempt_id"]
        ).read_cutoff,
    )
    assert rows == [
        {
            # The scope the investigator checks on every row comes from the job.
            "project_id": str(claim["project_id"]),
            "trace_id": str(claim["trace_id"]),
            "org_id": str(claim["organization_id"]),
            "id": "span-1",
            "parent_span_id": "",
            "name": "Call Log",
            "observation_type": "conversation",
            "start_time": "2026-09-11T10:00:00+00:00",
            "end_time": "2026-09-11T10:00:42+00:00",
            # Only what the audio tool needs; no copy of the transcript.
            "attrs_string": {"conversation.recording.mono.combined": RECORDING},
            "conversation": conversation_dossier(_span()),
        }
    ]
    # The served rows satisfy the declared response contract.
    assert ConversationEvidenceResponseSerializer(data={"rows": rows}).is_valid()


@pytest.mark.parametrize(
    "spans",
    [[], [_span(), _span(id="span-2")], [_span(observation_type="llm")]],
    ids=["no root span", "more than one root", "not a provider call"],
)
def test_trace_that_is_not_one_provider_call_gets_no_rows(observe_project, spans):
    rows, _reader = _rows(_claim(observe_project), spans)

    assert rows == []


def test_evidence_is_served_only_to_the_live_claim(observe_project):
    claim = _claim(observe_project)

    with pytest.raises(InvestigationConflict, match="attempt is not active"):
        _rows(claim, [_span()], lease_token="another-token")

    TraceInvestigationAttempt.no_workspace_objects.filter(
        id=claim["attempt_id"]
    ).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(InvestigationConflict, match="attempt is not active"):
        _rows(claim, [_span()])


def test_claim_names_conversation_evidence_only_for_a_provider_project(
    observe_project,
):
    assert "evidence_source" not in _claim(observe_project)


def test_claim_for_a_provider_project_asks_for_conversation_evidence(observe_project):
    ObservabilityProvider.no_workspace_objects.create(
        project=observe_project,
        provider="retell",
        organization=observe_project.organization,
        workspace=observe_project.workspace,
    )

    assert _claim(observe_project)["evidence_source"] == "conversation"


@override_settings(INTERNAL_API_SECRET="test-secret")
def test_endpoint_serves_rows_to_the_worker_and_reports_a_dead_claim(
    client, observe_project
):
    claim = _claim(observe_project)
    path = f"/tracer/internal/error-feed-v2/attempts/{claim['attempt_id']}/conversation-evidence/"
    reader = mock.MagicMock()
    reader.roots_by_trace_ids.return_value = [_span()]

    with mock.patch("tracer.services.conversation_evidence.get_reader") as get_reader:
        get_reader.return_value.__enter__.return_value = reader
        served = client.post(
            path,
            {"lease_token": claim["lease_token"]},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-secret",
        )
        refused = client.post(
            path,
            {"lease_token": "another-token"},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-secret",
        )

    assert served.status_code == 200
    assert [row["id"] for row in served.json()["rows"]] == ["span-1"]
    assert refused.status_code == 409
