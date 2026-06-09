"""Tests for `_process_mapping`: literal lookup → dotted-path walk fallback."""

import uuid

import pytest

from tracer.utils.eval import EvalSkippedMissingAttribute, _process_mapping


@pytest.fixture
def missing_eval_template_id():
    return uuid.uuid4()


@pytest.fixture
def _span_with_attrs(observation_span):
    """Helper to set `span_attributes` on the shared fixture and return it."""

    def _set(attrs):
        observation_span.span_attributes = attrs
        observation_span.save(update_fields=["span_attributes"])
        return observation_span

    return _set


def test_literal_key_resolves(_span_with_attrs, missing_eval_template_id):
    span = _span_with_attrs({"input": "hello"})
    out = _process_mapping(
        {"prompt": "input"}, span, eval_template_id=missing_eval_template_id
    )
    assert out == {"prompt": "hello"}


def test_dot_value_fallback_resolves(_span_with_attrs, missing_eval_template_id):
    span = _span_with_attrs({"input.value": "hello"})
    out = _process_mapping(
        {"prompt": "input"}, span, eval_template_id=missing_eval_template_id
    )
    assert out == {"prompt": "hello"}


def test_alias_literal_resolves(_span_with_attrs, missing_eval_template_id):
    # `recording_url` shorthand → resolves via alias entry `stereo_recording_url`.
    span = _span_with_attrs({"stereo_recording_url": "https://x/y.wav"})
    out = _process_mapping(
        {"audio": "recording_url"}, span, eval_template_id=missing_eval_template_id
    )
    assert out == {"audio": "https://x/y.wav"}


def test_dotted_nested_path_resolves_through_walk(
    _span_with_attrs, missing_eval_template_id
):
    # Repro of the prod voice-eval bug: nested JSON only resolves via the walker.
    span = _span_with_attrs(
        {
            "conversation": {
                "recording": {"mono": {"combined": "https://x/combined.wav"}}
            }
        }
    )
    out = _process_mapping(
        {"audio": "conversation.recording.mono.combined"},
        span,
        eval_template_id=missing_eval_template_id,
    )
    assert out == {"audio": "https://x/combined.wav"}


def test_alias_with_dotted_path_resolves_against_nested(
    _span_with_attrs, missing_eval_template_id
):
    # `transcript` shorthand → alias `conversation.transcript` walks nested JSON.
    transcript = [{"role": "user", "text": "hello"}]
    span = _span_with_attrs({"conversation": {"transcript": transcript}})
    out = _process_mapping(
        {"text": "transcript"}, span, eval_template_id=missing_eval_template_id
    )
    # Non-string values are JSON-serialised by the resolver.
    assert out == {"text": '[{"role": "user", "text": "hello"}]'}


def test_provider_transcript_alias_resolves(_span_with_attrs, missing_eval_template_id):
    span = _span_with_attrs({"provider_transcript": "hello world"})
    out = _process_mapping(
        {"text": "transcript"}, span, eval_template_id=missing_eval_template_id
    )
    assert out == {"text": "hello world"}


def test_missing_attribute_raises_typed_skip(
    _span_with_attrs, missing_eval_template_id
):
    span = _span_with_attrs({"unrelated": "value"})
    # Subclasses ValueError so legacy `except ValueError` handlers still catch
    # it, while carrying the structured skip reason the eval logger persists.
    with pytest.raises(ValueError, match="Required attribute 'input'") as exc:
        _process_mapping(
            {"prompt": "input"}, span, eval_template_id=missing_eval_template_id
        )
    assert isinstance(exc.value, EvalSkippedMissingAttribute)
    assert exc.value.skipped_reason == "missing_required_attribute: input"


# ───────────────────────────────────────────────────────────────────────────
# Voice-call raw_log fallback (gated on observation_type == "conversation")
#
# These tests cover the layer that walks ``span_attributes["raw_log"]`` with
# per-segment snake_case ↔ camelCase coercion when every prior resolution
# layer misses. The motivating case is the voice eval mapping picker
# offering paths like ``messages.0.end_time`` against vapi raw_log keys
# like ``endTime``.
# ───────────────────────────────────────────────────────────────────────────


