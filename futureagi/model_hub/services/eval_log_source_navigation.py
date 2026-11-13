"""Resolve an eval log's exact, currently authorized Observe source."""

from dataclasses import dataclass
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


def _is_uuid(value) -> bool:
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
    if not (_is_uuid(trace_id) and is_span_id(span_id)):
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


def _resolve_project_scope(
    request, log_row, candidate
) -> tuple[list[str], SourceNavigation | None]:
    """Return the project ids the trace read may search, or a terminal state."""
    # Import lazily: the shared scope helper belongs to the tracer view module.
    from tracer.views.trace import _project_queryset_for_request

    scope = _project_queryset_for_request(request)
    if candidate.project_id is not None:
        project = scope.filter(id=candidate.project_id).first()
        if project is None:
            return [], SourceNavigation("unavailable")
        if (
            log_row.workspace_id
            and project.workspace_id
            and str(log_row.workspace_id) != str(project.workspace_id)
        ):
            return [], SourceNavigation("invalid_reference")
        return [str(project.id)], None

    # Older logs did not record their project. The eval config is writable (its
    # project and template can change after the eval ran), so it says nothing
    # about where this log's trace lives and is never read here: search the
    # projects this user may read in the log's workspace for the trace itself,
    # and let the read refuse a trace found in more than one of them.
    if log_row.workspace_id:
        scope = scope.filter(
            Q(workspace_id=log_row.workspace_id) | Q(workspace_id__isnull=True)
        )
    # One past the reader's project bound, so an oversized scope fails closed.
    project_ids = [
        str(project_id)
        for project_id in scope.order_by("id").values_list("id", flat=True)[:4097]
    ]
    if not project_ids:
        return [], SourceNavigation("unavailable")
    return project_ids, None


def _map_read_error(exc) -> SourceNavigation:
    if isinstance(exc, TraceDetailNotFound):
        return SourceNavigation("unavailable")
    # More readable projects than one read may bind: retrying cannot help.
    if (
        isinstance(exc, TraceDetailReadUnavailable)
        and exc.code == "project_scope_too_large"
    ):
        return SourceNavigation("unavailable")
    if isinstance(exc, TraceDetailReadUnavailable) and exc.code in (
        "ambiguous_span_identity",
        "ambiguous_project",
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
        else:
            project_ids, result = _resolve_project_scope(request, log_row, candidate)
            if result is None:
                detail = (read_detail or read_trace_detail)(
                    analytics=analytics or V2AnalyticsQueryService(),
                    project_ids=project_ids,
                    trace_id=candidate.trace_id,
                    eval_config_ids_resolver=None,
                    include_annotations=False,
                    deadline_ms=deadline_ms,
                    unique_project=True,
                )
                # A recorded project is the only one read; otherwise the read
                # found the trace in exactly one of the scoped projects.
                project_id = (
                    project_ids[0] if candidate.project_id else str(detail.project_id)
                )
                result = (
                    classify_trace_source(
                        detail,
                        project_id=project_id,
                        trace_id=candidate.trace_id,
                        span_id=candidate.span_id,
                    )
                    if project_id in project_ids
                    else SourceNavigation("invalid_reference")
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
