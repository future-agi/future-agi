from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from tracer.models.project import Project
from tracer.models.trace_investigation import (
    TraceInvestigationJob,
    TraceInvestigationReconciliationCursor,
)
from tracer.models.trace_scan import TraceScanConfig, TraceScanEngine, TraceScanResult
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.v2.query_settings import ch_query_settings

_MAX_PAGE_SIZE = 1000
_MAX_PROJECTS = 1000
_MAX_PAGES_PER_PROJECT = 100
_RECONCILIATION_CH_GUARDRAILS = {
    "max_memory_usage": 4 * 1024 * 1024 * 1024,
    "max_execution_time": 30,
    "max_bytes_before_external_sort": 512 * 1024 * 1024,
}


@dataclass(frozen=True)
class _Window:
    cursor_id: uuid.UUID
    lower: datetime
    upper: datetime
    after_created_at: datetime | None
    after_trace_id: str | None


@dataclass
class ReconciliationSummary:
    projects_considered: int = 0
    projects_completed: int = 0
    pages_read: int = 0
    candidates_seen: int = 0
    jobs_created: int = 0
    existing_jobs: int = 0
    completed_traces: int = 0
    invalid_traces: int = 0
    contended_pages: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _bounded(name: str, value: int, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
        raise ValueError(f"{name} must be between 1 and {upper}")
    return value


def _eligible_configs(project_limit: int) -> list[TraceScanConfig]:
    return list(
        TraceScanConfig.no_workspace_objects.filter(
            enabled=True,
            engine=TraceScanEngine.OMEGA,
            project__trace_type="observe",
        )
        .select_related("project")
        .order_by(
            F("project__omega_reconciliation_cursor__completed_through").asc(
                nulls_first=True
            ),
            "project_id",
        )[:project_limit]
    )


def _prepare_window(
    *,
    config_id: uuid.UUID,
    upper: datetime,
    lookback: timedelta,
) -> _Window | None:
    with transaction.atomic():
        try:
            config = (
                TraceScanConfig.no_workspace_objects.select_for_update()
                .select_related("project")
                .get(
                    id=config_id,
                    enabled=True,
                    engine=TraceScanEngine.OMEGA,
                    project__trace_type="observe",
                )
            )
        except TraceScanConfig.DoesNotExist:
            return None

        cursor, _ = (
            TraceInvestigationReconciliationCursor.no_workspace_objects.select_for_update().get_or_create(
                project_id=config.project_id,
                defaults={
                    "organization_id": config.project.organization_id,
                    "workspace_id": config.project.workspace_id,
                },
            )
        )
        if (
            cursor.organization_id != config.project.organization_id
            or cursor.workspace_id != config.project.workspace_id
        ):
            raise RuntimeError("Omega reconciliation cursor tenant scope changed")

        if cursor.window_upper is None:
            lower = cursor.completed_through or upper - lookback
            if lower > upper:
                lower = upper
            cursor.window_lower = lower
            cursor.window_upper = upper
            cursor.after_created_at = None
            cursor.after_trace_id = None
            cursor.last_started_at = timezone.now()
            cursor.save(
                update_fields=[
                    "window_lower",
                    "window_upper",
                    "after_created_at",
                    "after_trace_id",
                    "last_started_at",
                    "updated_at",
                ]
            )

        return _Window(
            cursor_id=cursor.id,
            lower=cursor.window_lower,
            upper=cursor.window_upper,
            after_created_at=cursor.after_created_at,
            after_trace_id=cursor.after_trace_id,
        )


def _valid_trace_ids(candidates: list[tuple[str, datetime]]) -> tuple[list[str], int]:
    valid = []
    invalid = 0
    for trace_id, _ in candidates:
        try:
            valid.append(str(uuid.UUID(str(trace_id))))
        except (TypeError, ValueError, AttributeError):
            invalid += 1
    return valid, invalid


def _apply_page(
    *,
    config: TraceScanConfig,
    window: _Window,
    candidates: list[tuple[str, datetime]],
    roots: dict[str, dict],
    page_size: int,
) -> tuple[bool, dict[str, int]]:
    valid_ids, invalid_count = _valid_trace_ids(candidates)
    now = timezone.now()
    with transaction.atomic():
        project = Project.no_workspace_objects.select_for_update().get(
            id=config.project_id,
            organization_id=config.project.organization_id,
            workspace_id=config.project.workspace_id,
            trace_type="observe",
        )
        current_config = TraceScanConfig.no_workspace_objects.select_for_update().get(
            id=config.id,
            project=project,
            enabled=True,
            engine=TraceScanEngine.OMEGA,
        )
        cursor = TraceInvestigationReconciliationCursor.no_workspace_objects.select_for_update().get(
            id=window.cursor_id,
            project=project,
            organization_id=project.organization_id,
            workspace_id=project.workspace_id,
        )
        if (
            cursor.window_lower != window.lower
            or cursor.window_upper != window.upper
            or cursor.after_created_at != window.after_created_at
            or cursor.after_trace_id != window.after_trace_id
        ):
            return False, {}

        existing_job_ids = {
            str(trace_id)
            for trace_id in TraceInvestigationJob.no_workspace_objects.filter(
                project_id=current_config.project_id,
                trace_id__in=valid_ids,
            ).values_list("trace_id", flat=True)
        }
        completed_ids = {
            str(trace_id)
            for trace_id in TraceScanResult.no_workspace_objects.filter(
                project_id=current_config.project_id,
                trace_id__in=valid_ids,
            ).values_list("trace_id", flat=True)
        }
        new_jobs = []
        missing_roots = 0
        for trace_id in valid_ids:
            if trace_id in existing_job_ids or trace_id in completed_ids:
                continue
            root = roots.get(trace_id)
            if root is None or root.get("end_time") is None:
                missing_roots += 1
                continue
            new_jobs.append(
                TraceInvestigationJob(
                    organization_id=project.organization_id,
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    trace_id=trace_id,
                    root_span_id=str(root["id"]),
                    root_end_time=root["end_time"],
                    not_before=now,
                )
            )
        TraceInvestigationJob.no_workspace_objects.bulk_create(
            new_jobs, ignore_conflicts=True
        )

        complete = len(candidates) < page_size
        if complete:
            cursor.completed_through = window.upper
            cursor.window_lower = None
            cursor.window_upper = None
            cursor.after_created_at = None
            cursor.after_trace_id = None
            cursor.last_completed_at = now
            update_fields = [
                "completed_through",
                "window_lower",
                "window_upper",
                "after_created_at",
                "after_trace_id",
                "last_completed_at",
                "updated_at",
            ]
        else:
            cursor.after_trace_id, cursor.after_created_at = candidates[-1]
            update_fields = ["after_created_at", "after_trace_id", "updated_at"]
        cursor.save(update_fields=update_fields)

    return True, {
        "created": len(new_jobs),
        "existing": len(existing_job_ids),
        "completed": len(completed_ids),
        "invalid": invalid_count + missing_roots,
        "complete": int(complete),
    }


def reconcile_omega_investigations(
    *,
    project_limit: int | None = None,
    page_size: int | None = None,
    max_pages_per_project: int | None = None,
    lookback_seconds: int | None = None,
    grace_seconds: int | None = None,
    reader=None,
) -> dict[str, int]:
    """Recover CH roots missed by Kafka without advancing past uncommitted jobs."""
    project_limit = _bounded(
        "project_limit",
        project_limit
        if project_limit is not None
        else settings.ERROR_FEED_OMEGA_RECONCILE_PROJECT_LIMIT,
        _MAX_PROJECTS,
    )
    page_size = _bounded(
        "page_size",
        page_size
        if page_size is not None
        else settings.ERROR_FEED_OMEGA_RECONCILE_PAGE_SIZE,
        _MAX_PAGE_SIZE,
    )
    max_pages_per_project = _bounded(
        "max_pages_per_project",
        max_pages_per_project
        if max_pages_per_project is not None
        else settings.ERROR_FEED_OMEGA_RECONCILE_MAX_PAGES_PER_PROJECT,
        _MAX_PAGES_PER_PROJECT,
    )
    lookback_seconds = _bounded(
        "lookback_seconds",
        lookback_seconds
        if lookback_seconds is not None
        else settings.ERROR_FEED_OMEGA_RECONCILE_LOOKBACK_SECONDS,
        31 * 24 * 60 * 60,
    )
    grace_seconds = _bounded(
        "grace_seconds",
        grace_seconds
        if grace_seconds is not None
        else settings.ERROR_FEED_OMEGA_RECONCILE_GRACE_SECONDS,
        24 * 60 * 60,
    )

    summary = ReconciliationSummary()
    configs = _eligible_configs(project_limit)
    reader_context = nullcontext(reader) if reader is not None else get_reader()
    with ch_query_settings(**_RECONCILIATION_CH_GUARDRAILS), reader_context as ch:
        upper = ch.ch_now() - timedelta(seconds=grace_seconds)
        for config in configs:
            summary.projects_considered += 1
            for _ in range(max_pages_per_project):
                window = _prepare_window(
                    config_id=config.id,
                    upper=upper,
                    lookback=timedelta(seconds=lookback_seconds),
                )
                if window is None:
                    break
                after = (
                    (window.after_created_at, window.after_trace_id)
                    if window.after_created_at is not None
                    and window.after_trace_id is not None
                    else None
                )
                candidates = ch.root_trace_candidates_page(
                    str(config.project_id),
                    window.lower,
                    window.upper,
                    after=after,
                    limit=page_size,
                )
                summary.pages_read += 1
                summary.candidates_seen += len(candidates)
                valid_ids, _ = _valid_trace_ids(candidates)
                roots = (
                    ch.list_root_spans_by_trace_ids(
                        valid_ids,
                        project_id=str(config.project_id),
                        columns=["trace_id", "id", "end_time"],
                    )
                    if valid_ids
                    else {}
                )
                applied, counts = _apply_page(
                    config=config,
                    window=window,
                    candidates=candidates,
                    roots=roots,
                    page_size=page_size,
                )
                if not applied:
                    summary.contended_pages += 1
                    break
                summary.jobs_created += counts["created"]
                summary.existing_jobs += counts["existing"]
                summary.completed_traces += counts["completed"]
                summary.invalid_traces += counts["invalid"]
                if counts["complete"]:
                    summary.projects_completed += 1
                    break
    return summary.as_dict()
