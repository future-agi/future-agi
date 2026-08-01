"""Trace-detail dispatch handler — V2 (ClickHouse).

Under a V2 routing mode (`CH25_QUERY_TYPES_*` lists TRACE_DETAIL as v2_primary /
v2_only) the dispatch returns this class instead of the V1 (PostgreSQL)
``TraceDetailHandler``. It serves the trace detail from the ClickHouse ``spans``
table — which works for CH-only traces (collector ingest, no PG ``Trace`` row)
that the PG path 404s.

It mixes ``V2RewriteMixin`` for parity with the other v2 builders (the
ch25 builder-contract test requires it); the mixin only auto-rewrites ``build*``
SQL methods, of which this handler has none — the ClickHouse query is hand-written
in ``retrieve_trace_detail_ch`` below (the v2 data source), so there is nothing to
rewrite and ``_v2_rewrite_exclude`` stays empty.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from tracer.services.clickhouse.query_builders.trace_detail import (
    TraceDetail,
    TraceDetailHandler,
    compute_trace_summary_and_graph,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.span_reader import merge_span_attributes

if TYPE_CHECKING:
    from rest_framework.request import Request

    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.views.trace import TraceView

logger = structlog.get_logger(__name__)

TRACE_DETAIL_READ_TIMEOUT_MS = 750
TRACE_DETAIL_MAX_SPANS = 5000
TRACE_DETAIL_READ_SETTINGS = {
    "max_threads": 2,
    "max_memory_usage": 256 * 1024 * 1024,
    "max_bytes_to_read": 1024 * 1024 * 1024,
    "read_overflow_mode": "throw",
    # One sentinel row lets the caller distinguish exactly-at-cap from a trace
    # that would otherwise be silently truncated by LIMIT.
    "max_result_rows": TRACE_DETAIL_MAX_SPANS + 1,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}


class TraceDetailIncompleteError(RuntimeError):
    """The bounded trace read could not prove it returned the complete tree."""


class TraceDetailHandlerV2(V2RewriteMixin, TraceDetailHandler):
    """V2 / ClickHouse trace-detail handler."""

    # The handler has no ``build*`` SQL methods for the mixin to rewrite (the
    # ClickHouse query is hand-written in ``retrieve_trace_detail_ch``); the
    # mixin is inherited solely because ``test_ch25_builder_contract`` requires
    # every v2 builder to carry it, so the exclude set is empty.
    _v2_rewrite_exclude = frozenset()

    def fetch(self) -> TraceDetail:
        """Return the assembled trace-detail dict from ClickHouse."""
        return retrieve_trace_detail_ch(
            self.view, self.request, self.pk, self.analytics
        )


def retrieve_trace_detail_ch(
    view: TraceView,
    request: Request,
    trace_id: str,
    analytics: AnalyticsQueryService,
) -> TraceDetail:
    """V2 trace detail from ClickHouse.

    The trace's project is resolved through the CH ``trace_dict`` and
    tenant-gated against PG ``Project`` (Project stays in PG) before any span
    payload is read. A compact ``traces`` lookup is the bounded fallback while
    the dictionary refreshes after a brand-new trace. Trace metadata is
    synthesized from the root span. PostgreSQL remains the
    authorization/configuration store, but no telemetry is read from it.
    Returns the response dict.
    """
    from tracer.constants.provider_logos import PROVIDER_LOGOS
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.project import Project
    from tracer.models.trace import Trace
    from tracer.services.clickhouse.eval_logger_table import eval_logger_source
    from tracer.views.trace import _project_workspace_scope_q

    # ``spans`` is ordered with project_id first. Looking up a trace's project
    # by scanning that table without the project prefix was one of the hottest
    # production trace-detail fingerprints (p95 ~1.6 s before any hydration).
    # The in-memory dictionary is keyed by trace UUID and resolves the same
    # tenant in O(1). ``traces`` is a compact direct-write table and covers the
    # short dictionary-refresh window for a newly ingested trace.
    zero_uuid = "00000000-0000-0000-0000-000000000000"
    project_lookup_query = """
        SELECT toString(
            dictGetOrDefault(
                'trace_dict',
                'project_id',
                toUUID(%(trace_id)s),
                toUUID(%(zero_uuid)s)
            )
        ) AS project_id
    """
    try:
        project_result = analytics.execute_ch_query(
            project_lookup_query,
            {"trace_id": str(trace_id), "zero_uuid": zero_uuid},
            timeout_ms=TRACE_DETAIL_READ_TIMEOUT_MS,
            settings=TRACE_DETAIL_READ_SETTINGS,
        )
    except Exception as exc:
        # The compact direct-write table remains an authoritative bounded
        # fallback if the dictionary is warming or temporarily unavailable.
        logger.warning(
            "trace detail dictionary lookup failed; using compact fallback",
            error=str(exc)[:200],
        )
        project_result = None
    project_id = (
        str(project_result.data[0].get("project_id"))
        if project_result
        and project_result.data
        and project_result.data[0].get("project_id") not in (None, "", zero_uuid)
        else None
    )
    if not project_id:
        compact_lookup_query = """
            SELECT toString(project_id) AS project_id
            FROM traces FINAL
            WHERE id = toUUID(%(trace_id)s)
              AND is_deleted = 0
            ORDER BY updated_at DESC
            LIMIT 1
        """
        compact_result = analytics.execute_ch_query(
            compact_lookup_query,
            {"trace_id": str(trace_id)},
            timeout_ms=TRACE_DETAIL_READ_TIMEOUT_MS,
            settings=TRACE_DETAIL_READ_SETTINGS,
        )
        project_id = (
            str(compact_result.data[0].get("project_id"))
            if compact_result.data
            and compact_result.data[0].get("project_id") not in (None, "")
            else None
        )

    project_manager = getattr(Project, "no_workspace_objects", Project.objects)
    authorized_project_id = (
        project_manager.filter(
            _project_workspace_scope_q(request, project_prefix=""),
            id=project_id,
        )
        .values_list("id", flat=True)
        .first()
        if project_id
        else None
    )
    if not authorized_project_id:
        raise Trace.DoesNotExist
    project_id = str(authorized_project_id)

    # Hydrate only after authorization, with the project prefix that matches
    # the production spans table's leading sort key.
    query = """
        SELECT
            toString(project_id) AS project_id,
            id, trace_id, parent_span_id, name, observation_type,
            start_time, end_time, input, output, model,
            '' AS model_parameters, latency_ms, prompt_tokens,
            completion_tokens, total_tokens, cost, status,
            status_message, tags, span_events,
            provider, attributes_extra AS span_attributes,
            project_version_id, custom_eval_config_id,
            toString(trace_session_id) AS trace_session_id,
            toJSONString(metadata) AS metadata_json,
            attrs_string, attrs_number, attrs_bool
        FROM spans
        PREWHERE project_id = toUUID(%(project_id)s)
          AND trace_id = %(trace_id)s
        WHERE is_deleted = 0
        ORDER BY start_time
        LIMIT 1 BY id
        LIMIT %(trace_detail_limit)s
    """
    result = analytics.execute_ch_query(
        query,
        {
            "project_id": project_id,
            "trace_id": str(trace_id),
            "trace_detail_limit": TRACE_DETAIL_MAX_SPANS + 1,
        },
        timeout_ms=TRACE_DETAIL_READ_TIMEOUT_MS,
        settings=TRACE_DETAIL_READ_SETTINGS,
    )
    if len(result.data) > TRACE_DETAIL_MAX_SPANS:
        raise TraceDetailIncompleteError(
            f"Trace {trace_id} exceeds the {TRACE_DETAIL_MAX_SPANS}-span detail cap"
        )
    span_rows = [row for row in result.data if str(row.get("project_id")) == project_id]
    if not span_rows:
        raise Trace.DoesNotExist

    # Build span tree
    span_map = {}  # id -> span data
    root_spans = []
    orphan_spans = []

    import json as _json

    def _parse_json(val, default=None):
        if default is None:
            default = {}
        if not val or not isinstance(val, str):
            return val if val is not None else default
        try:
            return _json.loads(val)
        except (ValueError, TypeError):
            return default

    for row in span_rows:
        span_id = str(row.get("id", ""))
        parent_id = row.get("parent_span_id")
        parent_id_str = str(parent_id) if parent_id else None

        provider = row.get("provider")

        span_attrs = merge_span_attributes(
            row.get("attrs_string"),
            row.get("attrs_number"),
            row.get("attrs_bool"),
            row.get("span_attributes"),
        )

        # Build metadata from CH JSON column
        metadata_raw = row.get("metadata_json") or "{}"
        metadata = _parse_json(metadata_raw, default={})

        span_data = {
            "id": span_id,
            "project": project_id,
            "project_version": (
                str(row["project_version_id"])
                if row.get("project_version_id")
                else None
            ),
            "trace": str(row.get("trace_id", "")),
            "parent_span_id": parent_id_str,
            "name": row.get("name"),
            "observation_type": row.get("observation_type"),
            "start_time": row.get("start_time"),
            "end_time": row.get("end_time"),
            "input": _parse_json(row.get("input")),
            "output": _parse_json(row.get("output")),
            "model": row.get("model"),
            "model_parameters": _parse_json(row.get("model_parameters")),
            "latency_ms": row.get("latency_ms"),
            "org_id": None,
            "org_user_id": None,
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "total_tokens": row.get("total_tokens"),
            "response_time": None,
            "eval_id": None,
            "cost": (
                round(row["cost"], 6)
                if row.get("cost") and row["cost"] > 0
                else row.get("cost")
            ),
            "status": row.get("status"),
            "status_message": row.get("status_message"),
            "tags": _parse_json(row.get("tags"), default=[]),
            "metadata": metadata,
            "span_events": _parse_json(row.get("span_events"), default=[]),
            "provider": provider,
            "provider_logo": (
                PROVIDER_LOGOS.get(provider.lower()) if provider else None
            ),
            "span_attributes": span_attrs,
            "custom_eval_config": (
                str(row["custom_eval_config_id"])
                if row.get("custom_eval_config_id")
                else None
            ),
            "eval_status": None,
            "prompt_version": None,
        }

        span_map[span_id] = {
            "observation_span": span_data,
            "children": [],
            "_parent_id": parent_id_str,
        }

    # ----- Phase 8: Batch fetch eval scores from CH -----
    eval_map = {}
    try:
        eval_table, eval_nd = eval_logger_source()
        eval_query = f"""
        SELECT
            toString(observation_span_id) AS span_id,
            toString(custom_eval_config_id) AS eval_config_id,
            output_float,
            output_bool,
            output_str,
            eval_explanation,
            error,
            status,
            skipped_reason
        FROM {eval_table} FINAL
        WHERE trace_id = %(trace_id)s
          AND observation_span_id IN %(span_ids)s
          AND {eval_nd}
        """
        eval_result = analytics.execute_ch_query(
            eval_query,
            {
                "trace_id": str(trace_id),
                "span_ids": tuple(span_map),
            },
            timeout_ms=TRACE_DETAIL_READ_TIMEOUT_MS,
            settings=TRACE_DETAIL_READ_SETTINGS,
        )
        # Collect unique config IDs for name lookup
        config_ids_set = set()
        for row in eval_result.data:
            cid = row.get("eval_config_id", "")
            if cid:
                config_ids_set.add(cid)
        # Lookup eval config names from PG
        config_lookup = {}
        if config_ids_set:
            configs = CustomEvalConfig.objects.filter(
                id__in=list(config_ids_set), deleted=False
            ).select_related("eval_template")
            config_lookup = {
                str(c.id): {
                    # Prefer the CustomEvalConfig's user-given name (e.g.
                    # "voice_sentence_count"), fall back to the template
                    # name only if unset. This keeps the drawer labels in
                    # sync with the trace list column headers.
                    "name": c.name
                    or (c.eval_template.name if c.eval_template else str(c.id)),
                    "output_type": (
                        getattr(c.eval_template, "output_type_normalized", None)
                        if c.eval_template
                        else None
                    ),
                    "template_type": (
                        getattr(c.eval_template, "template_type", None)
                        if c.eval_template
                        else None
                    ),
                }
                for c in configs
            }
        # Pivot into per-span map
        for row in eval_result.data:
            sid = row.get("span_id", "")
            if not sid:
                continue
            if sid not in eval_map:
                eval_map[sid] = []
            cid = row.get("eval_config_id", "")
            info = config_lookup.get(cid, {})
            # Compute score from output columns
            output_float = row.get("output_float")
            output_bool = row.get("output_bool")
            output_str = row.get("output_str")

            if output_float is not None:
                score = round(output_float * 100, 2)
            elif output_bool is not None:
                score = 100 if output_bool else 0
            else:
                score = None

            explanation = row.get("eval_explanation", "")
            # Lifecycle status (pending/running/completed/errored/skipped) so the
            # drawer can render a loading / pending / skipped state per eval.
            status = (row.get("status") or "").lower()
            skipped_reason = row.get("skipped_reason")

            # An errored or non-terminal row can carry stale/coerced output (the
            # CH mirror stores 0 for a NULL bool), so drop the fabricated
            # score/result — the drawer renders the error / lifecycle state
            # instead. ``status == 'errored'`` is treated as an error even when
            # the legacy ``error`` flag/``output_str`` weren't set. (Named
            # ``result_value`` — ``result`` is the CH query result in the outer
            # scope and must not be shadowed by this loop.)
            is_errored = (
                bool(row.get("error")) or output_str == "ERROR" or status == "errored"
            )
            is_non_terminal = status in ("pending", "running", "skipped")
            drop_derived = is_errored or is_non_terminal
            eval_score = None if drop_derived else score
            result_value = (
                None
                if drop_derived
                else (output_str or (output_bool if output_bool is not None else None))
            )

            eval_map[sid].append(
                {
                    "eval_config_id": cid,
                    "eval_name": info.get("name", cid),
                    "output_type": info.get("output_type"),
                    "template_type": info.get("template_type"),
                    "score": eval_score,
                    "result": result_value,
                    "explanation": (
                        explanation
                        or (skipped_reason if status == "skipped" else None)
                        or None
                    ),
                    "status": status or None,
                    "error": is_errored,
                    "skipped": status == "skipped",
                    "skipped_reason": skipped_reason,
                }
            )
    except Exception:
        logger.exception("Failed to fetch trace eval scores")

    # ----- Phase 8: Batch fetch annotations from PG -----
    annotation_map = {}
    try:
        from model_hub.models.score import Score as ScoreModel

        scores = (
            ScoreModel.objects.filter(trace_id=trace_id, deleted=False)
            .select_related("label")
            .values(
                "observation_span_id",
                "label_id",
                "label__name",
                "label__type",
                "value",
            )
        )
        for s in scores:
            sid = (
                str(s["observation_span_id"]) if s.get("observation_span_id") else None
            )
            if not sid:
                continue
            if sid not in annotation_map:
                annotation_map[sid] = []
            annotation_map[sid].append(
                {
                    "label_id": str(s["label_id"]) if s.get("label_id") else None,
                    "label_name": s.get("label__name"),
                    "label_type": s.get("label__type"),
                    "value": s.get("value"),
                }
            )
    except Exception:
        logger.exception("Failed to fetch trace annotations")

    # ----- Attach evals + annotations to each span -----
    for sid, entry in span_map.items():
        entry["eval_scores"] = eval_map.get(sid, [])
        entry["annotations"] = annotation_map.get(sid, [])

    # Build tree: link children to parents
    for entry in span_map.values():
        parent_id = entry["_parent_id"]
        if parent_id is None:
            root_spans.append(entry)
        elif parent_id in span_map:
            span_map[parent_id]["children"].append(entry)
        else:
            orphan_spans.append(entry)

    # Clean up internal fields
    def _clean_entry(entry):
        del entry["_parent_id"]
        for child in entry["children"]:
            _clean_entry(child)

    for entry in root_spans:
        _clean_entry(entry)
    for entry in orphan_spans:
        _clean_entry(entry)

    observation_spans_response = root_spans + orphan_spans

    # Summary + agent graph from the shared compute over the assembled span
    # tree — the same helper the V1 (PG) handler uses, so the two paths cannot
    # drift in the totals or graph shape.
    summary, graph = compute_trace_summary_and_graph(observation_spans_response)

    # Trace metadata is synthesized from the CH root span. Direct ingestion no
    # longer writes a PostgreSQL Trace row, so consulting one would be stale.
    root_obs = (root_spans[0].get("observation_span") if root_spans else {}) or {}
    session_id = None
    if span_rows:
        _sid = span_rows[0].get("trace_session_id")
        session_id = str(_sid) if _sid else None
    trace_data = {
        "id": str(trace_id),
        "project": str(project_id),
        "project_version": root_obs.get("project_version"),
        "name": root_obs.get("name"),
        "metadata": root_obs.get("metadata") or {},
        "input": root_obs.get("input"),
        "output": root_obs.get("output"),
        "error": summary["error_count"] > 0,
        "session": session_id,
        "external_id": None,
        "tags": root_obs.get("tags") or [],
    }

    return {
        "trace": trace_data,
        "observation_spans": observation_spans_response,
        "summary": summary,
        "graph": graph,
    }
