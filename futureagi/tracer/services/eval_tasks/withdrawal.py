"""Withdraw eval tasks that must not exist: the create that made them failed.

Withdrawn as list delete withdraws: the task is ``deleted`` with
``status=deleted``, and its task loggers and eval results are soft-deleted, so
neither the Tasks list nor the stranded sweep sees it again. A workflow that
still starts for it fails at its first task read, which never returns a
deleted row.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime

import structlog
from django.db import transaction
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus
from tracer.models.observation_span import EvalLogger
from tracer.selectors.eval_tasks.failed_creates import failed_create_candidates
from tracer.services.eval_tasks.entries import soft_delete_eval_results

logger = structlog.get_logger(__name__)

# Per-organization tallies, in the order the command prints them.
TALLIES = (
    "matched",
    "would_withdraw",
    "withdrawn",
    "workflow_on_record",
    "temporal_unreachable",
    "changed_since_read",
)


def withdraw_tasks(task_ids: Iterable) -> int:
    """Soft-delete the tasks and everything they wrote. Returns tasks changed."""
    ids = [str(task_id) for task_id in task_ids]
    if not ids:
        return 0
    now = timezone.now()
    with transaction.atomic():
        withdrawn = EvalTask.no_workspace_objects.filter(id__in=ids).update(
            deleted=True, deleted_at=now, status=EvalTaskStatus.DELETED
        )
        EvalTaskLogger.no_workspace_objects.filter(eval_task_id__in=ids).update(
            deleted=True, deleted_at=now
        )
        soft_delete_eval_results(
            EvalLogger.no_workspace_objects.filter(eval_task_id__in=ids)
        )
    return withdrawn


def withdraw_failed_creates(
    *, created_before: datetime, apply: bool, batch_size: int = 500
) -> dict[str, Counter]:
    """Find, and with ``apply`` withdraw, rows failed creates left behind.

    Returns tallies keyed by organization id. A candidate (see
    ``failed_create_candidates``) is withdrawn only when Temporal says no
    workflow was ever recorded for it. A recorded one, closed or running, is a
    start that Temporal accepted, so it is skipped and counted, never guessed
    at. A describe that cannot answer counts as unreachable and withdraws
    nothing. The dry run describes too, so its ``would_withdraw`` is what
    ``apply`` would do.

    Each withdrawal re-reads its row under the row lock with the same
    predicate: a sweep or a user edit that started the task after the describe
    has stamped it, and the row is skipped as ``changed_since_read``. A sweep
    that reaches the row after the withdrawal finds it deleted and skips it.
    Running it again matches nothing it already withdrew.
    """
    from tfc.temporal.eval_tasks.client import (
        WF_ABSENT,
        describe_eval_task_workflow_sync,
    )

    tallies: dict[str, Counter] = defaultdict(Counter)
    candidates = failed_create_candidates(created_before=created_before).order_by("id")
    last_id = None
    while True:
        page = candidates.filter(id__gt=last_id) if last_id else candidates
        rows = list(page.values_list("id", "project__organization_id")[:batch_size])
        if not rows:
            break
        last_id = rows[-1][0]
        for task_id, organization_id in rows:
            tally = tallies[str(organization_id)]
            tally["matched"] += 1
            try:
                verdict = describe_eval_task_workflow_sync(task_id)
            except Exception:
                # A call into Temporal: anything but an answer fails closed,
                # and this row is left exactly as it is.
                tally["temporal_unreachable"] += 1
                logger.exception(
                    "eval_task_failed_create_describe_failed", task_id=str(task_id)
                )
                continue
            if verdict != WF_ABSENT:
                tally["workflow_on_record"] += 1
                continue
            if not apply:
                tally["would_withdraw"] += 1
                continue
            with transaction.atomic():
                locked = list(
                    failed_create_candidates(created_before=created_before)
                    .filter(id=task_id)
                    .select_for_update()
                    .values_list("id", flat=True)
                )
                if not locked:
                    tally["changed_since_read"] += 1
                    continue
                withdraw_tasks(locked)
            tally["withdrawn"] += 1
    return tallies


__all__ = ["TALLIES", "withdraw_failed_creates", "withdraw_tasks"]
