"""
DB helpers for the trace scanner pipeline.

Handles: config checks, span fetching, result writing.
All return typed dataclasses — no raw dicts at the boundary.
"""

import hashlib
import json

import structlog

from tracer.models.trace_scan import TraceScanConfig, TraceScanResult
from tracer.types.scan_types import ScanConfig, SpanData, TraceData

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_scan_config(project_id: str) -> ScanConfig | None:
    """
    Get scan config for a project. Returns None if scanning is disabled.

    Lazy-creates a default config on first access so the setting is always
    visible in the project's tracing settings UI instead of being a hidden
    code fallback.
    """
    config, _ = TraceScanConfig.objects.get_or_create(
        project_id=project_id,
        defaults={"sampling_rate": 0, "enabled": True},
    )
    if not config.enabled:
        return None
    return ScanConfig(
        sampling_rate=config.sampling_rate,
        scan_version=config.scan_version,
        enabled=config.enabled,
    )


# ---------------------------------------------------------------------------
# Sampling & filtering
# ---------------------------------------------------------------------------


def is_trace_sampled(trace_id: str, sampling_rate: float) -> bool:
    """Stable per-trace sampling decision.

    Hashes the trace_id (not Python's per-process-salted ``hash()``) so the
    sweep and ``scan_and_write`` reach the SAME verdict for a trace without
    persisting a marker for every skipped one — the sweep relies on this to lag
    its watermark behind only sampled-in work. Consistent head sampling: the
    scanned set is a fixed ~rate fraction, stable across retries.
    """
    if sampling_rate >= 1.0:
        return True
    if sampling_rate <= 0.0:
        return False
    digest = hashlib.md5(str(trace_id).encode()).hexdigest()[:8]
    return int(digest, 16) / 0xFFFFFFFF < sampling_rate


def apply_sampling(trace_ids: list[str], sampling_rate: float) -> list[str]:
    """Filter trace IDs by the stable sampling decision."""
    if sampling_rate >= 1.0:
        return trace_ids
    return [tid for tid in trace_ids if is_trace_sampled(tid, sampling_rate)]


def filter_already_scanned(trace_ids: list[str]) -> list[str]:
    """Remove trace IDs that already have a scan result.

    ``no_workspace_objects`` so the anti-join is correct in the worker (no
    ambient workspace) and can't be silently scoped to a leaked one. Both sides
    are normalised to ``str``: the column is a UUID, so ``values_list`` yields
    ``uuid.UUID`` while callers pass CH/ingest string ids — comparing the two
    raw never matches, so the anti-join would silently keep everything.
    """
    already_scanned = {
        str(t)
        for t in TraceScanResult.no_workspace_objects.filter(trace_id__in=trace_ids)
        .values_list("trace_id", flat=True)
        .iterator()
    }
    return [tid for tid in trace_ids if str(tid) not in already_scanned]


def mark_traces_failed(trace_ids: list[str], project_id: str, reason: str) -> int:
    """Write a FAILED marker per trace so the anti-join treats it as terminal.

    The sweep abandons a sampled-in trace here when it stays unscanned past the
    watermark lag bound (stuck scanner/CH, or a candidate root that resolves no
    span data) — giving it a durable record instead of silently losing it or
    letting it pin the cursor. Idempotent: skips ids that already have a result.
    """
    from tracer.models.trace_scan import TraceScanStatus

    fresh = filter_already_scanned([str(t) for t in trace_ids])
    if not fresh:
        return 0
    TraceScanResult.objects.bulk_create(
        [
            TraceScanResult(
                trace_id=tid,
                project_id=project_id,
                status=TraceScanStatus.FAILED,
                has_issues=False,
                error_message=reason,
            )
            for tid in fresh
        ],
        ignore_conflicts=True,
    )
    return len(fresh)


# ---------------------------------------------------------------------------
# Fetch trace data
# ---------------------------------------------------------------------------

