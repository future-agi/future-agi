import structlog

from model_hub.models.choices import QueueItemSourceType

logger = structlog.get_logger(__name__)

# Maps source_type to (app_label.ModelName, fk_field_name)
SOURCE_MODEL_MAP = {
    QueueItemSourceType.DATASET_ROW.value: ("model_hub.Row", "dataset_row"),
    QueueItemSourceType.TRACE.value: ("tracer.Trace", "trace"),
    QueueItemSourceType.OBSERVATION_SPAN.value: (
        "tracer.ObservationSpan",
        "observation_span",
    ),
    QueueItemSourceType.PROTOTYPE_RUN.value: (
        "model_hub.RunPrompter",
        "prototype_run",
    ),
    QueueItemSourceType.CALL_EXECUTION.value: (
        "simulate.CallExecution",
        "call_execution",
    ),
    QueueItemSourceType.TRACE_SESSION.value: (
        "tracer.TraceSession",
        "trace_session",
    ),
}


def get_source_model(source_type):
    """Return the Django model class for a given source_type."""
    from django.apps import apps

    model_path, _ = SOURCE_MODEL_MAP.get(source_type, (None, None))
    if not model_path:
        return None
    app_label, model_name = model_path.rsplit(".", 1)
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        logger.warning("source_model_not_found", source_type=source_type)
        return None


def get_fk_field_name(source_type):
    """Return the FK field name on QueueItem for a given source_type."""
    _, fk_field = SOURCE_MODEL_MAP.get(source_type, (None, None))
    return fk_field


def resolve_source_object(source_type, source_id, organization=None, workspace=None):
    """Look up a source model instance by type and ID.

    When *organization* is provided the returned object is verified to belong
    to that organization.  The check accounts for the fact that some source
    models store ``organization`` directly while others reach it through a
    related FK (e.g. ``project.organization`` or ``dataset.organization``).
    ``None`` is returned when the object exists but does not belong to the
    requested organization.

    When *workspace* is provided, an additional check ensures the object
    belongs to that workspace (via direct FK or through a related project /
    dataset).  ``None`` is returned on mismatch.
    """
    model = get_source_model(source_type)
    if not model:
        return None
    try:
        obj = model.objects.get(pk=source_id)
    except model.DoesNotExist:
        return None

    if organization is not None:
        obj_org = _get_source_organization(obj)
        if obj_org is None or obj_org != organization:
            logger.warning(
                "source_org_mismatch",
                source_type=source_type,
                source_id=str(source_id),
                expected_org=str(organization.pk),
                actual_org=str(obj_org.pk) if obj_org else None,
            )
            return None

    if workspace is not None:
        obj_ws = _get_source_workspace(obj)
        ws_match = (
            obj_ws == workspace
            or (obj_ws is None and getattr(workspace, "is_default", False))
        )
        if not ws_match:
            logger.warning(
                "source_workspace_mismatch",
                source_type=source_type,
                source_id=str(source_id),
                expected_workspace=str(workspace.pk),
                actual_workspace=str(obj_ws.pk) if obj_ws else None,
            )
            return None

    return obj


def _get_source_organization(obj):
    """Return the organization that owns *obj*, traversing FKs as needed."""
    # Direct organization FK (ObservationSpan, RunPrompter, Dataset, …)
    org = getattr(obj, "organization", None)
    if org is not None:
        return org

    # Via project (Trace, TraceSession)
    project = getattr(obj, "project", None)
    if project is not None:
        return getattr(project, "organization", None)

    # Via dataset (Row)
    dataset = getattr(obj, "dataset", None)
    if dataset is not None:
        return getattr(dataset, "organization", None)

    # Via test_execution → run_test → organization (CallExecution)
    test_execution = getattr(obj, "test_execution", None)
    if test_execution is not None:
        run_test = getattr(test_execution, "run_test", None)
        if run_test is not None:
            return getattr(run_test, "organization", None)

    return None


