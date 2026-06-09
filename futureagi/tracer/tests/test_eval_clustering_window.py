"""
Tests for the recency window on eval clustering.

get_unclustered_eval_results must only return eval failures from the last
_CLUSTER_WINDOW_DAYS — old failures aren't actionable and an unbounded
history is what let the clustering work unit balloon.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.observation_span import EvalLogger
from tracer.queries.eval_clustering import (
    _CLUSTER_WINDOW_DAYS,
    get_unclustered_eval_results,
)


def _make_failing_eval(trace, span, cfg, explanation, age_days):
    """Create a failing span eval and backdate created_at by age_days.

    created_at is auto_now_add, so it must be set via a direct UPDATE.
    """
    ev = EvalLogger.objects.create(
        trace=trace,
        observation_span=span,
        custom_eval_config=cfg,
        target_type="span",
        output_bool=False,
        eval_explanation=explanation,
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

    _make_failing_eval(
        trace, observation_span, custom_eval_config,
        old_exp, age_days=_CLUSTER_WINDOW_DAYS + 30,
    )
    _make_failing_eval(
        trace, observation_span, custom_eval_config,
        new_exp, age_days=1,
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


@pytest.mark.unit
def test_window_constant_unchanged():
    """Guards against an accidental edit to the recency window."""
    assert _CLUSTER_WINDOW_DAYS == 60
