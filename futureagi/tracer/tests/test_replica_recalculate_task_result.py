"""Recalculate on an eval result that an eval task produced.

Observe results are entries of an eval task's engine: one live entry per
(task, row, config), written by ``run_entry``. Recalculate must recompute such
a result through that core, not through the per-span activity (which refuses
task work), keep exactly one live result for the span and config, and keep the
previous result when the recompute does not complete.

Each test runs twice: with replica routing off, and with the TH-3119 models
opted in to the real read router over ``tfc.tests.lagging_replica`` (a second
PostgreSQL connection frozen in a REPEATABLE READ snapshot taken before the
result was written). Only the evaluator engine and the cost log are replaced.
"""

from __future__ import annotations

import pytest

from model_hub.models.choices import FeedbackSourceChoices
from model_hub.models.evals_metric import Feedback
from tfc.tests.lagging_replica import lagging_replica  # noqa: F401  (fixture)
from tracer.models.observation_span import EvalEntryStatus, EvalLogger

pytestmark = pytest.mark.django_db(transaction=True)

URL = "/tracer/observation-span/submit_feedback_action_type/"


@pytest.fixture(autouse=True)
def quiet_side_effects(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")
    monkeypatch.setattr(
        "tracer.views.observation_span.track_mixpanel_event", lambda *a, **k: None
    )


@pytest.fixture(params=["routing_off", "lagging_replica"])
def replica(request):
    """None with routing off; the frozen lagging replica otherwise."""
    if request.param == "routing_off":
        return None
    return request.getfixturevalue("lagging_replica")


def _task_result(eval_task, span, config, **fields):
    """A completed entry of ``eval_task``, as the engine leaves one."""
    return EvalLogger.objects.create(
        trace=span.trace,
        observation_span=span,
        custom_eval_config=config,
        eval_task_id=str(eval_task.id),
        status=EvalEntryStatus.COMPLETED,
        config_hash="hash-of-the-run",
        output_bool=False,
        eval_explanation="the previous answer",
        **fields,
    )


def _feedback(user, span, config):
    return Feedback.objects.create(
        source=FeedbackSourceChoices.OBSERVE.value,
        source_id=str(span.id),
        value="Passed",
        eval_template=config.eval_template,
        user=user,
        custom_eval_config_id=config.id,
        organization=span.project.organization,
        workspace=span.project.workspace,
    )


def _recalculate(client, span, config, feedback):
    return client.post(
        URL,
        {
            "observation_span_id": span.id,
            "action_type": "recalculate",
            "custom_eval_config_id": str(config.id),
            "feedback_id": str(feedback.id),
        },
        format="json",
    )


def _live(span, config):
    return EvalLogger.objects.using("default").filter(
        observation_span=span, custom_eval_config=config
    )


def _stored(pk):
    return EvalLogger.all_objects.using("default").get(pk=pk)


def _write_result(replica, eval_task, span, config, user):
    """The previous result and the user's feedback; written after the
    replica's snapshot, so only the primary has them."""
    if replica is not None:
        replica.freeze()
    previous = _task_result(eval_task, span, config)
    feedback = _feedback(user, span, config)
    if replica is not None:
        assert not EvalLogger.objects.filter(pk=previous.pk).exists()
    return previous, feedback


def test_recalculate_recomputes_a_task_result(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    monkeypatch,
):
    sources = []

    class _CostLog:
        status = "processing"

        def __init__(self, config):
            import json

            self.config = json.dumps(config)
            self.log_id = None

        def save(self):
            pass

    def _cost_log(*, source, config, **_kwargs):
        sources.append(source)
        return _CostLog(config)

    monkeypatch.setattr(
        "tracer.utils.eval.log_and_deduct_cost_for_api_request", _cost_log
    )
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 200, response.data
    assert _stored(previous.pk).deleted is True
    live = _live(observation_span, custom_eval_config)
    assert live.count() == 1
    new = live.get()
    assert new.pk != previous.pk
    assert (new.eval_task_id, new.status, new.output_bool, new.eval_explanation) == (
        str(eval_task.id),
        EvalEntryStatus.COMPLETED,
        True,
        "stubbed pass",
    )
    assert len(stub_run_eval.requests) == 1
    # Billed and logged as a feedback run, as the per-span activity does.
    assert sources == ["feedback"]


def test_recalculate_task_result_twice_keeps_one_live_result(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
):
    """The second Recalculate finds the first one's result (newer than the
    replica) and replaces it in turn."""
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    first = _recalculate(auth_client, observation_span, custom_eval_config, feedback)
    stub_run_eval(value=False, reason="second look")
    second = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert (first.status_code, second.status_code) == (200, 200), second.data
    live = _live(observation_span, custom_eval_config)
    assert live.count() == 1
    assert (live.get().output_bool, live.get().eval_explanation) == (
        False,
        "second look",
    )
    assert (
        EvalLogger.all_objects.using("default")
        .filter(observation_span=observation_span, deleted=True)
        .count()
        == 2
    )


def test_failed_recompute_of_a_task_result_keeps_it(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_cost_log,
    monkeypatch,
):
    """An eval that errors leaves an errored entry; Recalculate keeps the
    previous answer instead and says so."""

    def _engine_down(_request):
        raise RuntimeError("model provider unavailable")

    monkeypatch.setattr("evaluations.engine.run_eval", _engine_down, raising=False)
    monkeypatch.setattr(
        "evaluations.engine.runner.run_eval", _engine_down, raising=False
    )
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "previous result is kept" in str(response.data)
    live = _live(observation_span, custom_eval_config)
    assert [row.pk for row in live] == [previous.pk]
    assert live.get().eval_explanation == "the previous answer"


def test_recompute_that_raises_keeps_the_task_result(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """An infrastructure error from the run (it propagates out of run_entry)
    puts the previous result back too."""
    from tracer.services.clickhouse.v2.eval_loader import EvalTelemetryReadError

    def _ch_down(*_args, **_kwargs):
        raise EvalTelemetryReadError("clickhouse unavailable")

    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry._run_for_target", _ch_down
    )
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    live = _live(observation_span, custom_eval_config)
    assert [row.pk for row in live] == [previous.pk]
    retired = EvalLogger.all_objects.using("default").filter(
        observation_span=observation_span, deleted=True
    )
    assert retired.count() == 1
    assert retired.get().status == EvalEntryStatus.RUNNING


# -- review follow-ups: what a failed or superseded recompute leaves behind ---


def _engine_down(monkeypatch):
    def _down(_request):
        raise RuntimeError("model provider unavailable")

    monkeypatch.setattr("evaluations.engine.run_eval", _down, raising=False)
    monkeypatch.setattr("evaluations.engine.runner.run_eval", _down, raising=False)


def _issue_with(result):
    """A Feed issue that counts ``result`` as its only member."""
    from tracer.models.trace_error_analysis import (
        ClusterSource,
        ErrorClusterTraces,
        TraceErrorGroup,
    )

    config = result.custom_eval_config
    issue = TraceErrorGroup.objects.create(
        project=config.project,
        cluster_id=f"E-RECALC-{result.pk.hex[:6]}",
        error_type="eval",
        issue_group=config.name,
        source=ClusterSource.EVAL,
        eval_target_type=result.target_type,
        eval_config=config,
        error_count=1,
        total_events=1,
        unique_traces=1,
    )
    member = ErrorClusterTraces.objects.create(
        cluster=issue, trace=result.trace, eval_logger=result
    )
    return issue, member


def _membership(member):
    from tracer.models.trace_error_analysis import ErrorClusterTraces

    return ErrorClusterTraces.all_objects.using("default").get(pk=member.pk)


def test_failed_recompute_keeps_the_result_in_its_feed_issue(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_cost_log,
    monkeypatch,
):
    _engine_down(monkeypatch)
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )
    issue, member = _issue_with(previous)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert _stored(previous.pk).deleted is False
    assert _membership(member).deleted is False
    issue.refresh_from_db()
    assert (issue.deleted, issue.error_count) == (False, 1)