def _get_source_workspace(obj):
    """Return the workspace that owns *obj*, traversing FKs as needed."""
    # Direct workspace FK
    ws = getattr(obj, "workspace", None)
    if ws is not None:
        return ws

    # Via project (Trace, TraceSession, ObservationSpan)
    project = getattr(obj, "project", None)
    if project is not None:
        return getattr(project, "workspace", None)

    # Via dataset (Row)
    dataset = getattr(obj, "dataset", None)
    if dataset is not None:
        return getattr(dataset, "workspace", None)

    # Via test_execution → run_test → workspace (CallExecution)
    test_execution = getattr(obj, "test_execution", None)
    if test_execution is not None:
        run_test = getattr(test_execution, "run_test", None)
        if run_test is not None:
            return getattr(run_test, "workspace", None)

    return None


def resolve_source_preview(item):
    """Return a standardized preview dict for a QueueItem's source."""
    try:
        if item.source_type == QueueItemSourceType.DATASET_ROW.value:
            row = item.dataset_row
            if not row:
                return {"type": "dataset_row", "deleted": True}
            return {
                "type": "dataset_row",
                "dataset_id": str(row.dataset_id),
                "dataset_name": getattr(row.dataset, "name", ""),
                "row_order": row.order,
            }

        elif item.source_type == QueueItemSourceType.TRACE.value:
            trace = item.trace
            if not trace:
                return {"type": "trace", "deleted": True}
            return {
                "type": "trace",
                "name": trace.name or "",
                "project_id": str(trace.project_id) if trace.project_id else None,
                "input_preview": _truncate(str(trace.input or ""), 200),
                "output_preview": _truncate(str(trace.output or ""), 200),
            }

        elif item.source_type == QueueItemSourceType.OBSERVATION_SPAN.value:
            span = item.observation_span
            if not span:
                return {"type": "observation_span", "deleted": True}
            return {
                "type": "observation_span",
                "name": span.name or "",
                "observation_type": getattr(span, "observation_type", ""),
                "input_preview": _truncate(str(getattr(span, "input", "") or ""), 200),
                "output_preview": _truncate(
                    str(getattr(span, "output", "") or ""), 200
                ),
            }

        elif item.source_type == QueueItemSourceType.PROTOTYPE_RUN.value:
            run = item.prototype_run
            if not run:
                return {"type": "prototype_run", "deleted": True}
            return {
                "type": "prototype_run",
                "name": getattr(run, "name", ""),
                "model": getattr(run, "model", ""),
                "status": getattr(run, "status", ""),
            }

        elif item.source_type == QueueItemSourceType.CALL_EXECUTION.value:
            call = item.call_execution
            if not call:
                return {"type": "call_execution", "deleted": True}
            return {
                "type": "call_execution",
                "status": getattr(call, "status", ""),
                "duration_seconds": getattr(call, "duration_seconds", None),
                "simulation_call_type": getattr(call, "simulation_call_type", ""),
            }

        elif item.source_type == QueueItemSourceType.TRACE_SESSION.value:
            session = item.trace_session
            if not session:
                return {"type": "trace_session", "deleted": True}
            return {
                "type": "trace_session",
                "session_id": str(session.id),
                "name": session.name or "",
                "project_id": str(session.project_id) if session.project_id else None,
            }

    except Exception as e:
        logger.warning("source_preview_error", item_id=str(item.id), error=str(e))

    return {"type": item.source_type, "error": "Could not resolve preview"}


