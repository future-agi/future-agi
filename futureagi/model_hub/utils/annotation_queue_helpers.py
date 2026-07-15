from datetime import datetime, timedelta
from typing import Any, TypedDict

import structlog
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.db.models import DateTimeField, F, FloatField, Q
from django.db.models.functions import Cast

from model_hub.constants import ANNOTATION_LABEL_VALUE_KEYS
from model_hub.models.choices import (
    AnnotatorRole,
    AutomationRuleTriggerFrequency,
    QueueItemSourceType,
)
from simulate.serializers import CallTranscriptSerializer
from simulate.utils.stored_transcript_roles import get_displayable_transcript_roles

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

# Tracer-owned sources live only in ClickHouse (the tracer app is CH-native; the
# legacy PG tables are gone). Annotation reads of these resolve/render/filter from
# CH, never PG. The remaining source types stay PG-backed.
_CH_NATIVE_SOURCE_TYPES = frozenset(
    {
        QueueItemSourceType.TRACE.value,
        QueueItemSourceType.OBSERVATION_SPAN.value,
        QueueItemSourceType.TRACE_SESSION.value,
    }
)

FILTER_MODE_SOURCE_TYPES = {
    QueueItemSourceType.DATASET_ROW.value,
    QueueItemSourceType.TRACE.value,
    QueueItemSourceType.OBSERVATION_SPAN.value,
    QueueItemSourceType.TRACE_SESSION.value,
    QueueItemSourceType.CALL_EXECUTION.value,
}

AUTOMATION_RULE_FILTER_ERROR_MESSAGE = (
    "Rule evaluation failed while applying filters. Check the selected fields "
    "and values, then try again."
)
TRACE_IN_PROGRESS_ADD_ERROR = (
    "Trace is still in progress and can't be added to an annotation queue yet."
)

# A trace's root span is terminal (the trace is complete and addable) once its
# status reaches OK / ERROR; any other value (e.g. UNSET) means still in progress.
_TERMINAL_SPAN_STATUSES = frozenset({"OK", "ERROR"})


def _is_terminal_span_status(status):
    return (status or "").upper() in _TERMINAL_SPAN_STATUSES


def _automation_rule_filter_error_message(exc):
    """Return a short public error while full exception details stay in logs."""
    message = str(exc).strip()
    if isinstance(exc, ValueError) and message and "\n" not in message:
        return message[:240]
    return AUTOMATION_RULE_FILTER_ERROR_MESSAGE


