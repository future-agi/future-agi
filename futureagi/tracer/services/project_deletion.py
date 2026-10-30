"""Project soft-delete + collector cache invalidation.

Single owner of the project cascade-delete so every surface (bulk `delete()`,
single `destroy()`, and the `delete_project` AI tool) publishes the same
fi-collector invalidation. See `fi-collector/pkg/auth/auth.go`
(`projectInvalidateChannel`) for the consuming side of the wire contract.
"""

from __future__ import annotations

import contextlib

import redis
import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.insights import InsightConfig, InsightRun, InsightUnit
from tracer.models.instant_eval import InstantEvalAnswer, InstantEvalRow, InstantEvalRun
from tracer.models.monitor import UserAlertMonitor
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession

logger = structlog.get_logger(__name__)

# Cross-language wire contract. Must match `projectInvalidateChannel` in
# fi-collector/pkg/auth/auth.go.
FI_PROJECT_INVALIDATE_CHANNEL = "fi:project:invalidate"


def publish_project_invalidation(project_ids: list[str]) -> None:
    """Publish deleted project ids so fi-collector drops them from its auth
    cache. Best-effort with short timeouts; never blocks or fails the delete
    (the on_commit callback runs inline in autocommit mode)."""
    if not project_ids:
        return
    r = None
    try:
        r = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        # One pipeline so a mid-loop failure doesn't silently drop the tail.
        pipe = r.pipeline()
        for pid in project_ids:
            pipe.publish(FI_PROJECT_INVALIDATE_CHANNEL, pid)
        pipe.execute()
    except Exception:
        logger.warning("fi_project_invalidate_publish_failed", exc_info=True)
    finally:
        if r is not None:
            with contextlib.suppress(Exception):
                r.close()


def soft_delete_projects(projects: QuerySet[Project], project_type: str) -> list[str]:
    """Cascade soft-delete the given projects and their related rows, then
    publish a collector cache-invalidation for each deleted id on commit.

    Returns the deleted project ids (stringified)."""
    project_ids = [str(pid) for pid in projects.values_list("id", flat=True)]
    if not project_ids:
        return project_ids

    with transaction.atomic():
        now = timezone.now()
        if project_type == "experiment":
            ProjectVersion.objects.filter(project__in=projects).update(
                deleted=True, deleted_at=now
            )
        else:
            TraceSession.objects.filter(project__in=projects).update(
                deleted=True, deleted_at=now
            )
        Trace.objects.filter(project__in=projects).update(deleted=True, deleted_at=now)
        ObservationSpan.objects.filter(project__in=projects).update(
            deleted=True, deleted_at=now
        )
        UserAlertMonitor.objects.filter(project__in=projects).update(
            deleted=True, deleted_at=now
        )
        pause_task_ids = _retire_instant_eval_and_insights(projects, now)
        EvalTask.objects.filter(project__in=projects).update(
            deleted=True, deleted_at=now
        )
        eval_configs = CustomEvalConfig.objects.filter(project__in=projects)
        EvalLogger.objects.filter(custom_eval_config__in=eval_configs).update(
            deleted=True, deleted_at=now
        )
        eval_configs.update(deleted=True, deleted_at=now)
        projects.update(deleted=True, deleted_at=now)

    transaction.on_commit(lambda: publish_project_invalidation(project_ids))
    if pause_task_ids:
        transaction.on_commit(lambda: _pause_eval_task_workflows(pause_task_ids))
    return project_ids


def _retire_instant_eval_and_insights(projects: QuerySet[Project], now) -> list[str]:
    """Stop the projects' Instant eval and Insights work and drop the text it
    copied out of their traces.

    Instant eval rows keep an excerpt of each row they read, and Insights
    units keep the excerpt, content and reading of each unit; neither table
    has a soft-delete flag, and the traces they copied are going, so the
    copies are deleted outright. A report's result quotes those units, so it
    is cleared; the report and its config are soft-deleted like the traces.
    A run still going is cancelled (Instant eval) or failed (Insights) first,
    so no stage reads or bills for a deleted project.

    Returns the ids of eval tasks whose workflows should be paused."""
    instant = InstantEvalRun.objects.filter(project__in=projects)
    active = list(
        instant.filter(
            cancelled_at__isnull=True,
            eval_task__status__in=(EvalTaskStatus.PENDING, EvalTaskStatus.RUNNING),
        ).values_list("eval_task_id", flat=True)
    )
    instant.filter(cancelled_at__isnull=True).update(cancelled_at=now)
    task_ids = list(instant.values_list("eval_task_id", flat=True))
    if task_ids:
        InstantEvalAnswer.objects.filter(task_id__in=task_ids).delete()
        InstantEvalRow.objects.filter(task_id__in=task_ids).delete()

    # The base manager has no workspace or soft-delete filter: a report
    # deleted earlier still holds copied text.
    runs = InsightRun._base_manager.filter(project__in=projects)
    runs.filter(status__in=("pending", "running")).update(
        status="failed",
        error="The project was deleted.",
        finished_at=now,
        updated_at=now,
    )
    InsightUnit.objects.filter(run__in=runs).delete()
    runs.update(result={}, deleted=True, deleted_at=now, updated_at=now)
    InsightConfig._base_manager.filter(project__in=projects).update(
        deleted=True, deleted_at=now, updated_at=now
    )
    return [str(task_id) for task_id in active]


def _pause_eval_task_workflows(task_ids: list[str]) -> None:
    """Best effort: a workflow that misses the signal finds its task deleted
    and its run cancelled at its next claim, and reads nothing more."""
    try:
        from tfc.temporal.eval_tasks.client import signal_pause_eval_task_workflow
    except Exception:
        logger.warning("project_delete_pause_import_failed", exc_info=True)
        return
    for task_id in task_ids:
        try:
            signal_pause_eval_task_workflow(task_id)
        except Exception:
            logger.warning("project_delete_pause_failed", task_id=task_id)