def test_replaced_result_leaves_its_feed_issue(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
):
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )
    _issue, member = _issue_with(previous)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 200, response.data
    assert _stored(previous.pk).deleted is True
    assert _membership(member).deleted is True


def test_task_deleted_during_recompute_keeps_its_results_retired(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """Deleting the task retires every live entry, the new one included; a
    failed recompute must not bring the previous result back."""
    from tracer.services.eval_tasks.entries import soft_delete_live

    def _deleted_meanwhile(entry, **_kwargs):
        soft_delete_live(eval_task)
        return "deleted"

    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry", _deleted_meanwhile
    )
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert _stored(previous.pk).deleted is True
    assert not _live(observation_span, custom_eval_config).exists()


def test_failed_recompute_copies_the_kept_answer_back(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_cost_log,
    monkeypatch,
):
    """An errored run copies its answer to an Instant eval run's answers
    (mark_terminal); the kept result's answer is copied back over it."""
    from tracer.services.eval_tasks import entries

    copied = []
    monkeypatch.setattr(
        entries,
        "_record_instant_answer",
        lambda entry_id, task_id: copied.append((str(entry_id), str(task_id))),
    )
    _engine_down(monkeypatch)
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert len(copied) == 2
    assert copied[-1] == (str(previous.pk), str(eval_task.id))