from tfc.utils.case import to_camel_case, to_snake_case  # noqa: E402
from tracer.models.observation_span import ObservationType  # noqa: E402
from tracer.utils.eval import _MISSING, _walk_raw_log  # noqa: E402

# Vapi-shaped raw_log used by every integration test below. Mirrors the
# real prod fixture under tracer/tests/fixtures/voice_call_root_span_attrs.json
# but trimmed to just the keys the tests exercise.
_VAPI_RAW_LOG = {
    "id": "call-abc",
    "startedAt": "2026-05-27T10:00:00Z",
    "endedAt": "2026-05-27T10:01:00Z",
    "recordingUrl": "https://example.com/rec.wav",
    "assistantId": "asst-1",
    "costBreakdown": {"llm": 0.5, "tts": 0.2, "total": 0.7},
    "messages": [
        # System-prompt entry: vapi raw_log omits timing keys here entirely
        # (no ``endTime``, ``duration``, ``metadata``). These should miss.
        {"role": "system", "message": "You are helpful"},
        # Bot entry: full timing keys, ``endTime``/``secondsFromStart`` in
        # camelCase, plus a real ``null`` in ``metadata``.
        {
            "role": "bot",
            "message": "Hello",
            "time": 0.858,
            "endTime": 1.629,
            "duration": 0.771,
            "secondsFromStart": 0.858,
            "metadata": None,
        },
    ],
}


@pytest.fixture
def voice_span(_span_with_attrs):
    """A conversation root span carrying the vapi raw_log payload."""
    span = _span_with_attrs({"raw_log": _VAPI_RAW_LOG})
    span.observation_type = ObservationType.CONVERSATION
    span.save(update_fields=["observation_type"])
    return span


# ── Unit tests for the case-coercion helpers ──────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("end_time", "endTime"),
        ("seconds_from_start", "secondsFromStart"),
        ("a", "a"),
        ("already_camelCase", "alreadyCamelCase"),  # only the first char is upcased
        ("nounderscore", "nounderscore"),
        ("", ""),
    ],
)
def test_to_camel_case(raw, expected):
    assert to_camel_case(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("endTime", "end_time"),
        ("secondsFromStart", "seconds_from_start"),
        ("already_snake", "already_snake"),
        ("URLPath", "urlpath"),  # consecutive caps fold without separators
        ("simple", "simple"),
        ("", ""),
    ],
)
def test_to_snake_case(raw, expected):
    assert to_snake_case(raw) == expected


# ── Direct _walk_raw_log unit tests ───────────────────────────────────────


def test_walk_raw_log_literal_path():
    assert _walk_raw_log(_VAPI_RAW_LOG, "id") == "call-abc"


def test_walk_raw_log_snake_to_camel_top_level():
    assert _walk_raw_log(_VAPI_RAW_LOG, "started_at") == "2026-05-27T10:00:00Z"


def test_walk_raw_log_snake_to_camel_nested():
    assert _walk_raw_log(_VAPI_RAW_LOG, "messages.1.end_time") == 1.629


def test_walk_raw_log_array_index():
    assert _walk_raw_log(_VAPI_RAW_LOG, "messages.0.role") == "system"


def test_walk_raw_log_preserves_real_none_value():
    # ``metadata`` is explicitly null on the bot message; the walker must
    # return that None — not _MISSING — so the caller resolves it instead
    # of raising "Required attribute not found".
    result = _walk_raw_log(_VAPI_RAW_LOG, "messages.1.metadata")
    assert result is None
    assert result is not _MISSING


def test_walk_raw_log_missing_key_returns_missing():
    # System-prompt entry has no ``duration`` key at all → genuine miss.
    assert _walk_raw_log(_VAPI_RAW_LOG, "messages.0.duration") is _MISSING