def resolve_source_content(item):
    """Return full renderable content for a QueueItem's source (used in annotation view)."""
    try:
        if item.source_type == QueueItemSourceType.DATASET_ROW.value:
            row = item.dataset_row
            if not row:
                return {"type": "dataset_row", "deleted": True}
            data = {
                "type": "dataset_row",
                "dataset_id": str(row.dataset_id),
                "dataset_name": getattr(row.dataset, "name", ""),
                "row_order": row.order,
                "row_id": str(row.id),
            }
            # Include row field values from cells
            fields = {}
            field_types = {}
            try:
                from model_hub.models.develop_dataset import Cell

                cells = Cell.objects.filter(row=row).select_related("column")
                for cell in cells:
                    col_name = (
                        cell.column.name if cell.column else f"column_{cell.column_id}"
                    )
                    fields[col_name] = cell.value
                    if cell.column:
                        field_types[col_name] = cell.column.data_type
            except Exception:
                pass
            # Fallback: check for direct data/input fields
            if not fields:
                if hasattr(row, "data") and row.data:
                    fields = row.data
                elif hasattr(row, "input"):
                    for field in ["input", "output", "expected_output", "context"]:
                        val = getattr(row, field, None)
                        if val is not None:
                            fields[field] = val
            data["fields"] = fields
            if field_types:
                data["field_types"] = field_types
            return data

        elif item.source_type == QueueItemSourceType.TRACE.value:
            trace = item.trace
            if not trace:
                return {"type": "trace", "deleted": True}
            project_source = trace.project.source if trace.project_id else None
            return {
                "type": "trace",
                "trace_id": str(trace.id),
                "name": trace.name or "",
                "project_id": str(trace.project_id) if trace.project_id else None,
                "project_source": project_source,
                "input": trace.input,
                "output": trace.output,
                "metadata": trace.metadata if hasattr(trace, "metadata") else {},
                "latency": getattr(trace, "latency", None),
                "status": getattr(trace, "status", None),
            }

        elif item.source_type == QueueItemSourceType.OBSERVATION_SPAN.value:
            span = item.observation_span
            if not span:
                return {"type": "observation_span", "deleted": True}
            return {
                "type": "observation_span",
                "span_id": str(span.id),
                "trace_id": str(span.trace_id) if span.trace_id else None,
                "name": span.name or "",
                "observation_type": getattr(span, "observation_type", ""),
                "input": getattr(span, "input", None),
                "output": getattr(span, "output", None),
                "metadata": getattr(span, "metadata", {}),
                "events": getattr(span, "events", []),
            }

        elif item.source_type == QueueItemSourceType.PROTOTYPE_RUN.value:
            run = item.prototype_run
            if not run:
                return {"type": "prototype_run", "deleted": True}
            return {
                "type": "prototype_run",
                "run_id": str(run.id),
                "name": getattr(run, "name", ""),
                "model": getattr(run, "model", ""),
                "status": getattr(run, "status", ""),
                "prompt": getattr(run, "prompt", None),
                "response": getattr(run, "response", None),
            }

        elif item.source_type == QueueItemSourceType.CALL_EXECUTION.value:
            call = item.call_execution
            if not call:
                return {"type": "call_execution", "deleted": True}
            return {
                "type": "call_execution",
                "call_id": str(call.id),
                "status": getattr(call, "status", ""),
                "simulation_call_type": getattr(call, "simulation_call_type", ""),
                "duration_seconds": getattr(call, "duration_seconds", None),
                "input": getattr(call, "input", None),
                "output": getattr(call, "output", None),
            }

        elif item.source_type == QueueItemSourceType.TRACE_SESSION.value:
            session = item.trace_session
            if not session:
                return {"type": "trace_session", "deleted": True}
            return {
                "type": "trace_session",
                "session_id": str(session.id),
                "name": session.name or "",
                "project_id": str(session.project_id) if session.project_id else None,
            }

    except Exception as e:
        logger.warning("source_content_error", item_id=str(item.id), error=str(e))

    return {"type": item.source_type, "error": "Could not resolve content"}


def auto_assign_items(queue, items):
    """Assign items to annotators based on queue strategy. Mutates items in-place."""
    from model_hub.models.annotation_queues import QueueItem

    annotator_ids = list(
        queue.queue_annotators.filter(deleted=False).values_list("user_id", flat=True)
    )
    if not annotator_ids or queue.assignment_strategy == "manual":
        return

    if queue.assignment_strategy == "round_robin":
        # Evenly distribute across annotators
        existing_count = (
            QueueItem.objects.filter(queue=queue, deleted=False)
            .exclude(assigned_to__isnull=True)
            .count()
        )
        for i, item in enumerate(items):
            idx = (existing_count + i) % len(annotator_ids)
            item.assigned_to_id = annotator_ids[idx]

    elif queue.assignment_strategy == "load_balanced":
        # Assign to annotator with fewest pending + in_progress items
        from django.db.models import Count
        from django.db.models import Q as DQ

        counts = dict.fromkeys(annotator_ids, 0)
        qs = (
            QueueItem.objects.filter(
                queue=queue,
                deleted=False,
                status__in=["pending", "in_progress"],
            )
            .values("assigned_to_id")
            .annotate(cnt=Count("id"))
        )
        for row in qs:
            if row["assigned_to_id"] in counts:
                counts[row["assigned_to_id"]] = row["cnt"]
        for item in items:
            uid = min(counts, key=counts.get)
            item.assigned_to_id = uid
            counts[uid] += 1


