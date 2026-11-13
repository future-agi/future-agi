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
    # The message says what happened: the previous result is gone.
    assert "previous result is kept" not in str(response.data)
    assert "was not kept" in str(response.data)
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


# -- last round: overlapping Recalculates, a request killed mid-eval ----------


def _recalculation_rows():
    from tracer.models.observation_span import EvalRecalculation

    return EvalRecalculation.objects.using("default")


def _snapshot(span, config):
    """Every result of the span and config, deleted or not, as stored."""
    return sorted(
        (str(row.pk), row.status, row.deleted)
        for row in EvalLogger.all_objects.using("default").filter(
            observation_span=span, custom_eval_config=config
        )
    )


def test_recalculate_while_another_runs_is_refused(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
):
    """Request A has retired P and is running its replacement. Request B finds
    that RUNNING replacement as the live result: it is refused and changes
    nothing, so A's run stays the only one and P can still come back."""
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )
    seen = {}

    def _second_request_meanwhile(entry, **_kwargs):
        seen["before"] = _snapshot(observation_span, custom_eval_config)
        seen["response"] = _recalculate(
            auth_client, observation_span, custom_eval_config, feedback
        )
        seen["after"] = _snapshot(observation_span, custom_eval_config)
        # A's eval then errors, as run_entry records it.
        EvalLogger.objects.using("default").filter(pk=entry.pk).update(
            status=EvalEntryStatus.ERRORED
        )
        return EvalEntryStatus.ERRORED

    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry", _second_request_meanwhile
    )

    first = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    second = seen["response"]
    assert second.status_code == 400
    assert "already running" in str(second.data)
    assert seen["after"] == seen["before"]
    assert len(seen["before"]) == 2  # P retired, A running; no B entry
    # A failed afterwards and put P back: the refused B did not strand it.
    assert first.status_code == 400
    assert "previous result is kept" in str(first.data)
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        previous.pk
    ]
    assert not _recalculation_rows().exists()


@pytest.mark.parametrize("status", [EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING])
def test_recalculate_of_a_result_the_task_is_evaluating_is_refused(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
    status,
):
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )
    EvalLogger.objects.using("default").filter(pk=previous.pk).update(status=status)
    before = _snapshot(observation_span, custom_eval_config)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "still being evaluated" in str(response.data)
    assert _snapshot(observation_span, custom_eval_config) == before
    assert stub_run_eval.requests == []


def test_result_requeued_between_the_check_and_the_retirement_is_not_replaced(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
):
    """The race fence: the view reads a completed result, then the engine
    requeues it (Edit -> Save of a stale config) before Recalculate retires
    it. The retirement is conditional on the result still being terminal, so
    nothing is replaced."""
    import tracer.views.observation_span as view

    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )
    real_properties = view.get_mixpanel_properties

    def _requeued_meanwhile(*args, **kwargs):
        EvalLogger.objects.using("default").filter(pk=previous.pk).update(
            status=EvalEntryStatus.PENDING
        )
        return real_properties(*args, **kwargs)

    monkeypatch.setattr(view, "get_mixpanel_properties", _requeued_meanwhile)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "still being evaluated" in str(response.data)
    assert _snapshot(observation_span, custom_eval_config) == [
        (str(previous.pk), EvalEntryStatus.PENDING, False)
    ]
    assert stub_run_eval.requests == []
    assert not _recalculation_rows().exists()


def test_concurrent_begin_on_the_same_result_lets_one_through(
    replica,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
):
    """Two requests that both read P as terminal: the second retirement finds
    P already retired by the first and is refused, adding no entry."""
    from tracer.services.eval_tasks.recalculate import (
        ALREADY_RUNNING,
        RecalculationRefused,
        begin_recalculation,
    )

    previous, _feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )
    stale_copy = EvalLogger.objects.using("default").get(pk=previous.pk)

    entry = begin_recalculation(previous)
    with pytest.raises(RecalculationRefused) as refused:
        begin_recalculation(stale_copy)

    assert refused.value.message == ALREADY_RUNNING
    assert _snapshot(observation_span, custom_eval_config) == sorted(
        [
            (str(previous.pk), EvalEntryStatus.COMPLETED, True),
            (str(entry.pk), EvalEntryStatus.RUNNING, False),
        ]
    )
    assert list(_recalculation_rows().values_list("replacement_id", flat=True)) == [
        entry.pk
    ]