# Map our observation_type to the span role the scanner understands.
# Kept vendor-neutral — compress_v2 reads span kind by suffix, not by
# a specific SDK prefix, so we just emit plain "span.kind".
_OBS_TYPE_TO_KIND = {
    "GENERATION": "LLM",
    "SPAN": "CHAIN",
    "TOOL": "Tool",
    "RETRIEVER": "Retriever",
    "AGENT": "AGENT",
}

_TOKEN_KEYS = [
    "llm.token_count.prompt",
    "llm.token_count.completion",
    "gen_ai.usage.prompt_tokens",
    "gen_ai.usage.completion_tokens",
]


def fetch_trace_data(trace_ids: list[str]) -> list[TraceData]:
    """Fetch trace spans from DB and build nested span trees for the scanner."""
    # Was: ObservationSpan.objects.filter(trace_id=).order_by("start_time")
    #      .values("id", "name", "parent_span_id", "start_time", "end_time",
    #              "input", "output", "metadata", "model", "observation_type",
    #              "status_message") — one PG query per trace_id.
    # Now: a single CH `list_by_trace_ids` covers every trace in the batch
    # and returns spans in (trace_id, start_time, id) order, which matches
    # the per-trace .order_by("start_time") contract the scanner relies on
    # (children come after parents within the same trace, and span_map is
    # populated before linking — order across parents/children doesn't
    # change the resulting tree).
    from tracer.services.clickhouse.v2 import get_reader

    if not trace_ids:
        return []

    with get_reader() as reader:
        all_spans = reader.list_by_trace_ids([str(t) for t in trace_ids])

    # Group CH spans by trace_id while preserving CH's start_time order.
    by_trace: dict[str, list] = {}
    for span in all_spans:
        by_trace.setdefault(str(span.trace_id), []).append(span)

    traces: list[TraceData] = []
    # Iterate the caller-provided trace_ids order so the consumer sees the
    # same order as the legacy per-trace loop (the previous code processed
    # each trace_id in input order).
    for trace_id in trace_ids:
        ch_spans = by_trace.get(str(trace_id))
        if not ch_spans:
            continue

        # Build flat map
        span_map: dict[str, SpanData] = {}
        for span in ch_spans:
            span_map[span.id] = _ch_span_to_span(span)

        # Link children → parents (root = no parent or parent not in map)
        root_spans = []
        for span in ch_spans:
            sd = span_map[span.id]
            parent_id = span.parent_span_id
            if parent_id and parent_id in span_map:
                span_map[parent_id].child_spans.append(sd)
            else:
                root_spans.append(sd)

        traces.append(TraceData(trace_id=str(trace_id), spans=root_spans))

    return traces