def calculate_agreement(queue):
    """Calculate inter-annotator agreement metrics for a queue."""
    from collections import defaultdict

    from model_hub.models.score import Score

    annotations = (
        Score.objects.filter(queue_item__queue=queue, deleted=False)
        .select_related("label")
        .values_list(
            "queue_item_id",
            "label_id",
            "label__name",
            "label__type",
            "annotator_id",
            "value",
        )
    )

    # Group by (item, label) → list of (annotator, value)
    item_label_map = defaultdict(list)
    label_info = {}
    for qi_id, label_id, label_name, label_type, ann_id, value in annotations:
        item_label_map[(qi_id, label_id)].append((ann_id, value))
        if label_id not in label_info:
            label_info[label_id] = {"name": label_name, "type": label_type}

    # Per-label agreement
    label_results = {}
    for label_id, info in label_info.items():
        agree_count = 0
        total_count = 0
        disagreement_items = []

        for (qi_id, lid), entries in item_label_map.items():
            if lid != label_id or len(entries) < 2:
                continue
            total_count += 1
            values = [_normalize_value(v) for _, v in entries]
            if len(set(values)) == 1:
                agree_count += 1
            else:
                disagreement_items.append(str(qi_id))

        agreement_pct = agree_count / total_count if total_count > 0 else None
        kappa = (
            _cohens_kappa(item_label_map, label_id)
            if info["type"] == "categorical"
            else None
        )

        label_results[str(label_id)] = {
            "label_name": info["name"],
            "label_type": info["type"],
            "agreement_pct": (
                round(agreement_pct, 3) if agreement_pct is not None else None
            ),
            "cohens_kappa": round(kappa, 3) if kappa is not None else None,
            "disagreement_count": len(disagreement_items),
            "disagreement_items": disagreement_items[:20],
        }

    # Overall agreement
    total_pairs = 0
    agree_pairs = 0
    for (qi_id, lid), entries in item_label_map.items():
        if len(entries) < 2:
            continue
        total_pairs += 1
        values = [_normalize_value(v) for _, v in entries]
        if len(set(values)) == 1:
            agree_pairs += 1

    overall = agree_pairs / total_pairs if total_pairs > 0 else None

    # Annotator pair agreement
    annotator_pairs = _annotator_pair_agreement(item_label_map)

    return {
        "overall_agreement": round(overall, 3) if overall is not None else None,
        "labels": label_results,
        "annotator_pairs": annotator_pairs,
    }


def _normalize_value(v):
    """Normalize annotation value for comparison.

    Dict values that are lists are sorted so that e.g.
    ``{"selected": ["A", "B"]}`` and ``{"selected": ["B", "A"]}``
    compare as equal.
    """
    if isinstance(v, dict):
        normalized = {
            k: sorted(val) if isinstance(val, list) else val for k, val in v.items()
        }
        return str(sorted(normalized.items()))
    if isinstance(v, list):
        return str(sorted(v))
    return str(v)