def test_failed_recompute_never_brings_back_an_unfinished_entry(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """Put-back only restores a terminal result. If the retired previous
    result is not terminal any more, it stays retired and the user is told
    it was not kept."""
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    def _errored_and_previous_unfinished(entry, **_kwargs):
        EvalLogger.all_objects.using("default").filter(pk=previous.pk).update(
            status=EvalEntryStatus.RUNNING
        )
        EvalLogger.objects.using("default").filter(pk=entry.pk).update(
            status=EvalEntryStatus.ERRORED
        )
        return EvalEntryStatus.ERRORED

    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry",
        _errored_and_previous_unfinished,
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "was not kept" in str(response.data)
    assert _stored(previous.pk).deleted is True
    assert not _live(observation_span, custom_eval_config).exists()


class _WorkerKilled(BaseException):
    """What the request sees of a SIGKILL or an OOM: nothing after it runs.
    A BaseException, so no ``except Exception`` restores anything."""


def _killed_mid_eval(monkeypatch):
    def _killed(entry, **_kwargs):
        raise _WorkerKilled()

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _killed)


def _age_recalculation(seconds):
    """Move the left-behind recalculation and its replacement into the past."""
    from datetime import timedelta

    from django.utils import timezone

    then = timezone.now() - timedelta(seconds=seconds)
    rows = list(_recalculation_rows())
    _recalculation_rows().update(started_at=then)
    EvalLogger.all_objects.using("default").filter(
        pk__in=[row.replacement_id for row in rows]
    ).update(updated_at=then)
    return rows


def _sweep():
    from tracer.tasks.eval_task_sweeper import sweep_stale_recalculations

    return sweep_stale_recalculations._original_func()


def _crashed_recalculation(
    monkeypatch, auth_client, user, replica, eval_task, span, config
):
    """A Recalculate whose worker died mid-eval, an hour and a half ago."""
    from tracer.services.eval_tasks.recalculate import RECOVER_AFTER_SECONDS

    previous, feedback = _write_result(replica, eval_task, span, config, user)
    issue, member = _issue_with(previous)
    _killed_mid_eval(monkeypatch)
    with pytest.raises(_WorkerKilled):
        _recalculate(auth_client, span, config, feedback)
    (row,) = _age_recalculation(RECOVER_AFTER_SECONDS + 60)
    assert _stored(previous.pk).deleted is True
    assert _stored(row.replacement_id).status == EvalEntryStatus.RUNNING
    return previous, row.replacement_id, issue, member


def test_sweep_restores_a_result_whose_recalculate_was_killed_mid_eval(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    previous, replacement_id, issue, member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    # The result was retired, but kept its Feed membership while it could
    # still come back.
    assert _membership(member).deleted is False

    counts = _sweep()

    assert counts["restored"] == 1
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        previous.pk
    ]
    assert _stored(replacement_id).deleted is True
    assert _membership(member).deleted is False
    issue.refresh_from_db()
    assert (issue.deleted, issue.error_count) == (False, 1)
    assert not _recalculation_rows().exists()
    # Idempotent: a second tick finds nothing.
    assert _sweep()["restored"] == 0


def test_sweep_keeps_a_replacement_that_completed_before_the_crash(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """The run finished but the request died before it retired the previous
    result's Feed membership: the sweep keeps the replacement and retires
    the membership, so the issue does not count a deleted eval."""
    previous, replacement_id, issue, member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    EvalLogger.objects.using("default").filter(pk=replacement_id).update(
        status=EvalEntryStatus.COMPLETED
    )

    counts = _sweep()

    assert counts["kept"] == 1
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        replacement_id
    ]
    assert _stored(previous.pk).deleted is True
    assert _membership(member).deleted is True
    issue.refresh_from_db()
    assert issue.error_count == 0
    assert not _recalculation_rows().exists()


