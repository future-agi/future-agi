"""Scanning keeps working when model serving is not running.

The standalone install runs ``serving`` only with the ``ml`` compose profile. The
trace scanner's embed → cluster chain and eval-result clustering both need it,
and used to fail their activities (and retry) on every scan or failing eval,
after spending an LLM distillation call on text they could not embed. Without
serving they now stop cleanly: scan results are already written, and the
unclustered rows are picked up by the next clustering pass once serving is up.

The root conftest reports serving as reachable; these request
``model_serving_down``. DB-free: every collaborator is mocked.
"""

import uuid
from unittest.mock import MagicMock, patch

from tracer.tasks import trace_scanner as scanner
from tracer.types.scan_types import ClusterableIssue
from tracer.utils.eval_clustering import cluster_eval_results
from tracer.utils.trace_scanner import cluster_issues


def _issue() -> ClusterableIssue:
    return ClusterableIssue(
        issue_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        project_id="p1",
        category="Language-only",
        group="Tool Failures",
        fix_layer="Tools",
        brief="Stated a price without calling the quote tool",
        confidence="high",
    )


class TestEmbedTraceInputsTask:
    def test_skips_embedding_and_clustering_without_serving(self, model_serving_down):
        with (
            patch.object(scanner, "embed_trace_inputs") as embed,
            patch.object(scanner, "cluster_scan_issues_task") as cluster,
        ):
            scanner.embed_trace_inputs_task._original_func(["t1"], "p1", True)

        embed.assert_not_called()
        cluster.apply_async.assert_not_called()

    def test_embeds_and_chains_clustering_with_serving(self):
        with (
            patch.object(scanner, "embed_trace_inputs", return_value=1) as embed,
            patch.object(scanner, "cluster_scan_issues_task") as cluster,
        ):
            scanner.embed_trace_inputs_task._original_func(["t1"], "p1", True)

        embed.assert_called_once_with(["t1"], "p1")
        cluster.apply_async.assert_called_once_with(args=("p1",))


def test_scan_clustering_stops_before_distilling(model_serving_down):
    with (
        patch(
            "tracer.utils.trace_scanner.get_unclustered_issues", return_value=[_issue()]
        ),
        patch("tracer.utils.trace_scanner.distill_scan_briefs") as distill,
        patch("tracer.utils.trace_scanner.embed_texts") as embed,
        patch("tracer.utils.trace_scanner.create_cluster") as create,
    ):
        summary = cluster_issues("p1")

    assert summary.clustered == 0
    distill.assert_not_called()
    embed.assert_not_called()
    create.assert_not_called()


def test_eval_clustering_ends_the_drain_without_serving(model_serving_down):
    """``fetched == 0`` is what stops ``cluster_eval_results_task``'s loop."""
    result = MagicMock(embedding_text="the answer ignores the question")
    with (
        patch(
            "tracer.utils.eval_clustering.get_unclustered_eval_results",
            return_value=[result],
        ),
        patch("tracer.utils.eval_clustering.distill_eval_failure_phrases") as distill,
        patch("tracer.utils.eval_clustering.embed_texts") as embed,
    ):
        summary = cluster_eval_results("p1")

    assert summary.fetched == 0
    assert summary.clustered == 0
    distill.assert_not_called()
    embed.assert_not_called()
