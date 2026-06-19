"""Export simulation spans through the fi-collector — the common ingestion path.

Production span ingestion is collector-only: customer SDKs export OTLP to the
fi-collector, whose ClickHouse exporter writes the CH ``spans`` table directly.
The legacy PG→CH PeerDB CDC chain is dropped by default
(``CH25_DROP_LEGACY_CDC_CHAIN``), so a span that is only written to PG
``tracer_observation_span`` never reaches CH ``spans`` — the table the
voice-call / trace UI reads. Simulated conversations were on exactly that dead
path, so they were invisible in the CH-backed observability UI.

This module is the sim half of the single, shared ingestion interface: it turns
the OTLP span dicts produced by ``build_sim_spans`` into real OTLP spans and
exports them to the fi-collector, authenticated with the call's org/workspace
ingest key. The collector resolves/creates the project from the key plus the
``project_name`` resource attribute and writes CH ``spans`` natively — exactly
as it does for production SDK traffic. Sim therefore stops depending on the
KEEP-PG ``create_single_otel_span`` write path entirely.

Idempotency is preserved: ``build_sim_spans`` assigns deterministic trace/span
ids (uuid5 of the call id), so a Temporal retry re-exports the same ids and the
CH ``spans`` ReplacingMergeTree collapses the duplicate. Eval verdicts are
attached by re-exporting the root span (same id) with the verdicts merged in.
"""

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

# Resource attribute keys the fi-collector reads (mirrors fi_instrumentation.otel
# PROJECT_NAME / PROJECT_TYPE / EVAL_TAGS — the SDK resource contract). The
# collector resolves the project by (org, workspace, project_name) and ignores
# any client-supplied fi.project_id, so project_name is the only project lever.
RES_PROJECT_NAME = "project_name"
RES_PROJECT_TYPE = "project_type"
RES_EVAL_TAGS = "eval_tags"

# build_sim_spans carries the span kind under the OpenInference/GenAI key, but the
# collector derives observation_type from `fi.span.kind` / `openinference.span.kind`
# (clickhouse25exporter/converter.go) — NOT `gen_ai.span.kind`. Mirror the value
# onto the key the collector reads so the span lands as conversation/llm/tool
# instead of the generic "SPAN".
SPAN_KIND_ATTR_SRC = "gen_ai.span.kind"
SPAN_KIND_ATTR_DST = "fi.span.kind"

_SCOPE = InstrumentationScope("fi.simulation", "1.0.0")
_SAMPLED = TraceFlags(TraceFlags.SAMPLED)


class SimSpanDict(TypedDict, total=False):
    """The OTLP span dict shape produced by ``build_sim_spans``.

    ``start_time``/``end_time`` are nanosecond epoch ints (omitted only when the
    builder could not resolve a window); ``trace_id`` is 32 hex chars, ``span_id``
    16 hex chars, ``parent_span_id`` 16 hex chars or ``None`` for the root.
    """

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


def _collector_endpoint() -> str:
    """gRPC endpoint of the fi-collector OTLP receiver.

    Reuses the backend's configured OTLP endpoint so sim points at the same
    collector as the app's own telemetry; ``SIM_COLLECTOR_OTLP_ENDPOINT`` lets a
    deployment override it independently. Default targets the docker-compose
    service name.
    """
    return (
        os.environ.get("SIM_COLLECTOR_OTLP_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or "fi-collector:4317"
    )


def _otlp_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Coerce sim span attributes into OTLP-legal values.

    OTLP attribute values must be scalars or homogeneous scalar sequences — a
    nested dict (``metadata``, tool-call args) or mixed list is not allowed, so
    those are JSON-serialized, matching how the FI SDK flattens them.
    """
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
    # Mirror the span kind onto the key the collector reads for observation_type.
    if SPAN_KIND_ATTR_SRC in out and SPAN_KIND_ATTR_DST not in out:
        out[SPAN_KIND_ATTR_DST] = out[SPAN_KIND_ATTR_SRC]
    return out


def _readable_span(span: SimSpanDict, resource: Resource) -> ReadableSpan:
    """Build an OTLP ReadableSpan from a ``build_sim_spans`` dict.

    The deterministic hex ids are parsed straight into the OTLP integer id
    space (128-bit trace, 64-bit span); the collector formats trace_id back to a
    dashed UUID and keeps span_id as 16-char hex, so sim ids line up with
    production SDK ids and with CH ``traces``.
    """
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
        # observation_type is derived by the collector from the
        # gen_ai.span.kind attribute (set by build_sim_spans), not OTel SpanKind.
        kind=SpanKind.INTERNAL,
        status=Status(StatusCode.OK),
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
) -> int:
    """Export sim spans to the fi-collector over OTLP/gRPC.

    Returns the number of spans accepted by the collector (0 on a failed
    export). Best-effort and loud: a collector hiccup logs at ``warning`` and
    returns 0 rather than failing the sim — span emission is observability, not
    the sim's primary result.
    """
    if not spans:
        return 0

    resource = Resource.create(
        {
            RES_PROJECT_NAME: project_name,
            RES_PROJECT_TYPE: project_type,
            RES_EVAL_TAGS: json.dumps(eval_tags or []),
            "service.name": "fi-simulation",
        }
    )
    readable = [_readable_span(s, resource) for s in spans]

    exporter = OTLPSpanExporter(
        endpoint=_collector_endpoint(),
        insecure=True,
        headers=(("x-api-key", api_key), ("x-secret-key", secret_key)),
    )
    try:
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
