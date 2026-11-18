"""Red baseline budgets (S12): each asserts the post-fix
Phase-B budget against CURRENT code and must fail today — strict xfail.
The fix packet that lands each fix removes its xfail marker.
(S6 moved to test_trace_loading.py as part of A4 (lean-first loading).)
(S10 moved to test_reconcile_counts.py once A7 landed.)
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.stress.budgets import (
    DRAIN_BATCH_MAX_CH_QUERIES,
    DRAIN_PER_ENTRY_MAX_PG_QUERIES,
)
from tests.stress.ch_asserts import ch_query_budget
from tracer.models.eval_task import RowType
from tracer.services.eval_tasks.entries import claim_pending_batch
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.services.eval_tasks.run_entry import run_entry

pytestmark = pytest.mark.stress


@pytest.mark.django_db
@pytest.mark.xfail(
    strict=True, reason="B1/B2/B3 (batch-prefetch drain) not yet implemented"
)
def test_s12_drain_batch_query_counts(
    stress_dataset, eval_task_factory, stub_run_eval, stub_cost_log
):
    m = stress_dataset.target
    batch_size = 500
    task = eval_task_factory(m.project_id, RowType.SPANS, spans_limit=batch_size)
    reconcile(task)
    entries = claim_pending_batch(task, batch_size)
    assert len(entries) == batch_size

    with (
        CaptureQueriesContext(connection) as ctx,
        ch_query_budget("stress:B:drain-batch") as b,
    ):
        for entry in entries:
            run_entry(entry)

    assert b.count <= DRAIN_BATCH_MAX_CH_QUERIES
    assert len(ctx.captured_queries) <= batch_size * DRAIN_PER_ENTRY_MAX_PG_QUERIES