def _call_execution_metric_payload(call):
    avg_agent_latency_ms = getattr(call, "avg_agent_latency_ms", None)
    payload = {
        "response_time_ms": getattr(call, "response_time_ms", None),
        "latency_ms": avg_agent_latency_ms,
        "avg_agent_latency_ms": avg_agent_latency_ms,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _call_transcript_turns(call: Any) -> list[dict[str, Any]]:
    if not call:
        return []
    try:
        rows = getattr(call, "_displayable_transcripts", None)
        if rows is None:
            rows = list(
                call.transcripts.filter(
                    speaker_role__in=get_displayable_transcript_roles()
                ).order_by("start_time_ms")
            )
        return list(CallTranscriptSerializer(rows, many=True).data)
    except (ObjectDoesNotExist, AttributeError, DatabaseError) as exc:
        logger.warning(
            "call_transcript_fetch_failed", call_id=str(call.id), error=str(exc)
        )
        return []


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


def _trace_project_workspace_filter(workspace):
    if getattr(workspace, "is_default", False):
        return Q(project__workspace=workspace) | Q(project__workspace__isnull=True)
    return Q(project__workspace=workspace)


def is_source_available_for_annotation(source_type, source_obj):
    """Return ``(is_available, reason)`` for a single queue add-item.

    CH-native: a trace is in progress while its CH root span has not reached a
    terminal status (OK / ERROR); otherwise it's addable. ``source_obj`` is the
    duck-typed :class:`_CHTraceSource` the resolver already built, so its
    ``root_span`` is in hand — no extra CH read. A source with no resolvable root
    never reaches here (the resolver returns ``None`` first); defensively, a
    missing root reads as available. Never query PG.
    """
    if source_type != QueueItemSourceType.TRACE.value:
        return True, None
    root_span = getattr(source_obj, "root_span", None)
    if root_span is None or _is_terminal_span_status(
        getattr(root_span, "status", None)
    ):
        return True, None
    return False, TRACE_IN_PROGRESS_ADD_ERROR


def filter_available_source_ids_for_annotation(
    source_type, source_ids, *, organization=None, workspace=None, project_id=None
):
    """Split resolved filter-mode trace IDs into available / unavailable.

    CH-native mirror of :func:`is_source_available_for_annotation` for the bulk
    filter path: a trace is in progress while its CH root span is not terminal.
    Reads the roots LEAN in one batched call (OOM-safe) via
    :func:`_batch_ch_trace_roots`, scoped by ``project_id`` (the filter selection is
    per-project). A trace with no CH root reads as available (it was never resolvable
    as in-progress). FAIL OPEN on a CH error — a transient read must not silently
    drop every add. ``organization`` / ``workspace`` are already applied upstream by
    ``resolve_filtered_trace_ids``. Returns ``(available_ids, unavailable_count,
    unavailable_message)`` preserving input order. Never query PG.
    """
    ordered_ids = [str(source_id) for source_id in source_ids]
    if source_type != QueueItemSourceType.TRACE.value or not ordered_ids:
        return ordered_ids, 0, None

    roots_by_trace = _batch_ch_trace_roots(
        ordered_ids, project_id=str(project_id) if project_id else None
    )

    def _available(trace_id):
        root = roots_by_trace.get(trace_id)
        # No CH root → available (matches the single-add no-root branch); else
        # addable only once the root span reaches a terminal status.
        return root is None or _is_terminal_span_status(getattr(root, "status", None))

    available_ids = [sid for sid in ordered_ids if _available(sid)]
    unavailable_count = len(ordered_ids) - len(available_ids)
    if unavailable_count <= 0:
        return available_ids, 0, None

    if unavailable_count == 1:
        message = (
            "1 trace is still in progress and was not added to the annotation queue."
        )
    else:
        message = (
            f"{unavailable_count} traces are still in progress and "
            "were not added to the annotation queue."
        )
    return available_ids, unavailable_count, message


def source_project(source_obj, organization=None):
    """Return the PG ``Project`` for *source_obj*, or ``None``.

    A tracer source resolves CH-native and duck-types (``_CHTraceSource`` /
    :class:`CHSpan` / ``_CHTraceSessionSource``) — it carries only ``project_id``,
    not a ``.project`` relation. Resolve the Project row directly (org-scoped when
    given, fail-closed): the default-queue scope row lives in PG, which is not a
    tracer table, and cross-store FK joins aren't possible. A PG-backed source
    (dataset_row / call_execution) still exposes ``.project`` and short-circuits.
    """
    project = getattr(source_obj, "project", None)
    if project is not None:
        return project
    project_id = getattr(source_obj, "project_id", None)
    if not project_id:
        return None
    from tracer.models.project import Project

    qs = Project.objects.filter(id=project_id)
    if organization is not None:
        qs = qs.filter(organization=organization)
    return qs.first()


def _resolve_default_queue_scope(source_type, source_obj, organization=None):
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
        project = source_project(source_obj, organization)
        if not project:
            return None, None
        scope_name = (
            getattr(project, "name", None)
            or getattr(project, "agent_name", None)
            or str(project)
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

    lookup, scope_name = _resolve_default_queue_scope(
        source_type, source_obj, organization=organization
    )
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
        FULL_ACCESS_QUEUE_ROLES,
        AnnotationQueueAnnotator,
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


def resolve_default_queue_item_for_source(source_type, source_obj, organization, user):
    """Return a ``QueueItem`` on the source's default queue, creating both
    the queue and the item if they don't exist yet.

    Used by score writes to guarantee every Score has a ``queue_item``.
    Returns ``None`` when the source has no resolvable default-queue scope.

    *source_obj* may be either a Django model instance (uses ``.pk``) or
    a CH-loaded dataclass like :class:`CHSpan` (uses ``.id``); the FK
    column on ``QueueItem`` is a ``CharField``-like that accepts the str
    UUID identically either way.
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

    # CHSpan dataclasses don't expose a Django ``pk`` descriptor; fall back
    # to ``.id`` for parity. Django models also expose ``.id`` for the PK on
    # every model in this codebase, so this works uniformly.
    source_pk = getattr(source_obj, "pk", None) or getattr(source_obj, "id", None)
    if source_pk is None:
        return None

    item, _ = QueueItem.objects.get_or_create(
        queue=queue,
        source_type=source_type,
        **{f"{fk_field}_id": source_pk},
        deleted=False,
        defaults={
            "organization": queue.organization,
            "workspace": queue.workspace,
            "status": QueueItemStatus.PENDING.value,
        },
    )
    return item


def resolve_source_object(source_type, source_id, organization=None, workspace=None):
    """Look up a source object by type and ID.

    Tracer sources (trace / observation_span / trace_session) live only in
    ClickHouse — the tracer app is CH-native — so they resolve straight from CH and
    come back duck-typed (``.id`` / ``.project_id``); the CH resolver tenant-scopes
    them against the PG ``Project`` (which is not a tracer table), fail-closed.

    Non-tracer source types (dataset_row / call_execution / prototype_run) are
    PG-backed models, verified to belong to *organization* / *workspace* (directly or
    via a related project / dataset). ``None`` on mismatch or absence.
    """
    if source_type in _CH_NATIVE_SOURCE_TYPES:
        return _resolve_ch_source_object(
            source_type, source_id, organization=organization, workspace=workspace
        )

    model = get_source_model(source_type)
    if not model:
        return None

    obj = model.objects.filter(pk=source_id).first()
    if obj is None:
        return None

    if not _source_passes_tenant_gate(
        obj, source_type, source_id, organization=organization, workspace=workspace
    ):
        return None

    return obj


def _source_passes_tenant_gate(
    obj, source_type, source_id, *, organization=None, workspace=None
):
    """Org + workspace gate for a PG-backed source, shared by the per-item
    (:func:`resolve_source_object`) and bulk (:func:`_resolve_pg_sources_bulk`)
    resolvers so both apply the identical rule. FAIL CLOSED: a missing/mismatched org,
    or a workspace that doesn't match (allowing a null source workspace only under a
    default workspace), denies. ``None`` org/workspace skips that check (unscoped call)."""
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
            return False

    if workspace is not None:
        obj_ws = _get_source_workspace(obj)
        ws_match = obj_ws == workspace or (
            obj_ws is None and getattr(workspace, "is_default", False)
        )
        if not ws_match:
            logger.warning(
                "source_workspace_mismatch",
                source_type=source_type,
                source_id=str(source_id),
                expected_workspace=str(workspace.pk),
                actual_workspace=str(obj_ws.pk) if obj_ws else None,
            )
            return False

    return True


def _tenant_scoped_project(project_id, *, organization=None, workspace=None):
    """Tenant gate for CH-resolved collector sources: return the PG ``Project`` for
    *project_id* iff accessible to the org/workspace, else ``None``. ``organization``
    is required (a CH source has no tenant of its own — the org distinguishes a
    same-org null-workspace project from a foreign one). FAIL CLOSED: missing
    project_id / missing organization / org or workspace mismatch all deny.
    """
    if not project_id or organization is None:
        return None
    from tracer.models.project import Project

    project = Project.objects.filter(id=project_id, organization=organization).first()
    if project is None:
        return None
    if workspace is not None:
        proj_ws = getattr(project, "workspace", None)
        ws_match = proj_ws == workspace or (
            proj_ws is None and getattr(workspace, "is_default", False)
        )
        if not ws_match:
            return None
    return project


class _CHTraceSource:
    """Duck-typed collector trace for annotation resolution (no PG ``Trace`` row).

    Exposes the soft id the add path stores (``.id`` / ``.pk`` = trace_id), the
    ``.project_id``, and the resolved ``.root_span`` (:class:`CHSpan`). Mirrors how a
    collector observation_span is duck-typed as a bare CHSpan. The in-progress guard
    reads ``.root_span.status`` (terminal = addable) — the trace already resolved
    from its CH root, so no extra read is needed.
    """

    def __init__(self, trace_id, root_span):
        self.id = str(trace_id)
        self.pk = str(trace_id)
        self.root_span = root_span
        self.project_id = str(getattr(root_span, "project_id", "") or "") or None


def _resolve_ch_source_object(
    source_type, source_id, *, organization=None, workspace=None
):
    """CH fallback for :func:`resolve_source_object` (collector data, no PG row).
    Returns a duck-typed CH object (``.id`` / ``.project_id``) or ``None``, tenant-
    verified against the PG ``Project``. CH errors are logged and denied (fail closed).
    """
    if source_type == QueueItemSourceType.OBSERVATION_SPAN.value:
        from tracer.services.clickhouse.v2 import get_reader

        try:
            with get_reader() as reader:
                span = reader.get(str(source_id))
        except Exception as exc:  # narrow: any CH read failure → deny
            logger.warning(
                "ch_source_resolve_error",
                source_type=source_type,
                source_id=str(source_id),
                error=str(exc),
            )
            return None
        if span is None:
            return None
        if (
            _tenant_scoped_project(
                getattr(span, "project_id", None),
                organization=organization,
                workspace=workspace,
            )
            is None
        ):
            logger.warning(
                "ch_source_tenant_denied",
                source_type=source_type,
                source_id=str(source_id),
            )
            return None
        return span

    if source_type == QueueItemSourceType.TRACE_SESSION.value:
        return _resolve_ch_trace_session(
            source_id, organization=organization, workspace=workspace
        )

    if source_type == QueueItemSourceType.TRACE.value:
        # Collector trace (no PG row): resolve its root span from CH for the
        # project_id, tenant-gate, and duck-type the trace so the add path
        # stores the soft trace_id. The item is annotated at this root span.
        root_span = _ch_root_span_for_trace(source_id)
        if root_span is None:
            return None
        if (
            _tenant_scoped_project(
                getattr(root_span, "project_id", None),
                organization=organization,
                workspace=workspace,
            )
            is None
        ):
            logger.warning(
                "ch_source_tenant_denied",
                source_type=source_type,
                source_id=str(source_id),
            )
            return None
        return _CHTraceSource(source_id, root_span)

    # other source types are not CH-resolvable in this wave.
    return None


def _resolve_ch_trace_session(source_id, *, organization=None, workspace=None):
    """CH fallback for a collector ``trace_session`` (no PG row): existence +
    project_id from the CH reader, tenant-verified against PG. Returns a duck-typed
    object (``.id`` / ``.project_id`` / ``.name``) or ``None`` (fail closed)."""
    from tracer.services.clickhouse.v2.trace_session_dict_reader import (
        resolve_session_fields,
    )

    try:
        fields = resolve_session_fields([str(source_id)]).get(str(source_id))
    except Exception as exc:  # narrow: CH read failure → deny
        logger.warning(
            "ch_session_resolve_error",
            source_id=str(source_id),
            error=str(exc),
        )
        return None
    if not fields:
        return None
    project_id = fields.get("project_id")
    if (
        _tenant_scoped_project(
            project_id, organization=organization, workspace=workspace
        )
        is None
    ):
        logger.warning("ch_session_tenant_denied", source_id=str(source_id))
        return None
    return _CHTraceSessionSource(
        id=str(source_id),
        project_id=str(project_id) if project_id else None,
        name=fields.get("display_name") or fields.get("external_session_id") or "",
        first_seen=fields.get("first_seen"),
    )


def _ch_span_for_item(span_id):
    """Best-effort CH point-read for a render path (preview/content). Returns the
    :class:`CHSpan` or ``None`` (genuinely-gone span OR CH error). FAIL OPEN on the
    render (None → ``deleted`` sentinel) but logs — never raises into the page."""
    if not span_id:
        return None
    from tracer.services.clickhouse.v2 import get_reader

    try:
        with get_reader() as reader:
            return reader.get(str(span_id))
    except Exception as exc:
        logger.warning("ch_span_render_error", span_id=str(span_id), error=str(exc))
        return None


def _pick_conversation_root(root_spans):
    """From a trace's parentless (root) spans, prefer the conversation root, else
    the first. Shared by the per-item (:func:`_ch_root_span_for_trace`) and batched
    (:func:`_batch_ch_trace_roots`) CH trace reads so both pick identically."""
    if not root_spans:
        return None
    for span in root_spans:
        if getattr(span, "observation_type", None) == "conversation":
            return span
    return root_spans[0]


def _ch_root_span_for_trace(trace_id):
    """Best-effort CH read of a trace's root span (resolve/render path).

    Reads ONLY the parentless (root) spans, LEAN, via ``roots_by_trace_ids``: a
    voice conversation root carries its whole raw log in the heavy columns, and a
    full ``list_by_trace`` here is the exact TH-6442 code-241 OOM shape on the
    shared cluster — this runs on every trace resolution. Prefers the conversation
    root, else the first parentless span; a trace with no parentless span in CH
    resolves to ``None`` (fail closed — never a full scan to find one root). FAIL
    OPEN on CH error — logs, never raises into the page."""
    if not trace_id:
        return None
    from tracer.services.clickhouse.v2 import get_reader

    try:
        with get_reader() as reader:
            roots = reader.roots_by_trace_ids([str(trace_id)], include_heavy=False)
    except Exception as exc:
        logger.warning("ch_trace_render_error", trace_id=str(trace_id), error=str(exc))
        return None
    return _pick_conversation_root(roots)


def _ch_session_fields_for_item(session_id):
    """Best-effort CH read of a session's identity fields for a render path.
    Returns the fields dict or ``None`` (missing session OR CH error). FAIL OPEN on
    the render but logs."""
    if not session_id:
        return None
    from tracer.services.clickhouse.v2.trace_session_dict_reader import (
        resolve_session_fields,
    )

    try:
        return resolve_session_fields([str(session_id)]).get(str(session_id))
    except Exception as exc:
        logger.warning(
            "ch_session_render_error", session_id=str(session_id), error=str(exc)
        )
        return None


def _batch_ch_spans(span_ids, *, project_id=None, include_heavy=True):
    """Batch CH point-read for a render path: ``{str(id): CHSpan}`` over *span_ids*
    in one query. CH error → ``{}`` (FAIL OPEN — the per-item collector branch then
    renders the ``deleted`` sentinel, same as a single-read miss). Backs
    :class:`CollectorSourceCache` so list/export pages do one CH read, not one per item.
    ``project_id`` (optional) scopes the read to one tenant on the ``spans`` PK prefix;
    omit for prior behavior — see :func:`_batch_ch_trace_roots` on why not ``org_id``.
    ``include_heavy`` defaults True (the render path needs the preview/content columns);
    the add path passes ``False`` to read only the identity/status columns it gates and
    stamps on, so a large scoped add stays a sub-second point read, not a heavy scan."""
    if not span_ids:
        return {}
    from tracer.services.clickhouse.v2 import get_reader

    try:
        with get_reader() as reader:
            spans = reader.list_by_ids(
                [str(s) for s in span_ids],
                project_id=project_id,
                include_heavy=include_heavy,
            )
    except Exception as exc:
        logger.warning(
            "ch_span_batch_render_error", count=len(span_ids), error=str(exc)
        )
        return {}
    return {str(span.id): span for span in spans}


# Chunk the trace-id IN-list so one batch can't grow unbounded; a queue's selection
# reaches 10k items. The read itself is LEAN (see below), so peak memory is bounded
# by row count, not by the fat voice columns.
_CH_TRACE_ID_BATCH = 500


def _batch_ch_trace_roots(trace_ids, *, project_id=None):
    """Batch CH read of each trace's root span for a render/availability path:
    ``{str(trace_id): CHSpan}`` over *trace_ids* (chunked, LEAN).

    Reads the parentless (root) spans LEAN — input / output / metadata /
    span_attributes stay real; the heavy ``resource_attributes`` and ``events`` come
    back empty. That is a deliberate trade against ClickHouse OOM: those columns
    carry the voice raw_log on the CONVERSATION ROOT, and a batched heavy read of
    many roots is exactly the code-241 shape on the shared cluster. ``project_id``
    (optional) scopes the read to one tenant on the non-null PK-prefix column — do
    NOT scope by ``org_id`` here: a collector span row may carry a NULL ``org_id``
    (``ObservationSpan.org_id`` is nullable and copied verbatim in the backfill), so
    an org filter would silently drop those roots and read them as "no root". When
    *project_id* is omitted the ``trace_ids`` are already tenant-scoped by the caller.
    CH error → ``{}`` (FAIL OPEN — the per-item branch then does its own point-read
    or renders the ``deleted`` sentinel). Backs :class:`CollectorSourceCache` so a
    list page over CH traces does one read per chunk, not one per item."""
    if not trace_ids:
        return {}
    from tracer.services.clickhouse.v2 import get_reader

    ids = [str(t) for t in trace_ids]
    roots_by_trace: dict[str, list] = {}
    try:
        with get_reader() as reader:
            for start in range(0, len(ids), _CH_TRACE_ID_BATCH):
                chunk = ids[start : start + _CH_TRACE_ID_BATCH]
                for span in reader.roots_by_trace_ids(
                    chunk, include_heavy=False, project_id=project_id
                ):
                    roots_by_trace.setdefault(str(span.trace_id), []).append(span)
    except Exception as exc:
        logger.warning("ch_trace_roots_batch_error", count=len(ids), error=str(exc))
        return {}
    return {
        trace_id: _pick_conversation_root(spans)
        for trace_id, spans in roots_by_trace.items()
    }


def _batch_ch_session_fields(session_ids, *, project_id=None):
    """Batch CH read of session identity fields: ``{str(id): fields}`` in one query.
    CH error → ``{}`` (FAIL OPEN). Companion to :func:`_batch_ch_spans`. ``project_id``
    (optional) scopes the read to one tenant on the ``trace_sessions`` PK prefix."""
    if not session_ids:
        return {}
    from tracer.services.clickhouse.v2.trace_session_dict_reader import (
        resolve_session_fields,
    )

    try:
        return (
            resolve_session_fields([str(s) for s in session_ids], project_id=project_id)
            or {}
        )
    except Exception as exc:
        logger.warning(
            "ch_session_batch_render_error", count=len(session_ids), error=str(exc)
        )
        return {}


class CollectorSourceCache:
    """Page-scoped batch cache of CH-resolved sources (trace / span / session).

    ``resolve_source_preview`` / ``resolve_source_content`` run once per item; tracer
    sources (trace / observation_span / trace_session) are read CH-only, so each call
    would otherwise do its own CH point-read — an N+1 against ClickHouse on every
    list/export page (CH has no ORM prefetch). Build one cache per page with
    :meth:`for_items` and pass it as ``ch_cache=`` so the page does a single CH read
    per kind. A cache miss returns ``None`` → ``deleted`` sentinel, matching the
    single-read fail-open.
    """

    __slots__ = ("_spans", "_sessions", "_trace_roots")

    def __init__(self, *, spans=None, sessions=None, trace_roots=None):
        self._spans = spans or {}
        self._sessions = sessions or {}
        self._trace_roots = trace_roots or {}

    @classmethod
    def for_items(cls, items):
        """Collect the tracer source ids across *items* and batch-resolve each kind
        from CH. Traces resolve to their root span (LEAN), spans and sessions by soft id.

        Reads are grouped by the item's denormalized ``project_id`` and scoped to it,
        so each read prunes the ``spans`` PK prefix to one tenant instead of scanning
        the whole multi-tenant table. A page's items can legitimately span projects
        (add-items only tenant-scopes the source), so a single queue-wide scope would
        drop off-project items and render them ``deleted`` — hence per-item project.
        Items whose ``project_id`` is NULL (pre-denormalization rows) fall into one
        unscoped group: correct, just unpruned. A page spans few distinct projects, so
        this is a handful of scoped reads, not one per item. Empty id-sets short-circuit."""
        # project_id (str, or None for pre-denorm rows) -> per-kind soft-id sets
        by_project: dict[object, dict] = {}
        for item in items or []:
            source_type = getattr(item, "source_type", None)
            pid = getattr(item, "project_id", None)
            buckets = by_project.setdefault(
                str(pid) if pid else None,
                {"spans": set(), "sessions": set(), "traces": set()},
            )
            if (
                source_type == QueueItemSourceType.OBSERVATION_SPAN.value
                and item.observation_span_id
            ):
                buckets["spans"].add(str(item.observation_span_id))
            elif (
                source_type == QueueItemSourceType.TRACE_SESSION.value
                and item.trace_session_id
            ):
                buckets["sessions"].add(str(item.trace_session_id))
            elif source_type == QueueItemSourceType.TRACE.value and item.trace_id:
                buckets["traces"].add(str(item.trace_id))

        # A soft id belongs to exactly one project, so the per-group results never
        # collide on merge. NULL-project group (project_id=None) is the prior read.
        spans, sessions, trace_roots = {}, {}, {}
        for pid, buckets in by_project.items():
            spans.update(_batch_ch_spans(buckets["spans"], project_id=pid))
            sessions.update(
                _batch_ch_session_fields(buckets["sessions"], project_id=pid)
            )
            trace_roots.update(_batch_ch_trace_roots(buckets["traces"], project_id=pid))
        return cls(spans=spans, sessions=sessions, trace_roots=trace_roots)

    def span(self, span_id):
        return self._spans.get(str(span_id)) if span_id else None

    def trace_root(self, trace_id):
        return self._trace_roots.get(str(trace_id)) if trace_id else None

    def session_fields(self, session_id):
        return self._sessions.get(str(session_id)) if session_id else None


class _CHTraceSessionSource:
    """Duck-typed stand-in for a PG ``TraceSession`` resolved from CH. Carries only
    the attributes the annotation scope/store/render path reads off a session
    (``id`` / ``pk`` / ``project_id`` / ``name`` / ``first_seen``); it is NOT a Django
    model and must never be assigned to a relation (the store path uses the soft id).

    ``pk`` mirrors ``id`` for Django-model parity — the score store reads
    ``source_obj.pk`` (same as :class:`CHSpan` and :class:`_CHTraceSource`)."""

    __slots__ = ("id", "pk", "project_id", "name", "first_seen")

    def __init__(self, *, id, project_id, name, first_seen):
        self.id = id
        self.pk = str(id)
        self.project_id = project_id
        self.name = name
        self.first_seen = first_seen


# Above this many CH-native ids resolved without a project to scope by, the add path
# takes the per-item CH fallback (bounded, but slow enough to be worth a log line).
_CH_BULK_FALLBACK_WARN = 25

# select_related paths that let the PG tenant gate traverse org/workspace off a batched
# fetch without a lazy load per row. Only dataset_row is common on the enumerated add
# path; the rarer kinds keep their multi-hop traversal lazy (small N).
_PG_SOURCE_SELECT_RELATED = {
    QueueItemSourceType.DATASET_ROW.value: ("dataset",),
}


def resolve_source_objects_bulk(
    items_data, *, project_id=None, organization=None, workspace=None
):
    """Batch sibling of :func:`resolve_source_object` for the enumerated add path.

    Returns ``{(source_type, str(source_id)): source_obj}`` for every resolved,
    tenant-verified source in *items_data* (a missing or cross-tenant id is simply
    absent — the caller reports it). One scoped CH read per tracer kind + one ``pk__in``
    fetch per PG kind, replacing the former per-item N+1 (a fresh CH client + point read
    + ``.exists()`` per item, which blew the 30s gateway on large adds).

    The CH-native kinds need *project_id* (the payload's project) to scope the read to
    the ``spans`` PK prefix — an unscoped batch of a few hundred ids scans the whole
    multi-tenant table and times out, so batching without scoping is strictly worse than
    the N+1. The tenant is gated against that project **once** here (fail closed); a
    scoped read then only returns that project's rows, so no per-row project check is
    needed. When *project_id* is absent (or not the caller's), the CH kinds fall back to
    the per-item resolver (bounded — a point read per id). PG kinds resolve regardless."""
    from collections import defaultdict

    ids_by_type: dict[str, list[str]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for item in items_data:
        source_type = item["source_type"]
        source_id = str(item["source_id"])
        if (source_type, source_id) in seen:
            continue
        seen.add((source_type, source_id))
        ids_by_type[source_type].append(source_id)

    ch_scope_ok = (
        project_id is not None
        and _tenant_scoped_project(
            project_id, organization=organization, workspace=workspace
        )
        is not None
    )

    resolved: dict[tuple[str, str], object] = {}
    for source_type, ids in ids_by_type.items():
        if source_type in _CH_NATIVE_SOURCE_TYPES:
            resolved.update(
                _resolve_ch_sources_bulk(
                    source_type,
                    ids,
                    project_id=project_id if ch_scope_ok else None,
                    organization=organization,
                    workspace=workspace,
                )
            )
        else:
            resolved.update(
                _resolve_pg_sources_bulk(
                    source_type,
                    ids,
                    organization=organization,
                    workspace=workspace,
                )
            )
    return resolved


def _resolve_ch_sources_bulk(source_type, ids, *, project_id, organization, workspace):
    """Resolve CH-native sources (trace / observation_span / trace_session) for the add
    path → ``{(source_type, str(id)): source_obj}``. With *project_id* (already tenant-
    gated by the caller): one scoped, LEAN batch read — the only shape that stays under
    the gateway timeout at scale. Without it: per-item fallback via
    :func:`resolve_source_object` (bounded — a point read per id, never a full scan).
    FAIL OPEN on CH errors is inherited from the batch helpers (a miss ⇒ the id is
    absent ⇒ the caller reports it as an add error)."""
    if not ids:
        return {}

    if not project_id:
        if len(ids) > _CH_BULK_FALLBACK_WARN:
            logger.warning(
                "add_items_ch_resolve_unscoped_fallback",
                source_type=source_type,
                count=len(ids),
            )
        resolved = {}
        for source_id in ids:
            obj = resolve_source_object(
                source_type,
                source_id,
                organization=organization,
                workspace=workspace,
            )
            if obj is not None:
                resolved[(source_type, str(source_id))] = obj
        return resolved

    pid = str(project_id)
    if source_type == QueueItemSourceType.TRACE.value:
        roots = _batch_ch_trace_roots(ids, project_id=pid)
        return {
            (source_type, str(trace_id)): _CHTraceSource(trace_id, root)
            for trace_id, root in roots.items()
            if root is not None
        }
    if source_type == QueueItemSourceType.OBSERVATION_SPAN.value:
        spans = _batch_ch_spans(ids, project_id=pid, include_heavy=False)
        return {(source_type, str(span_id)): span for span_id, span in spans.items()}
    if source_type == QueueItemSourceType.TRACE_SESSION.value:
        fields_by_id = _batch_ch_session_fields(ids, project_id=pid)
        resolved = {}
        for session_id, fields in fields_by_id.items():
            if not fields:
                continue
            session_pid = fields.get("project_id")
            resolved[(source_type, str(session_id))] = _CHTraceSessionSource(
                id=str(session_id),
                project_id=str(session_pid) if session_pid else None,
                name=fields.get("display_name")
                or fields.get("external_session_id")
                or "",
                first_seen=fields.get("first_seen"),
            )
        return resolved
    return {}


def _resolve_pg_sources_bulk(source_type, ids, *, organization, workspace):
    """Batch-fetch + tenant-gate PG-backed sources (dataset_row / call_execution /
    prototype_run) for the add path → ``{(source_type, str(pk)): obj}``. One ``pk__in``
    query for the fetch (vs one per item), gated per row through the SAME
    :func:`_source_passes_tenant_gate` the per-item resolver uses (fail closed). A row
    that isn't found or doesn't pass the gate is simply absent."""
    if not ids:
        return {}
    model = get_source_model(source_type)
    if model is None:
        return {}
    queryset = model.objects.filter(pk__in=list(ids))
    select_related = _PG_SOURCE_SELECT_RELATED.get(source_type)
    if select_related:
        queryset = queryset.select_related(*select_related)
    resolved = {}
    for obj in queryset:
        if _source_passes_tenant_gate(
            obj, source_type, obj.pk, organization=organization, workspace=workspace
        ):
            resolved[(source_type, str(obj.pk))] = obj
    return resolved


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
                getattr(related, "organization", None) if related is not None else None
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
            ws = getattr(related, "workspace", None) if related is not None else None
            if ws is not None:
                return ws

    # Via scenario (CallExecution)
    scenario = getattr(obj, "scenario", None)
    if scenario is not None:
        return getattr(scenario, "workspace", None)

    return None


def resolve_source_preview(item, *, ch_cache=None):
    """Return a standardized preview dict for a QueueItem's source.

    Tracer sources (trace / observation_span / trace_session) are read CH-only.
    ``ch_cache`` (opt-in :class:`CollectorSourceCache`): when supplied, they read
    from the page-batched map instead of a per-item CH point-read. Single-item
    callers pass ``None`` and keep the per-item read."""
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
            # CH-only: a trace previews from its CH root span (conversation root
            # preferred). The PG tracer tables are dropped — never read them.
            root_span = (
                ch_cache.trace_root(item.trace_id)
                if ch_cache is not None
                else _ch_root_span_for_trace(item.trace_id)
            )
            if root_span is None:
                return {"type": "trace", "deleted": True}
            return {
                "type": "trace",
                "name": root_span.name or "",
                "project_id": (
                    str(root_span.project_id) if root_span.project_id else None
                ),
                "input_preview": _truncate(str(root_span.input or ""), 200),
                "output_preview": _truncate(str(root_span.output or ""), 200),
                # CH has no response_time column; latency is the only signal.
                "latency_ms": root_span.latency_ms,
                "response_time_ms": root_span.latency_ms,
            }

        elif item.source_type == QueueItemSourceType.OBSERVATION_SPAN.value:
            # CH-only: resolve the span from CH by its soft id.
            ch_span = (
                ch_cache.span(item.observation_span_id)
                if ch_cache is not None
                else _ch_span_for_item(item.observation_span_id)
            )
            if ch_span is None:
                return {"type": "observation_span", "deleted": True}
            return {
                "type": "observation_span",
                "name": ch_span.name or "",
                "observation_type": ch_span.observation_type or "",
                "input_preview": _truncate(str(ch_span.input or ""), 200),
                "output_preview": _truncate(str(ch_span.output or ""), 200),
                # CH has no response_time column; latency is the only signal.
                "latency_ms": ch_span.latency_ms,
                "response_time_ms": ch_span.latency_ms,
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
            # CH-only: resolve the session identity fields from CH.
            fields = (
                ch_cache.session_fields(item.trace_session_id)
                if ch_cache is not None
                else _ch_session_fields_for_item(item.trace_session_id)
            )
            if fields is None:
                return {"type": "trace_session", "deleted": True}
            return {
                "type": "trace_session",
                "session_id": str(item.trace_session_id),
                "name": fields.get("display_name")
                or fields.get("external_session_id")
                or "",
                "project_id": (
                    str(fields["project_id"]) if fields.get("project_id") else None
                ),
            }

    except Exception as e:
        logger.warning("source_preview_error", item_id=str(item.id), error=str(e))

    return {"type": item.source_type, "error": "Could not resolve preview"}


def resolve_source_content(item, *, ch_cache=None):
    """Return full renderable content for a QueueItem's source (used in annotation view).

    Tracer sources (trace / observation_span / trace_session) are read CH-only.
    ``ch_cache`` (opt-in :class:`CollectorSourceCache`): when supplied, they read
    from the page-batched map instead of a per-item CH point-read. Single-item
    callers pass ``None`` and keep the per-item read."""
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
            # CH-only: render a trace from its CH root span (conversation root
            # preferred). The PG tracer tables are dropped — never read them. The
            # non-voice UI focuses the root via ``root_span_id`` (a render choice —
            # the stored item stays a trace).
            root_span = (
                ch_cache.trace_root(item.trace_id)
                if ch_cache is not None
                else _ch_root_span_for_trace(item.trace_id)
            )
            if root_span is None:
                return {"type": "trace", "deleted": True}
            from tracer.services.clickhouse.v2.span_reader import (
                chspan_to_annotation_source_dict,
            )

            # Headline content of the trace = its root span, reshaped to the trace
            # dict. Drop span_id: the FE renders a trace item via
            # InlineTraceView(trace_id) and only reads span_id for an
            # observation_span item, so it's dead + mis-attachment-prone here.
            data = chspan_to_annotation_source_dict(root_span)
            data["type"] = "trace"
            data["trace_id"] = str(item.trace_id)
            data.pop("span_id", None)
            return data

        elif item.source_type == QueueItemSourceType.OBSERVATION_SPAN.value:
            # CH-only: rebuild content from CH via the canonical mapper.
            ch_span = (
                ch_cache.span(item.observation_span_id)
                if ch_cache is not None
                else _ch_span_for_item(item.observation_span_id)
            )
            if ch_span is None:
                return {"type": "observation_span", "deleted": True}
            from tracer.services.clickhouse.v2.span_reader import (
                chspan_to_annotation_source_dict,
            )

            return chspan_to_annotation_source_dict(ch_span)

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
            call_meta = getattr(call, "call_metadata", {}) or {}
            provider = getattr(call, "provider_call_data", {}) or {}
            transcript_turns = _call_transcript_turns(call)
            raw_transcript = ""
            for sub in provider.values():
                if isinstance(sub, dict) and isinstance(sub.get("transcript"), str):
                    raw_transcript = sub["transcript"]
                    break
            call_summary = getattr(call, "call_summary", "") or ""
            scenario_name = call_meta.get("scenario_name") or ""
            row_data = call_meta.get("row_data") or {}
            if isinstance(row_data, dict) and row_data:
                scenario_input = row_data
            elif scenario_name:
                scenario_input = {"scenario_name": scenario_name}
            else:
                scenario_input = {}
            return {
                "type": "call_execution",
                "call_id": str(call.id),
                "source_id": str(call.id),
                "name": scenario_name or (call_summary[:80] if call_summary else ""),
                "status": getattr(call, "status", ""),
                "simulation_call_type": getattr(call, "simulation_call_type", ""),
                "call_type": getattr(call, "call_type", None),
                "phone_number": getattr(call, "phone_number", None),
                "service_provider_call_id": getattr(
                    call, "service_provider_call_id", None
                ),
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
                "call_summary": call_summary or None,
                "user_wpm": getattr(call, "user_wpm", None),
                "agent_wpm": getattr(call, "bot_wpm", None),
                "talk_ratio": getattr(call, "talk_ratio", None),
                "user_interruption_count": getattr(
                    call, "user_interruption_count", None
                ),
                "ai_interruption_count": getattr(call, "ai_interruption_count", None),
                "input": scenario_input,
                "output": transcript_turns or raw_transcript or call_summary or "",
                "metadata": call_meta,
                "provider_call_data": provider,
                "monitor_call_data": getattr(call, "monitor_call_data", {}) or {},
                "analysis_data": getattr(call, "analysis_data", {}) or {},
                "evaluation_data": getattr(call, "evaluation_data", {}) or {},
                "customer_latency_metrics": (
                    getattr(call, "customer_latency_metrics", {}) or {}
                ),
            }

        elif item.source_type == QueueItemSourceType.TRACE_SESSION.value:
            # CH-only: resolve the session from CH (first_seen is the
            # created_at/updated_at proxy — CH has no audit columns).
            fields = (
                ch_cache.session_fields(item.trace_session_id)
                if ch_cache is not None
                else _ch_session_fields_for_item(item.trace_session_id)
            )
            if fields is None:
                return {"type": "trace_session", "deleted": True}
            first_seen = fields.get("first_seen")
            return {
                "type": "trace_session",
                "session_id": str(item.trace_session_id),
                "source_id": str(item.trace_session_id),
                "name": fields.get("display_name")
                or fields.get("external_session_id")
                or "",
                "project_id": (
                    str(fields["project_id"]) if fields.get("project_id") else None
                ),
                "created_at": first_seen,
                "updated_at": first_seen,
            }

    except Exception as e:
        logger.warning("source_content_error", item_id=str(item.id), error=str(e))

    return {"type": item.source_type, "error": "Could not resolve content"}


def assign_items_to_all_annotators(queue, items):
    """Assign every item to every queue member with the annotator role."""
    from model_hub.models.annotation_queues import (
        QueueItemAssignment,
        annotation_queue_role_q,
    )

    item_list = list(items or [])
    if not item_list:
        return 0

    annotator_ids = list(
        queue.queue_annotators.filter(deleted=False)
        .filter(annotation_queue_role_q(AnnotatorRole.ANNOTATOR.value))
        .values_list("user_id", flat=True)
        .distinct()
    )
    if not annotator_ids:
        return 0

    assignments = [
        QueueItemAssignment(queue_item=item, user_id=user_id)
        for item in item_list
        for user_id in annotator_ids
    ]
    QueueItemAssignment.objects.bulk_create(assignments, ignore_conflicts=True)
    return len(assignments)


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
        comparable_for_kappa = info["type"] in {
            "categorical",
            "numeric",
            "star",
            "thumbs_up_down",
        }
        kappa = (
            _cohens_kappa(item_label_map, label_id)
            if comparable_for_kappa and total_count > 0
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
    for (_qi_id, _lid), entries in item_label_map.items():
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
    from itertools import combinations

    all_values = []
    pairs = []
    for (_qi_id, lid), entries in item_label_map.items():
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

    for (_qi_id, _lid), entries in item_label_map.items():
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
# Field mapping: canonical snake_case field IDs → Django ORM field names.
# This serves as an allowlist; unmapped fields are rejected.
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
    },
    QueueItemSourceType.OBSERVATION_SPAN.value: {
        # Snake_case (primary)
        "trace_id": "trace_id",
        "trace_name": "trace__name",  # trace's name via FK
        "node_type": "observation_type",
        # CH-derived-dimensions cutover (DESIGN §4.3): EndUser moved to CH, so
        # the old ``end_user__user_id`` FK join into ``tracer_enduser`` is gone.
        # ``user_id`` is now an annotation (see ``_annotate_span_for_rules``) that
        # resolves the span's OWN ``end_user_id`` soft-id through the CH dict.
        "user_id": "user_id",  # annotated from the span's own end_user_id
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
    },
    QueueItemSourceType.DATASET_ROW.value: {
        # Snake_case (primary)
        "dataset_name": "dataset__name",
        "order": "order",
        "created_at": "created_at",
        "dataset__name": "dataset__name",
    },
    QueueItemSourceType.PROTOTYPE_RUN.value: {
        "name": "name",
        "model": "model",
        "status": "status",
        "created_at": "created_at",
    },
}

# ORM field names that require queryset annotation (not stored on model).
_NEEDS_ANNOTATION = {
    QueueItemSourceType.TRACE.value: {"node_type", "status", "user_id"},
    # ``user_id`` is annotated post EndUser→CH cutover (DESIGN §4.3): it used to
    # be a direct ``end_user__user_id`` FK-join filter, now it is resolved from
    # the span's own ``end_user_id`` via the CH dict.
    QueueItemSourceType.OBSERVATION_SPAN.value: {"user_id"},
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
    if source_type == QueueItemSourceType.OBSERVATION_SPAN.value:
        return _annotate_span_for_rules(qs, to_annotate)
    if source_type == QueueItemSourceType.TRACE_SESSION.value:
        return _annotate_session_for_rules(qs, to_annotate)
    return qs


def _annotate_trace_for_rules(qs, fields):
    """Annotate Trace queryset with computed fields derived from root spans.

    ``user_id`` is the CH-derived-dimensions cutover (DESIGN §4.3). The old
    annotation traversed the PG ``end_user`` FK
    (``Subquery(root_span_qs.values("end_user__user_id"))``); EndUser now lives
    in CH, so the FK target ``tracer_enduser`` is gone. We keep the EXACT same
    correlated ``Subquery(root_span_qs...)`` shape — so the annotation stays lazy
    and the downstream ``qs.filter(user_id__…)`` / ``exclude`` rule loop, the
    ``qs[:cap+1]`` capped scan, and ``rules_applied`` accounting are untouched —
    but swap the FK column for the span's OWN ``end_user_id`` soft-id mapped
    through a CASE that carries the CH ``end_users_dict`` label (built once,
    bounded by enduser count, NOT by trace count). See ``_user_id_label_case``.
    """
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
        # Distinct end_user_ids reachable from these traces' root spans (bounded
        # by enduser count). dictGet their labels and fold into a CASE the
        # correlated subquery evaluates per root span — matching the OLD
        # ``root_span_qs.values("end_user__user_id")[:1]`` exactly: no added
        # order_by / null-exclusion, so it picks the SAME arbitrary root span and
        # yields NULL on a null/orphan end_user_id, just as the FK join did.
        end_user_ids = (
            ObservationSpan.objects.filter(
                trace_id__in=qs.values("id"), parent_span_id__isnull=True
            )
            .exclude(end_user_id__isnull=True)
            .values_list("end_user_id", flat=True)
            .distinct()
        )
        label_case = _user_id_label_case(end_user_ids)
        qs = qs.annotate(
            user_id=Subquery(
                root_span_qs.annotate(_uid_label=label_case).values("_uid_label")[:1]
            )
        )

    return qs


def _annotate_span_for_rules(qs, fields):
    """Annotate an ObservationSpan queryset with ``user_id`` (CH cutover).

    OLD: the span rule used ``user_id`` as a DIRECT FK-join filter
    (``end_user__user_id`` → ``tracer_enduser.user_id``). After EndUser→CH that
    table is gone (DESIGN §4.3). The entity here IS the span, so unlike the
    trace/session paths there is no correlation: we annotate ``user_id`` directly
    from the span's OWN ``end_user_id`` column via a CASE that carries the CH
    ``end_users_dict`` label (bounded by enduser count). The annotated
    ``user_id`` is a normal CharField, so the downstream filter loop, cap scan,
    and accounting are unchanged.

    NULL fidelity: a span with NULL ``end_user_id`` → no CASE arm → NULL (old
    inner FK join produced no/NULL match → the row was filtered/NULL the same
    way); an orphan ``end_user_id`` (missing dict key) → NULL via the CASE
    default (old FK miss → NULL too).
    """
    if "user_id" not in fields:
        return qs

    from tracer.models.observation_span import ObservationSpan  # noqa: F401

    end_user_ids = (
        qs.exclude(end_user_id__isnull=True)
        .values_list("end_user_id", flat=True)
        .distinct()
    )
    label_case = _user_id_label_case(end_user_ids)
    return qs.annotate(user_id=label_case)


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
        # CH-derived-dimensions cutover (DESIGN §4.3). OLD:
        # ``spans_qs.exclude(end_user__isnull=True).order_by("start_time")
        #   .values("end_user__user_id")[:1]`` — first non-null-enduser span by
        # start_time, then the FK label. We keep that EXACT correlated shape
        # (exclude + order_by + ``[:1]``) so the picked span is identical, and
        # the annotation stays a lazy Subquery (cap/accounting untouched); only
        # the FK-label column is swapped for the span's OWN ``end_user_id`` mapped
        # through a CASE carrying the CH ``end_users_dict`` label (bounded by
        # enduser count). ``end_user__isnull`` → ``end_user_id__isnull`` is a
        # column NULL check, NOT an FK join.
        end_user_ids = (
            ObservationSpan.objects.filter(trace__session_id__in=qs.values("id"))
            .exclude(end_user_id__isnull=True)
            .values_list("end_user_id", flat=True)
            .distinct()
        )
        label_case = _user_id_label_case(end_user_ids)
        qs = qs.annotate(
            user_id=Subquery(
                spans_qs.exclude(end_user_id__isnull=True)
                .order_by("start_time")
                .annotate(_uid_label=label_case)
                .values("_uid_label")[:1]
            )
        )

    return qs


def _user_id_label_case(end_user_ids):
    """Build a Django ``Case`` mapping a span's ``end_user_id`` → its CH
    ``user_id`` label, for the EndUser reads cutover (DESIGN §4.3).

    ``end_user_ids`` is an iterable (typically a lazy ``ValuesList`` queryset) of
    the DISTINCT non-null ``end_user_id``s reachable from the queryset's spans —
    bounded by enduser count, NOT by trace/session count, so the resulting CASE
    stays small even on a million-row span scan. The labels are fetched from CH
    ``end_users_dict`` in ONE batched ``dictGet`` and folded into
    ``When(end_user_id=eu, then=Value(label))`` arms.

    Used INSIDE a correlated ``Subquery`` over the spans (``.annotate(...)
    .values(...)[:1]``), so the host annotation stays lazy — the cap-and-scan
    machinery in ``_evaluate_rule_inner`` is preserved.

    NULL fidelity vs the old ``end_user__user_id`` FK join:
      • a span whose ``end_user_id`` is NULL → no ``When`` matches → CASE
        ``default=None`` → NULL (old FK was NULL too).
      • a span whose ``end_user_id`` is an ORPHAN (no dict row — possible under
        ``db_constraint=False``) → ``dictGetOrNull`` returned ``None`` →
        we emit NO arm for it → CASE default → NULL (old FK miss → NULL too).
    Both reproduce the old subquery's NULL exactly.
    """
    from django.db.models import Case, CharField, Value, When

    from tracer.services.clickhouse.v2.end_user_dict_reader import resolve_user_ids

    ids = list(end_user_ids)
    label_by_end_user = resolve_user_ids(ids)

    whens = []
    for end_user_id in ids:
        label = label_by_end_user.get(str(end_user_id))
        if label is None:
            # Orphan / missing dict key → leave to default=None (FK-miss → NULL).
            continue
        whens.append(When(end_user_id=end_user_id, then=Value(label)))

    if not whens:
        # No resolvable labels → a constant-NULL CharField, so the subquery still
        # yields NULL for every span (identical to an all-NULL FK join).
        return Value(None, output_field=CharField())

    return Case(*whens, default=Value(None), output_field=CharField())


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
            AnnotationQueueAnnotator.objects.filter(queue=queue, deleted=False)
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
                    {"display_name": item.get("display_name")}
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
    if op in ("between", "not_between"):
        start, end = _coerce_range_value(value)
        lookup = {f"{field_name}__range": (start, end)}
        if op == "not_between":
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
        if filter_op in ("between", "not_between"):
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
            if filter_op == "not_between":
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
        if filter_op in ("between", "not_between"):
            start_raw, end_raw = _coerce_range_value(filter_value)
            start = _parse_datetime_value(start_raw)
            end = _parse_datetime_value(end_raw)
            cells = cells.annotate(datetime_value=Cast("value", DateTimeField()))
            condition = Q()
            if start:
                condition &= Q(datetime_value__gte=start)
            if end:
                condition &= Q(datetime_value__lte=end)
            if filter_op == "not_between":
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


def _add_source_ids_to_queue(
    rule, source_ids, total_matching, dry_run=False, project_id=None
):
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
                workspace=rule.queue.workspace,
                project_id=project_id,
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


def _evaluate_filter_mode_rule(
    rule, filters, scope, dry_run=False, user=None, cap=1000
):
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
                error_type=exc.__class__.__name__,
            )
            return {
                "matched": 0,
                "added": 0,
                "duplicates": 0,
                "error": _automation_rule_filter_error_message(exc),
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
        from model_hub.services.bulk_selection import (
            resolve_filtered_call_execution_ids,
        )

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
            error_type=exc.__class__.__name__,
        )
        return {
            "matched": 0,
            "added": 0,
            "duplicates": 0,
            "error": _automation_rule_filter_error_message(exc),
        }

    return _add_source_ids_to_queue(
        rule,
        result.ids,
        result.total_matching,
        dry_run=dry_run,
        # ``project_id`` here is a real project only for trace/span/session; for
        # call_execution the same local holds an agent_definition_id (not a
        # Project), so leave those items' project NULL — they aren't read through
        # the project-scoped span path anyway.
        project_id=(
            project_id
            if source_type != QueueItemSourceType.CALL_EXECUTION.value
            else None
        ),
    )


def evaluate_rule(rule, dry_run=False, user=None, cap=1000):
    """Evaluate an automation rule and add matching items to the queue.
    Returns dict with 'matched', 'added', 'duplicates' counts.
    """
    from django.db import transaction

    from model_hub.models.annotation_queues import AutomationRule

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
                    if op in ("between", "not_between"):
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

        if op in ("between", "not_between"):
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
                if op == "not_between":
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
                    workspace=rule.queue.workspace,
                    project_id=getattr(obj, "project_id", None),
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
            triggered_by_name = user.get_full_name() or user.email or triggered_by_name
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


EvalOutputScalar = bool | float | int | str | list[str] | dict[str, Any] | None


class EvalMetricEntry(TypedDict):
    score: EvalOutputScalar
    explanation: str | None
    tags: list[str] | None
    error: bool | str | None
    error_message: str | None
    created_at: str | None


def eval_output_value(source: Any) -> EvalOutputScalar:
    """Resolve score scalar from an EvalLogger row or a call_execution eval_outputs entry."""
    if source is None:
        return None
    if isinstance(source, dict):
        if source.get("output_float") is not None:
            return source["output_float"]
        if source.get("output_bool") is not None:
            return source["output_bool"]
        output_str = source.get("output_str")
        if output_str not in (None, ""):
            return output_str
        if source.get("output_str_list"):
            return source["output_str_list"]
        output = source.get("output")
        if isinstance(output, dict):
            score = output.get("score")
            return score if score is not None else output.get("choice")
        return output
    if source.output_float is not None:
        return source.output_float
    if source.output_bool is not None:
        return source.output_bool
    if source.output_str not in (None, ""):
        return source.output_str
    return source.output_str_list


def eval_metrics_from_call_execution(
    call: Any,
) -> dict[str, list[EvalMetricEntry]]:
    if not call:
        return {}
    raw = getattr(call, "eval_outputs", {}) or {}
    metrics: dict[str, list[EvalMetricEntry]] = {}
    for eval_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        key = entry.get("name") or str(eval_id)
        error = entry.get("error")
        metric: EvalMetricEntry = {
            "score": eval_output_value(entry),
            "explanation": entry.get("reason") or entry.get("explanation"),
            "tags": entry.get("tags"),
            "error": error,
            "error_message": entry.get("error_message") if error else None,
            "created_at": entry.get("created_at"),
        }
        metrics.setdefault(key, []).append(metric)
    return metrics


def canonical_score_value(label: Any, raw: Any) -> Any:
    if raw is None or not isinstance(raw, dict):
        return raw
    label_type = getattr(label, "type", None) if label else None
    key = ANNOTATION_LABEL_VALUE_KEYS.get(label_type)
    if key and key in raw:
        return raw[key]
    logger.warning(
        "label_type_missing_in_value_map",
        label_type=label_type,
        label_id=str(getattr(label, "id", "")) or None,
    )
    return raw
