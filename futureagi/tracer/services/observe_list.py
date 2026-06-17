"""Observe list selectors: read-side business logic for the trace / voice-call
list endpoints.

These functions hold the ClickHouse orchestration that used to live inside the
``ObservationTraceViewSet`` view methods. They are HTTP-free: each takes plain
arguments and returns the plain response ``dict`` the view wraps. View-layer
collaborators that are shared with other endpoints (the annotation-map builder,
the voice-metrics extractor, the heavy-key set) are injected so the service
never imports the view (keeps the dependency direction view -> service).

See coding-standards/03-architecture-and-layers: views route + (de)serialize
and delegate; business logic lives in services/selectors.
"""

import json
import math

import structlog
from django.db.models import Max

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger
from tracer.models.trace import Trace
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders import VoiceCallListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.dispatch import get_query_builder_class
from tracer.services.observability_providers import ObservabilityService
from tracer.utils.helper import (
    build_eval_task_map,
    eval_count_cell,
    get_annotation_labels_for_project,
    get_default_trace_config,
    update_column_config_based_on_eval_config,
    update_span_column_config_based_on_annotations,
)
from tracer.utils.trace_ingestion import _sanitize_nonfinite_floats

logger = structlog.get_logger(__name__)


def build_traces_of_session_list(
    request,
    project_id,
    validated_data,
    analytics,
    org_project_ids=None,
    org=None,
    *,
    build_annotation_map,
):
    """List traces-of-session using ClickHouse backend.

    When ``org_project_ids`` is provided (cross-project user-detail
    mode), the builder is constructed with `project_ids=...` and the
    view falls back to a PG-side EvalLogger lookup scoped to those
    projects (the CH dict-lookup path requires a single project_id).

    Builder class resolved via v1↔v2 dispatch — set
    CH25_QUERY_TYPES_V2_PRIMARY=TRACE_LIST to flip to CH 25.3.
    """
    from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

    BuilderCls = get_query_builder_class("TRACE_LIST")  # noqa: N806

    org_scope = bool(org_project_ids)
    filters = list(validated_data.get("filters", []) or [])
    page_number = validated_data["page_number"]
    page_size = validated_data["page_size"]
    session_id = (
        str(validated_data["session_id"]) if validated_data.get("session_id") else None
    )
    if session_id:
        filters.append(
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "col_type": "NORMAL",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": session_id,
                },
            }
        )

    # Get eval config IDs together with the (eval_task, target_type) each
    # config was actually applied under. Project mode uses a CH dict-lookup
    # (fast); org mode uses a PG scan because the CH dict-lookup takes a
    # single project_id — multi-project CH variant not implemented yet.
    #
    # Only NON-DELETED EvalLogger rows are considered. ``build_eval_task_map``
    # groups the surviving rows by (config, task, target_type), so a config
    # whose loggers under a given task are all soft-deleted drops out of
    # that task; one deleted everywhere disappears entirely. It keeps the
    # most-recent surviving (task, target_type) per config.
    eval_config_ids = []
    discovery_rows = []  # (config_id, eval_task_id, target_type, last_seen)
    if org_scope:
        rows = (
            EvalLogger.objects.filter(
                trace_id__in=Trace.objects.filter(
                    project_id__in=org_project_ids
                ).values("id"),
                deleted=False,
            )
            .values("custom_eval_config_id", "eval_task_id", "target_type")
            .annotate(last_seen=Max("created_at"))
            .order_by()
        )
        discovery_rows = [
            (
                r["custom_eval_config_id"],
                r["eval_task_id"],
                r["target_type"],
                r["last_seen"],
            )
            for r in rows
        ]
    else:
        eval_table, eval_nd = eval_logger_source()
        ch_result = analytics.execute_ch_query(
            "SELECT toString(custom_eval_config_id) AS cid, "
            "toString(eval_task_id) AS task_id, "
            "target_type AS target_type, "
            "max(created_at) AS last_seen "
            f"FROM {eval_table} FINAL "
            f"WHERE {eval_nd} "
            "AND dictGet('trace_dict', 'project_id', "
            "trace_id) = toUUID(%(pid)s) "
            "GROUP BY cid, task_id, target_type",
            {"pid": str(project_id)},
            timeout_ms=30000,
        )
        discovery_rows = [
            (
                r.get("cid"),
                r.get("task_id"),
                r.get("target_type"),
                r.get("last_seen"),
            )
            for r in ch_result.data
        ]

    ch_ids = [str(r[0]) for r in discovery_rows if r[0]]
    if ch_ids:
        eval_configs = CustomEvalConfig.objects.filter(
            id__in=ch_ids, deleted=False
        ).select_related("eval_template")
        eval_config_ids = [str(c.id) for c in eval_configs]
    else:
        eval_configs = []

    eval_task_map = build_eval_task_map(discovery_rows, eval_config_ids)

    # Annotation labels — skip in org-scoped mode (deferred enhancement)
    if org_scope:
        annotation_labels = []
    else:
        annotation_labels = get_annotation_labels_for_project(project_id)
    annotation_label_ids = [str(label.id) for label in annotation_labels]
    label_types = {str(label.id): label.type for label in annotation_labels}

    builder = BuilderCls(
        project_id=None if org_scope else str(project_id),
        project_ids=[str(p) for p in org_project_ids] if org_scope else None,
        filters=filters,
        page_number=page_number,
        page_size=page_size,
        eval_config_ids=eval_config_ids,
        annotation_label_ids=annotation_label_ids,
    )

    # Phase 1: Paginated traces (light columns only — no input/output)
    query, params = builder.build()
    result = analytics.execute_ch_query(query, params, timeout_ms=10000)
    result.data = result.data[:page_size]

    # Count
    count_query, count_params = builder.build_count_query()
    count_result = analytics.execute_ch_query(
        count_query, count_params, timeout_ms=30000
    )
    total_count = count_result.data[0].get("total", 0) if count_result.data else 0

    # Phase 1b: Fetch heavy columns (input/output/attrs) for the page
    trace_ids = [str(row.get("trace_id", "")) for row in result.data]
    content_map = {}
    if trace_ids:
        content_query, content_params = builder.build_content_query(trace_ids)
        if content_query:
            content_result = analytics.execute_ch_query(
                content_query, content_params, timeout_ms=10000
            )
            for crow in content_result.data:
                content_map[str(crow.get("trace_id", ""))] = crow

    # Merge content into Phase 1 results
    for row in result.data:
        tid = str(row.get("trace_id", ""))
        content = content_map.get(tid, {})
        row["input"] = content.get("input", "")
        row["output"] = content.get("output", "")
        row["attrs_string"] = content.get("attrs_string", {})
        row["attrs_number"] = content.get("attrs_number", {})
        raw_meta = content.get("metadata", "{}")
        if isinstance(raw_meta, str):
            try:
                row["metadata"] = json.loads(raw_meta)
            except (json.JSONDecodeError, TypeError):
                row["metadata"] = {}
        else:
            row["metadata"] = raw_meta or {}
        row["trace_tags"] = content.get("trace_tags", [])

    user_id_map = builder.resolve_user_ids(trace_ids, analytics)

    # Phase 2: Eval scores
    eval_map = {}
    if trace_ids and eval_config_ids:
        eval_query, eval_params = builder.build_eval_query(trace_ids)
        if eval_query:
            eval_result = analytics.execute_ch_query(
                eval_query, eval_params, timeout_ms=30000
            )
            eval_map = builder.pivot_eval_results(
                [(list(row.values())) for row in eval_result.data],
                list(eval_result.data[0].keys()) if eval_result.data else [],
                count_mode=True,
            )

    # Phase 3: Annotations — fetch from PG Score (unified annotation system)
    annotation_map = build_annotation_map(trace_ids, annotation_label_ids, label_types)

    # Phase 4: Aggregated span attributes for custom columns
    _SKIP_ATTR_PREFIXES = (
        "raw.",
        "llm.input_messages",
        "llm.output_messages",
        "input.value",
        "output.value",
    )
    aggregated_attrs = {}  # trace_id -> {attr_key -> [unique_values]}
    if trace_ids:
        try:
            attr_query, attr_params = builder.build_span_attributes_query(trace_ids)
            if attr_query:
                attr_result = analytics.execute_ch_query(
                    attr_query, attr_params, timeout_ms=30000
                )
                for attr_row in attr_result.data:
                    tid = str(attr_row.get("trace_id", ""))
                    raw = attr_row.get("attributes_extra", "{}")
                    try:
                        attrs = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except (json.JSONDecodeError, TypeError):
                        attrs = {}
                    # Fallback: merge from typed Map columns when raw is empty
                    if not attrs:
                        str_map = attr_row.get("attrs_string") or {}
                        num_map = attr_row.get("attrs_number") or {}
                        if isinstance(str_map, dict):
                            attrs.update(str_map)
                        if isinstance(num_map, dict):
                            for k, v in num_map.items():
                                if k not in attrs:
                                    attrs[k] = v
                    if tid not in aggregated_attrs:
                        aggregated_attrs[tid] = {}
                    for key, value in attrs.items():
                        if key.startswith(_SKIP_ATTR_PREFIXES):
                            continue
                        if isinstance(value, str) and len(value) > 500:
                            continue
                        if key not in aggregated_attrs[tid]:
                            aggregated_attrs[tid][key] = (
                                set()
                                if isinstance(value, (str, int, float, bool))
                                else []
                            )
                        if isinstance(value, (str, int, float, bool)):
                            aggregated_attrs[tid][key].add(
                                value
                                if not isinstance(value, bool)
                                else str(value).lower()
                            )
                        elif isinstance(value, (list, dict)):
                            pass  # skip complex values for aggregation
        except Exception as e:
            logger.warning(f"Span attribute aggregation failed: {e}")

    # Build column config — get_default_trace_config() already includes
    # all standard columns (latency, tokens, cost, user_id, etc.)
    column_config = get_default_trace_config()
    # skip_choices=True: CHOICES evals render as a SINGLE chip-style column
    # (id=config_id) carrying per-label counts, not one column per choice.
    column_config = update_column_config_based_on_eval_config(
        column_config, eval_configs, skip_choices=True, eval_task_map=eval_task_map
    )
    column_config = update_span_column_config_based_on_annotations(
        column_config, annotation_labels
    )

    # Format response matching PG format
    table_data = []
    for row in result.data:
        trace_id = str(row.get("trace_id", ""))
        raw_cost = row.get("cost")
        entry = {
            "trace_id": trace_id,
            "project_id": (
                str(row.get("project_id")) if row.get("project_id") else None
            ),
            "input": row.get("input", ""),
            "output": row.get("output", ""),
            "created_at": (
                row.get("start_time").isoformat() + "Z"
                if row.get("start_time")
                else None
            ),
            "node_type": row.get("observation_type", ""),
            "latency": row.get("latency_ms"),
            "total_tokens": row.get("total_tokens"),
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "cost": (
                round(raw_cost, 6)
                if isinstance(raw_cost, (int, float))
                and not isinstance(raw_cost, bool)
                and math.isfinite(raw_cost)
                else 0
            ),
            "trace_name": row.get("trace_name") or row.get("span_name") or "",
            "start_time": row.get("start_time"),
            "status": row.get("status"),
            "model": row.get("model"),
            "provider": row.get("provider"),
            "tags": row.get("trace_tags") or [],
            "user_id": user_id_map.get(trace_id),
        }

        # Add eval metrics. count_mode pivot gives raw appearance counts;
        # eval_count_cell renders each eval as ONE column whose value is a
        # chip-style counts object (Pass/Fail -> {"pass","fail"}, Choices ->
        # {label: count}) or a plain average (Score).
        trace_evals = eval_map.get(trace_id, {})
        for config in eval_configs:
            config_id = str(config.id)
            if config_id not in trace_evals:
                continue
            scores = trace_evals[config_id]
            if isinstance(scores, dict) and scores.get("error"):
                entry[config_id] = scores
                continue
            entry[config_id] = eval_count_cell(scores, config)

        # Add annotations
        trace_annotations = annotation_map.get(trace_id, {})
        for label in annotation_labels:
            label_id = str(label.id)
            if label_id in trace_annotations:
                entry[label_id] = trace_annotations[label_id]

        # Include metadata for custom columns
        metadata = row.get("metadata") or {}
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                if key not in entry:
                    if isinstance(value, str) and len(value) > 500:
                        entry[key] = value[:500] + "..."
                    else:
                        entry[key] = value

        # Include aggregated span attributes — single value or array of unique values
        trace_attrs = aggregated_attrs.get(trace_id, {})
        for key, values in trace_attrs.items():
            if key not in entry:
                if isinstance(values, set):
                    vals = sorted(values, key=str)
                    entry[key] = vals[0] if len(vals) == 1 else vals
                else:
                    entry[key] = values

        table_data.append(entry)

    response = {
        "metadata": {"total_rows": total_count},
        "table": _sanitize_nonfinite_floats(table_data),
        "config": column_config,
    }

    return response


