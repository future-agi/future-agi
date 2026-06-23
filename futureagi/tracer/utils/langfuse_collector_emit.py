"""Emit Langfuse-ingested observations to the fi-collector (TH-5642, CDC-off).

The Langfuse public-ingestion path (``langfuse_upsert``) writes ObservationSpans
to PG and mirrors CH ``traces`` + curated dims, but NEVER CH ``spans`` — the
table the observe UI reads. CDC-off (the legacy PG→CH PeerDB chain dropped) those
observations are therefore invisible in the CH-backed UI, exactly like the
provider pulls and sim were before they were routed through the collector.

This module is the Langfuse half of that same seam: after the PG upserts commit,
it rebuilds OTLP spans from the persisted ObservationSpans and ships them through
``emit_spans_to_collector`` so they land in CH ``spans``. PG stays the source of
truth (evals/annotations FK targets unchanged).

Key id rules (mirror of the corrected design):
  * ``trace_id`` = the PG trace UUID hex (``trace.id.hex``). The collector
    reformats it back to the dashed UUID, which == CH ``traces.id`` ==
    ``trace_dict`` key, so ``spans.trace_name`` resolves and spans/traces agree.
    (Do NOT derive it from the langfuse external_id — that breaks trace_name.)
  * ``span_id`` = ``uuid5(NS, "lf-span:{external_id}:{key}")[:16]`` where ``key``
    is ``"root"`` for the synthetic root else the langfuse observation id. Stable
    across Vapi multi-batch re-sends (deterministic) → ReplacingMergeTree dedups.
"""

from __future__ import annotations

import uuid

import structlog

logger = structlog.get_logger(__name__)

# Fixed namespace for deterministic Langfuse span ids (distinct from sim's).
_LF_SPAN_NS = uuid.UUID("7f3a2b6c-2d41-5e80-9c11-8a1b2c3d4e5f")


def _key(obs_id, root_span_id: str) -> str:
    return "root" if str(obs_id) == root_span_id else str(obs_id)


def _span_id(external_id: str, key: str) -> str:
    return uuid.uuid5(_LF_SPAN_NS, f"lf-span:{external_id}:{key}").hex[:16]


def build_langfuse_otlp_spans(trace, external_id: str, root_span_id: str) -> list[dict]:
    """Build collector-shaped OTLP span dicts from the trace's persisted PG spans."""
    from tracer.models.observation_span import ObservationSpan

    trace_hex = trace.id.hex
    out: list[dict] = []
    for s in ObservationSpan.no_workspace_objects.filter(trace=trace, deleted=False):
        is_root = str(s.id) == root_span_id
        sid = _span_id(external_id, _key(s.id, root_span_id))
        if is_root:
            parent = None
        else:
            parent_obs = s.parent_span_id or root_span_id
            parent = _span_id(external_id, _key(parent_obs, root_span_id))

        attrs = dict(s.span_attributes or {})
        if "fi.span.kind" not in attrs and s.observation_type:
            # converter.go reads fi.span.kind first; uppercase tolerated (ToLower'd).
            attrs["fi.span.kind"] = str(s.observation_type).upper()
        if s.input is not None and "input.value" not in attrs:
            attrs["input.value"] = s.input
        if s.output is not None and "output.value" not in attrs:
            attrs["output.value"] = s.output
        if s.model:
            attrs.setdefault("llm.model_name", s.model)
        # Tokens as NUMERIC attrs so DeriveHotKeys lands them in the token columns.
        if s.prompt_tokens:
            attrs["gen_ai.usage.input_tokens"] = int(s.prompt_tokens)
        if s.completion_tokens:
            attrs["gen_ai.usage.output_tokens"] = int(s.completion_tokens)
        if s.total_tokens:
            attrs["gen_ai.usage.total_tokens"] = int(s.total_tokens)

        d: dict = {
            "trace_id": trace_hex,
            "span_id": sid,
            "parent_span_id": parent,
            "name": s.name or "span",
            "attributes": attrs,
        }
        if s.start_time is not None:
            d["start_time"] = int(s.start_time.timestamp() * 1_000_000_000)
        if s.end_time is not None:
            d["end_time"] = int(s.end_time.timestamp() * 1_000_000_000)
        out.append(d)
    return out


def emit_langfuse_spans_to_collector(trace, external_id: str, root_span_id: str) -> int:
    """Post-commit, best-effort: ship the trace's Langfuse spans to the collector.

    Self-gated on ``dual_write_enabled()`` so PeerDB-on deployments (where PG
    spans still reach CH via CDC) do NOT double-write; CDC-off it is the only path
    that lands these spans in CH ``spans``. Never raises into the caller.
    """
    try:
        from tracer.services.clickhouse.v2.trace_writer import dual_write_enabled

        if not dual_write_enabled():
            return 0
        from tracer.models.project import Project
        from tracer.services.collector_ingest import emit_spans_to_collector

        proj = Project.no_workspace_objects.filter(id=trace.project_id).first()
        if proj is None:
            return 0
        spans = build_langfuse_otlp_spans(trace, external_id, root_span_id)
        if not spans:
            return 0
        return emit_spans_to_collector(
            spans,
            project_name=proj.name,
            project_type=proj.trace_type,
            organization_id=str(proj.organization_id),
            workspace_id=str(proj.workspace_id) if proj.workspace_id else None,
            service_name="fi-langfuse",
        )
    except Exception:
        logger.exception(
            "langfuse_collector_emit_failed", trace_id=str(getattr(trace, "id", ""))
        )
        return 0
