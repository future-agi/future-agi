"""Replay must consume ClickHouse-only conversations, not propose generic ones."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rest_framework.exceptions import ValidationError

from tracer.models.trace import Trace
from tracer.services.clickhouse import v2
from tracer.utils import replay_session as replay

PROJECT = "10000000-0000-0000-0000-000000000001"
SESSION = "20000000-0000-0000-0000-000000000001"
OTHER_SESSION = "20000000-0000-0000-0000-000000000002"
EARLY = "30000000-0000-0000-0000-000000000001"
LATE = "30000000-0000-0000-0000-000000000002"

pytestmark = pytest.mark.unit


def root(trace_id=EARLY, session_id=SESSION, *, hour=1, **overrides):
    fields = {
        "id": f"root-{trace_id}",
        "project_id": PROJECT,
        "trace_id": trace_id,
        "trace_session_id": session_id,
        "parent_span_id": "",
        "start_time": datetime(2026, 1, 1, hour, tzinfo=UTC),
        "created_at": datetime(2026, 1, 2, tzinfo=UTC),
        "observation_type": "span",
        "input": json.dumps(f"question-{hour}"),
        "output": json.dumps(f"answer-{hour}"),
        "attrs_string": {},
        "attrs_number": {},
        "attrs_bool": {},
        "attributes_extra": "{}",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def sources(monkeypatch):
    # No PostgreSQL mirror: the original implementation returns None, not data.
    empty = MagicMock()
    empty.filter.return_value = empty
    empty.values.return_value = []
    empty.values_list.return_value = []
    monkeypatch.setattr(Trace.objects, "filter", MagicMock(return_value=empty))

    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader.__exit__.return_value = False
    reader.roots_by_session_ids.return_value = []
    reader.roots_by_trace_ids.return_value = []
    reader.root_ids_by_project.return_value = []
    reader.list_by_ids.return_value = []
    reader.distinct_session_ids_with_filters.return_value = []
    reader.list_by_trace_ids.return_value = []
    reader.get_trace_row.return_value = None
    monkeypatch.setattr(v2, "get_reader", lambda: reader)
    return reader


def transcript(result, source_id):
    assert result is not None, "Selected collector conversations must reach generation"
    return json.loads(result[source_id]["transcript"])


def test_collector_sessions_group_and_order_root_conversations(sources):
    early = root(start_time=datetime(2026, 1, 1, 1))  # CH can return naive UTC.
    late = root(LATE, hour=2)
    other = root("30000000-0000-0000-0000-000000000003", OTHER_SESSION, hour=3)
    # The remap-aware reader returns the caller's session id, not its raw alias.
    sources.roots_by_session_ids.return_value = [late, other, early, early]
    result = replay.get_transcripts(PROJECT, "session", [SESSION, OTHER_SESSION])
    assert transcript(result, SESSION) == [
        {"input": "question-1", "output": "answer-1"},
        {"input": "question-2", "output": "answer-2"},
    ]
    assert transcript(result, OTHER_SESSION) == [
        {"input": "question-3", "output": "answer-3"}
    ]
    sources.roots_by_session_ids.assert_called_once_with(
        [SESSION, OTHER_SESSION], project_id=PROJECT
    )
    Trace.objects.filter.assert_not_called()


@pytest.mark.parametrize(
    "wire_input,wire_output,expected_input,expected_output",
    [
        (
            '[{"role":"user","content":"customer question"}]',
            '{"answer":"yes"}',
            [{"role": "user", "content": "customer question"}],
            {"answer": "yes"},
        ),
        ("plain input", "plain output", "plain input", "plain output"),
    ],
    ids=["json", "plain-text"],
)
def test_collector_trace_decodes_root_payloads(
    sources, wire_input, wire_output, expected_input, expected_output
):
    sources.roots_by_trace_ids.return_value = [
        root(input=wire_input, output=wire_output)
    ]
    result = replay.get_transcripts(PROJECT, "trace", [EARLY])
    assert transcript(result, EARLY) == [
        {"input": expected_input, "output": expected_output}
    ]
    sources.roots_by_trace_ids.assert_called_once_with([EARLY], project_id=PROJECT)
    Trace.objects.filter.assert_not_called()


def test_mirrored_trace_content_falls_back_to_scoped_clickhouse_row(sources):
    sources.roots_by_trace_ids.return_value = [root(input="null", output="null")]
    sources.get_trace_row.return_value = {
        "input": json.dumps({"question": "curated input"}),
        "output": json.dumps("curated output"),
    }
    result = replay.get_transcripts(PROJECT, "trace", [EARLY])
    assert transcript(result, EARLY) == [
        {"input": {"question": "curated input"}, "output": "curated output"}
    ]
    sources.get_trace_row.assert_called_once_with(EARLY, project_id=PROJECT)
    Trace.objects.filter.assert_not_called()


@pytest.mark.parametrize("replay_type", ["trace", "session"])
def test_curated_conversation_wins_over_distinct_populated_roots(sources, replay_type):
    early = root(id="root-first")
    late = root(id="root-later", hour=2)
    # Ingestion updates the curated conversation from the later root. Neither
    # root is empty, and the reader's ordering must not decide the conversation.
    sources.roots_by_trace_ids.return_value = [late, early]
    sources.roots_by_session_ids.return_value = [late, early]
    sources.get_trace_row.return_value = {
        "input": late.input,
        "output": late.output,
        # Keep the session identity resolved by roots, not this historical alias.
        "session_id": OTHER_SESSION,
    }
    source_id = EARLY if replay_type == "trace" else SESSION

    result = replay.get_transcripts(PROJECT, replay_type, [source_id])

    assert set(result) == {source_id}
    assert result[source_id]["replay_type"] == replay_type
    assert transcript(result, source_id) == [
        {"input": "question-2", "output": "answer-2"}
    ]
    sources.get_trace_row.assert_called_once_with(EARLY, project_id=PROJECT)
    Trace.objects.filter.assert_not_called()


@pytest.mark.parametrize(
    "curated,expected",
    [
        (
            {"input": '{"question":"edited input"}', "output": "null"},
            {"input": {"question": "edited input"}, "output": "answer-1"},
        ),
        (
            {"output": '"edited output"'},
            {"input": "question-1", "output": "edited output"},
        ),
    ],
    ids=["curated-input-empty-output", "missing-input-curated-output"],
)
def test_curated_content_precedence_and_root_fallback_are_per_field(
    sources, curated, expected
):
    sources.roots_by_trace_ids.return_value = [root()]
    sources.get_trace_row.return_value = curated

    result = replay.get_transcripts(PROJECT, "trace", [EARLY])

    assert transcript(result, EARLY) == [expected]
    sources.get_trace_row.assert_called_once_with(EARLY, project_id=PROJECT)
    Trace.objects.filter.assert_not_called()


def test_select_all_sessions_uses_project_scoped_session_reader(sources):
    sources.distinct_session_ids_with_filters.return_value = [SESSION]
    sources.roots_by_session_ids.return_value = [root()]
    result = replay.get_transcripts(PROJECT, "session", select_all=True)
    assert transcript(result, SESSION)[0]["input"] == "question-1"
    sources.distinct_session_ids_with_filters.assert_called_once_with(
        project_id=PROJECT
    )
    sources.roots_by_session_ids.assert_called_once_with([SESSION], project_id=PROJECT)


def test_select_all_traces_includes_sessionless_collector_roots(sources):
    span = root(session_id=None)
    sources.root_ids_by_project.return_value = [span.id]
    sources.list_by_ids.return_value = [span]
    result = replay.get_transcripts(PROJECT, "trace", select_all=True)
    assert transcript(result, EARLY)[0]["output"] == "answer-1"
    sources.root_ids_by_project.assert_called_once_with(PROJECT)
    sources.list_by_ids.assert_called_once_with(
        [span.id], project_id=PROJECT, include_heavy=False
    )


@pytest.mark.parametrize("replay_type", ["trace", "session"])
def test_unresolved_selected_source_is_not_silently_discarded(sources, replay_type):
    sources.roots_by_trace_ids.return_value = [root()]
    sources.roots_by_session_ids.return_value = [root()]
    ids = [EARLY, LATE] if replay_type == "trace" else [SESSION, OTHER_SESSION]
    with pytest.raises(ValidationError, match="Replay source"):
        replay.get_transcripts(PROJECT, replay_type, ids)


@pytest.mark.parametrize("replay_type", ["trace", "session"])
def test_empty_select_all_raises_source_resolution_error(sources, replay_type):
    with pytest.raises(ValidationError, match="Replay source"):
        replay.get_transcripts(PROJECT, replay_type, select_all=True)


def test_empty_root_content_cannot_trigger_generic_generation(sources):
    sources.roots_by_trace_ids.return_value = [root(input="null", output="{}")]
    with pytest.raises(ValidationError, match="Replay source"):
        replay.get_transcripts(PROJECT, "trace", [EARLY])


def test_partially_empty_session_cannot_drop_selected_turns(sources):
    sources.roots_by_session_ids.return_value = [
        root(),
        root(LATE, hour=2, input="", output="null"),
    ]
    with pytest.raises(ValidationError, match="Replay source"):
        replay.get_transcripts(PROJECT, "session", [SESSION])


def test_collector_simulator_input_keeps_latest_user_turn(sources):
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "latest"},
    ]
    span = root(
        input=json.dumps(messages),
        attrs_string={"fi.simulator.call_execution_id": "execution-1"},
    )
    sources.roots_by_trace_ids.return_value = [span]
    sources.list_by_trace_ids.return_value = [span]
    result = replay.get_transcripts(PROJECT, "trace", [EARLY])
    assert transcript(result, EARLY)[0]["input"] == messages[-1]
    for call in sources.list_by_trace_ids.call_args_list:
        assert call.kwargs["project_id"] == PROJECT


def test_collector_voice_detection_and_configuration_are_project_scoped(sources):
    raw_log = {
        "assistantId": "assistant-1",
        "phoneCallProvider": "vapi",
        "messages": [{"role": "system", "message": "Help with bookings"}],
    }
    span = root(
        observation_type="conversation",
        attrs_string={"raw_log": json.dumps(raw_log)},
    )
    sources.roots_by_trace_ids.return_value = [span]
    sources.list_by_trace_ids.return_value = [span]
    sources.list_by_trace.return_value = [span]
    rows = replay._build_trace_query(PROJECT, "trace", [EARLY])
    assert replay._is_voice_trace_query(rows, project_id=PROJECT) is True
    config = replay._extract_voice_trace_original_config(rows, project_id=PROJECT)
    assert config["assistant_id"] == "assistant-1"
    assert config["description"] == "Help with bookings"
    for method in (sources.list_by_trace_ids, sources.list_by_trace):
        for call in method.call_args_list:
            assert call.kwargs["project_id"] == PROJECT


def test_collector_voice_recording_reaches_replay(sources):
    key = (
        f"{replay.ConversationAttributes.CONVERSATION_RECORDING}."
        f"{replay.ConversationAttributes.MONO_COMBINED}"
    )
    span = root(
        observation_type="conversation",
        input="null",
        output="null",
        attrs_string={key: "https://example.invalid/conversation.wav"},
    )
    sources.roots_by_trace_ids.return_value = [span]
    sources.list_by_trace_ids.return_value = [span]
    result = replay.get_transcripts(PROJECT, "trace", [EARLY])
    assert result == {
        EARLY: {
            "replay_type": "trace",
            "audio_url": "https://example.invalid/conversation.wav",
        }
    }


def test_collector_voice_text_fallback_keeps_session_grouping(sources, monkeypatch):
    spans = [root(LATE, hour=2), root()]
    for span in spans:
        span.observation_type = "conversation"
        span.input = span.output = "null"
        span.attrs_string = {"turn": span.trace_id}
    sources.roots_by_session_ids.return_value = spans
    sources.list_by_trace_ids.return_value = spans
    monkeypatch.setattr(
        replay,
        "parse_voice_span_transcripts",
        lambda attrs: [
            {"role": "user", "messages": [attrs["turn"]]},
            {"role": "assistant", "messages": ["response"]},
        ],
    )
    result = replay.get_transcripts(PROJECT, "session", [SESSION])
    assert transcript(result, SESSION) == [
        {"input": EARLY, "output": "response"},
        {"input": LATE, "output": "response"},
    ]


def test_collector_system_prompt_resolves_without_postgres(sources, monkeypatch):
    sources.roots_by_session_ids.return_value = [root()]
    prompt = MagicMock(return_value="Help with bookings")
    monkeypatch.setattr(
        replay.SQL_query_handler, "get_system_prompt_from_traces", prompt
    )
    assert (
        replay.get_system_prompt(PROJECT, "session", [SESSION]) == "Help with bookings"
    )
    prompt.assert_called_once_with(project_id=PROJECT, trace_ids=[EARLY])


def test_clickhouse_failure_does_not_fall_back_to_generic_generation(sources):
    sources.roots_by_trace_ids.side_effect = RuntimeError("source read unavailable")
    with pytest.raises(RuntimeError, match="source read unavailable"):
        replay.get_transcripts(PROJECT, "trace", [EARLY])
