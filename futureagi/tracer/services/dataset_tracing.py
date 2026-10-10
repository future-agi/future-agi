"""Emit dataset/experiment row-execution spans into the existing tracing model.

Issue #2665: dataset and experiment row runs record only a final value. This
module bridges the row-execution path into the fi-collector (the sole writer of
CH ``spans``) so each row leaves a trace with spans for template rendering, the
model call, and any evals.

Every helper here is best-effort: tracing must never fail or slow a dataset run,
so emission failures are logged and swallowed.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# OTel id widths: 128-bit trace id (32 hex chars), 64-bit span id (16 hex chars).
# Must stay plain hex (no dashes): SimSpanDict ids are parsed with int(x, 16).
_TRACE_ID_BYTES = 16
_SPAN_ID_BYTES = 8

# gen_ai.span.kind values are mirrored onto the collector's fi.span.kind
# (observation_type) key by SimCollectorEmit._otlp_attributes.
SPAN_KIND_CHAIN = "CHAIN"
SPAN_KIND_LLM = "LLM"
SPAN_KIND_EVALUATOR = "EVALUATOR"

# Semantic-convention attribute keys (see tfc/telemetry/llm_spans.py).
ATTR_SPAN_KIND = "gen_ai.span.kind"
ATTR_OPERATION = "gen_ai.operation.name"
ATTR_REQUEST_MODEL = "gen_ai.request.model"
ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
ATTR_INPUT_VALUE = "input.value"
ATTR_OUTPUT_VALUE = "output.value"
ATTR_DURATION_MS = "gen_ai.client.operation.duration"
ATTR_ERROR_TYPE = "error.type"
ATTR_ERROR_MESSAGE = "error.message"

# Dataset identity attributes attached to every span so a trace can be found
# again from the grid.
ATTR_DATASET_ID = "fi.dataset.id"
ATTR_DATASET_ROW_ID = "fi.dataset.row.id"
ATTR_DATASET_COLUMN_ID = "fi.dataset.column.id"
ATTR_DATASET_VARIANT = "fi.dataset.variant"
ATTR_DATASET_SOURCE = "fi.dataset.run.source"


def new_trace_id() -> str:
    """Return a fresh OTLP-compatible trace id (32 hex chars)."""
    return secrets.token_hex(_TRACE_ID_BYTES)


def new_span_id() -> str:
    """Return a fresh OTLP-compatible span id (16 hex chars)."""
    return secrets.token_hex(_SPAN_ID_BYTES)


def now_ns() -> int:
    """Wall-clock epoch nanoseconds."""
    return time.time_ns()


def identity_attributes(
    *,
    dataset_id: str | None,
    row_id: str | None,
    column_id: str | None,
    variant: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Build the shared identity attributes for a row-execution trace."""
    attrs: dict[str, Any] = {}
    if dataset_id is not None:
        attrs[ATTR_DATASET_ID] = dataset_id
    if row_id is not None:
        attrs[ATTR_DATASET_ROW_ID] = row_id
    if column_id is not None:
        attrs[ATTR_DATASET_COLUMN_ID] = column_id
    if variant is not None:
        attrs[ATTR_DATASET_VARIANT] = variant
    if source is not None:
        attrs[ATTR_DATASET_SOURCE] = source
    return attrs


def build_span(
    *,
    name: str,
    trace_id: str,
    span_id: str,
    parent_span_id: str | None,
    start_time: int,
    end_time: int,
    span_kind: str,
    attributes: dict[str, Any] | None = None,
    status_code: str = "OK",
) -> dict[str, Any]:
    """Build a ``SimSpanDict``-compatible span dict.

    Mirrors ``simulate/services/sim_collector_emit.py::SimSpanDict``: ns-epoch
    times, hex ids, ``None`` root parent.
    """
    attrs = dict(attributes or {})
    attrs.setdefault(ATTR_SPAN_KIND, span_kind)
    return {
        "name": name,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "start_time": start_time,
        "end_time": end_time,
        "attributes": attrs,
        "status_code": status_code,
    }


def emit_dataset_spans(
    spans: list[dict[str, Any]],
    *,
    organization_id: str | None,
    workspace_id: str | None,
) -> None:
    """Best-effort emit of dataset spans to the fi-collector. Never raises."""
    if not spans:
        return
    try:
        from tracer.services.collector_ingest import emit_spans_to_collector

        emit_spans_to_collector(
            spans,
            project_name="dataset-execution",
            # "experiment" (not a bespoke "dataset" type) — the collector's
            # ProjectType enum only knows EXPERIMENT/OBSERVE, and experiment
            # projects are the "throwaway evaluation run" type (not scanned by
            # the observe feed). An unknown type would be dropped/coerced.
            project_type="experiment",
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001 - tracing must never fail a dataset run
        logger.exception("dataset_span_emit_failed", span_count=len(spans))
