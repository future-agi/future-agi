"""Simulation observability — emit a trace of spans for a sim call (TH-5642).

Competitor research (Cekura/Coval/Roark) shows production-grade observability of
EVERY simulated conversation as a span tree in the trace/Session UI is table stakes.
Voice sims already produce latency/recording observability; CHAT sims previously
emitted only ChatMessage rows and never appeared as traces. This builds a UNIFIED
span tree for BOTH modalities so chat + voice sims land in the same trace UI as
production traces.

Span tree (OpenInference/FI conventions):
  AGENT  "<modality> simulation"            (root: the whole conversation)
   └─ LLM  "<modality>.turn N"              (each agent-under-test turn)
        └─ TOOL "<tool name>"               (each tool call in that turn)

This module is Django-free and pure (build_sim_spans returns plain dicts) so it is
trivially unit-tested; the thin emit layer (emit_sim_trace) resolves the project and
hands each span to the tracer's existing ``create_single_otel_span`` write path.
"""

from __future__ import annotations

import secrets
from typing import Any

# Attribute keys mirror tracer.utils.otel.SpanAttributes (FI/OpenInference). Kept
# local so this module stays import-light + pure; test_sim_observability pins them
# against the real SpanAttributes so they can't drift.
FI_SPAN_KIND = "gen_ai.span.kind"  # SpanAttributes.FI_SPAN_KIND (converter also reads fi./openinference.)
INPUT_VALUE = "input.value"
OUTPUT_VALUE = "output.value"
SESSION_ID = "session.id"
REQUEST_MODEL = "gen_ai.request.model"
LLM_MODEL_NAME = "llm.model_name"
USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
TOOL_NAME = "gen_ai.tool.name"
TOOL_CALL_ID = "gen_ai.tool.call.id"
TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
TOOL_CALL_RESULT = "gen_ai.tool.call.result"
METADATA = "metadata"

# Voice-only: per-turn pipeline latency split (only emitted when present).
VOICE_LATENCY_KEYS = {
    "stt": "gen_ai.voice.latency.stt",
    "llm": "gen_ai.voice.latency.llm",
    "tts": "gen_ai.voice.latency.tts",
    "ttfb": "gen_ai.voice.latency.ttfb",
    "total": "gen_ai.voice.latency.total",
}

SPAN_KIND_AGENT = "AGENT"
SPAN_KIND_LLM = "LLM"
SPAN_KIND_TOOL = "TOOL"


def _trace_id() -> str:
    return secrets.token_hex(16)  # 32 hex chars (128-bit), OTLP trace id


def _span_id() -> str:
    return secrets.token_hex(8)  # 16 hex chars (64-bit), OTLP span id


def _span(
    *, name, kind, span_id, parent_span_id, trace_id, attributes,
    project_name, project_type, latency=None,
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "latency": latency,
        "project_name": project_name,
        "project_type": project_type,
        "attributes": {FI_SPAN_KIND: kind, **attributes},
    }


def build_sim_spans(
    turns: list[dict[str, Any]],
    *,
    modality: str,                 # "chat" | "voice"
    project_name: str,
    project_type: str = "observe",
    session_id: str | None = None,
    agent_name: str = "agent-under-test",
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build the OTLP span dicts for one simulated conversation.

    ``turns``: ordered ``[{"role": "user"|"assistant", "content": str,
    "model"?: str, "input_tokens"?: int, "output_tokens"?: int,
    "total_tokens"?: int, "latency_ms"?: int, "voice_latency"?: {...},
    "tool_calls"?: [{"name","arguments","result","id"}]}]`` — user = simulated
    customer, assistant = the agent-under-test.

    Returns root AGENT span + one LLM span per assistant turn + TOOL spans, all
    sharing one trace_id, parented correctly.
    """
    trace_id = trace_id or _trace_id()
    root_id = _span_id()

    user_turns = [t for t in turns if t.get("role") == "user"]
    agent_turns = [t for t in turns if t.get("role") == "assistant"]
    first_user = next((t.get("content", "") for t in user_turns), "")
    last_agent = next((t.get("content", "") for t in reversed(agent_turns)), "")

    base_attrs: dict[str, Any] = {INPUT_VALUE: first_user, OUTPUT_VALUE: last_agent}
    if session_id:
        base_attrs[SESSION_ID] = session_id
    if metadata:
        base_attrs[METADATA] = {**metadata, "simulation_modality": modality}
    else:
        base_attrs[METADATA] = {"simulation_modality": modality}

    spans = [
        _span(
            name=f"{modality} simulation", kind=SPAN_KIND_AGENT, span_id=root_id,
            parent_span_id=None, trace_id=trace_id, attributes=base_attrs,
            project_name=project_name, project_type=project_type,
        )
    ]

    # Walk turns in order; each assistant turn becomes an LLM span whose input is
    # the conversation so far and whose output is the agent's reply.
    history: list[str] = []
    turn_no = 0
    for t in turns:
        role = t.get("role")
        content = t.get("content", "")
        if role != "assistant":
            history.append(f"user: {content}")
            continue
        turn_no += 1
        llm_id = _span_id()
        attrs: dict[str, Any] = {
            INPUT_VALUE: "\n".join(history) or first_user,
            OUTPUT_VALUE: content,
        }
        turn_model = t.get("model") or model
        if turn_model:
            attrs[REQUEST_MODEL] = turn_model
            attrs[LLM_MODEL_NAME] = turn_model
        if t.get("input_tokens") is not None:
            attrs[USAGE_INPUT_TOKENS] = t["input_tokens"]
        if t.get("output_tokens") is not None:
            attrs[USAGE_OUTPUT_TOKENS] = t["output_tokens"]
        if t.get("total_tokens") is not None:
            attrs[USAGE_TOTAL_TOKENS] = t["total_tokens"]
        if modality == "voice" and isinstance(t.get("voice_latency"), dict):
            for k, attr_key in VOICE_LATENCY_KEYS.items():
                if t["voice_latency"].get(k) is not None:
                    attrs[attr_key] = t["voice_latency"][k]
        spans.append(
            _span(
                name=f"{modality}.turn {turn_no}", kind=SPAN_KIND_LLM, span_id=llm_id,
                parent_span_id=root_id, trace_id=trace_id, attributes=attrs,
                project_name=project_name, project_type=project_type,
                latency=t.get("latency_ms"),
            )
        )
        for tc in t.get("tool_calls") or []:
            spans.append(
                _span(
                    name=tc.get("name") or "tool", kind=SPAN_KIND_TOOL,
                    span_id=_span_id(), parent_span_id=llm_id, trace_id=trace_id,
                    attributes={
                        TOOL_NAME: tc.get("name") or "",
                        TOOL_CALL_ID: tc.get("id") or "",
                        TOOL_CALL_ARGUMENTS: tc.get("arguments") or "",
                        TOOL_CALL_RESULT: tc.get("result") or "",
                    },
                    project_name=project_name, project_type=project_type,
                )
            )
        history.append(f"assistant: {content}")

    return spans