def _cohens_kappa(item_label_map, label_id):
    """Calculate Cohen's Kappa for a specific label across ALL annotator pairs.

    When there are 3+ annotators on an item, every pair is compared using
    ``itertools.combinations`` rather than only the first two entries.
    """
    from collections import Counter
    from itertools import combinations

    all_values = []
    pairs = []
    for (qi_id, lid), entries in item_label_map.items():
        if lid != label_id or len(entries) < 2:
            continue
        # Compare ALL annotator pairs, not just the first two
        for (_, v1_raw), (_, v2_raw) in combinations(entries, 2):
            v1 = _normalize_value(v1_raw)
            v2 = _normalize_value(v2_raw)
            pairs.append((v1, v2))
            all_values.extend([v1, v2])

    if not pairs:
        return None

    n = len(pairs)
    categories = list(set(all_values))

    # Observed agreement
    p_o = sum(1 for v1, v2 in pairs if v1 == v2) / n

    # Expected agreement
    p_e = 0
    for cat in categories:
        p1 = sum(1 for v1, _ in pairs if v1 == cat) / n
        p2 = sum(1 for _, v2 in pairs if v2 == cat) / n
        p_e += p1 * p2

    if p_e >= 1:
        return 1.0
    return (p_o - p_e) / (1 - p_e)


def _annotator_pair_agreement(item_label_map):
    """Calculate agreement between each pair of annotators."""
    from collections import defaultdict
    from itertools import combinations

    pair_data = defaultdict(lambda: {"agree": 0, "total": 0})

    for (qi_id, lid), entries in item_label_map.items():
        if len(entries) < 2:
            continue
        for (a1_id, v1), (a2_id, v2) in combinations(entries, 2):
            key = tuple(sorted([str(a1_id), str(a2_id)]))
            pair_data[key]["total"] += 1
            if _normalize_value(v1) == _normalize_value(v2):
                pair_data[key]["agree"] += 1

    result = []
    for (a1, a2), data in pair_data.items():
        pct = data["agree"] / data["total"] if data["total"] > 0 else 0
        result.append(
            {
                "annotator_1_id": a1,
                "annotator_2_id": a2,
                "agreement_pct": round(pct, 3),
                "total_comparisons": data["total"],
            }
        )

    return result


# ---------------------------------------------------------------------------
# Field mapping: view-level camelCase field IDs → Django ORM field names.
# The frontend sends camelCase propertyIds (matching the tracing / session /
# simulation filter UIs).  This mapping converts them to ORM lookups.
# It also serves as an allowlist – unmapped fields are rejected.
# ---------------------------------------------------------------------------
FIELD_MAPPING = {
    QueueItemSourceType.TRACE.value: {
        # Snake_case (primary)
        "trace_id": "id",
        "trace_name": "name",
        "node_type": "node_type",  # annotated from root span
        "user_id": "user_id",  # annotated from root span
        "project_name": "project__name",
        "name": "name",
        "input": "input",
        "output": "output",
        "error": "error",
        "tags": "tags",
        "status": "status",  # annotated from root span
        "created_at": "created_at",
        "project__name": "project__name",
        # Legacy camelCase
        "traceId": "id",
        "traceName": "name",
        "nodeType": "node_type",
        "userId": "user_id",
        "projectName": "project__name",
    },
    QueueItemSourceType.OBSERVATION_SPAN.value: {
        # Snake_case (primary)
        "trace_id": "trace_id",
        "trace_name": "trace__name",  # trace's name via FK
        "node_type": "observation_type",
        "user_id": "end_user__user_id",
        "project_name": "project__name",
        "name": "name",
        "observation_type": "observation_type",
        "input": "input",
        "output": "output",
        "model": "model",
        "provider": "provider",
        "status": "status",  # direct field on span
        "created_at": "created_at",
        "project__name": "project__name",
        # Legacy camelCase
        "traceId": "trace_id",
        "traceName": "trace__name",
        "nodeType": "observation_type",
        "userId": "end_user__user_id",
        "projectName": "project__name",
    },
    QueueItemSourceType.TRACE_SESSION.value: {
        # Snake_case (primary)
        "duration": "duration_seconds",  # annotated
        "total_cost": "total_cost",  # annotated
        "start_time": "start_time",  # annotated
        "end_time": "end_time",  # annotated
        "user_id": "user_id",  # annotated
        "project_name": "project__name",
        "name": "name",
        "created_at": "created_at",
        "project__name": "project__name",
        # Legacy camelCase
        "totalCost": "total_cost",
        "startTime": "start_time",
        "endTime": "end_time",
        "userId": "user_id",
        "projectName": "project__name",
    },
    QueueItemSourceType.CALL_EXECUTION.value: {
        # Snake_case (primary)
        "status": "status",
        "persona": "call_metadata__rowData__persona",
        "agent_definition": "test_execution__agent_definition__name",
        "call_type": "simulation_call_type",
        "simulation_call_type": "simulation_call_type",
        "duration_seconds": "duration_seconds",
        "overall_score": "overall_score",
        "created_at": "created_at",
        # Legacy camelCase
        "agentDefinition": "test_execution__agent_definition__name",
        "callType": "simulation_call_type",
    },
    QueueItemSourceType.DATASET_ROW.value: {
        # Snake_case (primary)
        "dataset_name": "dataset__name",
        "order": "order",
        "created_at": "created_at",
        "dataset__name": "dataset__name",
        # Legacy camelCase
        "datasetName": "dataset__name",
        "createdAt": "created_at",
    },
    QueueItemSourceType.PROTOTYPE_RUN.value: {
        "name": "name",
        "model": "model",
        "status": "status",
        "created_at": "created_at",
        # Legacy camelCase
        "createdAt": "created_at",
    },
}

