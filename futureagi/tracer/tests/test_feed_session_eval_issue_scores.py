"""A session eval issue is scored by its own eval on every tab, not by its peers.

Session evals anchor to the session, not a trace. The Traces tab, sidebar and
Trends read the member sessions' representative traces first, so a session
issue showed other evaluators' span scores there, and when there were none,
the average (or lowest) of every session eval on those sessions.
"""

from contextlib import nullcontext
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.queries import feed

pytestmark = pytest.mark.django_db

OWN_SCORE = 0.2


def _eval(config, score, *, session_id=None, trace_id=None):
    anchors = (
        {"trace_session_id": session_id, "target_type": EvalTargetType.SESSION}
        if session_id
        else {
            "trace_id": trace_id,
            "observation_span_id": f"span_{uuid4().hex[:16]}",
            "target_type": EvalTargetType.SPAN,
        }
    )
    return EvalLogger.objects.create(
        **anchors,
        custom_eval_config=config,
        output_float=score,
        eval_explanation=f"{config.name} explanation",
        eval_task_id=f"et-{uuid4()}",
    )


@pytest.fixture(params=[True, False], ids=["peer-span-eval", "no-span-eval"])
def session_issue(request, project, custom_eval_config):
    """A failing session eval at 0.2, a peer session eval at 0.1 on the same
    session and (first case) a peer span eval at 0.98 on its latest trace."""
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="E-SESSION-SCORE-1",
        error_type="eval",
        issue_group=custom_eval_config.name,
        source=ClusterSource.EVAL,
        eval_target_type=EvalTargetType.SESSION,
        eval_config=custom_eval_config,
    )
    peer = CustomEvalConfig.objects.create(
        project=project,
        name="Peer evaluator",
        eval_template=custom_eval_config.eval_template,
        config={"threshold": 0.8},
        mapping={"input": "input", "output": "output"},
        filters={},
    )
    session_id, trace_id = str(uuid4()), str(uuid4())
    own = _eval(custom_eval_config, OWN_SCORE, session_id=session_id)
    _eval(peer, 0.1, session_id=session_id)
    if request.param:
        _eval(peer, 0.98, trace_id=trace_id)
    ErrorClusterTraces.objects.create(
        cluster=cluster, eval_logger=own, trace_session_id=session_id
    )
    reader = MagicMock()
    reader.first_span_by_type.return_value = None
    with (
        patch.object(
            feed,
            "_session_rep_trace_map",
            side_effect=lambda sids, *_: {
                str(s): trace_id for s in sids if str(s) == session_id
            },
        ),
        patch.object(
            feed,
            "_resolve_member_traces",
            side_effect=lambda ids, *_a, **_k: {
                str(t): feed._CHTraceShim(str(t), None) for t in ids
            },
        ),
        patch.object(feed, "_get_trace_totals_batch", return_value={}),
        patch.object(feed, "_get_root_spans_batch", return_value={}),
        patch.object(feed, "_get_investigation_reports_batch", return_value={}),
        patch.object(feed, "_users_affected_in_window", return_value=0),
        patch.object(feed, "_project_scope_total", return_value=10),
        patch.object(feed, "_fetch_latest_trace_id_batch", return_value={}),
        patch.object(feed, "get_reader", return_value=nullcontext(reader)),
    ):
        yield cluster, trace_id


def test_traces_tab_scores_a_session_issue_by_its_own_eval(session_issue):
    cluster, _trace_id = session_issue

    tab = feed.get_traces_tab(cluster.cluster_id, [str(cluster.project_id)])

    assert tab.aggregates.avg_score == pytest.approx(OWN_SCORE)
    assert [row.score for row in tab.traces] == [pytest.approx(OWN_SCORE)]


def test_sidebar_eval_score_is_the_session_issue_eval(session_issue):
    cluster, trace_id = session_issue
    project_ids = [str(cluster.project_id)]

    default = feed.get_sidebar(cluster.cluster_id, project_ids)
    selected = feed.get_sidebar(cluster.cluster_id, project_ids, trace_id=trace_id)

    assert default.ai_metadata.eval_score == pytest.approx(OWN_SCORE)
    assert selected.ai_metadata.eval_score == pytest.approx(OWN_SCORE)


def test_trend_score_is_the_session_issue_eval(session_issue):
    cluster, _trace_id = session_issue

    metrics = feed._fetch_trend_metrics(
        cluster.cluster_id, str(cluster.project_id), days=14
    )

    score = next(m for m in metrics if m.label == "Avg eval score")
    assert score.value == f"{OWN_SCORE:.2f}"
