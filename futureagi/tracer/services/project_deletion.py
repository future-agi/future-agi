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
from tracer.models.eval_task import EvalTask
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
    return project_ids
