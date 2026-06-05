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
import uuid
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Fixed namespace so deterministic ids are stable across processes/retries.
_SIM_TRACE_NS = uuid.UUID("9f1b6d2e-0c3a-4e7b-9a1d-5e2f7c8b4a10")


def _det_trace_id(seed: str) -> str:
    """Deterministic 128-bit (32 hex) trace id from a seed (e.g. the call id)."""
    return uuid.uuid5(_SIM_TRACE_NS, f"trace:{seed}").hex


def _det_span_id(seed: str, key: str) -> str:
    """Deterministic 64-bit (16 hex) span id from a seed + a per-span key."""
    return uuid.uuid5(_SIM_TRACE_NS, f"span:{seed}:{key}").hex[:16]

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
        # ``parent_span_id`` is the canonical key (asserted by tests / read by
        # other sim tooling); ``parent_id`` is the key the OTel ingest converter
        # (tracer.utils.otel.convert_otel_span_to_observation_span) actually reads
        # to set ObservationSpan.parent_span_id. Emit BOTH so the persisted trace
        # is a real tree, not flat. (Caught by TH-5642 local DB-verified emit.)
        "parent_span_id": parent_span_id,
        "parent_id": parent_span_id,
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
    seed: str | None = None,
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
    # Deterministic ids from `seed` (the call id) make re-emits idempotent — a
    # Temporal retry produces the SAME trace + span ids instead of a duplicate trace.
    trace_id = trace_id or (_det_trace_id(seed) if seed else _trace_id())
    root_id = _det_span_id(seed, "root") if seed else _span_id()

    user_turns = [t for t in turns if t.get("role") == "user"]
    agent_turns = [t for t in turns if t.get("role") == "assistant"]
    first_user = next((t.get("content", "") for t in user_turns), "")
    last_agent = next((t.get("content", "") for t in reversed(agent_turns)), "")

    base_attrs: dict[str, Any] = {INPUT_VALUE: first_user, OUTPUT_VALUE: last_agent}
    # Conversation-level token rollup on the root span (the ingest converter computes
    # per-LLM-span cost, but not the trace total) — so the trace shows total usage.
    _ti = sum(int(t.get("input_tokens") or 0) for t in agent_turns)
    _to = sum(int(t.get("output_tokens") or 0) for t in agent_turns)
    _tt = sum(int(t.get("total_tokens") or 0) for t in agent_turns)
    if _ti:
        base_attrs[USAGE_INPUT_TOKENS] = _ti
    if _to:
        base_attrs[USAGE_OUTPUT_TOKENS] = _to
    if _tt:
        base_attrs[USAGE_TOTAL_TOKENS] = _tt
    if model:
        base_attrs[REQUEST_MODEL] = model
        base_attrs[LLM_MODEL_NAME] = model
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
        llm_id = _det_span_id(seed, f"turn:{turn_no}") if seed else _span_id()
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
        for tool_idx, tc in enumerate(t.get("tool_calls") or []):
            tool_sid = (
                _det_span_id(seed, f"turn:{turn_no}:tool:{tool_idx}")
                if seed
                else _span_id()
            )
            spans.append(
                _span(
                    name=tc.get("name") or "tool", kind=SPAN_KIND_TOOL,
                    span_id=tool_sid, parent_span_id=llm_id, trace_id=trace_id,
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


# ---------------------------------------------------------------------------
# Emit layer — resolves the project + reads the call's messages, then hands each
# built span to the tracer's existing OTel write path. Lazy imports keep the
# builder above pure/Django-free.
# ---------------------------------------------------------------------------
def _message_text(row) -> str:
    """Extract plain text from a ChatMessageModel row (messages list or content)."""
    msgs = getattr(row, "messages", None)
    if isinstance(msgs, list) and msgs:
        return " ".join(str(m) for m in msgs if m)
    content = getattr(row, "content", None)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("content"):
                parts.append(str(item["content"]))
        return " ".join(parts)
    return str(content) if content else ""


def _tool_calls_from_row(row) -> list[dict[str, Any]]:
    content = getattr(row, "content", None)
    out: list[dict[str, Any]] = []
    if isinstance(content, list):
        for item in content:
            for tc in (item.get("tool_calls") or []) if isinstance(item, dict) else []:
                fn = tc.get("function") or {}
                out.append({
                    "name": fn.get("name") or tc.get("name") or "",
                    "arguments": fn.get("arguments") or tc.get("arguments") or "",
                    "result": tc.get("result") or "",
                    "id": tc.get("id") or "",
                })
    return out


def _row_to_turn(row) -> dict[str, Any]:
    role = "assistant" if str(getattr(row, "role", "")) == "assistant" else "user"
    turn: dict[str, Any] = {"role": role, "content": _message_text(row)}
    if role == "assistant":
        if getattr(row, "tokens", None) is not None:
            turn["output_tokens"] = row.tokens
            turn["total_tokens"] = row.tokens
        if getattr(row, "latency_ms", None) is not None:
            turn["latency_ms"] = row.latency_ms
        tcs = _tool_calls_from_row(row)
        if tcs:
            turn["tool_calls"] = tcs
    return turn


def emit_sim_trace(
    call_execution,
    *,
    turns: list[dict[str, Any]] | None = None,
    user_id: str | None = None,
) -> int:
    """Emit a trace (span tree) for a completed sim CallExecution.

    Resolves the project from the agent definition's observability provider (falling
    back to a per-org "Simulations" project), builds the span tree, and writes each
    span via the tracer's OTel ingest path so the sim appears in the same
    trace/Session UI as production traces.

    ``turns``: pass a normalized ``[{"role","content",...}]`` conversation directly —
    used by the VOICE completion hook, which stores its transcript outside
    ChatMessageModel. When omitted (CHAT), the persisted ChatMessage rows are read.

    Returns the number of spans emitted.
    """
    from tracer.utils.create_otel_span import create_single_otel_span

    test_execution = getattr(call_execution, "test_execution", None)
    run_test = getattr(test_execution, "run_test", None)
    agent_def = getattr(test_execution, "agent_definition", None) or getattr(
        run_test, "agent_definition", None
    )
    organization_id = str(
        getattr(run_test, "organization_id", None)
        or getattr(call_execution, "organization_id", "")
        or ""
    )
    workspace_id = getattr(run_test, "workspace_id", None)
    workspace_id = str(workspace_id) if workspace_id else None

    # Project: the agent's observability project, else a per-org Simulations project.
    obs = getattr(agent_def, "observability_provider", None)
    project = getattr(obs, "project", None)
    if project is not None:
        project_name, project_type = project.name, project.trace_type
    else:
        project_name, project_type = "Simulations", "observe"

    session_id = None
    if turns is None:
        # CHAT: read the persisted ChatMessage rows.
        from simulate.models.chat_message import ChatMessageModel

        rows = list(
            ChatMessageModel.objects.filter(call_execution=call_execution).order_by(
                "created_at"
            )
        )
        turns = [_row_to_turn(r) for r in rows]
        session_id = next(
            (str(r.session_id) for r in rows if getattr(r, "session_id", None)), None
        )
    session_id = session_id or str(
        (getattr(call_execution, "call_metadata", None) or {}).get("chat_session_id")
        or getattr(call_execution, "id", "")
    )

    modality = (
        "voice"
        if str(getattr(call_execution, "simulation_call_type", "")) == "voice"
        else "chat"
    )

    spans = build_sim_spans(
        turns,
        modality=modality,
        project_name=project_name,
        project_type=project_type,
        session_id=session_id,
        agent_name=getattr(agent_def, "agent_name", "agent-under-test"),
        metadata={"call_execution_id": str(getattr(call_execution, "id", ""))},
        seed=str(getattr(call_execution, "id", "")) or None,
    )
    emitted = 0
    for span in spans:
        try:
            create_single_otel_span(span, organization_id, user_id, workspace_id)
            emitted += 1
        except Exception:  # pragma: no cover - one bad span must not lose the rest
            logger.exception("sim_trace_span_emit_failed", extra={"name": span.get("name")})
    return emitted


def attach_sim_evals_to_trace(call_execution, eval_attributes: dict[str, Any]) -> int:
    """Write eval results onto the sim trace's root span, AFTER evals run.

    The trace is emitted at sim completion (before async evals finish), so this is
    the second half: it finds the root span by its DETERMINISTIC id (same seed = the
    call id) and merges the eval results into ``span_attributes``, making eval
    verdicts filterable at span granularity in the trace UI. Idempotent. Returns the
    number of root spans updated (0 if the trace wasn't emitted / no evals).
    """
    from tracer.models.observation_span import ObservationSpan

    seed = str(getattr(call_execution, "id", ""))
    if not seed or not eval_attributes:
        return 0
    root_span_id = _det_span_id(seed, "root")
    updated = 0
    # ObservationSpan's PK ``id`` IS the OTLP span id (CharField); there is no
    # separate ``span_id`` column — mirror tracer.utils.eval_tasks which filters
    # spans by ``id__in``. (Caught by the TH-5642 local DB-verified eval run.)
    for span in ObservationSpan.objects.filter(id=root_span_id):
        attrs = dict(span.span_attributes or {})
        attrs.update(eval_attributes)
        span.span_attributes = attrs
        span.save(update_fields=["span_attributes"])
        updated += 1
    if not updated:
        logger.info("sim_eval_attach_no_root_span", call_execution_id=seed)
    return updated
