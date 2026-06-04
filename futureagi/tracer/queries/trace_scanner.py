"""
DB helpers for the trace scanner pipeline.

Handles: config checks, span fetching, result writing.
All return typed dataclasses — no raw dicts at the boundary.
"""

import json
import random
from typing import Dict, List, Optional

import structlog

from tracer.models.trace_scan import TraceScanConfig, TraceScanResult
from tracer.types.scan_types import ScanConfig, SpanData, TraceData

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_scan_config(project_id: str) -> Optional[ScanConfig]:
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


def apply_sampling(trace_ids: List[str], sampling_rate: float) -> List[str]:
    """Apply sampling rate to filter trace IDs."""
    if sampling_rate >= 1.0:
        return trace_ids
    return [tid for tid in trace_ids if random.random() < sampling_rate]


def filter_already_scanned(trace_ids: List[str]) -> List[str]:
    """Remove trace IDs that already have a scan result."""
    already_scanned = set(
        TraceScanResult.objects.filter(trace_id__in=trace_ids)
        .values_list("trace_id", flat=True)
        .iterator()
    )
    return [tid for tid in trace_ids if tid not in already_scanned]


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


def fetch_trace_data(trace_ids: List[str]) -> List[TraceData]:
    """Fetch trace spans from DB and build nested span trees for the scanner."""
    from tracer.models.observation_span import ObservationSpan

    traces = []

    for trace_id in trace_ids:
        rows = list(
            ObservationSpan.objects.filter(trace_id=trace_id)
            .order_by("start_time")
            .values(
                "id",
                "name",
                "parent_span_id",
                "start_time",
                "end_time",
                "input",
                "output",
                "metadata",
                "model",
                "observation_type",
                "status_message",
            )
        )

        if not rows:
            continue

        # Build flat map
        span_map: Dict[str, SpanData] = {}
        for row in rows:
            span_map[row["id"]] = _row_to_span(row)

        # Link children → parents
        root_spans = []
        for row in rows:
            span = span_map[row["id"]]
            parent_id = row["parent_span_id"]
            if parent_id and parent_id in span_map:
                span_map[parent_id].child_spans.append(span)
            else:
                root_spans.append(span)

        traces.append(TraceData(trace_id=str(trace_id), spans=root_spans))

    return traces


def _row_to_span(row: Dict) -> SpanData:
    """Convert a DB row dict to SpanData."""
    duration = ""
    if row["start_time"] and row["end_time"]:
        delta = (row["end_time"] - row["start_time"]).total_seconds()
        duration = f"PT{delta}S"

    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    attrs = {}
    if row.get("input"):
        inp = row["input"]
        attrs["input.value"] = json.dumps(inp) if isinstance(inp, dict) else str(inp)
    if row.get("output"):
        out = row["output"]
        attrs["output.value"] = json.dumps(out) if isinstance(out, dict) else str(out)
    if row.get("model"):
        attrs["llm.model_name"] = row["model"]

    obs_type = row.get("observation_type", "")
    # Default unrecognized / missing types (commonly "unknown" from older SDKs)
    # to CHAIN so the scanner still sees structural role info instead of a blank span.
    attrs["span.kind"] = _OBS_TYPE_TO_KIND.get(obs_type, "CHAIN")

    for key in _TOKEN_KEYS:
        if key in metadata:
            attrs[key] = metadata[key]

    status = "Unset"
    if row.get("status_message"):
        status = "Error" if "error" in str(row["status_message"]).lower() else "Ok"

    return SpanData(
        span_id=str(row["id"]),
        span_name=row.get("name") or "unknown",
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
    from dataclasses import asdict

    from tracer.models.trace_scan import (
        TraceScanIssue,
        TraceScanResult,
        TraceScanStatus,
    )

    written = 0

    for result in results:
        try:
            # Serialize dataclasses to JSON-safe dicts for JSONField storage
            key_moments = [
                {"kevinified": km.kevinified, "verbatim": km.verbatim}
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