# ORM field names that require queryset annotation (not stored on model).
_NEEDS_ANNOTATION = {
    QueueItemSourceType.TRACE.value: {"node_type", "status", "user_id"},
    QueueItemSourceType.TRACE_SESSION.value: {
        "duration_seconds",
        "total_cost",
        "start_time",
        "end_time",
        "user_id",
    },
}


def _annotate_for_rules(qs, source_type, needed_orm_fields):
    """Add computed-field annotations that rule conditions require."""
    annotatable = _NEEDS_ANNOTATION.get(source_type, set())
    to_annotate = needed_orm_fields & annotatable
    if not to_annotate:
        return qs

    if source_type == QueueItemSourceType.TRACE.value:
        return _annotate_trace_for_rules(qs, to_annotate)
    if source_type == QueueItemSourceType.TRACE_SESSION.value:
        return _annotate_session_for_rules(qs, to_annotate)
    return qs


def _annotate_trace_for_rules(qs, fields):
    """Annotate Trace queryset with computed fields derived from root spans."""
    from django.db.models import (
        Case,
        CharField,
        Exists,
        OuterRef,
        Subquery,
        Value,
        When,
    )

    from tracer.models.observation_span import ObservationSpan

    root_span_qs = ObservationSpan.objects.filter(
        trace_id=OuterRef("id"), parent_span_id__isnull=True
    )

    if "node_type" in fields:
        qs = qs.annotate(
            node_type=Case(
                When(
                    Exists(root_span_qs),
                    then=Subquery(root_span_qs.values("observation_type")[:1]),
                ),
                default=Value("unknown"),
                output_field=CharField(),
            )
        )

    if "status" in fields:
        qs = qs.annotate(
            status=Case(
                When(
                    Exists(root_span_qs.filter(status="ERROR")),
                    then=Value("ERROR"),
                ),
                When(
                    Exists(root_span_qs.filter(status="OK")),
                    then=Value("OK"),
                ),
                default=Value("UNSET"),
                output_field=CharField(),
            )
        )

    if "user_id" in fields:
        qs = qs.annotate(user_id=Subquery(root_span_qs.values("end_user__user_id")[:1]))

    return qs


