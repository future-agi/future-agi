"""
Bounded work unit + self-continuation for eval clustering.

The work unit must be bounded: an unbounded backfill in one activity
times out, retries once, times out again, and the backlog never drains
(the exact incident TH-4789 exists to fix). These tests pin down:

  * a full batch (== cap) schedules a follow-up run so the backlog
    drains over successive bounded runs, and
  * a partial batch (< cap) does NOT — no pointless re-trigger.

The continuation must use the per-project task id + USE_EXISTING + a
start_delay (so it isn't coalesced into the run that spawns it).
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from tracer.utils.eval_clustering import _CLUSTER_BATCH_LIMIT, cluster_eval_results


class _FakeResult:
    def __init__(self, i: int):
        self.eval_logger_id = f"el-{i}"
        self.eval_name = "prosody_and_intonation"

    @property
    def embedding_text(self) -> str:
        return "robotic rhythm"


def _run_with(n_results: int, cluster_raises: bool = False):
    """Run cluster_eval_results with deps mocked, return the patched task."""
    results = [_FakeResult(i) for i in range(n_results)]

    task = MagicMock()
    create = (
        MagicMock(side_effect=RuntimeError("centroid store down"))
        if cluster_raises
        else MagicMock(return_value="E-X")
    )
    with patch(
        "tracer.utils.eval_clustering.get_unclustered_eval_results",
        return_value=results,
    ), patch(
        "tracer.utils.eval_clustering.embed_texts",
        return_value=[[0.0] for _ in results],
    ), patch(
        "tracer.utils.eval_clustering.find_nearest_centroid", return_value=None
    ), patch(
        "tracer.utils.eval_clustering.create_cluster", create
    ), patch(
        "tracer.tasks.eval_clustering.cluster_eval_results_task", task
    ):
        cluster_eval_results("proj-1")
    return task


def test_full_batch_schedules_distinct_id_continuation():
    """
    Full batch + progress → exactly one follow-up, with a DISTINCT id and
    NO conflict policy. Reusing the fixed id + USE_EXISTING here is the
    bug found in e2e: it coalesces the follow-up into the still-open
    parent and the backlog never drains past one batch.
    """
    task = _run_with(_CLUSTER_BATCH_LIMIT)

    assert task.apply_async.call_count == 1
    kw = task.apply_async.call_args.kwargs
    assert kw["args"] == ("proj-1",)
    assert kw["task_id"].startswith("eval-cluster-proj-1-cont-")
    assert kw["task_id"] != "eval-cluster-proj-1"
    assert "id_conflict_policy" not in kw  # must NOT coalesce into parent
    assert isinstance(kw["start_delay"], timedelta)


def test_two_runs_get_unique_continuation_ids():
    """Chained continuations must not collide on workflow id."""
    id1 = _run_with(_CLUSTER_BATCH_LIMIT).apply_async.call_args.kwargs["task_id"]
    id2 = _run_with(_CLUSTER_BATCH_LIMIT).apply_async.call_args.kwargs["task_id"]
    assert id1 != id2


def test_full_batch_zero_progress_does_not_loop():
    """
    Full batch but every cluster op fails (downstream down) → progress is
    0 → NO continuation. Guards against a hot retrigger loop.
    """
    task = _run_with(_CLUSTER_BATCH_LIMIT, cluster_raises=True)
    task.apply_async.assert_not_called()


def test_partial_batch_does_not_continue():
    task = _run_with(_CLUSTER_BATCH_LIMIT - 1)
    task.apply_async.assert_not_called()


def test_empty_does_not_continue():
    task = _run_with(0)
    task.apply_async.assert_not_called()
