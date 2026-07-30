"""Export simulation spans to the fi-collector (replaces the dropped PG->CH CDC); deterministic ids keep retries idempotent."""

from __future__ import annotations

import json
import os
from typing import Any, TypedDict

import structlog
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, TraceFlags
from opentelemetry.trace.status import Status, StatusCode

logger = structlog.get_logger(__name__)

# Resource attrs the collector reads to resolve the project (org, workspace, project_name).
RES_PROJECT_NAME = "project_name"
RES_PROJECT_TYPE = "project_type"
RES_EVAL_TAGS = "eval_tags"

# Mirror gen_ai kind onto fi.span.kind (collector's observation_type key; else generic "SPAN").
SPAN_KIND_ATTR_SRC = "gen_ai.span.kind"
SPAN_KIND_ATTR_DST = "fi.span.kind"

_SCOPE = InstrumentationScope("fi.simulation", "1.0.0")
_SAMPLED = TraceFlags(TraceFlags.SAMPLED)


class SimSpanDict(TypedDict, total=False):
    """OTLP span dict from ``build_sim_spans``: ns-epoch times, hex ids, ``None`` root parent."""

    start_time: int
    end_time: int
    trace_id: str
    span_id: str
    parent_span_id: str | None
    parent_id: str | None
    name: str
    latency: float | None
    project_name: str
    project_type: str
    attributes: dict[str, Any]
    # "ERROR" -> StatusCode.ERROR, else OK; set by provider exports for failed calls.
    status_code: str


def _collector_endpoint() -> str:
    """fi-collector OTLP gRPC endpoint; ``SIM_COLLECTOR_OTLP_ENDPOINT`` overrides."""
    return (
        os.environ.get("SIM_COLLECTOR_OTLP_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or "fi-collector:4317"
    )


def _otlp_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Coerce attrs to OTLP-legal values: scalars/homogeneous scalar seqs pass through, nested/mixed are JSON-serialized (as the FI SDK does)."""
    out: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            out[key] = value
        elif isinstance(value, (list, tuple)) and all(
            isinstance(v, (str, bool, int, float)) for v in value
        ):
            out[key] = list(value)
        else:
            out[key] = json.dumps(value, default=str)
    # Mirror span kind onto the collector's observation_type key.
    if SPAN_KIND_ATTR_SRC in out and SPAN_KIND_ATTR_DST not in out:
        out[SPAN_KIND_ATTR_DST] = out[SPAN_KIND_ATTR_SRC]
    return out


def _readable_span(span: SimSpanDict, resource: Resource) -> ReadableSpan:
    """Build an OTLP ReadableSpan from a ``build_sim_spans`` dict; hex ids parse into the OTLP integer id space, aligning sim ids with production SDK / CH ``traces``."""
    trace_id = int(span["trace_id"], 16)
    span_id = int(span["span_id"], 16)
    ctx = SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        is_remote=False,
        trace_flags=_SAMPLED,
    )
    parent_hex = span.get("parent_span_id") or span.get("parent_id")
    parent = (
        SpanContext(
            trace_id=trace_id,
            span_id=int(parent_hex, 16),
            is_remote=False,
            trace_flags=_SAMPLED,
        )
        if parent_hex
        else None
    )
    return ReadableSpan(
        name=span["name"],
        context=ctx,
        parent=parent,
        resource=resource,
        attributes=_otlp_attributes(span.get("attributes", {})),
        events=(),
        links=(),
        # observation_type derives from the span-kind attr, not OTel SpanKind.
        kind=SpanKind.INTERNAL,
        status=Status(
            StatusCode.ERROR
            if span.get("status_code") == "ERROR"
            else StatusCode.OK
        ),
        start_time=span.get("start_time"),
        end_time=span.get("end_time"),
        instrumentation_scope=_SCOPE,
    )


def export_sim_spans(
    spans: list[SimSpanDict],
    *,
    project_name: str,
    project_type: str,
    api_key: str,
    secret_key: str,
    eval_tags: list[dict[str, Any]] | None = None,
    service_name: str = "fi-simulation",
) -> int:
    """Export sim spans to the fi-collector over OTLP/gRPC; returns spans accepted.
    Best-effort: a collector failure logs and returns 0 rather than failing the sim."""
    if not spans:
        return 0

    resource = Resource.create(
        {
            RES_PROJECT_NAME: project_name,
            RES_PROJECT_TYPE: project_type,
            RES_EVAL_TAGS: json.dumps(eval_tags or []),
            "service.name": service_name,
        }
    )
    exporter = OTLPSpanExporter(
        endpoint=_collector_endpoint(),
        insecure=True,
        headers=(("x-api-key", api_key), ("x-secret-key", secret_key)),
    )
    try:
        # Build inside the try so a malformed span is caught here, not the worker.
        readable = [_readable_span(s, resource) for s in spans]
        result = exporter.export(readable)
    except Exception:
        logger.exception("sim_collector_export_failed", project=project_name)
        return 0
    finally:
        exporter.shutdown()

    if result is not SpanExportResult.SUCCESS:
        logger.warning(
            "sim_collector_export_rejected",
            project=project_name,
            result=str(result),
            span_count=len(readable),
        )
        return 0
    return len(readable)
