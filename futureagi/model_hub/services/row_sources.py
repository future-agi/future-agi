"""Row source provenance: the one writer and the one reader.

``record_row_sources`` runs inside the Observe import worker's transaction,
so a chunk's rows and their source records commit or roll back together.
The import's kind (trace, span or session) comes from the source context the
import request built; the ids come from the ClickHouse span rows the worker
already read. Organization and workspace come from the dataset row.

Before the worker reads anything for an import with a source context,
``provenance_span_fields`` checks that the read is filtered on the import's
project and the dataset's organization, and that the project belongs to the
dataset's organization and workspace. ClickHouse applies those filters before
it aggregates a span's versions, so a row of another project or organization
that shares a span id never counts.

``attach_row_sources`` projects ``row_meta.source`` onto rows a dataset read
returns, with one query per call. With ``live=True`` it also says whether
the source is still in Observe: a deleted or foreign project, trace, span or
session reads as ``expired``, never as another tenant's data. Each record is
checked in its own project.
"""

import uuid

import structlog
from django.db.models import Q

from model_hub.models.develop_dataset import Dataset
from model_hub.models.row_sources import RowSource, RowSourceKind
from model_hub.services.row_source_gate import row_source_enabled

logger = structlog.get_logger(__name__)

SOURCE_CONTEXT_VERSION = 1
# Span fields the worker reads only when an import carries a source context.
# The project is not among them: the export read already filters on the
# import's project, and selecting the column under its own name would collide
# with that filter. The worker hands the project it read from instead.
ROW_SOURCE_SPAN_FIELDS = frozenset({"trace_id", "trace_session_id"})
_EMPTY_IDS = (None, "", "NULL", "None", "00000000-0000-0000-0000-000000000000")
_KINDS = {kind.value for kind in RowSourceKind}


class RowSourceContextError(ValueError):
    """The source context or a span row cannot be recorded as given."""


def _clean_id(value):
    if value in _EMPTY_IDS:
        return None
    text = str(value).strip()
    return None if text in _EMPTY_IDS else text


def _context_kind_and_project(source_context):
    if not isinstance(source_context, dict):
        raise RowSourceContextError("source context must be an object")
    if source_context.get("v") != SOURCE_CONTEXT_VERSION:
        raise RowSourceContextError("unsupported source context version")
    kind = source_context.get("kind")
    if kind not in _KINDS:
        raise RowSourceContextError("unsupported source kind")
    try:
        project_id = uuid.UUID(str(source_context.get("project_id")))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RowSourceContextError("source context names no project") from exc
    return kind, project_id


def _as_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _project_in_dataset_scope(project_id, dataset):
    """Whether the project is live in the dataset's organization and workspace.

    The same rule the import request applies to the caller's workspace: a
    default workspace also sees the organization's default-workspace and
    unassigned projects; any other workspace sees only its own.
    """

    from tracer.models.project import Project

    scope = Q()
    workspace = dataset.workspace
    if workspace is not None:
        scope = Q(workspace_id=workspace.id)
        if workspace.is_default:
            scope |= Q(
                workspace__is_default=True,
                workspace__organization_id=dataset.organization_id,
            ) | Q(workspace__isnull=True)
    return Project.no_workspace_objects.filter(
        scope,
        id=project_id,
        organization_id=dataset.organization_id,
        deleted=False,
    ).exists()


def provenance_span_fields(source_context, *, dataset_id, project_id, org_id):
    """The span fields an import with a source context also reads.

    Raises before anything is read when the read would not be filtered on the
    dataset's own tenant: the task's project must be the context's project,
    the task's organization must be the dataset's, and the project must be
    live in the dataset's organization and workspace.
    """

    _, context_project = _context_kind_and_project(source_context)
    if _as_uuid(_clean_id(project_id)) != context_project:
        raise RowSourceContextError("the span read is not filtered on the project")
    dataset = (
        Dataset.no_workspace_objects.select_related("workspace")
        .only("id", "organization_id", "workspace__id", "workspace__is_default")
        .get(id=dataset_id)
    )
    if _as_uuid(_clean_id(org_id)) != dataset.organization_id:
        raise RowSourceContextError(
            "the span read is not filtered on the dataset's organization"
        )
    if not _project_in_dataset_scope(context_project, dataset):
        raise RowSourceContextError("the project is outside the dataset's workspace")
    return ROW_SOURCE_SPAN_FIELDS


def record_row_sources(*, dataset_id, rows, source_context, project_id=None):
    """Write one source record per new row; return how many were written.

    ``rows`` holds this chunk's ``(row id, span row)`` pairs and ``project_id``
    is the project the worker read those spans from. ``None`` as the context
    writes nothing (legacy and already-queued imports). A malformed context,
    or spans read from a project other than the context's, raises, so the
    caller's transaction rolls the chunk back instead of keeping unattributed
    rows.
    """

    if source_context is None:
        return 0
    kind, context_project = _context_kind_and_project(source_context)
    read_project = _clean_id(project_id)
    dataset = Dataset.no_workspace_objects.only(
        "id", "organization_id", "workspace_id"
    ).get(id=dataset_id)

    records = []
    for row_id, span in rows:
        span_id = _clean_id(span.get("id"))
        if span_id is None:
            raise RowSourceContextError("span row has no id")
        span_project = _clean_id(span.get("project_id")) or read_project
        if span_project is None or uuid.UUID(span_project) != context_project:
            raise RowSourceContextError("span row is outside the import's project")
        session_id = _clean_id(span.get("trace_session_id"))
        records.append(
            RowSource(
                row_id=row_id,
                dataset_id=dataset.id,
                organization_id=dataset.organization_id,
                workspace_id=dataset.workspace_id,
                kind=kind,
                project_id=context_project,
                trace_id=_clean_id(span.get("trace_id")) or "",
                span_id=span_id,
                session_id=session_id,
                session_missing=kind == RowSourceKind.SESSION and session_id is None,
            )
        )
    RowSource.objects.bulk_create(records)
    return len(records)


