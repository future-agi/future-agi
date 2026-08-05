"""
Bounded work unit + task-level drain loop for eval clustering.

The work unit must be bounded: an unbounded backfill in one activity times
out, retries once, times out again, and the backlog never drains. So
``cluster_eval_results`` does exactly one capped batch and returns a summary,
and the *caller* (``cluster_eval_results_task``) loops it to drain the backlog.
This replaced an in-function self-continuation whose distinct-id follow-up ran
concurrently with the next per-eval trigger and double-counted. These tests pin:

  * the inner batch is bounded and never self-continues, and
  * the caller's drain loop terminates on a short batch (backlog drained) or on
    zero progress (downstream down), aggregates counters across batches, and is
    backstopped for the pathological full-batch-of-re-fetched-rows case.
"""

from unittest.mock import MagicMock, patch

import pytest

from tracer.tasks.eval_clustering import (
    _MAX_DRAIN_BATCHES,
    cluster_eval_results_task,
)
from tracer.types.eval_cluster_types import EvalClusteringSummary
from tracer.utils.eval_clustering import _CLUSTER_BATCH_LIMIT, cluster_eval_results


class _FakeResult:
    def __init__(self, i: int):
        self.eval_logger_id = f"el-{i}"
        self.eval_name = "prosody_and_intonation"
        self.target_type = "span"

    @property
    def embedding_text(self) -> str:
        return "robotic rhythm"


# ---------------------------------------------------------------------------
# Inner batch: bounded, reports `fetched`, and never self-continues.
# ---------------------------------------------------------------------------


def _run_batch(n_results: int, cluster_raises: bool = False) -> EvalClusteringSummary:
    """Run one ``cluster_eval_results`` batch with deps mocked; return its
    summary. Also asserts the inner batch never schedules a follow-up itself —
    draining is the caller's job now, and a re-added self-continuation would
    race the per-eval trigger."""
    results = [_FakeResult(i) for i in range(n_results)]
    create = (
        MagicMock(side_effect=RuntimeError("centroid store down"))
        if cluster_raises
        else MagicMock(return_value="E-X")
    )
    task = MagicMock()
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
        summary = cluster_eval_results("proj-1")
    task.apply_async.assert_not_called()  # inner batch must never self-continue
    return summary


def test_full_batch_reports_fetched_at_cap():
    """A full batch reports fetched == cap so the caller keeps draining."""
    summary = _run_batch(_CLUSTER_BATCH_LIMIT)
    assert summary.fetched == _CLUSTER_BATCH_LIMIT
    assert summary.new_clusters == _CLUSTER_BATCH_LIMIT
    assert summary.clustered == _CLUSTER_BATCH_LIMIT


def test_partial_batch_reports_fetched_below_cap():
    """A short batch signals the fetchable set is drained (caller then stops)."""
    summary = _run_batch(_CLUSTER_BATCH_LIMIT - 1)
    assert summary.fetched == _CLUSTER_BATCH_LIMIT - 1


def test_empty_batch_is_zero_summary():
    summary = _run_batch(0)
    assert summary.fetched == 0
    assert summary.clustered == 0


def test_full_batch_zero_progress_reports_no_clustered():
    """Full batch but every cluster op fails → clustered 0 at cap-fetched. The
    caller uses ``clustered == 0`` to stop rather than hot-loop."""
    summary = _run_batch(_CLUSTER_BATCH_LIMIT, cluster_raises=True)
    assert summary.fetched == _CLUSTER_BATCH_LIMIT
    assert summary.clustered == 0


# ---------------------------------------------------------------------------
# Caller drain loop: cluster_eval_results_task.
# ---------------------------------------------------------------------------


def _full(n: int = 1) -> EvalClusteringSummary:
    return EvalClusteringSummary(
        clustered=n, new_clusters=n, assigned=0, fetched=_CLUSTER_BATCH_LIMIT
    )


def _short(n: int = 3) -> EvalClusteringSummary:
    return EvalClusteringSummary(
        clustered=n, new_clusters=n, assigned=0, fetched=_CLUSTER_BATCH_LIMIT - 1
    )


def _drain_with(summaries):
    """Run the drain loop with ``cluster_eval_results`` scripted to return the
    given summaries in order (repeating the last if the loop asks for more);
    return (result_dict, call_count)."""
    seq = list(summaries)
    calls = {"n": 0}

    def _next(project_id):
        i = calls["n"]
        calls["n"] += 1
        return seq[i] if i < len(seq) else seq[-1]

    with patch(
        "tracer.utils.eval_clustering.cluster_eval_results", side_effect=_next
    ):
        result = cluster_eval_results_task("proj-1")
    return result, calls["n"]


# The drain loop runs the real activity body, which calls close_old_connections()
# — that touches the DB connection, so these need the django_db mark even though
# cluster_eval_results itself is mocked out.
@pytest.mark.django_db
def test_drain_loops_until_short_batch():
    """Full batches keep the loop going; the first short batch ends it. Counters
    aggregate across every batch."""
    result, n = _drain_with([_full(5), _full(5), _short(2)])
    assert n == 3
    assert result["clustered"] == 12  # 5 + 5 + 2


@pytest.mark.django_db
def test_single_short_batch_runs_once():
    result, n = _drain_with([_short(4)])
    assert n == 1
    assert result["clustered"] == 4


@pytest.mark.django_db
def test_drain_stops_on_zero_progress():
    """A full batch that clustered nothing (downstream down) stops the loop — no
    hot re-fetch loop — even though ``fetched`` is still at the cap."""
    stuck = EvalClusteringSummary(clustered=0, fetched=_CLUSTER_BATCH_LIMIT)
    result, n = _drain_with([_full(5), stuck, _full(5)])
    assert n == 2  # stopped right after the zero-progress batch
    assert result["clustered"] == 5


@pytest.mark.django_db
def test_drain_backstops_at_max_batches():
    """Pathological: every batch is full AND clusters (assigned-but-not-
    junctioned rows re-fetch forever), so the loop never self-terminates. The
    ``_MAX_DRAIN_BATCHES`` backstop bounds it."""
    result, n = _drain_with([_full(1)])  # always full + progress
    assert n == _MAX_DRAIN_BATCHES
