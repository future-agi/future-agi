"""Unit tests for the unified sim-observability span builder (TH-5642).

Pins the span tree (AGENT root → LLM per agent turn → TOOL per tool call) for both
chat and voice, and that the attribute keys match the tracer's real SpanAttributes
so emitted sim spans are valid for the ingest converter.
"""

import pytest

from simulate.services import sim_observability as so
from simulate.services.sim_observability import build_sim_spans

CHAT_TURNS = [
    {"role": "user", "content": "Hi, do you sell scooters?"},
    {"role": "assistant", "content": "Yes! Which model?", "model": "gpt-4o-mini",
     "input_tokens": 30, "output_tokens": 8, "total_tokens": 38,
     "tool_calls": [{"name": "lookup_inventory", "arguments": "{\"q\":\"scooter\"}",
                     "result": "[3 models]", "id": "call_1"}]},
    {"role": "user", "content": "The cheapest one."},
    {"role": "assistant", "content": "That's the X1 at $199.", "model": "gpt-4o-mini",
     "input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
]


@pytest.mark.unit
def test_chat_span_tree_shape_and_parenting():
    spans = build_sim_spans(CHAT_TURNS, modality="chat", project_name="proj",
                            session_id="sess-1")
    by_kind = {}
    for s in spans:
        by_kind.setdefault(s["attributes"][so.FI_SPAN_KIND], []).append(s)
    assert len(by_kind["AGENT"]) == 1
    assert len(by_kind["LLM"]) == 2     # one per assistant turn
    assert len(by_kind["TOOL"]) == 1    # one tool call

    root = by_kind["AGENT"][0]
    assert root["parent_span_id"] is None
    assert root["name"] == "chat simulation"
    assert root["attributes"][so.INPUT_VALUE] == "Hi, do you sell scooters?"
    assert root["attributes"][so.OUTPUT_VALUE] == "That's the X1 at $199."
    assert root["attributes"][so.SESSION_ID] == "sess-1"

    # All spans share one trace; LLM spans parent the root; TOOL parents its LLM.
    trace_ids = {s["trace_id"] for s in spans}
    assert len(trace_ids) == 1
    for llm in by_kind["LLM"]:
        assert llm["parent_span_id"] == root["span_id"]
    tool = by_kind["TOOL"][0]
    llm_ids = {s["span_id"] for s in by_kind["LLM"]}
    assert tool["parent_span_id"] in llm_ids


@pytest.mark.unit
def test_llm_span_carries_io_tokens_model():
    spans = build_sim_spans(CHAT_TURNS, modality="chat", project_name="proj")
    llm = [s for s in spans if s["attributes"][so.FI_SPAN_KIND] == "LLM"][0]
    a = llm["attributes"]
    assert a[so.OUTPUT_VALUE] == "Yes! Which model?"
    assert a[so.USAGE_INPUT_TOKENS] == 30
    assert a[so.USAGE_TOTAL_TOKENS] == 38
    assert a[so.REQUEST_MODEL] == "gpt-4o-mini"


@pytest.mark.unit
def test_tool_span_attributes():
    spans = build_sim_spans(CHAT_TURNS, modality="chat", project_name="proj")
    tool = [s for s in spans if s["attributes"][so.FI_SPAN_KIND] == "TOOL"][0]
    a = tool["attributes"]
    assert a[so.TOOL_NAME] == "lookup_inventory"
    assert a[so.TOOL_CALL_ID] == "call_1"
    assert "scooter" in a[so.TOOL_CALL_ARGUMENTS]
    assert a[so.TOOL_CALL_RESULT] == "[3 models]"


@pytest.mark.unit
def test_root_span_carries_conversation_token_rollup():
    # The root AGENT span sums the per-turn usage so the trace shows total cost.
    spans = build_sim_spans(CHAT_TURNS, modality="chat", project_name="p")
    root = spans[0]
    assert root["attributes"][so.FI_SPAN_KIND] == "AGENT"
    # Two assistant turns: input 30+50=80, output 8+10=18, total 38+60=98.
    assert root["attributes"][so.USAGE_INPUT_TOKENS] == 80
    assert root["attributes"][so.USAGE_OUTPUT_TOKENS] == 18
    assert root["attributes"][so.USAGE_TOTAL_TOKENS] == 98


@pytest.mark.unit
def test_voice_modality_emits_pipeline_latency():
    voice_turns = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi there!", "model": "gpt-4o",
         "voice_latency": {"stt": 120, "llm": 300, "tts": 80, "ttfb": 500, "total": 900}},
    ]
    spans = build_sim_spans(voice_turns, modality="voice", project_name="proj")
    llm = [s for s in spans if s["attributes"][so.FI_SPAN_KIND] == "LLM"][0]
    assert llm["name"] == "voice.turn 1"
    assert llm["attributes"][so.VOICE_LATENCY_KEYS["ttfb"]] == 500
    assert llm["attributes"][so.VOICE_LATENCY_KEYS["total"]] == 900
    assert spans[0]["attributes"][so.METADATA]["simulation_modality"] == "voice"