def _records_for(dataset, row_ids):
    """This dataset's source records for ``row_ids``, in one query."""

    ids = [str(row_id) for row_id in row_ids if row_id]
    if not ids:
        return {}
    records = RowSource.objects.filter(
        dataset_id=dataset.id,
        organization_id=dataset.organization_id,
        workspace_id=dataset.workspace_id,
        row_id__in=ids,
    )
    return {str(record.row_id): record for record in records}


def _short(value):
    return str(value)[:8]


def source_payload(record, *, expired=None):
    """``row_meta.source`` for one record (``expired`` only when known)."""

    payload = {"type": record.kind, "project_id": str(record.project_id)}
    if record.kind == RowSourceKind.SESSION:
        if record.session_id:
            payload["session_id"] = record.session_id
        payload["label"] = (
            f"Session {_short(record.session_id)}"
            if record.session_id
            else "Session (no session id)"
        )
    else:
        if record.trace_id:
            payload["trace_id"] = record.trace_id
        payload["span_id"] = record.span_id
        payload["label"] = (
            f"Span {_short(record.span_id)}"
            if record.kind == RowSourceKind.SPAN
            else f"Trace {_short(record.trace_id or record.span_id)}"
        )
    if expired is not None:
        payload["expired"] = expired
    return payload


def _is_uuid(value):
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _live_records(dataset, records):
    """Ids of the records whose source is still in this organization's Observe.

    Returns ``None`` when ClickHouse cannot answer, so the caller leaves
    ``expired`` unset instead of guessing either way.
    """

    from tracer.models.project import Project

    project_ids = {record.project_id for record in records}
    live_projects = {
        str(value)
        for value in Project.no_workspace_objects.filter(
            id__in=project_ids,
            organization_id=dataset.organization_id,
            deleted=False,
        ).values_list("id", flat=True)
    }
    candidates = [r for r in records if str(r.project_id) in live_projects]
    # One read per project, filtered on that project and the dataset's
    # organization, so a span id another project shares never reads as live.
    spans_by_project = {}
    for record in candidates:
        if record.kind != RowSourceKind.SESSION:
            spans_by_project.setdefault(str(record.project_id), []).append(
                record.span_id
            )
    sessions = [
        r
        for r in candidates
        if r.kind == RowSourceKind.SESSION and _is_uuid(r.session_id)
    ]
    try:
        live_spans = set()
        if spans_by_project:
            from tracer.services.clickhouse.v2 import get_reader

            with get_reader() as reader:
                for project, span_ids in spans_by_project.items():
                    found = reader.export_fields_by_ids(
                        span_ids,
                        {"id"},
                        project_id=project,
                        org_id=str(dataset.organization_id),
                    )
                    live_spans.update((project, str(span_id)) for span_id in found)
        live_sessions = set()
        if sessions:
            from tracer.services.clickhouse.v2.trace_session_dict_reader import (
                resolve_session_fields,
            )

            fields = resolve_session_fields(
                [r.session_id for r in sessions],
                project_ids=sorted({str(r.project_id) for r in sessions}),
            )
            live_sessions = {
                (str(values.get("project_id")), session_id)
                for session_id, values in fields.items()
            }
    except Exception:
        logger.warning("row_source_liveness_unavailable", dataset_id=str(dataset.id))
        return None

    def is_live(record):
        if record.kind == RowSourceKind.SESSION:
            return (str(record.project_id), record.session_id) in live_sessions
        return (str(record.project_id), record.span_id) in live_spans

    return {record.id for record in candidates if is_live(record)}


def attach_row_sources(rows, *, dataset, live=False):
    """Set ``row_meta.source`` on each row dict that has a source record.

    Rows without a record are left exactly as they were. ``live`` adds
    ``expired`` from Observe: one Postgres query for the projects, then one
    ClickHouse read per project of the page and one for its sessions. While
    the server switch is OFF the rows are returned untouched before any query,
    so get-row-data and get-dataset-table answer as they did before.
    """

    if not row_source_enabled():
        return rows
    by_row = _records_for(dataset, [row.get("row_id") for row in rows])
    if not by_row:
        return rows
    live_ids = _live_records(dataset, list(by_row.values())) if live else None
    for row in rows:
        record = by_row.get(str(row.get("row_id")))
        if record is None:
            continue
        expired = None if live_ids is None else record.id not in live_ids
        meta = row.get("row_meta")
        if not isinstance(meta, dict):
            meta = {}
            row["row_meta"] = meta
        meta["source"] = source_payload(record, expired=expired)
    return rows