def build_voice_calls_list(
    project_id,
    validated_data,
    remove_simulation_calls,
    analytics,
    *,
    extract_voice_metrics,
    heavy_keys,
    build_annotation_map,
):
    """Build the ``list_voice_calls`` paginated response payload from ClickHouse.

    Returns the DRF-style paginated ``dict`` (``{count, results, config, …}``);
    the view wraps it in a ``Response``. ``extract_voice_metrics`` and
    ``heavy_keys`` are the view's voice helpers (shared with other endpoints,
    so injected rather than moved); ``build_annotation_map`` is the view's
    annotation selector.

    Builder classes resolved via v1↔v2 dispatch — flip with
    CH25_QUERY_TYPES_V2_PRIMARY=VOICE_CALL_LIST,TRACE_LIST.
    """
    VoiceBuilderCls = get_query_builder_class("VOICE_CALL_LIST")  # noqa: N806

    filters = validated_data.get("filters", [])
    page = validated_data.get("page", 1)
    page_size = validated_data.get("page_size", 30)
    page_number = page - 1  # Convert 1-based to 0-based

    # Get eval config IDs (with the eval_task + target_type each was applied
    # under) from CH. Only non-deleted rows count; build_eval_task_map keeps
    # the most-recent surviving (task, target_type) per config.
    eval_config_ids = []
    eval_table, eval_nd = eval_logger_source()
    ch_result = analytics.execute_ch_query(
        "SELECT toString(custom_eval_config_id) AS cid, "
        "toString(eval_task_id) AS task_id, "
        "target_type AS target_type, "
        "max(created_at) AS last_seen "
        f"FROM {eval_table} FINAL "
        f"WHERE {eval_nd} "
        "AND dictGet('trace_dict', 'project_id', "
        "trace_id) = toUUID(%(pid)s) "
        "GROUP BY cid, task_id, target_type",
        {"pid": str(project_id)},
        timeout_ms=30000,
    )
    discovery_rows = [
        (r.get("cid"), r.get("task_id"), r.get("target_type"), r.get("last_seen"))
        for r in ch_result.data
    ]
    ch_ids = [str(r[0]) for r in discovery_rows if r[0]]
    if ch_ids:
        eval_configs = CustomEvalConfig.objects.filter(
            id__in=ch_ids, deleted=False
        ).select_related("eval_template")
        eval_config_ids = [str(c.id) for c in eval_configs]
    else:
        eval_configs = []
    eval_task_map = build_eval_task_map(discovery_rows, eval_config_ids)

    # Get annotation labels that have actual annotations/scores for this project
    annotation_labels = get_annotation_labels_for_project(project_id)
    annotation_label_ids = [str(label.id) for label in annotation_labels]
    label_types = {str(label.id): label.type for label in annotation_labels}

    sim_flag = remove_simulation_calls and str(remove_simulation_calls).lower() not in (
        "false",
        "0",
        "",
    )

    builder = VoiceBuilderCls(
        project_id=str(project_id),
        filters=filters,
        page_number=page_number,
        page_size=page_size,
        eval_config_ids=eval_config_ids,
        remove_simulation_calls=sim_flag,
        annotation_label_ids=annotation_label_ids,
    )

    # Phase 1: Paginated root conversation spans (light columns only)
    query, params = builder.build()
    result = analytics.execute_ch_query(query, params, timeout_ms=10000)
    result.data = result.data[:page_size]

    # Phase 1b: Fetch span_attributes + provider for the paginated spans from
    # the v2 `spans` table. fi-collector populates attributes_extra JSON +
    # typed Maps (attrs_string/number/bool); reconstruct the flat dict here.
    page_rows = result.data[:page_size]
    span_ids = [str(row.get("span_id", "")) for row in page_rows if row.get("span_id")]
    attrs_map = {}
    if span_ids:
        attrs_result = analytics.execute_ch_query(
            "SELECT id, provider, "
            "attributes_extra AS span_attributes, "
            "attrs_string, attrs_number, attrs_bool "
            "FROM spans FINAL "
            "PREWHERE id IN %(span_ids)s "
            "WHERE is_deleted = 0",
            {"span_ids": tuple(span_ids)},
            timeout_ms=10000,
        )
        for arow in attrs_result.data:
            sid = str(arow.get("id", ""))
            raw = arow.get("span_attributes", "{}")
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            # Fall back to the typed Maps when attributes_extra is empty
            # (the common case for LLM spans).
            if not parsed:
                parsed = {}
                for k, v in (arow.get("attrs_string") or {}).items():
                    parsed[k] = v
                for k, v in (arow.get("attrs_number") or {}).items():
                    parsed[k] = v
                for k, v in (arow.get("attrs_bool") or {}).items():
                    parsed[k] = bool(v)
            attrs_map[sid] = {
                "span_attributes": parsed,
                "provider": arow.get("provider"),
            }

    # Count
    count_query, count_params = builder.build_count_query()
    count_result = analytics.execute_ch_query(
        count_query, count_params, timeout_ms=30000
    )
    total_count = count_result.data[0].get("total", 0) if count_result.data else 0

    trace_ids = [str(row.get("trace_id", "")) for row in page_rows]

    # Phase 2: Eval scores
    eval_map = {}
    if trace_ids and eval_config_ids:
        eval_query, eval_params = builder.build_eval_query(trace_ids)
        if eval_query:
            eval_result = analytics.execute_ch_query(
                eval_query, eval_params, timeout_ms=30000
            )
            eval_map = TraceListQueryBuilder.pivot_eval_results(
                [(list(row.values())) for row in eval_result.data],
                list(eval_result.data[0].keys()) if eval_result.data else [],
                count_mode=True,
            )

    # Phase 3: Annotations — fetch from PG Score (unified annotation system)
    annotation_map = build_annotation_map(trace_ids, annotation_label_ids, label_types)

    # Phase 4 (child spans) removed — observation_span is a detail-only field.

    # Build column config. skip_choices=True: CHOICES evals render as a
    # SINGLE chip-style column carrying per-label counts.
    column_config = update_column_config_based_on_eval_config(
        [],
        eval_configs,
        is_simulator=True,
        skip_choices=True,
        eval_task_map=eval_task_map,
    )
    column_config = update_span_column_config_based_on_annotations(
        column_config, annotation_labels
    )

    # Assemble results
    results = []
    for row in page_rows:
        trace_id = str(row.get("trace_id", ""))
        span_id = str(row.get("span_id", ""))
        provider = row.get("provider") or "vapi"

        # Get span_attributes from CH (Phase 1b)
        attr_row = attrs_map.get(span_id, {})
        span_attrs = attr_row.get("span_attributes") or {}
        provider = attr_row.get("provider") or provider

        # Post-filter simulator calls in Python (can't do in CH without OOM)
        if sim_flag and VoiceCallListQueryBuilder.is_simulator_call(
            span_attrs, provider
        ):
            continue

        raw_log = span_attrs.get("raw_log") or {}
        voice_metrics = extract_voice_metrics(span_attrs, raw_log)

        # Process raw_log through existing provider-specific logic
        processed_log = ObservabilityService.process_raw_logs(
            raw_log, provider, span_attributes=span_attrs
        )

        entry = {
            **processed_log,
            "id": trace_id,
            "trace_id": trace_id,
            "turn_count": voice_metrics.get("turn_count"),
            "talk_ratio": voice_metrics.get("talk_ratio"),
            "agent_talk_percentage": voice_metrics.get("agent_talk_percentage"),
            "avg_agent_latency_ms": span_attrs.get("avg_agent_latency_ms"),
            "user_wpm": span_attrs.get("call.user_wpm"),
            "bot_wpm": span_attrs.get("call.bot_wpm"),
            "user_interruption_count": span_attrs.get("user_interruption_count"),
            "ai_interruption_count": span_attrs.get("ai_interruption_count"),
        }
        # Only override with voice_metrics if they have values — otherwise
        # keep the ones computed by process_raw_logs.
        if voice_metrics.get("turn_count") is not None:
            entry["turn_count"] = voice_metrics["turn_count"]
        if voice_metrics.get("talk_ratio") is not None:
            entry["talk_ratio"] = voice_metrics["talk_ratio"]
        if voice_metrics.get("agent_talk_percentage") is not None:
            entry["agent_talk_percentage"] = voice_metrics["agent_talk_percentage"]
        # Backfill response_time_ms from avg_agent_latency if VAPI didn't set it
        if not entry.get("response_time_ms") and entry.get("avg_agent_latency_ms"):
            entry["response_time_ms"] = entry["avg_agent_latency_ms"]

        # Strip heavy fields from list response — served by voice_call_detail.
        for key in heavy_keys:
            entry.pop(key, None)
        entry.setdefault("observation_span", [])

        # Include span attributes for custom columns (skip heavy/nested values)
        for key, value in span_attrs.items():
            if key in ("raw_log", "call") or key in entry:
                continue
            if isinstance(value, (str, int, float, bool)):
                entry[key] = value

        # Add eval metrics
        trace_evals = eval_map.get(trace_id, {})
        if trace_evals:
            metrics = {}
            for config in eval_configs:
                config_id = str(config.id)
                if config_id in trace_evals:
                    scores = trace_evals[config_id]
                    metric_name = getattr(config, "name", None) or (
                        getattr(config, "eval_template", None).name
                        if getattr(config, "eval_template", None)
                        else None
                    )
                    eval_template_config = (
                        config.eval_template.config
                        if getattr(config, "eval_template", None)
                        else {}
                    ) or {}
                    output_type = eval_template_config.get("output", "score")
                    metric_entry = {"name": metric_name, "output_type": output_type}
                    # All eval rows errored — surface error to frontend
                    if isinstance(scores, dict) and scores.get("error"):
                        metric_entry["error"] = True
                        metrics[config_id] = metric_entry
                        continue
                    # count_mode chip value: Pass/Fail -> {"pass","fail"},
                    # Choices -> {label: count}, Score -> numeric average.
                    metric_entry["output"] = eval_count_cell(scores, config)
                    metrics[config_id] = metric_entry
            if metrics:
                entry["eval_outputs"] = metrics

        # Add annotation outputs — flatten onto the row for frontend grid
        # compatibility (FE valueGetter reads params.data[labelId] directly).
        trace_annotations = annotation_map.get(trace_id, {})
        if trace_annotations:
            annotation_outputs = {}
            for label in annotation_labels:
                label_id = str(label.id)
                if label_id in trace_annotations:
                    entry[label_id] = trace_annotations[label_id]
                    annotation_outputs[label_id] = trace_annotations[label_id]
            if annotation_outputs:
                entry["annotation_outputs"] = annotation_outputs

        results.append(entry)

    # DRF-style paginated response
    total_pages = math.ceil(total_count / page_size) if page_size else 1
    response_data = {
        "count": total_count,
        "total_pages": total_pages,
        "current_page": page,
        "next": None,
        "previous": None,
        "results": results,
        "config": column_config,
    }
    if page < total_pages:
        response_data["next"] = page + 1
    if page > 1:
        response_data["previous"] = page - 1
    return response_data
