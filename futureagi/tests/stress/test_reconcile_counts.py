"""Stress tests for reconciler PG statement counts."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.stress.budgets import RECONCILE_REQUEUE_MAX_PG_UPDATES
from tracer.models.eval_task import RowType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.services.eval_tasks.reconciler import reconcile

pytestmark = pytest.mark.stress


@pytest.mark.django_db
def test_s10_reconcile_requeue_update_fanout(stress_dataset, eval_task_factory):
    m = stress_dataset.target
    task = eval_task_factory(m.project_id, RowType.SPANS, spans_limit=60, n_evals=3)
    reconcile(task)
    EvalLogger.objects.filter(eval_task_id=str(task.id)).update(
        status=EvalEntryStatus.COMPLETED
    )
    # Edit every config: hash changes make all completed entries stale.
    for cfg in task.evals.all():
        cfg.config = {"threshold": 0.9}
        cfg.save()

    with CaptureQueriesContext(connection) as ctx:
        result = reconcile(task)

    assert result.requeued == 180  # parity: every stale entry requeued
    table = EvalLogger._meta.db_table
    updates = [
        q
        for q in ctx.captured_queries
        if q["sql"].strip().upper().startswith("UPDATE") and table in q["sql"]
    ]
    assert len(updates) <= RECONCILE_REQUEUE_MAX_PG_UPDATES
