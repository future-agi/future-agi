"""Compact evidence for a provider call log, served to the trace investigator.

A provider conversation is stored as one span that holds the provider's whole
call payload, with the transcript repeated in several attributes. The
investigator reads evidence in small windows, so it pays for every copy. This
module turns that span into one small row: the call facts, the agent's own
instructions, the configured lines, and the turn list with tool activity.
"""

import uuid
from collections.abc import Callable, Mapping
from typing import Any

from django.utils import timezone

from tracer.models.observability_provider import ProviderChoices
from tracer.models.observation_span import ObservationType
from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationAttempt,
    TraceInvestigationAttemptStatus,
)
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.v2.span_reader import CHSpan
from tracer.services.trace_investigation import InvestigationConflict, _token_digest
from tracer.utils.attribute_accessor import span_raw_log

# The audio tool of the investigator finds the recording under these keys.
_RECORDING_KEYS = ("conversation.recording.mono.combined", "gen_ai.voice.recording.url")
_LATENCY_STATS = ("p50", "p90", "max", "num")
# What the dossier leaves out, so the reader does not take silence for absence.
_NOT_INCLUDED = ["provider_log", "recording_audio"]


def _seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 2)


def _retell_turns(entries: object) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for index, entry in enumerate(entries if isinstance(entries, list) else []):
        if not isinstance(entry, Mapping):
            continue
        role = entry.get("role")
        if role in ("agent", "user"):
            words = [
                word
                for word in entry.get("words") or []
                if isinstance(word, Mapping) and _seconds(word.get("start")) is not None
            ]
            start = _seconds(words[0].get("start")) if words else None
            end = _seconds(words[-1].get("end")) if words else None
            turn = {
                "i": index,
                "role": role,
                "start": start,
                "end": end,
                "text": entry.get("content"),
            }
            if role == "agent":
                # Word timings that span no time: the provider wrote text that
                # the caller never heard.
                turn["spoken"] = start is not None and end is not None and end > start
            turns.append(turn)
        elif role == "tool_call_invocation":
            turns.append(
                {
                    "i": index,
                    "role": "tool_call",
                    "at": _seconds(entry.get("time_sec")),
                    "id": entry.get("tool_call_id"),
                    "name": entry.get("name"),
                    "arguments": entry.get("arguments"),
                }
            )
        elif role == "tool_call_result":
            turns.append(
                {
                    "i": index,
                    "role": "tool_result",
                    "at": _seconds(entry.get("time_sec")),
                    "id": entry.get("tool_call_id"),
                    "ok": entry.get("successful"),
                    "content": entry.get("content"),
                }
            )
        elif role == "node_transition":
            turns.append(
                {
                    "i": index,
                    "role": "transition",
                    "at": _seconds(entry.get("time_sec")),
                    "from": entry.get("former_node_name"),
                    "to": entry.get("new_node_name"),
                    "type": entry.get("transition_type"),
                }
            )
    return turns


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _retell_dossier(raw_log: Mapping[str, Any]) -> dict[str, Any]:
    analysis = _mapping(raw_log.get("call_analysis"))
    duration_ms = _seconds(raw_log.get("duration_ms"))
    return {
        "call": {
            "status": raw_log.get("call_status"),
            "direction": raw_log.get("direction"),
            "duration_seconds": (
                round(duration_ms / 1000, 2) if duration_ms is not None else None
            ),
            "ended_reason": raw_log.get("disconnection_reason"),
            "agent": {
                "id": raw_log.get("agent_id"),
                "version": raw_log.get("agent_version"),
                "name": raw_log.get("agent_name"),
            },
        },
        "variables": {
            "configured": dict(_mapping(raw_log.get("retell_llm_dynamic_variables"))),
            "collected": dict(_mapping(raw_log.get("collected_dynamic_variables"))),
        },
        "analysis": {
            "summary": analysis.get("call_summary"),
            "successful": analysis.get("call_successful"),
            "in_voicemail": analysis.get("in_voicemail"),
            "sentiment": analysis.get("user_sentiment"),
            "flags": dict(_mapping(analysis.get("custom_analysis_data"))),
        },
        "latency_ms": {
            name: {key: stats.get(key) for key in _LATENCY_STATS}
            for name, stats in _mapping(raw_log.get("latency")).items()
            if isinstance(stats, Mapping)
        },
        "turns": _retell_turns(raw_log.get("transcript_with_tool_calls")),
    }


_PROVIDER_DOSSIERS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    ProviderChoices.RETELL.value: _retell_dossier,
}


def conversation_dossier(span: CHSpan) -> dict[str, Any] | None:
    """The compact call record of a provider conversation span.

    None when the span is not a provider call log this module can read; the
    investigator then reads the span as it is stored.
    """
    if span.observation_type != ObservationType.CONVERSATION:
        return None
    attrs = span.attrs_string
    build = _PROVIDER_DOSSIERS.get(attrs.get("gen_ai.system") or span.provider)
    raw_log = span_raw_log(attrs, span_id=span.id)
    if build is None or not raw_log:
        return None
    system_prompt = (
        attrs.get("llm.input_messages.0.message.content")
        if attrs.get("llm.input_messages.0.message.role") == "system"
        else None
    )
    return {
        "provider": attrs.get("gen_ai.system") or span.provider,
        "agent_instructions": system_prompt,
        **build(raw_log),
        "not_included": _NOT_INCLUDED,
    }


def conversation_evidence_rows(
    *, attempt_id: uuid.UUID, lease_token: str
) -> dict[str, list[dict[str, Any]]]:
    """Evidence rows for a claimed trace attempt whose trace is a provider call.

    The rows have the shape the investigator indexes for stored spans, so its
    evidence ids, citations and coverage work unchanged. No rows means the
    trace is not a provider call log: the caller reads the stored spans.
    """
    attempt = (
        TraceInvestigationAttempt.no_workspace_objects.select_related("job")
        .filter(
            id=attempt_id,
            job__workload_type=InvestigationWorkload.TRACE,
            status=TraceInvestigationAttemptStatus.CLAIMED,
            lease_expires_at__gt=timezone.now(),
            lease_token_digest=_token_digest(lease_token),
        )
        .first()
    )
    if attempt is None:
        raise InvestigationConflict("attempt is not active")
    job = attempt.job
    with get_reader() as reader:
        roots = reader.roots_by_trace_ids(
            [str(job.trace_id)], project_id=str(job.project_id)
        )
    if len(roots) != 1:
        return {"rows": []}
    span = roots[0]
    dossier = conversation_dossier(span)
    if dossier is None:
        return {"rows": []}
    return {
        "rows": [
            {
                "project_id": str(job.project_id),
                "trace_id": str(job.trace_id),
                "org_id": str(job.organization_id),
                "id": span.id,
                "parent_span_id": span.parent_span_id or "",
                "name": span.name,
                "observation_type": span.observation_type,
                "start_time": span.start_time.isoformat(),
                "end_time": span.end_time.isoformat() if span.end_time else None,
                "attrs_string": {
                    key: span.attrs_string[key]
                    for key in _RECORDING_KEYS
                    if span.attrs_string.get(key)
                },
                "conversation": dossier,
            }
        ]
    }
