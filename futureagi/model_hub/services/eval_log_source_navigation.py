"""Resolve an eval log's exact, currently authorized Observe source."""

from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

import structlog

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailNotFound,
    TraceDetailReadUnavailable,
    read_trace_detail,
)

logger = structlog.get_logger(__name__)

Status = Literal[
    "ready",
    "no_reference",
    "unsupported_source",
    "unsupported_target",
    "incomplete_reference",
    "invalid_reference",
    "ambiguous_reference",
    "unavailable",
    "temporarily_unavailable",
]
Kind = Literal["trace", "voice_call"]


@dataclass(frozen=True)
class SourceNavigation:
    status: Status
    kind: Kind | None = None
    project_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None

    @property
    def retryable(self) -> bool:
        return self.status == "temporarily_unavailable"

    def as_dict(self) -> dict:
        ready = self.status == "ready"
        return {
            "status": self.status,
            "kind": self.kind if ready else None,
            "project_id": self.project_id if ready else None,
            "trace_id": self.trace_id if ready else None,
            "span_id": self.span_id if ready else None,
            "retryable": self.retryable,
        }


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
    if not (_is_uuid(trace_id) and _is_uuid(span_id)):
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
        span_id.lower(),
        config_id.lower(),
        project_id.lower() if project_id else None,
    )


def _resolve_project(
    request, log_row, candidate
) -> tuple[str | None, SourceNavigation | None]:
    # Import lazily: the shared scope helper belongs to the tracer view module.
    from tracer.views.trace import _project_queryset_for_request

    project_id = candidate.project_id
    if project_id is None:
        config_row = (
            CustomEvalConfig.all_objects.filter(id=candidate.config_id)
            .select_related("project")
            .first()
        )
        if config_row is None:
            return None, SourceNavigation("unavailable")
        project_id = str(config_row.project_id)
        if log_row.source_id and str(config_row.eval_template_id) != str(
            log_row.source_id
        ):
            return None, SourceNavigation("invalid_reference")

    project = _project_queryset_for_request(request).filter(id=project_id).first()
    if project is None:
        return None, SourceNavigation("unavailable")
    if (
        log_row.workspace_id
        and project.workspace_id
        and str(log_row.workspace_id) != str(project.workspace_id)
    ):
        return None, SourceNavigation("invalid_reference")
    return str(project.id), None


def _read_and_classify(detail, candidate) -> SourceNavigation:
    project_id, trace_id, span_id = (
        candidate.project_id,
        candidate.trace_id,
        candidate.span_id,
    )
    if str(detail.project_id) != project_id:
        return SourceNavigation("invalid_reference")
    spans = list(detail.spans)
    by_id = {}
    for row in spans:
        if (
            str(row.get("trace_id") or "") != trace_id
            or str(row.get("project_id") or "") != project_id
        ):
            return SourceNavigation("invalid_reference")
        by_id.setdefault(str(row.get("id") or ""), []).append(row)
    matches = by_id.get(span_id, [])
    if not matches:
        return SourceNavigation("unavailable")
    if len(matches) > 1:
        return SourceNavigation("ambiguous_reference")
    roots = [
        row
        for row in spans
        if row.get("parent_span_id") in (None, "")
        and str(row.get("observation_type") or "").lower() == "conversation"
    ]
    if not roots:
        return SourceNavigation("ready", "trace", project_id, trace_id, span_id)
    if len(roots) > 1:
        return SourceNavigation("ambiguous_reference")
    root_id = str(roots[0].get("id"))
    seen, current = set(), span_id
    while True:
        if current == root_id:
            return SourceNavigation(
                "ready", "voice_call", project_id, trace_id, span_id
            )
        if current in seen:
            return SourceNavigation("ambiguous_reference")
        seen.add(current)
        rows = by_id.get(current, [])
        if len(rows) != 1:
            return SourceNavigation("ambiguous_reference")
        parent = rows[0].get("parent_span_id")
        if parent in (None, ""):
            return SourceNavigation("ambiguous_reference")
        current = str(parent)


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
        else:
            project_id, result = _resolve_project(request, log_row, candidate)
            if result is None:
                candidate = replace(candidate, project_id=project_id)
                detail = (read_detail or read_trace_detail)(
                    analytics=analytics or V2AnalyticsQueryService(),
                    project_ids=[project_id],
                    trace_id=candidate.trace_id,
                    eval_config_ids_resolver=None,
                    include_annotations=False,
                    deadline_ms=deadline_ms,
                )
                result = _read_and_classify(detail, candidate)
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