def test_sweep_after_delete_and_rerun_leaves_the_result_retired(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """Delete & rerun retired the replacement after the crash. The previous
    result stays retired with it, and leaves its Feed issue."""
    from tracer.services.eval_tasks.entries import soft_delete_live

    previous, replacement_id, _issue, member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    soft_delete_live(eval_task)

    counts = _sweep()

    assert counts["left_retired"] == 1
    assert not _live(observation_span, custom_eval_config).exists()
    assert _membership(member).deleted is True
    assert not _recalculation_rows().exists()


@pytest.mark.parametrize("state", ["young", "requeued", "reclaimed_recently"])
def test_sweep_leaves_a_recalculation_that_may_still_be_running(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
    state,
):
    """Too young to be dead, or handed to the task's engine by its reaper
    (PENDING, or RUNNING under a fresh claim): the sweep waits."""
    from django.utils import timezone

    previous, replacement_id, _issue, member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    replacement = EvalLogger.objects.using("default").filter(pk=replacement_id)
    if state == "young":
        _age_recalculation(60)
    elif state == "requeued":
        replacement.update(status=EvalEntryStatus.PENDING)
    else:
        replacement.update(updated_at=timezone.now())
    before = _snapshot(observation_span, custom_eval_config)

    counts = _sweep()

    assert counts["restored"] == counts["kept"] == counts["left_retired"] == 0
    assert _snapshot(observation_span, custom_eval_config) == before
    assert _membership(member).deleted is False
    assert _recalculation_rows().count() == 1


def test_late_write_of_a_run_the_sweep_gave_up_on_is_refused(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """A run still alive past the bound cannot land on the retired
    replacement: its terminal write is fenced on the entry being live."""
    from tracer.services.eval_tasks.entries import mark_terminal

    previous, replacement_id, _issue, _member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    stale = EvalLogger.all_objects.using("default").get(pk=replacement_id)
    _sweep()

    landed = mark_terminal(
        stale, EvalEntryStatus.COMPLETED, config_hash="late", epoch=stale.updated_at
    )

    assert landed is False
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        previous.pk
    ]


def test_the_recalculation_sweep_is_scheduled():
    from tfc.temporal.schedules.tracer import TRACER_SCHEDULES

    (schedule,) = [
        s for s in TRACER_SCHEDULES if s.schedule_id == "sweep-stale-recalculations"
    ]
    assert schedule.activity_name == "sweep_stale_recalculations"


# -- review follow-ups of the last round --------------------------------------


def test_replacement_the_engine_took_over_is_left_to_it(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """The task's reaper requeued the replacement while the request's run was
    slow, and the engine owns it now. The failed request must not retire the
    engine's entry and bring back the old answer; the sweep finishes the
    recalculation once the engine's run has."""
    from tracer.services.eval_tasks.run_entry import RECLAIMED

    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    def _requeued_by_the_reaper(entry, **_kwargs):
        EvalLogger.objects.using("default").filter(pk=entry.pk).update(
            status=EvalEntryStatus.PENDING, attempts=1
        )
        return RECLAIMED

    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry", _requeued_by_the_reaper
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "task is evaluating this result again" in str(response.data)
    (replacement,) = _live(observation_span, custom_eval_config)
    assert (replacement.status, replacement.attempts) == (EvalEntryStatus.PENDING, 1)
    assert _stored(previous.pk).deleted is True
    assert _recalculation_rows().count() == 1

    # The engine completes it; the sweep then finishes the recalculation.
    EvalLogger.objects.using("default").filter(pk=replacement.pk).update(
        status=EvalEntryStatus.COMPLETED
    )
    _age_recalculation(7200)
    assert _sweep()["kept"] == 1
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        replacement.pk
    ]
    assert not _recalculation_rows().exists()


def test_kept_instant_answer_is_copied_back_before_the_recalculation_closes(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_cost_log,
    monkeypatch,
):
    """The copy of the restored answer is committed with the removal of the
    EvalRecalculation row: a crash between the two cannot leave the failed
    run's answer showing with nothing left to correct it."""
    from tracer.services.eval_tasks import entries

    journal_at_copy = []

    def _copy(entry_id, task_id):
        journal_at_copy.append(
            (str(entry_id), _recalculation_rows().filter(previous_id=entry_id).exists())
        )

    monkeypatch.setattr(entries, "_record_instant_answer", _copy)
    _engine_down(monkeypatch)
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert journal_at_copy[-1] == (str(previous.pk), True)
    assert not _recalculation_rows().exists()


def test_sweep_retires_memberships_at_once_after_delete_and_rerun(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """A crashed recalculation whose replacement Delete & rerun retired has
    nothing left to wait for: the next tick retires the previous result's
    Feed membership, however young the recalculation is."""
    from tracer.services.eval_tasks.entries import soft_delete_live

    previous, _replacement_id, issue, member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    _age_recalculation(60)
    soft_delete_live(eval_task)

    counts = _sweep()

    assert counts["left_retired"] == 1
    assert _membership(member).deleted is True
    issue.refresh_from_db()
    assert issue.error_count == 0
    assert not _recalculation_rows().exists()


def test_refused_recalculate_leaves_the_feedback_as_it_was(
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
    Feedback.objects.filter(pk=feedback.pk).update(action_type="retune")
    EvalLogger.objects.using("default").filter(pk=previous.pk).update(
        status=EvalEntryStatus.RUNNING
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "still being evaluated" in str(response.data)
    assert Feedback.objects.get(pk=feedback.pk).action_type == "retune"


def test_sweep_restores_a_requeued_replacement_of_a_paused_task(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """The reaper requeued the crashed request's replacement, then the task
    was paused: nothing will claim it. Once stale, the sweep puts the
    previous result back instead of leaving it retired for as long as the
    task stays paused."""
    from tracer.models.eval_task import EvalTask, EvalTaskStatus

    previous, replacement_id, issue, member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    EvalLogger.objects.using("default").filter(pk=replacement_id).update(
        status=EvalEntryStatus.PENDING, attempts=1
    )
    EvalTask.objects.filter(pk=eval_task.pk).update(status=EvalTaskStatus.PAUSED)

    counts = _sweep()

    assert counts["restored"] == 1
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        previous.pk
    ]
    assert _membership(member).deleted is False
    assert not _recalculation_rows().exists()
