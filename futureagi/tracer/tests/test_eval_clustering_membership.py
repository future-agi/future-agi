"""
Every clustered eval must leave a junction row carrying its ``eval_logger_id``.

That row is the ONLY marker ``get_unclustered_eval_results`` uses to exclude an
eval from the next batch. An eval that bumps a cluster's counters without
leaving one is re-fetched by every subsequent batch, forever: it sits at the head
of the oldest-first window, blocks the rows behind it, and re-inflates
``error_count`` and the ClickHouse centroid on every pass — up to
``_MAX_DRAIN_BATCHES`` times per dispatch once a project accumulates a full batch
of them.

These tests pin the invariant on both membership shapes:

  * repeat failures on ONE trace each get their own row — the unique key is
    (cluster, trace, span) with span left NULL, which Postgres permits; and
  * a session already in the cluster, where (cluster, trace_session) IS a real
    unique constraint and a second membership row is impossible, gets a
    provenance-only row (every target FK NULL) instead.

ClickHouse is stubbed — the centroid store is not what's under test here.
"""

from unittest.mock import MagicMock, patch

import pytest

from tracer.models.observation_span import EvalLogger
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_session import TraceSession
from tracer.queries.eval_clustering import (
    assign_to_cluster,
    create_cluster,
    get_unclustered_eval_results,
)
from tracer.types.eval_cluster_types import ClusterableEvalResult

_EMBEDDING = [0.1, 0.2, 0.3]


@pytest.fixture
def no_clickhouse():
    """Stub the centroid store and the cheap-LLM metadata helper.

    ``execute`` returns [] so the centroid read takes its "no existing centroid"
    branch — the vector store isn't what these tests are about.
    """
    ch = MagicMock()
    ch.return_value.client.execute.return_value = []
    with patch(
        "tracer.queries.eval_clustering.ClickHouseVectorDB", ch
    ), patch(
        "tracer.queries.eval_clustering.ensure_centroid_table", MagicMock()
    ), patch(
        "tracer.ee_boundary.generate_eval_cluster_meta", return_value=None
    ):
        yield


def _failing_eval(cfg, *, trace=None, span=None, session=None, target_type="span"):
    """Persist a failing eval-task EvalLogger — the shape clustering fetches."""
    return EvalLogger.objects.create(
        trace=trace,
        observation_span=span,
        trace_session=session,
        custom_eval_config=cfg,
        target_type=target_type,
        output_bool=False,
        eval_explanation="assistant ignored the retrieved context",
        # Distinct per eval: two live evals on one (task, span, config) collide
        # on the eval_logger_live_span_uniq work-item constraint.
        eval_task_id=f"et-{EvalLogger.objects.count() + 1}",
    )


def _result(ev, *, trace=None, session=None, target_type="span"):
    return ClusterableEvalResult(
        eval_logger_id=str(ev.id),
        project_id=str(ev.custom_eval_config.project_id),
        eval_name=ev.custom_eval_config.name,
        eval_config_id=str(ev.custom_eval_config_id),
        explanation=ev.eval_explanation,
        target_type=target_type,
        trace_id=str(trace.id) if trace else None,
        session_id=str(session.id) if session else None,
        score=0.0,
    )


def _unclustered_ids(project):
    return {r.eval_logger_id for r in get_unclustered_eval_results(str(project.id))}


@pytest.mark.django_db
def test_repeat_failure_on_one_trace_records_every_eval(
    project, trace, observation_span, custom_eval_config, no_clickhouse
):
    """Two failing evals on the same trace, same cluster. The second must still
    get its own junction row — and must drop out of the unclustered set."""
    first = _failing_eval(custom_eval_config, trace=trace, span=observation_span)
    second = _failing_eval(custom_eval_config, trace=trace, span=observation_span)

    cluster_id = create_cluster(
        str(project.id), _result(first, trace=trace), _EMBEDDING
    )
    assign_to_cluster(
        cluster_id, str(project.id), _result(second, trace=trace), _EMBEDDING
    )

    cluster = TraceErrorGroup.objects.get(
        cluster_id=cluster_id, project_id=project.id
    )
    rows = ErrorClusterTraces.objects.filter(cluster=cluster)
    assert rows.count() == 2, "each failing eval needs its own membership row"
    assert {str(r.eval_logger_id) for r in rows} == {str(first.id), str(second.id)}

    # Occurrences move per eval; the distinct unit does not.
    assert cluster.error_count == 2
    assert cluster.total_events == 2
    assert cluster.unique_traces == 1

    assert _unclustered_ids(project) == set(), (
        "an assigned eval that stays fetchable is the re-fetch loop"
    )


@pytest.mark.django_db
def test_session_already_member_still_records_the_eval(
    project, custom_eval_config, no_clickhouse
):
    """(cluster, trace_session) is a real unique constraint, so the second eval
    on an already-member session cannot carry the session. It must still be
    recorded — as a provenance-only row — or it re-fetches forever."""
    session = TraceSession.objects.create(project=project, name="sess-dupe")
    first = _failing_eval(custom_eval_config, session=session, target_type="session")
    second = _failing_eval(custom_eval_config, session=session, target_type="session")

    cluster_id = create_cluster(
        str(project.id),
        _result(first, session=session, target_type="session"),
        _EMBEDDING,
    )
    assign_to_cluster(
        cluster_id,
        str(project.id),
        _result(second, session=session, target_type="session"),
        _EMBEDDING,
    )

    cluster = TraceErrorGroup.objects.get(
        cluster_id=cluster_id, project_id=project.id
    )
    provenance = ErrorClusterTraces.objects.get(cluster=cluster, eval_logger=second)
    assert provenance.trace_session_id is None, "session membership is already taken"
    assert provenance.trace_id is None

    # Membership is unchanged; only the occurrence counters move.
    assert cluster.unique_traces == 1, "provenance row must not count as a unit"
    assert cluster.error_count == 2

    assert _unclustered_ids(project) == set(), (
        "the deduped session eval must leave the fetchable set"
    )


@pytest.mark.django_db
def test_reassigning_the_same_eval_does_not_strand_it(
    project, trace, observation_span, custom_eval_config, no_clickhouse
):
    """The drain is at-least-once (the activity retries). A replayed row must
    stay out of the fetchable set rather than reopening the loop."""
    ev = _failing_eval(custom_eval_config, trace=trace, span=observation_span)
    cluster_id = create_cluster(str(project.id), _result(ev, trace=trace), _EMBEDDING)

    assign_to_cluster(cluster_id, str(project.id), _result(ev, trace=trace), _EMBEDDING)

    assert _unclustered_ids(project) == set()
    cluster = TraceErrorGroup.objects.get(
        cluster_id=cluster_id, project_id=project.id
    )
    assert cluster.unique_traces == 1
