"""Resolve an eval log's exact, currently authorized Observe source."""

from dataclasses import dataclass
from typing import TypeGuard
from uuid import UUID

import structlog
from django.db.models import Q

from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailNotFound,
    TraceDetailReadUnavailable,
    read_trace_detail,
)
from tracer.services.trace_source_navigation import (
    SourceNavigation,
    classify_trace_source,
    is_span_id,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _Candidate:
    trace_id: str
    span_id: str
    config_id: str
    project_id: str | None


def _is_uuid(value: object) -> TypeGuard[str]:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


def _classify_reference(log_row, config) -> SourceNavigation | _Candidate:
    if not isinstance(config, dict) or (
        config == {} and log_row.config not in ({}, None, "{}")
    ):
        return SourceNavigation("invalid_reference")
    config_source = config.get("source")
    row_source = log_row.source
    if config_source is not None and not isinstance(config_source, str):
        return SourceNavigation("invalid_reference")
    effective = (config_source or row_source or "").strip().lower()
    if (
        config_source
        and config_source.lower() == "tracer"
        and row_source
        and row_source.lower() not in ("tracer", "feedback")
    ):
        return SourceNavigation("invalid_reference")
    if effective != "tracer":
        return SourceNavigation("unsupported_source")

    target_type = config.get("target_type")
    has_session = config.get("session_id") not in (None, "")
    has_trace_or_span = any(
        config.get(key) not in (None, "") for key in ("trace_id", "span_id")
    )
    if target_type == "session":
        return SourceNavigation("unsupported_target")
    if target_type not in (None, "span", "trace"):
        return SourceNavigation("invalid_reference")
    if has_session and has_trace_or_span:
        return SourceNavigation("invalid_reference")
    if has_session:
        return SourceNavigation("unsupported_target")

    trace_id, span_id = config.get("trace_id"), config.get("span_id")
    trace_absent, span_absent = trace_id in (None, ""), span_id in (None, "")
    if trace_absent and span_absent:
        return SourceNavigation("no_reference")
    if trace_absent != span_absent:
        return SourceNavigation("incomplete_reference")
    # is_span_id already requires a str; the isinstance only narrows the type.
    if not (_is_uuid(trace_id) and isinstance(span_id, str) and is_span_id(span_id)):
        return SourceNavigation("invalid_reference")
    config_id = config.get("custom_eval_config_id")
    if config_id in (None, ""):
        return SourceNavigation("incomplete_reference")
    if not _is_uuid(config_id):
        return SourceNavigation("invalid_reference")
    project_id = config.get("project_id")
    if project_id is not None and not _is_uuid(project_id):
        return SourceNavigation("invalid_reference")
    return _Candidate(
        trace_id.lower(),
        span_id,
        config_id.lower(),
        project_id.lower() if project_id else None,
    )


# A Usage read binds at most this many projects in one ClickHouse statement.
MAX_SOURCE_PROJECTS = 4096


def readable_source_projects(request, workspace_id=None):
    """Projects the caller may read in a workspace.

    Usage lists a log that recorded its project only while that project is
    one of these; the eval config's current project never decides it.
    """
    # Import lazily: the shared scope helper belongs to the tracer view module.
    from tracer.views.trace import _project_queryset_for_request

    scope = _project_queryset_for_request(request)
    if workspace_id:
        scope = scope.filter(
            Q(workspace_id=workspace_id) | Q(workspace_id__isnull=True)
        )
    return scope


def readable_source_project_ids(request, workspace_id=None) -> list[str] | None:
    """The ids of :func:`readable_source_projects`, or None past the bound."""
    project_ids = [
        str(project_id)
        for project_id in readable_source_projects(request, workspace_id)
        .order_by("id")
        .values_list("id", flat=True)[: MAX_SOURCE_PROJECTS + 1]
    ]
    return None if len(project_ids) > MAX_SOURCE_PROJECTS else project_ids


def _recorded_project_scope(request, log_row, candidate) -> SourceNavigation | str:
    """Return the recorded project's id when the caller may read it, else a state."""
    # Import lazily: the shared scope helper belongs to the tracer view module.
    from tracer.views.trace import _project_queryset_for_request

    project = (
        _project_queryset_for_request(request).filter(id=candidate.project_id).first()
    )
    if project is None:
        return SourceNavigation("unavailable")
    if (
        log_row.workspace_id
        and project.workspace_id
        and str(log_row.workspace_id) != str(project.workspace_id)
    ):
        return SourceNavigation("invalid_reference")
    return str(project.id)


def _map_read_error(exc) -> SourceNavigation:
    if isinstance(exc, TraceDetailNotFound):
        return SourceNavigation("unavailable")
    if (
        isinstance(exc, TraceDetailReadUnavailable)
        and exc.code == "ambiguous_span_identity"
    ):
        return SourceNavigation("ambiguous_reference")
    return SourceNavigation("temporarily_unavailable")


def resolve_eval_log_source_navigation(
    *,
    request,
    log_row,
    config: dict,
    analytics=None,
    read_detail=None,
    deadline_ms: int = 6000,
) -> SourceNavigation:
    """Never raise; unexpected failures leave the evaluation readable and retryable."""
    error_code = None
    try:
        candidate = _classify_reference(log_row, config)
        if isinstance(candidate, SourceNavigation):
            result = candidate
        elif candidate.project_id is None:
            # An older log never recorded the project it ran on. Its eval
            # config is writable, and a trace id is not unique across projects
            # (a replay or re-import writes the same ids elsewhere), so no
            # lookup can name its source: it gets no navigation, and no trace
            # is read for it.
            result = SourceNavigation("source_not_recorded")
        else:
            scope = _recorded_project_scope(request, log_row, candidate)
            if isinstance(scope, SourceNavigation):
                result = scope
            else:
                detail = (read_detail or read_trace_detail)(
                    analytics=analytics or V2AnalyticsQueryService(),
                    project_ids=[scope],
                    trace_id=candidate.trace_id,
                    eval_config_ids_resolver=None,
                    include_annotations=False,
                    deadline_ms=deadline_ms,
                )
                result = classify_trace_source(
                    detail,
                    project_id=scope,
                    trace_id=candidate.trace_id,
                    span_id=candidate.span_id,
                )
    except (TraceDetailNotFound, TraceDetailReadUnavailable) as exc:
        error_code = getattr(exc, "code", None)
        result = _map_read_error(exc)
    except Exception:
        logger.exception(
            "eval_log_source_navigation.unexpected", log_id=str(log_row.log_id)
        )
        result = SourceNavigation("temporarily_unavailable")
    if result.status != "ready":
        logger.info(
            "eval_log_source_navigation.resolved",
            log_id=str(log_row.log_id),
            status=result.status,
            error_code=error_code,
        )
    return result
