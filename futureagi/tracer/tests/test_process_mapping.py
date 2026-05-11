"""Tests for `_process_mapping`: literal lookup → dotted-path walk fallback."""

import pytest

from tracer.utils.eval import _process_mapping


@pytest.fixture
def _span_with_attrs(observation_span):
    """Helper to set `span_attributes` on the shared fixture and return it."""

    def _set(attrs):
        observation_span.span_attributes = attrs
        observation_span.save(update_fields=["span_attributes"])
        return observation_span

    return _set


def test_literal_key_resolves(_span_with_attrs):
    span = _span_with_attrs({"input": "hello"})
    out = _process_mapping({"prompt": "input"}, span, eval_template_id=-1)
    assert out == {"prompt": "hello"}


def test_dot_value_fallback_resolves(_span_with_attrs):
    span = _span_with_attrs({"input.value": "hello"})
    out = _process_mapping({"prompt": "input"}, span, eval_template_id=-1)
    assert out == {"prompt": "hello"}


def test_alias_literal_resolves(_span_with_attrs):
    # `recording_url` shorthand → resolves via alias entry `stereo_recording_url`.
    span = _span_with_attrs({"stereo_recording_url": "https://x/y.wav"})
    out = _process_mapping({"audio": "recording_url"}, span, eval_template_id=-1)
    assert out == {"audio": "https://x/y.wav"}


def test_dotted_nested_path_resolves_through_walk(_span_with_attrs):
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
        eval_template_id=-1,
    )
    assert out == {"audio": "https://x/combined.wav"}


def test_alias_with_dotted_path_resolves_against_nested(_span_with_attrs):
    # `transcript` shorthand → alias `conversation.transcript` walks nested JSON.
    transcript = [{"role": "user", "text": "hello"}]
    span = _span_with_attrs({"conversation": {"transcript": transcript}})
    out = _process_mapping({"text": "transcript"}, span, eval_template_id=-1)
    # Non-string values are JSON-serialised by the resolver.
    assert out == {"text": '[{"role": "user", "text": "hello"}]'}


def test_provider_transcript_alias_resolves(_span_with_attrs):
    span = _span_with_attrs({"provider_transcript": "hello world"})
    out = _process_mapping({"text": "transcript"}, span, eval_template_id=-1)
    assert out == {"text": "hello world"}


def test_missing_attribute_raises(_span_with_attrs):
    span = _span_with_attrs({"unrelated": "value"})
    with pytest.raises(ValueError, match="Required attribute 'input'"):
        _process_mapping({"prompt": "input"}, span, eval_template_id=-1)