def _annotate_session_for_rules(qs, fields):
    """Annotate TraceSession queryset with aggregate stats from spans."""
    from django.db.models import (
        DurationField,
        ExpressionWrapper,
        F,
        FloatField,
        OuterRef,
        Subquery,
        Sum,
    )
    from django.db.models.functions import Coalesce

    from tracer.models.observation_span import ObservationSpan

    spans_qs = ObservationSpan.objects.filter(trace__session_id=OuterRef("id"))

    # start_time and end_time are also needed internally for duration
    need_start = "start_time" in fields or "duration_seconds" in fields
    need_end = "end_time" in fields or "duration_seconds" in fields

    if need_start:
        qs = qs.annotate(
            start_time=Subquery(
                spans_qs.order_by("start_time").values("start_time")[:1]
            )
        )

    if need_end:
        qs = qs.annotate(
            end_time=Subquery(spans_qs.order_by("-end_time").values("end_time")[:1])
        )

    if "duration_seconds" in fields:
        qs = qs.annotate(
            _session_duration=ExpressionWrapper(
                F("end_time") - F("start_time"),
                output_field=DurationField(),
            ),
        )

    if "total_cost" in fields:
        qs = qs.annotate(
            total_cost=Coalesce(
                Subquery(
                    spans_qs.values("trace__session_id")
                    .annotate(_total=Sum("cost", output_field=FloatField()))
                    .values("_total")[:1]
                ),
                0.0,
            )
        )

    if "user_id" in fields:
        qs = qs.annotate(
            user_id=Subquery(
                spans_qs.exclude(end_user__isnull=True)
                .order_by("start_time")
                .values("end_user__user_id")[:1]
            )
        )

    return qs


def evaluate_rule(rule, dry_run=False):
    """Evaluate an automation rule and add matching items to the queue.
    Returns dict with 'matched', 'added', 'duplicates' counts.
    """
    from model_hub.models.annotation_queues import QueueItem

    model = get_source_model(rule.source_type)
    if not model:
        return {
            "matched": 0,
            "added": 0,
            "duplicates": 0,
            "error": "Invalid source_type",
        }

    fk_field = get_fk_field_name(rule.source_type)
    if not fk_field:
        return {"matched": 0, "added": 0, "duplicates": 0, "error": "Invalid FK field"}

    # Build Django queryset filters from conditions, scoped to the rule's org
    qs = model.objects.all()
    qs = qs.filter(deleted=False)
    if hasattr(model, "organization"):
        qs = qs.filter(organization=rule.organization)
    elif hasattr(model, "project"):
        qs = qs.filter(project__organization=rule.organization)
    elif hasattr(model, "dataset"):
        qs = qs.filter(dataset__organization=rule.organization)

    # Scope to the queue's project/dataset/agent_definition if set.
    queue = rule.queue
    if queue.project_id:
        # Traces, spans, sessions belong to a project
        if rule.source_type in ("trace", "observation_span", "trace_session"):
            qs = qs.filter(project_id=queue.project_id)
    if queue.dataset_id:
        # Rows belong to a dataset
        if rule.source_type == "dataset_row":
            qs = qs.filter(dataset_id=queue.dataset_id)
    if queue.agent_definition_id:
        # Call executions belong to an agent_definition via test_execution
        if rule.source_type == "call_execution":
            qs = qs.filter(
                test_execution__agent_definition_id=queue.agent_definition_id
            )

    conditions = rule.conditions or {}
    rules = conditions.get("rules", [])
    field_mapping = FIELD_MAPPING.get(rule.source_type, {})

    # Collect which ORM fields need annotation before filtering
    needed_orm_fields = set()
    for cond in rules:
        field = cond.get("field", "")
        orm_field = field_mapping.get(field)
        if orm_field:
            needed_orm_fields.add(orm_field)

    # Annotate computed fields before applying filter conditions
    qs = _annotate_for_rules(qs, rule.source_type, needed_orm_fields)

    skipped_fields = []
    rules_applied = 0
    for cond in rules:
        field = cond.get("field", "")
        op = cond.get("op", "eq")
        value = cond.get("value")

        # Map view-level field ID to Django ORM field
        django_field = field_mapping.get(field)
        if not django_field:
            logger.warning(
                "rule_field_not_mapped",
                field=field,
                source_type=rule.source_type,
            )
            skipped_fields.append(field)
            continue

        # Duration is stored as a DurationField annotation; convert seconds
        if django_field == "duration_seconds":
            django_field = "_session_duration"
            if op not in ("is_null", "is_not_null"):
                from datetime import timedelta

                try:
                    value = timedelta(seconds=float(value))
                except (ValueError, TypeError):
                    logger.warning(
                        "evaluate_rule_duration_parse_error",
                        value=value,
                        rule_id=str(rule.pk),
                    )
                    continue

        lookup, use_exclude = _op_to_lookup(django_field, op)
        if lookup:
            try:
                # is_null / is_not_null need boolean True for __isnull
                if op in ("is_null", "is_not_null"):
                    value = True
                if use_exclude:
                    qs = qs.exclude(**{lookup: value})
                else:
                    qs = qs.filter(**{lookup: value})
                rules_applied += 1
            except Exception as exc:
                logger.warning(
                    "evaluate_rule_condition_skipped",
                    field=field,
                    op=op,
                    error=str(exc),
                    rule_id=str(rule.pk),
                )
                continue

    # If no valid conditions were applied, refuse to match (avoids matching everything)
    if skipped_fields and rules_applied == 0:
        return {
            "matched": 0,
            "added": 0,
            "duplicates": 0,
            "error": f"No valid conditions — unmapped fields: {skipped_fields}",
        }

    matched = qs.count()
    if dry_run:
        return {"matched": matched, "added": 0, "duplicates": 0}

    added = 0
    duplicates = 0
    max_order = (
        QueueItem.objects.filter(queue=rule.queue, deleted=False)
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
    ) or 0

    candidates = list(qs[:1000])  # Limit to 1000 per evaluation
    if candidates:
        # Batch-check existing items with a single query
        existing_source_ids = set(
            QueueItem.objects.filter(
                queue=rule.queue,
                deleted=False,
                **{f"{fk_field}__in": candidates},
            ).values_list(f"{fk_field}_id", flat=True)
        )

        items_to_create = []
        for obj in candidates:
            if obj.pk in existing_source_ids:
                duplicates += 1
                continue
            max_order += 1
            items_to_create.append(
                QueueItem(
                    queue=rule.queue,
                    source_type=rule.source_type,
                    organization=rule.organization,
                    order=max_order,
                    **{fk_field: obj},
                )
            )

        if items_to_create:
            QueueItem.objects.bulk_create(items_to_create)
        added = len(items_to_create)

    # Update rule stats
    from django.utils import timezone as tz

    rule.last_triggered_at = tz.now()
    rule.trigger_count = (rule.trigger_count or 0) + 1
    rule.save(update_fields=["last_triggered_at", "trigger_count", "updated_at"])

    result = {"matched": matched, "added": added, "duplicates": duplicates}
    if matched > len(candidates):
        result["truncated"] = True
    return result


