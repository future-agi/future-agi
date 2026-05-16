import structlog
from datetime import datetime, timedelta

from django.db.models import DateTimeField, F, FloatField, Q
from django.db.models.functions import Cast

from model_hub.models.choices import (
    AnnotatorRole,
    AutomationRuleTriggerFrequency,
    QueueItemSourceType,
)

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

FILTER_MODE_SOURCE_TYPES = {
    QueueItemSourceType.DATASET_ROW.value,
    QueueItemSourceType.TRACE.value,
    QueueItemSourceType.OBSERVATION_SPAN.value,
    QueueItemSourceType.TRACE_SESSION.value,
    QueueItemSourceType.CALL_EXECUTION.value,
}


def _trace_primary_span(trace):
    if not trace:
        return None

    prefetched_spans = getattr(trace, "_queue_export_spans", None)
    if prefetched_spans is not None:
        spans = list(prefetched_spans)
        root_spans = [
            span for span in spans if not getattr(span, "parent_span_id", None)
        ]
        return (
            next(
                (
                    span
                    for span in root_spans
                    if getattr(span, "observation_type", None) == "conversation"
                ),
                None,
            )
            or (root_spans[0] if root_spans else None)
            or (spans[0] if spans else None)
        )

    spans = trace.observation_spans.filter(deleted=False)
    root_spans = spans.filter(Q(parent_span_id__isnull=True) | Q(parent_span_id=""))
    return (
        root_spans.filter(observation_type="conversation")
        .order_by("start_time", "created_at")
        .first()
        or root_spans.order_by("start_time", "created_at").first()
        or spans.order_by("start_time", "created_at").first()
    )


def _metric_payload(obj, *, response_field="response_time"):
    return {
        "latency_ms": getattr(obj, "latency_ms", None),
        "response_time_ms": getattr(obj, response_field, None),
    }


def _call_execution_metric_payload(call):
    avg_agent_latency_ms = getattr(call, "avg_agent_latency_ms", None)
    payload = {
        "response_time_ms": getattr(call, "response_time_ms", None),
        "latency_ms": avg_agent_latency_ms,
        "avg_agent_latency_ms": avg_agent_latency_ms,
    }
    return {key: value for key, value in payload.items() if value is not None}


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


def _resolve_default_queue_scope(source_type, source_obj):
    """Return ``(lookup_kwargs, scope_name)`` identifying the default-queue
    scope for *source_obj*, or ``(None, None)`` if the source has no
    resolvable scope (e.g. a prototype_run without a develop project).

    Default queues are scoped per project / dataset / agent definition —
    they're not per-row. This helper centralises the mapping so the scores
    endpoints and the explicit ``get-or-create-default`` endpoint agree on
    what counts as "the default queue" for a given source.
    """
    if source_type in (
        QueueItemSourceType.TRACE.value,
        QueueItemSourceType.OBSERVATION_SPAN.value,
        QueueItemSourceType.TRACE_SESSION.value,
    ):
        project = None
        if source_type == QueueItemSourceType.TRACE.value:
            project = getattr(source_obj, "project", None)
        elif source_type == QueueItemSourceType.OBSERVATION_SPAN.value:
            project = getattr(source_obj, "project", None) or getattr(
                getattr(source_obj, "trace", None), "project", None
            )
        else:  # trace_session
            project = getattr(source_obj, "project", None)
        if not project:
            return None, None
        scope_name = (
            getattr(project, "name", None) or getattr(project, "agent_name", None) or str(project)
        )
        return {"project": project}, scope_name
    if source_type == QueueItemSourceType.DATASET_ROW.value:
        dataset = getattr(source_obj, "dataset", None)
        if not dataset:
            return None, None
        return {"dataset": dataset}, getattr(dataset, "name", None) or str(dataset)
    if source_type == QueueItemSourceType.CALL_EXECUTION.value:
        agent_def = getattr(
            getattr(source_obj, "test_execution", None), "agent_definition", None
        )
        if not agent_def:
            return None, None
        scope_name = (
            getattr(agent_def, "name", None)
            or getattr(agent_def, "agent_name", None)
            or str(agent_def)
        )
        return {"agent_definition": agent_def}, scope_name
    return None, None


def resolve_default_queue_for_source(source_type, source_obj, organization, user):
    """Return the active default ``AnnotationQueue`` for *source_obj*, creating
    one if neither an active nor an archived default exists for the scope.

    Returns ``None`` when the source has no resolvable scope (e.g. an
    orphaned ``prototype_run`` not linked to a project) so callers can
    decide whether to attribute the score elsewhere or skip queue scoping.

    NOTE: this is the single source of truth for "what queue does an
    inline/auto-created score belong to". Score writes from
    ``/scores/`` and ``/scores/bulk/`` route through here so every Score
    row has a non-null queue_item — matching the new per-queue uniqueness.
    """
    from model_hub.models.annotation_queues import (
        AnnotationQueue,
        AnnotationQueueStatusChoices,
    )

    lookup, scope_name = _resolve_default_queue_scope(source_type, source_obj)
    if not lookup:
        return None

    queue = AnnotationQueue.objects.filter(
        **lookup,
        is_default=True,
        deleted=False,
        organization=organization,
    ).first()
    if queue:
        _ensure_default_queue_member_can_manage(queue, user)
        return queue

    archived = (
        AnnotationQueue.all_objects.filter(
            **lookup,
            is_default=True,
            deleted=True,
            organization=organization,
        )
        .order_by("-deleted_at")
        .first()
    )
    if archived:
        from model_hub.views.annotation_queues import _restore_archived_default_queue

        _restore_archived_default_queue(archived)
        _ensure_default_queue_member_can_manage(archived, user)
        return archived

    workspace = None
    scope_obj = next(iter(lookup.values()))
    workspace = getattr(scope_obj, "workspace", None)

    queue = AnnotationQueue.objects.create(
        is_default=True,
        name=f"Default - {scope_name}",
        description=f"Default annotation queue for {scope_name}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization,
        workspace=workspace,
        created_by=user,
        **lookup,
    )
    _ensure_default_queue_member_can_manage(queue, user)
    return queue


