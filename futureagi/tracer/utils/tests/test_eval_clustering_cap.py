"""
Bounded work unit + task-level drain loop for eval clustering.

The work unit must be bounded: an unbounded backfill in one activity times
out, retries once, times out again, and the backlog never drains. So
``cluster_eval_results`` does exactly one capped batch and returns a summary,
and the *caller* (``cluster_eval_results_task``) loops it to drain the backlog.
This replaced an in-function self-continuation whose distinct-id follow-up ran
concurrently with the next per-eval trigger and double-counted. These tests pin:

  * the inner batch is bounded and never self-continues, and
  * the caller's drain loop terminates on an EMPTY fetch (backlog drained) or on
    zero progress (downstream down), aggregates counters across batches, and is
    backstopped so one dispatch can't run away on a very large backlog — loudly,
    because a capped-out drain leaves a backlog nothing else will pick up.

A short-but-nonempty batch must NOT stop the loop: triggers arriving mid-run are
coalesced away by the fixed-workflow-id gate, so rows committed while the final
batch was being processed are only picked up if the loop looks again.

Terminating on empty is sound because every row that clusters leaves a junction
row and so drops out of the next fetch — that invariant is pinned in
``tracer/tests/test_eval_clustering_membership.py``.
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
        self.target_type = "session"  # read by cluster_eval_results

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
    """A short batch reports its true size — it does not imply "drained"; only a
    zero fetch does."""
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


def _empty() -> EvalClusteringSummary:
    """Nothing left to fetch — the only clean stop."""
    return EvalClusteringSummary()


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
def test_drain_loops_until_empty_batch():
    """Full batches keep the loop going; the first EMPTY fetch ends it. Counters
    aggregate across every batch."""
    result, n = _drain_with([_full(5), _full(5), _empty()])
    assert n == 3
    assert result["clustered"] == 10  # 5 + 5, the empty batch adds nothing


@pytest.mark.django_db
def test_short_batch_keeps_draining():
    """The pin for the coalescing gap: a short-but-nonempty batch means "nothing
    more right now", NOT "drained". A trigger that arrived while this batch was
    being processed was folded into this run and dropped, so stopping here would
    strand its rows until the project's next failing eval — which a finished
    one-shot task never produces. The loop must look again."""
    result, n = _drain_with([_short(2), _short(3), _empty()])
    assert n == 3, "a short batch must not terminate the drain"
    assert result["clustered"] == 5


@pytest.mark.django_db
def test_single_empty_batch_runs_once():
    result, n = _drain_with([_empty()])
    assert n == 1
    assert result["clustered"] == 0


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
    """A backlog bigger than the loop can drain in one dispatch: every batch
    comes back full AND makes progress, so nothing self-terminates it. The
    ``_MAX_DRAIN_BATCHES`` backstop bounds the dispatch, and the next trigger
    continues from where it stopped."""
    result, n = _drain_with([_full(1)])  # always full + progress
    assert n == _MAX_DRAIN_BATCHES


@pytest.mark.django_db
def test_capped_drain_is_logged_as_an_error():
    """Capping out leaves a backlog that nothing else is guaranteed to pick up —
    a finished one-shot task produces no further trigger. It must not be
    indistinguishable from a clean drain in the logs."""
    with patch("tracer.tasks.eval_clustering.logger") as log:
        _drain_with([_full(1)])
    assert log.error.called, "a capped-out drain must not exit quietly"
    assert log.error.call_args.args[0] == "eval_clustering_drain_cap_exhausted"