def _op_to_lookup(django_field, op):
    """Convert condition operator to a Django ORM lookup.

    Returns a ``(lookup_string, use_exclude)`` tuple.  When *use_exclude* is
    ``True`` the caller must use ``qs.exclude()`` instead of ``qs.filter()``.
    Returns ``(None, False)`` for unrecognised operators.
    """
    mapping = {
        # Short-form operators (original)
        "eq": (f"{django_field}", False),
        "ne": (f"{django_field}", True),
        "gt": (f"{django_field}__gt", False),
        "lt": (f"{django_field}__lt", False),
        "gte": (f"{django_field}__gte", False),
        "lte": (f"{django_field}__lte", False),
        "contains": (f"{django_field}__icontains", False),
        "in": (f"{django_field}__in", False),
        # Long-form operators (from frontend LLMFilterBox)
        "equals": (f"{django_field}", False),
        "not_equals": (f"{django_field}", True),
        "greater_than": (f"{django_field}__gt", False),
        "less_than": (f"{django_field}__lt", False),
        "greater_than_or_equal": (f"{django_field}__gte", False),
        "less_than_or_equal": (f"{django_field}__lte", False),
        "starts_with": (f"{django_field}__istartswith", False),
        "ends_with": (f"{django_field}__iendswith", False),
        "not_contains": (f"{django_field}__icontains", True),
        "is_null": (f"{django_field}__isnull", False),
        "is_not_null": (f"{django_field}__isnull", True),
    }
    return mapping.get(op, (None, False))


def _truncate(text, max_len):
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