def _ensure_default_queue_member_can_manage(queue, user):
    """Give active default-queue users full queue roles.

    Default queues are created/reused from inline annotation surfaces, not
    only from the queue settings page. Without this membership, a user can
    create annotations but cannot manage the resulting default queue.
    """
    if not queue or not user or not getattr(queue, "is_default", False):
        return None

    from model_hub.models.annotation_queues import (
        AnnotationQueueAnnotator,
        FULL_ACCESS_QUEUE_ROLES,
    )
    from model_hub.models.choices import AnnotatorRole

    active = AnnotationQueueAnnotator.objects.filter(
        queue=queue,
        user=user,
        deleted=False,
    ).first()
    if active:
        if (
            active.role == AnnotatorRole.MANAGER.value
            and active.normalized_roles == FULL_ACCESS_QUEUE_ROLES
        ):
            return active
        active.role = AnnotatorRole.MANAGER.value
        active.roles = FULL_ACCESS_QUEUE_ROLES
        active.save(update_fields=["role", "roles", "updated_at"])
        return active

    soft_deleted = (
        AnnotationQueueAnnotator.all_objects.filter(queue=queue, user=user)
        .order_by("-updated_at")
        .first()
    )
    if soft_deleted:
        soft_deleted.deleted = False
        soft_deleted.deleted_at = None
        soft_deleted.role = AnnotatorRole.MANAGER.value
        soft_deleted.roles = FULL_ACCESS_QUEUE_ROLES
        soft_deleted.save(
            update_fields=["deleted", "deleted_at", "role", "roles", "updated_at"]
        )
        return soft_deleted

    return AnnotationQueueAnnotator.objects.create(
        queue=queue,
        user=user,
        role=AnnotatorRole.MANAGER.value,
        roles=FULL_ACCESS_QUEUE_ROLES,
    )