def test_walk_raw_log_out_of_range_index_returns_missing():
    assert _walk_raw_log(_VAPI_RAW_LOG, "messages.99.role") is _MISSING


def test_walk_raw_log_empty_path_returns_missing():
    assert _walk_raw_log(_VAPI_RAW_LOG, "") is _MISSING


def test_walk_raw_log_walking_through_non_container_returns_missing():
    # ``id`` resolves to "call-abc" (a string); further path parts can't walk.
    assert _walk_raw_log(_VAPI_RAW_LOG, "id.subkey") is _MISSING


# ── _process_mapping integration tests for the voice raw_log fallback ─────


def test_voice_fallback_resolves_messages_subfield(
    voice_span, missing_eval_template_id
):
    out = _process_mapping(
        {"v": "messages.1.message"}, voice_span, eval_template_id=missing_eval_template_id
    )
    assert out == {"v": "Hello"}


def test_voice_fallback_resolves_snake_case_camel_key(
    voice_span, missing_eval_template_id
):
    # ``messages.1.end_time`` → raw_log["messages"][1]["endTime"] via coerce.
    out = _process_mapping(
        {"v": "messages.1.end_time"},
        voice_span,
        eval_template_id=missing_eval_template_id,
    )
    # Non-string values are JSON-dumped by the caller.
    assert out == {"v": "1.629"}


def test_voice_fallback_resolves_top_level_snake_case(
    voice_span, missing_eval_template_id
):
    out = _process_mapping(
        {"v": "started_at"}, voice_span, eval_template_id=missing_eval_template_id
    )
    assert out == {"v": "2026-05-27T10:00:00Z"}


def test_voice_fallback_resolves_nested_object(voice_span, missing_eval_template_id):
    # ``cost_breakdown.llm`` → raw_log["costBreakdown"]["llm"] via head coerce.
    out = _process_mapping(
        {"v": "cost_breakdown.llm"},
        voice_span,
        eval_template_id=missing_eval_template_id,
    )
    assert out == {"v": "0.5"}


def test_voice_fallback_resolves_real_null_to_json_null(
    voice_span, missing_eval_template_id
):
    # Real null in raw_log must resolve (to JSON literal "null") rather
    # than raise "Required attribute not found".
    out = _process_mapping(
        {"v": "messages.1.metadata"},
        voice_span,
        eval_template_id=missing_eval_template_id,
    )
    assert out == {"v": "null"}


def test_voice_fallback_missing_in_raw_log_still_raises(
    voice_span, missing_eval_template_id
):
    # ``messages.0.duration`` is absent from the system-prompt entry → miss.
    with pytest.raises(ValueError, match="Required attribute 'messages.0.duration'"):
        _process_mapping(
            {"v": "messages.0.duration"},
            voice_span,
            eval_template_id=missing_eval_template_id,
        )


def test_voice_fallback_skipped_for_non_conversation_span(
    _span_with_attrs, missing_eval_template_id
):
    # observation_type defaults to "llm" via the conftest fixture — even
    # with the same raw_log payload, the fallback must NOT activate so
    # non-voice resolution semantics stay intact.
    span = _span_with_attrs({"raw_log": _VAPI_RAW_LOG})
    assert span.observation_type == "llm"  # sanity-check the conftest default
    with pytest.raises(ValueError, match="Required attribute 'messages.0.role'"):
        _process_mapping(
            {"v": "messages.0.role"}, span, eval_template_id=missing_eval_template_id
        )


def test_voice_fallback_not_reached_when_literal_resolves(
    _span_with_attrs, missing_eval_template_id
):
    # When span_attributes already has the key as a flat literal, the
    # raw_log layer is never consulted — verifies the layer ordering.
    span = _span_with_attrs(
        {"call.duration": 42, "raw_log": {"messages": [{"role": "decoy"}]}}
    )
    span.observation_type = ObservationType.CONVERSATION
    span.save(update_fields=["observation_type"])
    out = _process_mapping(
        {"v": "call.duration"}, span, eval_template_id=missing_eval_template_id
    )
    assert out == {"v": "42"}
