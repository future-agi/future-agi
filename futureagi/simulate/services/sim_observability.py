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

build_sim_spans is Django-free and pure (returns plain dicts) so it is trivially
unit-tested; the thin emit layer (emit_sim_trace) resolves the project + ingest
credentials and exports the spans to the fi-collector — the same OTLP ingestion
path production traffic uses — via simulate.services.sim_collector_emit.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC
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
# The voice observability surface lists ROOT spans WHERE
# observation_type='conversation' (see query_builders/voice_call_list.py) —
# the same shape the provider pullers emit. A voice sim root emitted as
# AGENT never appears next to pulled calls, defeating this module's goal of
# landing sims in the same UI as production traffic.
SPAN_KIND_CONVERSATION = "CONVERSATION"


def _trace_id() -> str:
    return secrets.token_hex(16)  # 32 hex chars (128-bit), OTLP trace id


def _span_id() -> str:
    return secrets.token_hex(8)  # 16 hex chars (64-bit), OTLP span id


def _span(
    *,
    name,
    kind,
    span_id,
    parent_span_id,
    trace_id,
    attributes,
    project_name,
    project_type,
    latency=None,
    start_ns=None,
    end_ns=None,
) -> dict[str, Any]:
    # start_time/end_time are NANOSECOND epoch ints — the OTel ingest converter
    # does ``otel_span.get("start_time", 0) / 1e9`` with default 0, so spans
    # emitted WITHOUT timestamps landed at 1970-01-01: invisible in the
    # CH-backed trace UI (the spans store's 90-day TTL deletes them on merge)
    # and mis-sorted everywhere (found by the cross-provider sync audit).
    timing = {}
    if start_ns is not None:
        timing["start_time"] = int(start_ns)
    if end_ns is not None:
        timing["end_time"] = int(end_ns)
    return {
        **timing,
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
    modality: str,  # "chat" | "voice"
    project_name: str,
    project_type: str = "observe",
    session_id: str | None = None,
    agent_name: str = "agent-under-test",
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
    seed: str | None = None,
    started_at=None,
    ended_at=None,
    eval_attributes: dict[str, Any] | None = None,
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

    # Real wall-clock window for the trace. Fall back to "now" (a zero-length
    # window) rather than emitting timestamp-less spans — see _span().
    from datetime import datetime

    def _to_ns(dt) -> int:
        return int(dt.timestamp() * 1e9)

    _now = datetime.now(UTC)
    _start_dt = started_at or ended_at or _now
    _end_dt = ended_at or started_at or _now
    if _end_dt < _start_dt:
        _start_dt, _end_dt = _end_dt, _start_dt
    root_start_ns = _to_ns(_start_dt)
    root_end_ns = _to_ns(_end_dt)
    n_agent_turns = max(1, sum(1 for t in turns if t.get("role") == "assistant"))
    _slice_ns = max(1, (root_end_ns - root_start_ns) // n_agent_turns)

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
    # Eval verdicts (attached after evals finish) ride on the root span so they
    # are filterable at trace granularity, same as the at-emit attributes. A
    # re-emit with these set replaces the root row in CH (ReplacingMergeTree on
    # span id) without a separate write path.
    if eval_attributes:
        base_attrs.update(eval_attributes)

    root_kind = SPAN_KIND_CONVERSATION if modality == "voice" else SPAN_KIND_AGENT
    if modality == "voice":
        base_attrs["call.status"] = "completed"
        base_attrs["call.duration"] = max(
            0, int(round((root_end_ns - root_start_ns) / 1e9))
        )
    spans = [
        _span(
            name=f"{modality} simulation",
            kind=root_kind,
            span_id=root_id,
            parent_span_id=None,
            trace_id=trace_id,
            attributes=base_attrs,
            project_name=project_name,
            project_type=project_type,
            start_ns=root_start_ns,
            end_ns=root_end_ns,
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
        _turn_start = root_start_ns + (turn_no - 1) * _slice_ns
        _turn_end = min(root_end_ns, _turn_start + _slice_ns)
        spans.append(
            _span(
                name=f"{modality}.turn {turn_no}",
                kind=SPAN_KIND_LLM,
                span_id=llm_id,
                parent_span_id=root_id,
                trace_id=trace_id,
                attributes=attrs,
                project_name=project_name,
                project_type=project_type,
                latency=t.get("latency_ms"),
                start_ns=_turn_start,
                end_ns=_turn_end,
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
                    name=tc.get("name") or "tool",
                    kind=SPAN_KIND_TOOL,
                    span_id=tool_sid,
                    parent_span_id=llm_id,
                    trace_id=trace_id,
                    start_ns=_turn_start,
                    end_ns=_turn_end,
                    attributes={
                        TOOL_NAME: tc.get("name") or "",
                        TOOL_CALL_ID: tc.get("id") or "",
                        TOOL_CALL_ARGUMENTS: tc.get("arguments") or "",
                        TOOL_CALL_RESULT: tc.get("result") or "",
                    },
                    project_name=project_name,
                    project_type=project_type,
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
                out.append(
                    {
                        "name": fn.get("name") or tc.get("name") or "",
                        "arguments": fn.get("arguments") or tc.get("arguments") or "",
                        "result": tc.get("result") or "",
                        "id": tc.get("id") or "",
                    }
                )
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
    eval_attributes: dict[str, Any] | None = None,
) -> int:
    """Emit a trace (span tree) for a completed sim CallExecution.

    Resolves the project from the agent definition's observability provider
    (falling back to a per-org "Simulations" project), builds the span tree, and
    exports it to the fi-collector — the same OTLP ingestion path production SDK
    traffic uses — so the sim lands in CH ``spans`` and the same trace/Session UI
    as production traces.

    ``turns``: pass a normalized ``[{"role","content",...}]`` conversation
    directly — the VOICE completion hook does this. When omitted, the persisted
    conversation is re-read (ChatMessage for chat, CallTranscript for voice), so
    a later eval-attach re-emit needs no turns.

    ``eval_attributes``: when set, merged onto the root span. Used by
    ``attach_sim_evals_to_trace`` to re-emit the trace with eval verdicts — the
    deterministic ids make the re-export replace the root row in CH rather than
    duplicate it.

    Returns the number of spans emitted (exported to the collector).
    """
    from tracer.services.collector_ingest import emit_spans_to_collector

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

    modality = (
        "voice"
        if str(getattr(call_execution, "simulation_call_type", "")) == "voice"
        else "chat"
    )

    session_id = None
    if turns is None:
        turns, session_id = _resolve_turns(call_execution, modality)
    session_id = session_id or str(
        (getattr(call_execution, "call_metadata", None) or {}).get("chat_session_id")
        or getattr(call_execution, "id", "")
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
        started_at=getattr(call_execution, "started_at", None)
        or getattr(call_execution, "created_at", None),
        ended_at=getattr(call_execution, "ended_at", None),
        eval_attributes=eval_attributes,
    )
    return emit_spans_to_collector(
        spans,
        project_name=project_name,
        project_type=project_type,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def attach_sim_evals_to_trace(call_execution, eval_attributes: dict[str, Any]) -> int:
    """Write eval results onto the sim trace's root span, AFTER evals run.

    The trace is emitted at sim completion (before async evals finish), so this
    is the second half. Spans now live in CH ``spans`` (written by the
    collector), not PG, so the eval verdicts are attached by RE-EMITTING the
    trace with ``eval_attributes`` merged onto the root: the deterministic span
    ids make the CH ``spans`` ReplacingMergeTree replace the root row in place
    instead of duplicating it. Idempotent. Returns the number of spans
    re-exported (0 if the conversation could not be re-resolved / no evals).
    """
    seed = str(getattr(call_execution, "id", ""))
    if not seed or not eval_attributes:
        return 0
    emitted = emit_sim_trace(call_execution, eval_attributes=eval_attributes)
    if not emitted:
        logger.info("sim_eval_attach_no_spans", call_execution_id=seed)
    return emitted


def _resolve_turns(
    call_execution, modality: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Re-read a sim conversation as normalized turns + the session id.

    Chat turns come from ChatMessage rows; voice turns from CallTranscript rows
    (the voice completion hook stores its transcript there, not in ChatMessage).
    Re-readability is what lets eval-attach re-emit without the caller threading
    the turns back through.
    """
    if modality == "voice":
        from simulate.models.test_execution import CallTranscript

        rows = list(
            CallTranscript.objects.filter(call_execution=call_execution).order_by(
                "start_time_ms", "created_at"
            )
        )
        turns = [
            {
                "role": (
                    "assistant"
                    if str(getattr(r, "speaker_role", "")) == "assistant"
                    else "user"
                ),
                "content": getattr(r, "content", "") or "",
            }
            for r in rows
        ]
        return turns, None

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
    return turns, session_id
