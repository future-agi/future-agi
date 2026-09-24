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
from tracer.utils.helper import _normalize_eval_output_type

if TYPE_CHECKING:
    from rest_framework.request import Request

    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.views.trace import TraceView

logger = structlog.get_logger(__name__)


def _parse_output_str_list(raw) -> list[str]:
    """Parse a CHOICES eval's ``output_str_list`` (CH JSON string or native list)."""
    import json

    if isinstance(raw, list):
        return [str(x) for x in raw if x not in (None, "")]
    if isinstance(raw, str) and raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x not in (None, "")]
    return []


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

    The trace's project is resolved from the CH ``spans`` table (the trace
    may have no PG ``Trace`` row — collector ingest writes spans to CH only)
    and tenant-gated against PG ``Project`` (Project stays in PG). Trace
    metadata is taken from the PG ``Trace`` row when present and otherwise
    synthesized from the root span. Returns the response dict.
    """
    from django.db.utils import ProgrammingError

    from tracer.constants.provider_logos import PROVIDER_LOGOS
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.project import Project
    from tracer.models.trace import Trace
    from tracer.services.clickhouse.v2.trace_detail_reads import (
        MAX_TRACE_DETAIL_EVAL_CONFIGS,
        TraceDetailNotFound,
        read_trace_detail,
    )
    from tracer.views.trace import _project_workspace_scope_q

    # Resolve the caller's authorized project scope *before* touching CH. The
    # old first query searched trace_id across every tenant and only checked
    # the selected project afterward; at scale that was both slow and an
    # avoidable cross-tenant probe. Project remains a small PG dimension.
    project_manager = getattr(Project, "no_workspace_objects", Project.objects)
    authorized_project_ids = [
        str(value)
        for value in project_manager.filter(
            _project_workspace_scope_q(request, project_prefix="")
        ).values_list("id", flat=True)[:4097]
    ]

    # The eval logger has no project column. Resolve the selected project's
    # authorized config IDs only after ``read_trace_detail`` proves the trace's
    # project identity, and retain the same scoped objects for response labels.
    authorized_eval_configs = {}

    def _resolve_eval_config_ids(selected_project_id: str) -> tuple[str, ...]:
        configs = list(
            CustomEvalConfig.objects.filter(
                project_id=selected_project_id,
                deleted=False,
            ).select_related("eval_template")[: MAX_TRACE_DETAIL_EVAL_CONFIGS + 1]
        )
        authorized_eval_configs.update({str(config.id): config for config in configs})
        return tuple(authorized_eval_configs)

    try:
        detail_read = read_trace_detail(
            analytics=analytics,
            project_ids=authorized_project_ids,
            trace_id=str(trace_id),
            eval_config_ids_resolver=_resolve_eval_config_ids,
        )
    except TraceDetailNotFound:
        raise Trace.DoesNotExist from None
    project_id = detail_read.project_id
    span_rows = list(detail_read.spans)
    authorized_eval_config_ids = set(detail_read.eval_config_ids)

    # Trace metadata: PG row when present (full fidelity), else synthesized
    # from the root span below (CH-only trace, or `tracer_trace` dropped
    # post-cutover — the query then raises, treated the same as "no PG row").
    try:
        trace = Trace.objects.filter(id=trace_id, project_id=project_id).first()
    except ProgrammingError:
        trace = None  # tracer_trace dropped post-cutover — expected on CH25
    except Exception:
        logger.exception("trace_detail: PG Trace lookup failed")
        raise
    trace_data = view.get_serializer(trace).data if trace is not None else None

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

    def _parse_content(val):
        if not isinstance(val, str) or not val:
            return _parse_json(val)
        try:
            return _json.loads(val)
        except (ValueError, TypeError):
            return val

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

        raw_custom_eval_config_id = (
            str(row["custom_eval_config_id"])
            if row.get("custom_eval_config_id")
            else None
        )
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
            "input": _parse_content(row.get("input")),
            "output": _parse_content(row.get("output")),
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
                raw_custom_eval_config_id
                if raw_custom_eval_config_id in authorized_eval_config_ids
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
        from model_hub.utils.eval_list import derive_output_type

        eval_rows = list(detail_read.evals)
        # Reuse only the project-scoped config objects resolved before the CH
        # eval query. Never label or return a row whose config was not proven
        # to belong to the selected project.
        config_lookup = {
            config_id: {
                # Prefer the CustomEvalConfig's user-given name (e.g.
                # "voice_sentence_count"), fall back to the template
                # name only if unset. This keeps the drawer labels in
                # sync with the trace list column headers.
                "name": config.name
                or (config.eval_template.name if config.eval_template else config_id),
                "output_type": (
                    derive_output_type(config.eval_template)
                    if config.eval_template
                    else None
                ),
                "template_type": (
                    getattr(config.eval_template, "template_type", None)
                    if config.eval_template
                    else None
                ),
            }
            for config_id, config in authorized_eval_configs.items()
            if config_id in authorized_eval_config_ids
        }
        # Pivot into per-span map
        for row in eval_rows:
            sid = str(row.get("span_id") or "")
            cid = str(row.get("eval_config_id") or "")
            if not sid or sid not in span_map or cid not in config_lookup:
                continue
            if sid not in eval_map:
                eval_map[sid] = []
            info = config_lookup[cid]
            # Score is type-dependent; the CH mirror coerces unused typed
            # columns to 0, so route by type (choices → str_list, Pass/Fail →
            # bool, percentage → float) instead of trusting a populated column.
            output_float = row.get("output_float")
            output_bool = row.get("output_bool")
            output_str = row.get("output_str")
            str_list = _parse_output_str_list(row.get("output_str_list"))

            is_pass_fail = (
                _normalize_eval_output_type(info.get("output_type")) == "PASS_FAIL"
            )
            score_label = None
            if str_list:
                # Choices: no numeric score — surface the option(s), score None.
                score = None
                score_label = ", ".join(str_list)
            elif is_pass_fail and output_bool is not None:
                score = 100 if output_bool else 0
            elif output_float is not None:
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
            eval_score_label = None if drop_derived else score_label
            # Choices: per-option list the drawer renders as separate chips.
            eval_score_items = None if drop_derived else (str_list or None)

            # ``result`` = the raw verdict, by type: choices → the option list,
            # Pass/Fail → the bool, free-text → output_str, numeric → None.
            if drop_derived:
                result_value = None
            elif str_list:
                result_value = str_list
            elif output_str:
                result_value = output_str
            elif is_pass_fail and output_bool is not None:
                result_value = output_bool
            else:
                result_value = None

            eval_map[sid].append(
                {
                    "eval_config_id": cid,
                    "eval_name": info.get("name", cid),
                    "output_type": info.get("output_type"),
                    "template_type": info.get("template_type"),
                    "score": eval_score,
                    "score_label": eval_score_label,
                    "score_items": eval_score_items,
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
        raise

    # ----- Phase 8: Candidate-scoped latest annotations from ClickHouse -----
    annotation_map = {}
    try:
        from model_hub.models.develop_annotations import AnnotationsLabels

        annotation_rows = list(detail_read.annotations)
        label_ids = {
            str(row.get("label_id")) for row in annotation_rows if row.get("label_id")
        }
        label_lookup = (
            {
                str(label.id): label
                for label in AnnotationsLabels.objects.filter(id__in=label_ids)
            }
            if label_ids
            else {}
        )
        for row in annotation_rows:
            sid = str(row.get("span_id") or "")
            if not sid:
                continue
            label_id = str(row.get("label_id") or "")
            label = label_lookup.get(label_id)
            if sid not in annotation_map:
                annotation_map[sid] = []
            annotation_map[sid].append(
                {
                    "label_id": label_id or None,
                    "label_name": getattr(label, "name", None),
                    "label_type": getattr(label, "type", None),
                    "value": _parse_json(row.get("value"), default={}),
                }
            )
    except Exception:
        logger.exception("Failed to fetch trace annotations")
        raise

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

    # CH-only trace (no PG row): synthesize the trace metadata from the root
    # span so the response shape matches the PG serializer.
    if trace_data is None:
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
