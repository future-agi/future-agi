"""Tests for `_process_mapping`: literal lookup → dotted-path walk fallback."""

import uuid

import pytest

from tracer.models.observation_span import EvalLogger
from tracer.utils.eval import (
    EvalSkippedMissingAttribute,
    _process_mapping,
    _process_session_mapping,
    _process_trace_mapping,
    evaluate_trace_observe,
    resolve_session_mapping_lean_first,
    resolve_trace_mapping_lean_first,
)


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
    with pytest.raises(
        ValueError, match="Required attribute 'nonexistent_field'"
    ) as exc:
        _process_mapping(
            {"prompt": "nonexistent_field"},
            span,
            eval_template_id=missing_eval_template_id,
        )
    assert isinstance(exc.value, EvalSkippedMissingAttribute)
    assert exc.value.skipped_reason == "missing_required_attribute: nonexistent_field"


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
        {"v": "messages.1.message"},
        voice_span,
        eval_template_id=missing_eval_template_id,
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


# ───────────────────────────────────────────────────────────────────────────
# Non-string mapping values
#
# A saved mapping value is an attribute path string. A resolver handed any
# other type must fail as a ValueError — the type the eval callers catch and
# persist as a failed EvalLogger row — rather than a TypeError/AttributeError
# from inside a path walker.
# ───────────────────────────────────────────────────────────────────────────


def test_object_mapping_value_fails_span_mapping_as_value_error(
    _span_with_attrs, missing_eval_template_id
):
    span = _span_with_attrs({"input": "hello"})
    with pytest.raises(ValueError, match="must be an attribute path string"):
        _process_mapping(
            {"prompt": {"path": "input"}},
            span,
            eval_template_id=missing_eval_template_id,
        )
    assert _process_mapping(
        {"prompt": "input"}, span, eval_template_id=missing_eval_template_id
    ) == {"prompt": "hello"}


def test_object_mapping_value_fails_trace_mapping_as_value_error(trace, eval_template):
    with pytest.raises(ValueError, match="must be an attribute path string"):
        _process_trace_mapping({"prompt": {"path": "name"}}, trace, eval_template.id)
    assert _process_trace_mapping({"prompt": "name"}, trace, eval_template.id) == {
        "prompt": "Test Trace"
    }


def test_object_mapping_value_fails_session_mapping_as_value_error(
    trace_session, eval_template
):
    with pytest.raises(ValueError, match="must be an attribute path string"):
        _process_session_mapping(
            {"prompt": {"path": "name"}}, trace_session, eval_template.id
        )
    assert _process_session_mapping(
        {"prompt": "name"}, trace_session, eval_template.id
    ) == {"prompt": "Test Session"}


def test_list_mapping_raises_value_error_not_attribute_error(trace, eval_template):
    # ``(mapping or {}).items()`` only absorbs an *empty* list. A populated one
    # used to raise AttributeError, which the callers' broad ``except Exception``
    # swallows instead of recording — the exact failure mode this set out to end.
    with pytest.raises(ValueError, match="must be an object"):
        _process_trace_mapping(["prompt"], trace, eval_template.id)


def test_cleared_value_on_a_required_key_is_a_missing_attribute(
    observation_span, eval_template
):
    # ``None`` is admissible (it is how a cleared field is stored) and is popped
    # for optional keys. On a *required* key the span resolver used to reach
    # ``None.split(".")``; it now converges on the same missing-attribute
    # failure the trace and session resolvers already raised.
    with pytest.raises(EvalSkippedMissingAttribute):
        _process_mapping({"prompt": None}, observation_span, eval_template.id)


# The shape the reporting customer actually saved, quoted from their own report:
#   generated_value: {"source": "span_attribute", "column_id": "output.value"}
# Their 31 rows all failed with "'dict' object has no attribute 'startswith'",
# which is _heavy_span_ids_for_trace_mapping scanning raw mapping values before
# the resolver binds to a span -- hence trace_id/span_id/session_id all null.
CUSTOMER_MAPPING_VALUE = {"source": "span_attribute", "column_id": "output.value"}


def test_customer_mapping_shape_fails_before_the_heavy_span_scan(
    trace, eval_template, mocker
):
    mocker.patch(
        "tracer.services.clickhouse.v2.eval_loader._read_source",
        return_value="clickhouse",
    )
    with pytest.raises(ValueError, match="must be an attribute path string"):
        resolve_trace_mapping_lean_first(
            {"generated_value": CUSTOMER_MAPPING_VALUE}, trace, eval_template.id
        )


def test_object_mapping_value_fails_lean_first_trace_path(trace, eval_template, mocker):
    mocker.patch(
        "tracer.services.clickhouse.v2.eval_loader._read_source",
        return_value="clickhouse",
    )
    with pytest.raises(ValueError, match="must be an attribute path string"):
        resolve_trace_mapping_lean_first(
            {"prompt": {"path": "spans.0.input"}}, trace, eval_template.id
        )


def test_object_mapping_value_fails_lean_first_session_path(
    trace_session, eval_template, mocker
):
    mocker.patch(
        "tracer.services.clickhouse.v2.eval_loader._read_source",
        return_value="clickhouse",
    )
    with pytest.raises(ValueError, match="must be an attribute path string"):
        resolve_session_mapping_lean_first(
            {"prompt": {"path": "traces.0.spans.0.input"}},
            trace_session,
            eval_template.id,
        )


# ───────────────────────────────────────────────────────────────────────────
# The recorded outcome
#
# ``evaluate_trace_observe`` records a ValueError on EvalTask.failed_spans and
# writes an error EvalLogger row, but its bare ``except Exception`` only logs.
# A mapping value that used to raise TypeError out of a path walker therefore
# produced no eval result and no trace of why. Raising ValueError instead is
# what makes the failure visible, so that is what this asserts.
# ───────────────────────────────────────────────────────────────────────────


def test_object_mapping_value_is_recorded_not_swallowed(
    trace, observation_span, custom_eval_config, eval_task
):
    custom_eval_config.mapping = {"input": {"path": "input"}, "output": "output"}
    custom_eval_config.save(update_fields=["mapping"])

    # ``._original_func`` for the same reason ``inline_temporal`` uses it: the
    # temporal_activity wrapper's close_old_connections() drops pytest-django's
    # test transaction. The body it wraps is the one Temporal runs.
    result = evaluate_trace_observe._original_func(
        trace_id=str(trace.id),
        custom_eval_config_id=str(custom_eval_config.id),
        eval_task_id=str(eval_task.id),
    )

    assert result is False

    eval_task.refresh_from_db()
    assert eval_task.failed_spans, "the failure was swallowed, not recorded"
    recorded = eval_task.failed_spans[-1]
    assert recorded["trace_id"] == str(trace.id)
    assert "must be an attribute path string" in recorded["error"]

    errored = EvalLogger.objects.filter(
        trace_id=trace.id,
        custom_eval_config_id=custom_eval_config.id,
        error=True,
    ).first()
    assert errored is not None, "no error row was written for the failed eval"
    assert "must be an attribute path string" in errored.error_message