def resolve_default_queue_item_for_source(
    source_type, source_obj, organization, user
):
    """Return a ``QueueItem`` on the source's default queue, creating both
    the queue and the item if they don't exist yet.

    Used by score writes to guarantee every Score has a ``queue_item``.
    Returns ``None`` when the source has no resolvable default-queue scope.
    """
    from model_hub.models.annotation_queues import QueueItem
    from model_hub.models.choices import QueueItemStatus

    queue = resolve_default_queue_for_source(
        source_type, source_obj, organization, user
    )
    if not queue:
        return None

    fk_field = get_fk_field_name(source_type)
    if not fk_field:
        return None

    item, _ = QueueItem.objects.get_or_create(
        queue=queue,
        source_type=source_type,
        **{f"{fk_field}_id": source_obj.pk},
        deleted=False,
        defaults={
            "organization": queue.organization,
            "workspace": queue.workspace,
            "status": QueueItemStatus.PENDING.value,
        },
    )
    return item


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
            org = getattr(run_test, "organization", None)
            if org is not None:
                return org

        for relation_name in (
            "agent_definition",
            "agent_version",
            "simulator_agent",
        ):
            related = getattr(test_execution, relation_name, None)
            org = (
                getattr(related, "organization", None)
                if related is not None
                else None
            )
            if org is not None:
                return org

    # Via scenario (CallExecution)
    scenario = getattr(obj, "scenario", None)
    if scenario is not None:
        return getattr(scenario, "organization", None)

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
            ws = getattr(run_test, "workspace", None)
            if ws is not None:
                return ws

        for relation_name in (
            "agent_definition",
            "agent_version",
            "simulator_agent",
        ):
            related = getattr(test_execution, relation_name, None)
            ws = (
                getattr(related, "workspace", None)
                if related is not None
                else None
            )
            if ws is not None:
                return ws

    # Via scenario (CallExecution)
    scenario = getattr(obj, "scenario", None)
    if scenario is not None:
        return getattr(scenario, "workspace", None)

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
            primary_span = _trace_primary_span(trace)
            metrics = _metric_payload(primary_span) if primary_span else {}
            return {
                "type": "trace",
                "name": trace.name or "",
                "project_id": str(trace.project_id) if trace.project_id else None,
                "input_preview": _truncate(str(trace.input or ""), 200),
                "output_preview": _truncate(str(trace.output or ""), 200),
                **metrics,
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
                **_metric_payload(span),
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
                **_call_execution_metric_payload(call),
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
                "source_id": str(row.id),
                "name": f"Row {row.order}",
                "created_at": row.created_at,
                "updated_at": row.updated_at,
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
            primary_span = _trace_primary_span(trace)
            span_metrics = _metric_payload(primary_span) if primary_span else {}
            trace_latency = getattr(trace, "latency", None)
            trace_status = getattr(trace, "status", None)
            return {
                "type": "trace",
                "trace_id": str(trace.id),
                "name": trace.name or "",
                "project_id": str(trace.project_id) if trace.project_id else None,
                "project_source": project_source,
                "created_at": trace.created_at,
                "updated_at": trace.updated_at,
                "input": trace.input,
                "output": trace.output,
                "metadata": trace.metadata if hasattr(trace, "metadata") else {},
                "latency": trace_latency,
                "latency_ms": (
                    span_metrics.get("latency_ms")
                    if span_metrics.get("latency_ms") is not None
                    else trace_latency
                ),
                "response_time_ms": span_metrics.get("response_time_ms"),
                "status": (
                    trace_status
                    if trace_status is not None
                    else getattr(primary_span, "status", None)
                    if primary_span
                    else None
                ),
                "span_attributes": (
                    getattr(primary_span, "span_attributes", {}) if primary_span else {}
                ),
                "resource_attributes": (
                    getattr(primary_span, "resource_attributes", {})
                    if primary_span
                    else {}
                ),
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
                "project_id": str(span.project_id) if span.project_id else None,
                "created_at": span.created_at,
                "updated_at": span.updated_at,
                "start_time": span.start_time,
                "end_time": span.end_time,
                "input": getattr(span, "input", None),
                "output": getattr(span, "output", None),
                "metadata": getattr(span, "metadata", {}),
                "events": getattr(span, "events", []),
                "latency_ms": getattr(span, "latency_ms", None),
                "response_time_ms": getattr(span, "response_time", None),
                "model": getattr(span, "model", None),
                "provider": getattr(span, "provider", None),
                "cost": getattr(span, "cost", None),
                "prompt_tokens": getattr(span, "prompt_tokens", None),
                "completion_tokens": getattr(span, "completion_tokens", None),
                "total_tokens": getattr(span, "total_tokens", None),
                "status": getattr(span, "status", None),
                "status_message": getattr(span, "status_message", None),
                "tags": getattr(span, "tags", []),
                "span_attributes": getattr(span, "span_attributes", {}),
                "resource_attributes": getattr(span, "resource_attributes", {}),
                "eval_attributes": getattr(span, "eval_attributes", {}),
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
                "created_at": run.created_at,
                "updated_at": run.updated_at,
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
                "source_id": str(call.id),
                "status": getattr(call, "status", ""),
                "simulation_call_type": getattr(call, "simulation_call_type", ""),
                "call_type": getattr(call, "call_type", None),
                "phone_number": getattr(call, "phone_number", None),
                "service_provider_call_id": getattr(call, "service_provider_call_id", None),
                "customer_call_id": getattr(call, "customer_call_id", None),
                "customer_number": getattr(call, "customer_number", None),
                "assistant_id": getattr(call, "assistant_id", None),
                "created_at": call.created_at,
                "updated_at": call.updated_at,
                "start_time": getattr(call, "started_at", None),
                "end_time": getattr(call, "completed_at", None),
                "ended_at": getattr(call, "ended_at", None),
                "duration_seconds": getattr(call, "duration_seconds", None),
                **_call_execution_metric_payload(call),
                "cost": getattr(call, "cost_cents", None),
                "ended_reason": getattr(call, "ended_reason", None),
                "message_count": getattr(call, "message_count", None),
                "call_summary": getattr(call, "call_summary", None),
                "user_wpm": getattr(call, "user_wpm", None),
                "agent_wpm": getattr(call, "bot_wpm", None),
                "talk_ratio": getattr(call, "talk_ratio", None),
                "user_interruption_count": getattr(call, "user_interruption_count", None),
                "ai_interruption_count": getattr(call, "ai_interruption_count", None),
                "input": getattr(call, "input", None),
                "output": getattr(call, "output", None),
                "metadata": getattr(call, "call_metadata", {}) or {},
                "call_metadata": getattr(call, "call_metadata", {}) or {},
                "provider_call_data": getattr(call, "provider_call_data", {}) or {},
                "monitor_call_data": getattr(call, "monitor_call_data", {}) or {},
                "analysis_data": getattr(call, "analysis_data", {}) or {},
                "evaluation_data": getattr(call, "evaluation_data", {}) or {},
                "customer_latency_metrics": (
                    getattr(call, "customer_latency_metrics", {}) or {}
                ),
            }

        elif item.source_type == QueueItemSourceType.TRACE_SESSION.value:
            session = item.trace_session
            if not session:
                return {"type": "trace_session", "deleted": True}
            return {
                "type": "trace_session",
                "session_id": str(session.id),
                "source_id": str(session.id),
                "name": session.name or "",
                "project_id": str(session.project_id) if session.project_id else None,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            }

    except Exception as e:
        logger.warning("source_content_error", item_id=str(item.id), error=str(e))

    return {"type": item.source_type, "error": "Could not resolve content"}


def auto_assign_items(queue, items):
    """Assign items to annotators based on queue strategy. Mutates items in-place."""
    from model_hub.models.annotation_queues import QueueItem, annotation_queue_role_q

    annotator_ids = list(
        queue.queue_annotators.filter(deleted=False)
        .filter(annotation_queue_role_q(AnnotatorRole.ANNOTATOR.value))
        .values_list("user_id", flat=True)
        .distinct()
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


RULE_TRIGGER_INTERVALS = {
    AutomationRuleTriggerFrequency.HOURLY.value: timedelta(hours=1),
    AutomationRuleTriggerFrequency.DAILY.value: timedelta(days=1),
    AutomationRuleTriggerFrequency.WEEKLY.value: timedelta(weeks=1),
    # Calendar-month scheduling is handled as a due check from an hourly
    # scheduler. Thirty days keeps the rule deterministic without pulling in a
    # new date arithmetic dependency.
    AutomationRuleTriggerFrequency.MONTHLY.value: timedelta(days=30),
}

# Cutoff between "process inline in the HTTP request" and "hand to Temporal".
# A capped dry-run with this cap is the cheap peek used to decide; with the
# ``[:cap+1]`` count fix in place the peek is sub-100ms even on million-row
# tables. Tuned for "user clicks Run, gets answer in <2s" while still keeping
# auto-assign + finalize work bounded enough to fit inside an HTTP timeout.
RULE_RUN_SYNC_THRESHOLD = 500


def is_automation_rule_due(rule, now=None):
    """Return True when a non-manual automation rule should run."""
    frequency = getattr(rule, "trigger_frequency", None)
    if not frequency or frequency == AutomationRuleTriggerFrequency.MANUAL.value:
        return False

    interval = RULE_TRIGGER_INTERVALS.get(frequency)
    if interval is None:
        logger.warning(
            "automation_rule_unknown_frequency",
            rule_id=str(rule.pk),
            trigger_frequency=frequency,
        )
        return False

    if rule.last_triggered_at is None:
        return True

    from django.utils import timezone as tz

    now = now or tz.now()
    return now - rule.last_triggered_at >= interval


def _update_rule_stats(rule):
    """Atomically bump trigger_count + last_triggered_at on the rule.

    Uses ``F("trigger_count") + 1`` so concurrent evaluators don't lose
    increments, and refreshes the in-memory rule afterwards so callers see
    the new value.
    """
    from django.db.models import F
    from django.utils import timezone as tz

    AutomationRule = type(rule)
    AutomationRule.objects.filter(pk=rule.pk).update(
        last_triggered_at=tz.now(),
        trigger_count=F("trigger_count") + 1,
    )
    rule.refresh_from_db(fields=["last_triggered_at", "trigger_count"])


def _finalize_automation_items(rule, created_items):
    """Mirror the post-create work the manual ``add-items`` flow does.

    - Run auto-assign (round_robin / load_balanced strategies).
    - Materialize per-annotator ``QueueItemAssignment`` rows when the queue
      uses ``auto_assign``.
    - Re-activate the queue if it was COMPLETED so newly added items don't
      get rejected at submit time.

    Without this, recurring rules can pile items into a queue that's still
    flagged COMPLETED and annotators see nothing change.
    """
    if not created_items:
        return

    from model_hub.models.annotation_queues import (
        AnnotationQueueAnnotator,
        QueueItem,
        QueueItemAssignment,
        annotation_queue_role_q,
    )
    from model_hub.models.choices import AnnotationQueueStatusChoices

    queue = rule.queue
    if queue.assignment_strategy != "manual":
        auto_assign_items(queue, created_items)
        # Persist the assigned_to ids the helper just stamped on the
        # in-memory objects.
        QueueItem.objects.bulk_update(created_items, ["assigned_to"])
    elif queue.auto_assign:
        member_ids = list(
            AnnotationQueueAnnotator.objects.filter(
                queue=queue, deleted=False
            )
            .filter(annotation_queue_role_q(AnnotatorRole.ANNOTATOR.value))
            .values_list("user_id", flat=True)
            .distinct()
        )
        if member_ids:
            QueueItemAssignment.objects.bulk_create(
                [
                    QueueItemAssignment(queue_item=item, user_id=uid)
                    for item in created_items
                    for uid in member_ids
                ],
                ignore_conflicts=True,
            )

    if queue.status == AnnotationQueueStatusChoices.COMPLETED.value:
        queue.status = AnnotationQueueStatusChoices.ACTIVE.value
        queue.save(update_fields=["status", "updated_at"])


def _normalize_filter_payload(filters):
    """Keep queue rule filters in the canonical snake_case API shape."""
    normalized = []
    for item in filters or []:
        column_id = item.get("column_id")
        if not column_id:
            continue
        config = item.get("filter_config") or {}
        filter_config = {
            "filter_type": config.get("filter_type"),
            "filter_op": config.get("filter_op"),
            "filter_value": config.get("filter_value"),
        }
        col_type = config.get("col_type")
        if col_type:
            filter_config["col_type"] = col_type
        normalized.append(
            {
                "column_id": column_id,
                "filter_config": filter_config,
                **(
                    {
                        "display_name": item.get("display_name")
                    }
                    if item.get("display_name")
                    else {}
                ),
            }
        )
    return normalized


def _coerce_range_value(value):
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return value[0], value[1]
    if isinstance(value, str) and "," in value:
        first, second = value.split(",", 1)
        return first.strip(), second.strip()
    return None, None


def _parse_datetime_value(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


def _apply_scalar_filter(qs, field_name, op, value):
    """Apply rule operators to a regular Django field."""
    if op in ("between", "not_between", "not_in_between"):
        start, end = _coerce_range_value(value)
        lookup = {f"{field_name}__range": (start, end)}
        if op in ("not_between", "not_in_between"):
            return qs.exclude(**lookup)
        return qs.filter(**lookup)
    if op == "not_in":
        values = value if isinstance(value, list) else [value]
        return qs.exclude(**{f"{field_name}__in": values})
    lookup, use_exclude = _op_to_lookup(field_name, op)
    if not lookup:
        return qs
    if op in ("is_null", "is_not_null"):
        value = True
    if use_exclude:
        return qs.exclude(**{lookup: value})
    return qs.filter(**{lookup: value})


def _filter_dataset_cells(cells, filter_type, filter_op, filter_value, column_type):
    """Apply one DevelopFilterRow-style filter to a Cell queryset."""
    if filter_type == "number":
        if filter_op in ("between", "not_between", "not_in_between"):
            min_val, max_val = _coerce_range_value(filter_value)
            min_val, max_val = float(min_val), float(max_val)
            if column_type == "audio":
                cells = cells.filter(value__regex=r"^https?:\/\/[^\s]+$").annotate(
                    numeric_value=Cast(
                        F("column_metadata__audio_duration_seconds"),
                        output_field=FloatField(),
                    )
                )
            else:
                cells = cells.filter(value__regex=r"^-?\d*\.?\d+$").annotate(
                    numeric_value=Cast("value", FloatField())
                )
            condition = Q(numeric_value__gte=min_val) & Q(numeric_value__lte=max_val)
            if filter_op in ("not_between", "not_in_between"):
                return cells.filter(~condition)
            return cells.filter(condition)

        op_map = {
            "equals": "exact",
            "not_equals": "exact",
            "greater_than": "gt",
            "less_than": "lt",
            "greater_than_or_equal": "gte",
            "less_than_or_equal": "lte",
        }
        lookup = op_map.get(filter_op)
        if not lookup:
            return cells.none()
        if column_type == "audio":
            cells = cells.filter(value__regex=r"^https?:\/\/[^\s]+$").annotate(
                numeric_value=Cast(
                    F("column_metadata__audio_duration_seconds"),
                    output_field=FloatField(),
                )
            )
        else:
            cells = cells.filter(value__regex=r"^-?\d*\.?\d+$").annotate(
                numeric_value=Cast("value", FloatField())
            )
        condition = Q(**{f"numeric_value__{lookup}": float(filter_value)})
        if filter_op == "not_equals":
            return cells.filter(~condition)
        return cells.filter(condition)

    if filter_type in ("text", "array", "categorical"):
        values = filter_value if isinstance(filter_value, list) else [filter_value]
        if filter_op in ("in", "not_in"):
            condition = Q(value__in=[str(v) for v in values])
            if filter_op == "not_in":
                return cells.filter(~condition)
            return cells.filter(condition)
        text_value = "" if filter_value is None else str(filter_value)
        op_map = {
            "contains": Q(value__icontains=text_value),
            "not_contains": Q(value__icontains=text_value),
            "equals": Q(value__iexact=text_value),
            "not_equals": Q(value__iexact=text_value),
            "starts_with": Q(value__istartswith=text_value),
            "ends_with": Q(value__iendswith=text_value),
        }
        condition = op_map.get(filter_op)
        if condition is None:
            return cells.none()
        if filter_op in ("not_contains", "not_equals"):
            return cells.filter(~condition)
        return cells.filter(condition)

    if filter_type == "boolean":
        value = str(filter_value).lower()
        if value == "true":
            return cells.filter(Q(value__icontains="true") | Q(value__iexact="Passed"))
        if value == "false":
            return cells.filter(Q(value__icontains="false") | Q(value__iexact="Failed"))
        return cells.none()

    if filter_type == "datetime":
        if filter_op in ("between", "not_between", "not_in_between"):
            start_raw, end_raw = _coerce_range_value(filter_value)
            start = _parse_datetime_value(start_raw)
            end = _parse_datetime_value(end_raw)
            cells = cells.annotate(datetime_value=Cast("value", DateTimeField()))
            condition = Q()
            if start:
                condition &= Q(datetime_value__gte=start)
            if end:
                condition &= Q(datetime_value__lte=end)
            if filter_op in ("not_between", "not_in_between"):
                return cells.filter(~condition)
            return cells.filter(condition)

        parsed = _parse_datetime_value(filter_value)
        if not parsed:
            return cells.none()
        cells = cells.annotate(datetime_value=Cast("value", DateTimeField()))
        op_map = {
            "equals": Q(datetime_value=parsed),
            "not_equals": Q(datetime_value=parsed),
            "greater_than": Q(datetime_value__gt=parsed),
            "less_than": Q(datetime_value__lt=parsed),
            "greater_than_or_equal": Q(datetime_value__gte=parsed),
            "less_than_or_equal": Q(datetime_value__lte=parsed),
        }
        condition = op_map.get(filter_op)
        if condition is None:
            return cells.none()
        if filter_op == "not_equals":
            return cells.filter(~condition)
        return cells.filter(condition)

    return cells.none()


def _resolve_dataset_rule_ids(rule, filters, dataset_id, cap):
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    dataset = Dataset.objects.get(
        id=dataset_id,
        organization=rule.organization,
        deleted=False,
    )
    rows = Row.objects.filter(dataset=dataset, deleted=False)
    columns = {
        str(col.id): col
        for col in Column.objects.filter(dataset=dataset, deleted=False)
    }
    all_cells = Cell.objects.filter(
        dataset=dataset,
        row__deleted=False,
        deleted=False,
    )

    for item in filters:
        column_id = str(item.get("column_id"))
        config = item.get("filter_config") or {}
        filter_type = config.get("filter_type")
        filter_op = config.get("filter_op")
        filter_value = config.get("filter_value")
        if not column_id or not filter_type or not filter_op:
            continue

        if column_id in ("order", "created_at"):
            rows = _apply_scalar_filter(rows, column_id, filter_op, filter_value)
            continue
        if column_id in ("dataset_name", "dataset__name"):
            rows = _apply_scalar_filter(rows, "dataset__name", filter_op, filter_value)
            continue

        column = columns.get(column_id)
        if not column:
            logger.warning(
                "automation_rule_dataset_column_not_found",
                rule_id=str(rule.pk),
                column_id=column_id,
                dataset_id=str(dataset_id),
            )
            rows = rows.none()
            break

        matching_cells = _filter_dataset_cells(
            all_cells.filter(column_id=column_id),
            filter_type,
            filter_op,
            filter_value,
            column.data_type,
        )
        rows = rows.filter(id__in=matching_cells.values_list("row_id", flat=True))

    rows = rows.order_by("order", "id")
    total_matching = rows.count()
    ids = list(rows.values_list("id", flat=True)[:cap])
    return total_matching, ids


def _add_source_ids_to_queue(rule, source_ids, total_matching, dry_run=False):
    from model_hub.models.annotation_queues import QueueItem

    fk_field = get_fk_field_name(rule.source_type)
    if not fk_field:
        return {"matched": 0, "added": 0, "duplicates": 0, "error": "Invalid FK field"}

    if dry_run:
        result = {"matched": total_matching, "added": 0, "duplicates": 0}
        # Propagate truncation from the resolver so the manual-run endpoint's
        # peek can branch sync vs async. Without this, filter-mode dry-runs
        # always reported ``truncated`` absent and every run took the sync
        # path (regression from before the sync/async split).
        if total_matching > len(source_ids):
            result["truncated"] = True
        return result

    candidate_ids = list(dict.fromkeys(source_ids))
    existing_source_ids = {
        str(source_id)
        for source_id in QueueItem.objects.filter(
            queue=rule.queue,
            deleted=False,
            **{f"{fk_field}_id__in": candidate_ids},
        ).values_list(f"{fk_field}_id", flat=True)
    }

    max_order = (
        QueueItem.objects.filter(queue=rule.queue, deleted=False)
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
    ) or 0

    items_to_create = []
    for source_id in candidate_ids:
        if str(source_id) in existing_source_ids:
            continue
        max_order += 1
        items_to_create.append(
            QueueItem(
                queue=rule.queue,
                source_type=rule.source_type,
                organization=rule.organization,
                order=max_order,
                **{f"{fk_field}_id": source_id},
            )
        )

    added = 0
    newly_created_ids = set()
    if items_to_create:
        QueueItem.objects.bulk_create(items_to_create, ignore_conflicts=True)
        current_source_ids = {
            str(source_id)
            for source_id in QueueItem.objects.filter(
                queue=rule.queue,
                deleted=False,
                **{f"{fk_field}_id__in": candidate_ids},
            ).values_list(f"{fk_field}_id", flat=True)
        }
        newly_created_ids = current_source_ids - existing_source_ids
        added = len(newly_created_ids)

    duplicates = len(candidate_ids) - added

    if newly_created_ids:
        # Re-read the actually-persisted rows so auto-assign + queue
        # reactivation operate on real DB ids (some may have lost the
        # ignore_conflicts race).
        created_items = list(
            QueueItem.objects.filter(
                queue=rule.queue,
                deleted=False,
                **{f"{fk_field}_id__in": list(newly_created_ids)},
            )
        )
        _finalize_automation_items(rule, created_items)

    _update_rule_stats(rule)
    result = {
        "matched": total_matching,
        "added": added,
        "duplicates": duplicates,
    }
    if total_matching > len(candidate_ids):
        result["truncated"] = True
    return result


def _evaluate_filter_mode_rule(rule, filters, scope, dry_run=False, user=None, cap=1000):
    filters = _normalize_filter_payload(filters)
    source_type = rule.source_type
    queue = rule.queue
    queue_scope_locked = not getattr(queue, "is_default", False)

    if source_type == QueueItemSourceType.DATASET_ROW.value:
        # Custom queues stay scoped to their configured source. Default queues
        # are only the landing place for direct annotations, so rules may add
        # items from another selected source.
        scope_dataset_id = scope.get("dataset_id")
        if (
            queue_scope_locked
            and queue.dataset_id
            and scope_dataset_id
            and str(scope_dataset_id) != str(queue.dataset_id)
        ):
            return {
                "matched": 0,
                "added": 0,
                "duplicates": 0,
                "error": "rule scope dataset_id must match the queue's bound dataset",
            }
        dataset_id = (
            queue.dataset_id
            if queue_scope_locked and queue.dataset_id
            else scope_dataset_id or queue.dataset_id
        )
        if not dataset_id:
            return {
                "matched": 0,
                "added": 0,
                "duplicates": 0,
                "error": "dataset_id is required for dataset row filters",
            }
        try:
            total_matching, ids = _resolve_dataset_rule_ids(
                rule, filters, dataset_id, cap
            )
        except Exception as exc:
            logger.warning(
                "automation_rule_dataset_filter_mode_failed",
                rule_id=str(rule.pk),
                dataset_id=str(dataset_id),
                error=str(exc),
            )
            return {
                "matched": 0,
                "added": 0,
                "duplicates": 0,
                "error": str(exc),
            }
        return _add_source_ids_to_queue(rule, ids, total_matching, dry_run=dry_run)

    # Custom queue scope is authoritative for trace/span/session/call_execution
    # too. Default queues are flexible and prefer the rule's selected scope.
    resolver = None
    scope_project_id = scope.get("project_id")
    if source_type == QueueItemSourceType.CALL_EXECUTION.value:
        if (
            queue_scope_locked
            and queue.agent_definition_id
            and scope_project_id
            and str(scope_project_id) != str(queue.agent_definition_id)
        ):
            return {
                "matched": 0,
                "added": 0,
                "duplicates": 0,
                "error": (
                    "rule scope project_id must match the queue's bound "
                    "agent_definition for call_execution rules"
                ),
            }
        project_id = (
            queue.agent_definition_id
            if queue_scope_locked and queue.agent_definition_id
            else scope_project_id or queue.agent_definition_id
        )
    else:
        if (
            queue_scope_locked
            and queue.project_id
            and scope_project_id
            and str(scope_project_id) != str(queue.project_id)
        ):
            return {
                "matched": 0,
                "added": 0,
                "duplicates": 0,
                "error": "rule scope project_id must match the queue's bound project",
            }
        project_id = (
            queue.project_id
            if queue_scope_locked and queue.project_id
            else scope_project_id or queue.project_id
        )
    if source_type == QueueItemSourceType.TRACE.value:
        from model_hub.services.bulk_selection import resolve_filtered_trace_ids

        resolver = resolve_filtered_trace_ids
    elif source_type == QueueItemSourceType.OBSERVATION_SPAN.value:
        from model_hub.services.bulk_selection import resolve_filtered_span_ids

        resolver = resolve_filtered_span_ids
    elif source_type == QueueItemSourceType.TRACE_SESSION.value:
        from model_hub.services.bulk_selection import resolve_filtered_session_ids

        resolver = resolve_filtered_session_ids
    elif source_type == QueueItemSourceType.CALL_EXECUTION.value:
        from model_hub.services.bulk_selection import resolve_filtered_call_execution_ids

        resolver = resolve_filtered_call_execution_ids

    if resolver is None:
        return None
    if not project_id:
        return {
            "matched": 0,
            "added": 0,
            "duplicates": 0,
            "error": "project_id is required for filter-mode automation rules",
        }

    resolver_kwargs = {
        "project_id": project_id,
        "filters": filters,
        "exclude_ids": set(),
        "organization": rule.organization,
        "workspace": queue.workspace,
        "cap": cap,
        "user": user,
    }
    if source_type == QueueItemSourceType.TRACE.value:
        resolver_kwargs["is_voice_call"] = bool(scope.get("is_voice_call", False))
        resolver_kwargs["remove_simulation_calls"] = bool(
            scope.get("remove_simulation_calls", False)
        )

    try:
        result = resolver(**resolver_kwargs)
    except Exception as exc:
        logger.warning(
            "automation_rule_filter_mode_failed",
            rule_id=str(rule.pk),
            source_type=source_type,
            error=str(exc),
        )
        return {
            "matched": 0,
            "added": 0,
            "duplicates": 0,
            "error": str(exc),
        }

    return _add_source_ids_to_queue(
        rule,
        result.ids,
        result.total_matching,
        dry_run=dry_run,
    )


def evaluate_rule(rule, dry_run=False, user=None, cap=1000):
    """Evaluate an automation rule and add matching items to the queue.
    Returns dict with 'matched', 'added', 'duplicates' counts.
    """
    from django.db import transaction
    from model_hub.models.annotation_queues import AutomationRule, QueueItem

    if dry_run:
        return _evaluate_rule_inner(rule, dry_run, user, cap)

    # Serialize concurrent evaluations of the SAME rule. Without this, two
    # firings (e.g. manual + scheduled, or two scheduled retries) can both
    # pre-check existence, both succeed at bulk_create(ignore_conflicts),
    # and both re-read + finalize the rows the other one wrote — over-
    # reporting `added` and re-running auto-assign on already-assigned
    # rows. We hold the lock only for this rule, so different rules on
    # the same queue can still evaluate concurrently.
    with transaction.atomic():
        list(AutomationRule.objects.select_for_update().filter(pk=rule.pk))
        return _evaluate_rule_inner(rule, dry_run, user, cap)


def _evaluate_rule_inner(rule, dry_run, user, cap):
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

    # High-water mark for scheduled rules: each tick rescans only rows newer
    # than the last run, with one interval of overlap to absorb clock skew +
    # late CDC replication. Manual runs intentionally skip this cutoff: the
    # manual endpoint uses ``last_triggered_at`` as a short duplicate-click
    # reservation before async work starts, and treating that reservation as
    # a data watermark would skip the existing backlog.
    frequency = getattr(rule, "trigger_frequency", None)
    if (
        rule.last_triggered_at
        and not dry_run
        and frequency != AutomationRuleTriggerFrequency.MANUAL.value
        and hasattr(model, "created_at")
    ):
        overlap = RULE_TRIGGER_INTERVALS.get(frequency or "", timedelta(minutes=5))
        qs = qs.filter(created_at__gte=rule.last_triggered_at - overlap)

    conditions = rule.conditions or {}
    has_filter_payload = "filter" in conditions or "filters" in conditions
    filter_payload = (
        conditions.get("filter")
        if "filter" in conditions
        else conditions.get("filters")
    )
    filter_scope = conditions.get("scope") or {}
    if rule.source_type in FILTER_MODE_SOURCE_TYPES and (
        has_filter_payload or filter_scope
    ):
        filter_result = _evaluate_filter_mode_rule(
            rule,
            filter_payload or [],
            filter_scope,
            dry_run=dry_run,
            user=user,
            cap=cap,
        )
        if filter_result is not None:
            return filter_result

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
                try:
                    if op in ("between", "not_between", "not_in_between"):
                        start, end = _coerce_range_value(value)
                        value = (
                            timedelta(seconds=float(start)),
                            timedelta(seconds=float(end)),
                        )
                    else:
                        value = timedelta(seconds=float(value))
                except (ValueError, TypeError):
                    logger.warning(
                        "evaluate_rule_duration_parse_error",
                        value=value,
                        rule_id=str(rule.pk),
                    )
                    continue

        if op in ("between", "not_between", "not_in_between"):
            start, end = _coerce_range_value(value)
            if start is None or end is None:
                logger.warning(
                    "evaluate_rule_between_parse_error",
                    field=field,
                    value=value,
                    rule_id=str(rule.pk),
                )
                continue
            lookup = f"{django_field}__range"
            try:
                if op in ("not_between", "not_in_between"):
                    qs = qs.exclude(**{lookup: (start, end)})
                else:
                    qs = qs.filter(**{lookup: (start, end)})
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

    # Fail closed: if the rule had N conditions but only some applied, the
    # queryset is broader than what the user wrote. Silently broadening
    # the match (e.g. a malformed `between` value or an unmapped field
    # being silently `continue`d) is worse than refusing to evaluate.
    if rules and rules_applied < len(rules):
        skipped = ", ".join(skipped_fields) if skipped_fields else "<n/a>"
        return {
            "matched": 0,
            "added": 0,
            "duplicates": 0,
            "error": (
                f"{len(rules) - rules_applied} of {len(rules)} rule "
                f"conditions could not be applied; refusing to evaluate. "
                f"unmapped/invalid fields: {skipped}"
            ),
        }

    # Capped match check — avoid an unbounded COUNT(*) on 10M+ row span tables.
    # We only need to know "≥ cap" to set the truncated flag; the exact count
    # for huge matches is not actionable here and was the primary timeout
    # source on /preview (held under select_for_update for non-dry runs).
    capped_candidates = list(qs[: cap + 1])
    truncated = len(capped_candidates) > cap
    candidates = capped_candidates[:cap]
    matched = len(candidates) + (1 if truncated else 0)

    if dry_run:
        result = {"matched": matched, "added": 0, "duplicates": 0}
        if truncated:
            result["truncated"] = True
        return result

    added = 0
    duplicates = 0
    max_order = (
        QueueItem.objects.filter(queue=rule.queue, deleted=False)
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
    ) or 0
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

        added = 0
        if items_to_create:
            # ignore_conflicts so a concurrent evaluator that already wrote
            # the same source_id (queue + fk unique constraint) doesn't blow
            # up this run with IntegrityError. Note: with ignore_conflicts,
            # the in-memory objects don't get their PKs populated, so we
            # re-read freshly persisted rows below before bulk_update.
            QueueItem.objects.bulk_create(items_to_create, ignore_conflicts=True)
            staged_source_ids = [
                obj.pk for obj in candidates if obj.pk not in existing_source_ids
            ]
            if staged_source_ids:
                created_items = list(
                    QueueItem.objects.filter(
                        queue=rule.queue,
                        deleted=False,
                        **{f"{fk_field}__in": staged_source_ids},
                    )
                )
                added = len(created_items)
                # Wire automation-created items through the same finalize
                # path manual adds use: auto-assign by load-balancing
                # across queue annotators, and reactivate the queue if it
                # was previously marked complete.
                if created_items:
                    _finalize_automation_items(rule, created_items)

    _update_rule_stats(rule)

    result = {"matched": matched, "added": added, "duplicates": duplicates}
    if truncated:
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
        "not_in": (f"{django_field}__in", True),
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
        "before": (f"{django_field}__lt", False),
        "after": (f"{django_field}__gt", False),
        "on": (f"{django_field}", False),
    }
    return mapping.get(op, (None, False))


def _truncate(text, max_len):
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ---------------------------------------------------------------------------
# Rule-completion email
# ---------------------------------------------------------------------------


def _rule_completion_recipients(rule, triggered_by_user_id=None):
    """Return list of email addresses to notify when a rule run completes.

    Recipients: rule.created_by + queue managers (AnnotatorRole.MANAGER on the
    queue). Triggering user is added so a manager who ran someone else's rule
    still gets the result. Dedup by user_id; skip users without an email.
    """
    from model_hub.models.annotation_queues import (
        AnnotationQueueAnnotator,
        annotation_queue_role_q,
    )

    seen_ids = set()
    emails = []

    def _add(user):
        if not user or not getattr(user, "email", None):
            return
        if user.id in seen_ids:
            return
        seen_ids.add(user.id)
        emails.append(user.email)

    _add(getattr(rule, "created_by", None))

    if triggered_by_user_id and triggered_by_user_id != getattr(
        rule.created_by, "id", None
    ):
        from django.contrib.auth import get_user_model

        try:
            _add(get_user_model().objects.get(pk=triggered_by_user_id))
        except Exception:
            pass

    manager_users = (
        AnnotationQueueAnnotator.objects.filter(queue=rule.queue, deleted=False)
        .filter(annotation_queue_role_q(AnnotatorRole.MANAGER.value))
        .select_related("user")
    )
    for ann in manager_users:
        _add(ann.user)

    return emails


def send_rule_completion_email(
    rule,
    result,
    *,
    triggered_by_user_id=None,
    error_message=None,
):
    """Send the rule-run completion email to creator + queue managers.

    ``result`` is the dict returned by ``evaluate_rule`` (or partial when the
    run failed). ``error_message`` overrides the success template with a
    failure variant. Failures here must not crash the activity — log and
    continue so the underlying queue writes (which succeeded) aren't rolled
    back.
    """
    import os

    from tfc.utils.email import email_helper

    recipients = _rule_completion_recipients(
        rule, triggered_by_user_id=triggered_by_user_id
    )
    if not recipients:
        logger.info(
            "automation_rule_completion_email_no_recipients",
            rule_id=str(rule.pk),
        )
        return

    queue = rule.queue
    queue_id = str(queue.id)
    frontend_url = os.environ.get("FRONTEND_URL", "https://app.futureagi.com").rstrip(
        "/"
    )
    queue_url = f"{frontend_url}/annotation-queues/{queue_id}"

    triggered_by_name = "the rule schedule"
    if triggered_by_user_id:
        from django.contrib.auth import get_user_model

        try:
            user = get_user_model().objects.get(pk=triggered_by_user_id)
            triggered_by_name = (
                user.get_full_name() or user.email or triggered_by_name
            )
        except Exception:
            pass

    status = "error" if error_message else "ok"
    subject_prefix = "[failed] " if status == "error" else ""
    subject = (
        f"{subject_prefix}Rule run: {rule.name} added "
        f"{result.get('added', 0)} item(s) to {queue.name}"
    )

    try:
        email_helper(
            mail_subject=subject,
            template_name="automation_rule_completion.html",
            template_data={
                "rule_name": rule.name,
                "queue_name": queue.name,
                "source_type": rule.source_type,
                "matched": result.get("matched", 0),
                "added": result.get("added", 0),
                "duplicates": result.get("duplicates", 0),
                "queue_url": queue_url,
                "triggered_by_name": triggered_by_name,
                "status": status,
                "error_message": error_message or "",
            },
            to_email_list=recipients,
        )
    except Exception as exc:
        logger.warning(
            "automation_rule_completion_email_send_failed",
            rule_id=str(rule.pk),
            recipients=len(recipients),
            error=str(exc),
        )