def _ch_span_to_span(span) -> SpanData:
    """Convert a CHSpan dataclass to SpanData.

    CHSpan stores input / output / metadata as JSON strings (vs the legacy
    PG ObservationSpan.JSONField which returned dicts). The scanner's
    span_attributes contract is string-valued, so we json.loads metadata
    for the token-key lookup but treat input/output as opaque strings
    (the legacy `str(inp)`/`json.dumps(inp)` branch produced the same shape
    when `inp` arrived as already-serialized JSON).
    """
    duration = ""
    if span.start_time and span.end_time:
        delta = (span.end_time - span.start_time).total_seconds()
        duration = f"PT{delta}S"

    metadata: dict = {}
    if span.metadata:
        try:
            parsed = json.loads(span.metadata)
            metadata = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    attrs: dict[str, object] = {}
    if span.input:
        attrs["input.value"] = span.input
    if span.output:
        attrs["output.value"] = span.output
    if span.model:
        attrs["llm.model_name"] = span.model

    obs_type = span.observation_type or ""
    # Default unrecognized / missing types (commonly "unknown" from older SDKs)
    # to CHAIN so the scanner still sees structural role info instead of a blank span.
    attrs["span.kind"] = _OBS_TYPE_TO_KIND.get(obs_type, "CHAIN")

    # Surface function-calling tool definitions so the scanner can derive the
    # AVAILABLE tool set (vs. tools actually called). Kept verbatim from the raw
    # span attributes — no transformation, the scanner parses the names.
    # The definitions are a top-level key living in the typed string map or
    # the attributes_extra JSON overflow, depending on value size. Some
    # producers wrote attributes_extra as a stringified dict, so the reader
    # can hand back a double-encoded value — decode up to twice.
    extra_attrs: dict = {}
    if span.attributes_extra:
        try:
            parsed_extra = json.loads(span.attributes_extra)
            if isinstance(parsed_extra, str):
                parsed_extra = json.loads(parsed_extra)
            extra_attrs = parsed_extra if isinstance(parsed_extra, dict) else {}
        except (json.JSONDecodeError, TypeError):
            extra_attrs = {}
    for tool_key in ("llm.tools", "gen_ai.tool.definitions"):
        val = (span.attrs_string or {}).get(tool_key) or extra_attrs.get(tool_key)
        if val:
            attrs[tool_key] = val

    for key in _TOKEN_KEYS:
        if key in metadata:
            attrs[key] = metadata[key]

    status = "Unset"
    if span.status_message:
        status = "Error" if "error" in str(span.status_message).lower() else "Ok"

    return SpanData(
        span_id=str(span.id),
        span_name=span.name or "unknown",
        duration=duration,
        status_code=status,
        span_attributes=attrs,
    )


# ---------------------------------------------------------------------------
# Write results
# ---------------------------------------------------------------------------


def write_scan_results(
    results: list,  # List[ScanResult] from agentic_eval
    project_id: str,
    scan_version: str,
) -> int:
    """
    Write scanner results to DB. Returns count of successfully written results.

    Creates TraceScanResult + TraceScanIssue per trace.
    Failed writes still create a FAILED TraceScanResult to prevent re-scanning.
    """

    from tracer.models.trace_scan import (
        TraceScanIssue,
        TraceScanResult,
        TraceScanStatus,
    )

    written = 0

    for result in results:
        try:
            # Serialize dataclasses to JSON-safe dicts for JSONField storage.
            # role/span/status/is_failure are the deterministic span
            # attribution that lets the FE render a grounded breadcrumb.
            key_moments = [
                {
                    "kevinified": km.kevinified,
                    "verbatim": km.verbatim,
                    "role": km.role,
                    "span": km.span,
                    "status": km.status,
                    "is_failure": km.is_failure,
                }
                for km in result.key_moments
            ]
            meta = {
                "tools_called": result.meta.tools_called,
                "tools_available": result.meta.tools_available,
                "turn_count": result.meta.turn_count,
            }

            scan_result = TraceScanResult.objects.create(
                trace_id=result.trace_id,
                project_id=project_id,
                status=(
                    TraceScanStatus.FAILED
                    if result.error
                    else TraceScanStatus.COMPLETED
                ),
                has_issues=result.has_issues,
                key_moments=key_moments,
                meta=meta,
                scan_version=scan_version,
                error_message=result.error,
            )

            if result.issues:
                TraceScanIssue.objects.bulk_create(
                    [
                        TraceScanIssue(
                            scan_result=scan_result,
                            category=issue.category,
                            group=issue.group,
                            fix_layer=issue.fix_layer,
                            confidence=issue.confidence,
                            brief=issue.brief,
                        )
                        for issue in result.issues
                    ]
                )

            written += 1

        except Exception as e:
            logger.error(
                "scan_result_write_failed",
                trace_id=result.trace_id,
                error=str(e),
            )
            try:
                TraceScanResult.objects.create(
                    trace_id=result.trace_id,
                    project_id=project_id,
                    status=TraceScanStatus.FAILED,
                    has_issues=False,
                    scan_version=scan_version,
                    error_message=f"Write failed: {e}",
                )
            except Exception:
                pass

    return written
