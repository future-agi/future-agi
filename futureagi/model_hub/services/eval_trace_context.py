"""Resolve playground trace metadata and spans within an authorized project set."""

import json
from types import SimpleNamespace


def _decode_json(value, default):
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def resolve_eval_trace(trace_id, *, projects):
    """Return trace-shaped metadata and spans, or raise before scoring.

    ``projects`` must already enforce the request's organization and workspace.
    Keep legacy PG metadata when available; a collector-only trace instead uses
    Observe's bounded, latest-live identity replay, never a bare trace-id read.
    """
    from tracer.models.trace import Trace
    from tracer.services.clickhouse.v2 import get_reader
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
    from tracer.services.clickhouse.v2.trace_detail_reads import read_trace_detail

    trace = Trace.no_workspace_objects.filter(id=trace_id, project__in=projects).first()
    if trace is not None:
        with get_reader() as reader:
            spans = reader.list_by_trace(
                str(trace.id), project_id=str(trace.project_id)
            )
        return trace, spans

    # Match Observe's finite project scope and identity rules. The shared reader
    # rejects overflow, missing/tombstoned traces and incomplete content. It picks
    # one authorized project for coincident trace IDs, then pins every content
    # query to that project's exact physical span identities. No evals or
    # annotations are needed to supply the judge with trace context.
    project_ids = [str(pk) for pk in projects.values_list("id", flat=True)[:4097]]
    detail = read_trace_detail(
        analytics=V2AnalyticsQueryService(),
        project_ids=project_ids,
        trace_id=str(trace_id),
        include_annotations=False,
    )
    rows = detail.spans
    root = next((row for row in rows if not row.get("parent_span_id")), {})
    spans = [SimpleNamespace(**row) for row in rows]
    start_times = [row["start_time"] for row in rows if row.get("start_time")]
    trace = SimpleNamespace(
        id=str(trace_id),
        project_id=detail.project_id,
        name=root.get("name"),
        session_id=rows[0].get("trace_session_id") if rows else None,
        metadata=_decode_json(root.get("metadata_json"), {}) or {},
        tags=_decode_json(root.get("tags"), []) or [],
        input=_decode_json(root.get("input"), root.get("input")),
        output=_decode_json(root.get("output"), root.get("output")),
        error=any(row.get("status") == "ERROR" for row in rows),
        created_at=min(start_times) if start_times else None,
    )
    return trace, spans