@pytest.mark.unit
def test_seed_makes_ids_deterministic_and_idempotent():
    # Same seed → same trace + span ids (so a Temporal retry re-emits the SAME
    # trace instead of a duplicate). Different seed → different ids.
    a = build_sim_spans(CHAT_TURNS, modality="chat", project_name="p", seed="call-1")
    b = build_sim_spans(CHAT_TURNS, modality="chat", project_name="p", seed="call-1")
    c = build_sim_spans(CHAT_TURNS, modality="chat", project_name="p", seed="call-2")
    assert [s["trace_id"] for s in a] == [s["trace_id"] for s in b]
    assert [s["span_id"] for s in a] == [s["span_id"] for s in b]
    assert a[0]["trace_id"] != c[0]["trace_id"]
    # Ids are valid OTLP hex widths.
    assert len(a[0]["trace_id"]) == 32 and len(a[0]["span_id"]) == 16
    # Without a seed, ids are random (not equal across calls).
    d = build_sim_spans(CHAT_TURNS, modality="chat", project_name="p")
    e = build_sim_spans(CHAT_TURNS, modality="chat", project_name="p")
    assert d[0]["trace_id"] != e[0]["trace_id"]


@pytest.mark.unit
def test_empty_conversation_yields_just_root():
    spans = build_sim_spans([], modality="chat", project_name="proj")
    assert len(spans) == 1
    assert spans[0]["attributes"][so.FI_SPAN_KIND] == "AGENT"


@pytest.mark.unit
def test_attach_sim_evals_to_trace_merges_onto_root_span(monkeypatch):
    from types import SimpleNamespace

    import tracer.models.observation_span as osmod

    class _FakeSpan:
        def __init__(self):
            self.span_attributes = {"gen_ai.span.kind": "AGENT"}
            self.saved_fields = None

        def save(self, update_fields=None):
            self.saved_fields = update_fields

    fake = _FakeSpan()

    class _FakeObservationSpan:
        class objects:  # noqa: N801
            @staticmethod
            def filter(**kwargs):
                # Looked up by the deterministic root span id.
                assert kwargs.get("span_id")
                return [fake]

    monkeypatch.setattr(osmod, "ObservationSpan", _FakeObservationSpan)

    from simulate.services.sim_observability import attach_sim_evals_to_trace

    n = attach_sim_evals_to_trace(
        SimpleNamespace(id="call-1"),
        {"gen_ai.evaluation.score": 0.8, "gen_ai.evaluation.passed": True},
    )
    assert n == 1
    # Merged (existing kept) + eval results added; saved the field.
    assert fake.span_attributes["gen_ai.span.kind"] == "AGENT"
    assert fake.span_attributes["gen_ai.evaluation.score"] == 0.8
    assert fake.span_attributes["gen_ai.evaluation.passed"] is True
    assert fake.saved_fields == ["span_attributes"]


@pytest.mark.unit
def test_attach_sim_evals_noops_without_evals_or_id():
    from types import SimpleNamespace

    from simulate.services.sim_observability import attach_sim_evals_to_trace

    assert attach_sim_evals_to_trace(SimpleNamespace(id="c1"), {}) == 0
    assert attach_sim_evals_to_trace(SimpleNamespace(id=""), {"x": 1}) == 0


@pytest.mark.unit
def test_attribute_keys_match_tracer_spanattributes():
    # Drift guard: our local keys must equal the tracer's real SpanAttributes so
    # the ingest converter reads them correctly.
    from tracer.utils.otel import SpanAttributes as SA

    assert so.FI_SPAN_KIND == SA.FI_SPAN_KIND
    assert so.INPUT_VALUE == SA.INPUT_VALUE
    assert so.OUTPUT_VALUE == SA.OUTPUT_VALUE
    assert so.SESSION_ID == SA.SESSION_ID
    assert so.USAGE_INPUT_TOKENS == SA.USAGE_INPUT_TOKENS
    assert so.USAGE_OUTPUT_TOKENS == SA.USAGE_OUTPUT_TOKENS
    assert so.USAGE_TOTAL_TOKENS == SA.USAGE_TOTAL_TOKENS
    assert so.TOOL_NAME == SA.TOOL_NAME
    assert so.TOOL_CALL_ID == SA.TOOL_CALL_ID
    assert so.TOOL_CALL_ARGUMENTS == SA.TOOL_CALL_ARGUMENTS
    assert so.TOOL_CALL_RESULT == SA.TOOL_CALL_RESULT
