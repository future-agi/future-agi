"""
Tests for the recency window on eval clustering.

get_unclustered_eval_results must only return eval failures from the last
_CLUSTER_WINDOW_DAYS — old failures aren't actionable and an unbounded
history is what let the clustering work unit balloon.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.observation_span import EvalLogger
from tracer.queries.eval_clustering import (
    _CLUSTER_WINDOW_DAYS,
    get_unclustered_eval_results,
)


def _make_failing_eval(trace, span, cfg, explanation, age_days, eval_task_id="et-1"):
    """Create a failing span eval and backdate created_at by age_days.

    created_at is auto_now_add, so it must be set via a direct UPDATE. Defaults
    to an eval-task eval (``eval_task_id`` set) — clustering is eval-task-only;
    pass ``eval_task_id=None`` for an inline/continuous/external failure.
    """
    ev = EvalLogger.objects.create(
        trace=trace,
        observation_span=span,
        custom_eval_config=cfg,
        target_type="span",
        output_bool=False,
        eval_explanation=explanation,
        eval_task_id=eval_task_id,
    )
    EvalLogger.objects.filter(pk=ev.pk).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    return ev


@pytest.mark.django_db
def test_window_excludes_old_includes_recent(
    project, trace, observation_span, custom_eval_config
):
    old_exp = "old failing eval - outside the window"
    new_exp = "recent failing eval - inside the window"

    # Distinct eval_task_ids: two live evals on the same (task, span, config)
    # would collide on the eval_logger_live_span_uniq work-item constraint.
    _make_failing_eval(
        trace, observation_span, custom_eval_config,
        old_exp, age_days=_CLUSTER_WINDOW_DAYS + 30, eval_task_id="et-old",
    )
    _make_failing_eval(
        trace, observation_span, custom_eval_config,
        new_exp, age_days=1, eval_task_id="et-new",
    )

    explanations = {
        r.explanation for r in get_unclustered_eval_results(str(project.id))
    }

    assert new_exp in explanations, "recent failure must be clustered"
    assert old_exp not in explanations, "stale failure must be excluded"


@pytest.mark.django_db
def test_boundary_just_inside_window_is_included(
    project, trace, observation_span, custom_eval_config
):
    exp = "failure just inside the window boundary"
    _make_failing_eval(
        trace, observation_span, custom_eval_config,
        exp, age_days=_CLUSTER_WINDOW_DAYS - 1,
    )

    explanations = {
        r.explanation for r in get_unclustered_eval_results(str(project.id))
    }
    assert exp in explanations


@pytest.mark.django_db
def test_clustering_includes_eval_task_failures(
    project, trace, observation_span, custom_eval_config
):
    """An eval-task failure (``eval_task_id`` set) is clusterable."""
    exp = "eval-task failure"
    _make_failing_eval(trace, observation_span, custom_eval_config, exp, age_days=1)

    explanations = {
        r.explanation for r in get_unclustered_eval_results(str(project.id))
    }
    assert exp in explanations


@pytest.mark.django_db
def test_clustering_excludes_non_eval_task_failures(
    project, trace, observation_span, custom_eval_config
):
    """Clustering is eval-task-only. Inline / continuous-span / external failures
    carry no ``eval_task_id`` and must never enter clustering. Pins the
    ``eval_task_id`` scoping so a refactor can't silently re-admit the far larger
    non-eval-task backlog (the regression's blast radius).
    """
    exp = "inline (non-eval-task) failure"
    _make_failing_eval(
        trace, observation_span, custom_eval_config, exp, age_days=1, eval_task_id=None
    )

    explanations = {
        r.explanation for r in get_unclustered_eval_results(str(project.id))
    }
    assert exp not in explanations


@pytest.mark.django_db
def test_clustering_includes_ch_only_session_failure(project, custom_eval_config):
    """A session-target failure clusters via its config's project even when the
    session lives only in ClickHouse (no PG ``TraceSession`` row) and the eval
    row carries no ``trace``. This is the exact case the removed CH session
    pre-pass existed for; scoping by ``custom_eval_config__project_id`` covers it
    with zero reads of the (dropped) PG ``tracer_trace`` / ``tracer_observation_span``
    tables. Guards against a regression that re-introduces a trace/session join.
    """
    exp = "ch-only session-target failure"
    ev = EvalLogger.objects.create(
        trace_session_id=uuid.uuid4(),  # CH-only session: no PG TraceSession row
        custom_eval_config=custom_eval_config,
        target_type="session",
        output_bool=False,
        eval_explanation=exp,
        eval_task_id="et-1",
    )
    EvalLogger.objects.filter(pk=ev.pk).update(
        created_at=timezone.now() - timedelta(days=1)
    )

    explanations = {
        r.explanation for r in get_unclustered_eval_results(str(project.id))
    }
    assert exp in explanations


@pytest.mark.unit
def test_window_constant_unchanged():
    """Guards against an accidental edit to the recency window."""
    assert _CLUSTER_WINDOW_DAYS == 60
