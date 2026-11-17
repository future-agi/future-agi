"""An eval issue's working-call reference is a call the eval itself passes.

The failure classifier counts a result as failing below the eval template's
``pass_threshold`` (default 0.5). The issue detail picked its passing
("working") reference with a hard-coded perfect score instead, so a call
scoring 0.8 under a 0.5 threshold, a genuine pass, was never offered and the
voice panel hid "Compare with passing". The reference now uses the same
threshold as the classifier.

One eval config can also run on several targets (span, trace, session). The
reference is the newest pass at the issue's own target: a newer pass on
another target has no anchor at this one and must not hide the reference.

ClickHouse reads are faked; Postgres runs for real.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest

from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.queries import feed
from tracer.types.feed_types import TracePreview

pytestmark = pytest.mark.django_db

FAIL_TRACE = str(uuid.uuid4())
PASS_TRACE = str(uuid.uuid4())
OTHER_TRACE = str(uuid.uuid4())
FAIL_SESSION = str(uuid.uuid4())
PASS_SESSION = str(uuid.uuid4())
OTHER_SESSION = str(uuid.uuid4())
# Each session is represented by its latest trace.
SESSION_REP_TRACES = {
    FAIL_SESSION: FAIL_TRACE,
    PASS_SESSION: PASS_TRACE,
    OTHER_SESSION: OTHER_TRACE,
}
# (trace, session) of the failing member, the working call, and another call.
CALLS = {
    "failing": (FAIL_TRACE, FAIL_SESSION),
    "working": (PASS_TRACE, PASS_SESSION),
    "other": (OTHER_TRACE, OTHER_SESSION),
}


def _preview(trace_id, project_id):
    return TracePreview(trace_id=str(trace_id))


def _session_reps(session_ids, project_id):
    return {
        sid: SESSION_REP_TRACES[sid] for sid in session_ids if sid in SESSION_REP_TRACES
    }


@pytest.fixture(autouse=True)
def _fake_clickhouse():
    with (
        patch.object(feed, "_ch_trace_preview", _preview),
        patch.object(feed, "_session_rep_trace_map", _session_reps),
        patch.object(feed, "is_voice_project", return_value=True),
        patch.object(feed, "_fetch_trends_batch", return_value={}),
        patch.object(feed, "_fetch_users_affected_batch", return_value={}),
        patch.object(feed, "_fetch_sessions_batch", return_value={}),
    ):
        yield


def _result(config, target, *, call, **output):
    """One eval result on ``call`` (its session for session evals, its trace
    and a span otherwise)."""
    trace_id, session_id = CALLS[call]
    if target == EvalTargetType.SESSION:
        anchor = {"trace_session_id": session_id}
    else:
        anchor = {"trace_id": trace_id, "observation_span_id": f"span-{trace_id}"}
    return EvalLogger.objects.create(
        custom_eval_config=config,
        target_type=target,
        eval_explanation="synthetic judge reasoning",
        **anchor,
        **output,
    )


def _make_older(result):
    EvalLogger.objects.filter(pk=result.pk).update(
        created_at=result.created_at - timedelta(hours=1)
    )


def _eval_issue(project, config, target):
    """An eval issue whose one member scored 0.2."""
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id=f"E-REF-{target.value}",
        error_type="eval",
        issue_group=config.name,
        source=ClusterSource.EVAL,
        eval_config=config,
        eval_target_type=target,
    )
    failing = _result(config, target, call="failing", output_float=0.2)
    if target == EvalTargetType.SESSION:
        member = {"trace_session_id": FAIL_SESSION}
    else:
        member = {"trace_id": FAIL_TRACE}
    ErrorClusterTraces.objects.create(cluster=cluster, eval_logger=failing, **member)
    return cluster


def _working_trace_id(cluster):
    detail = feed.get_cluster_detail(cluster.cluster_id, [str(cluster.project_id)])
    return getattr(detail.success_trace, "trace_id", None)


@pytest.mark.parametrize(
    "target", [EvalTargetType.SPAN, EvalTargetType.SESSION], ids=["span", "session"]
)
def test_working_reference_is_a_call_the_eval_passes(
    project, custom_eval_config, target
):
    """0.8 passes the template's default 0.5 threshold, so it is the working call."""
    cluster = _eval_issue(project, custom_eval_config, target)
    _result(custom_eval_config, target, call="working", output_float=0.8)

    assert _working_trace_id(cluster) == PASS_TRACE


@pytest.mark.parametrize(
    ("pass_threshold", "output"),
    [
        (0.5, {"output_float": 0.4}),
        (0.9, {"output_float": 0.8}),
        (0.5, {"output_bool": False, "output_float": 1.0}),
    ],
    ids=["below-default-threshold", "below-stricter-threshold", "judged-false"],
)
def test_working_reference_skips_calls_the_eval_fails(
    project, custom_eval_config, pass_threshold, output
):
    template = custom_eval_config.eval_template
    template.pass_threshold = pass_threshold
    template.save(update_fields=["pass_threshold"])
    cluster = _eval_issue(project, custom_eval_config, EvalTargetType.SPAN)
    _result(custom_eval_config, EvalTargetType.SPAN, call="working", **output)

    detail = feed.get_cluster_detail(cluster.cluster_id, [str(project.id)])

    assert detail.success_trace is None


@pytest.mark.parametrize(
    ("target", "other_target", "other_score"),
    [
        (EvalTargetType.SPAN, EvalTargetType.SESSION, 0.8),
        (EvalTargetType.SESSION, EvalTargetType.SPAN, 0.8),
        (EvalTargetType.SPAN, EvalTargetType.SESSION, 1.0),
        (EvalTargetType.SESSION, EvalTargetType.SPAN, 1.0),
        (EvalTargetType.TRACE, EvalTargetType.SPAN, 1.0),
    ],
    ids=[
        "span-vs-newer-session-0.8",
        "session-vs-newer-span-0.8",
        "span-vs-newer-session-1.0",
        "session-vs-newer-span-1.0",
        "trace-vs-newer-span-1.0",
    ],
)
def test_working_reference_stays_on_the_issue_target(
    project, custom_eval_config, target, other_target, other_score
):
    """A newer pass of the same eval config on another target is not this
    issue's working call and does not hide the one at the issue's target."""
    cluster = _eval_issue(project, custom_eval_config, target)
    own = _result(custom_eval_config, target, call="working", output_float=1.0)
    _make_older(own)
    _result(custom_eval_config, other_target, call="other", output_float=other_score)

    assert _working_trace_id(cluster) == PASS_TRACE


@pytest.mark.parametrize(
    "own_target", [EvalTargetType.SPAN, EvalTargetType.TRACE], ids=["span", "trace"]
)
def test_issue_without_a_recorded_target_keeps_its_working_call(
    project, custom_eval_config, own_target
):
    """Eval issues from before the target was recorded have none. A pass on a
    trace (span or trace target) is still their working call; a newer session
    pass, which has no trace, does not hide it."""
    cluster = _eval_issue(project, custom_eval_config, own_target)
    cluster.eval_target_type = None
    cluster.save(update_fields=["eval_target_type"])
    own = _result(custom_eval_config, own_target, call="working", output_float=1.0)
    _make_older(own)
    _result(custom_eval_config, EvalTargetType.SESSION, call="other", output_float=1.0)

    assert _working_trace_id(cluster) == PASS_TRACE
