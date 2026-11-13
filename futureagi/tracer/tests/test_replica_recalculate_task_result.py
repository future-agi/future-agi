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

TERMINAL = [
    EvalEntryStatus.COMPLETED,
    EvalEntryStatus.ERRORED,
    EvalEntryStatus.SKIPPED,
]


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


def _task_result(eval_task, span, config, status=EvalEntryStatus.COMPLETED, **fields):
    """A finished entry of ``eval_task``, as the engine leaves one."""
    return EvalLogger.objects.create(
        trace=span.trace,
        observation_span=span,
        custom_eval_config=config,
        eval_task_id=str(eval_task.id),
        status=status,
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


def _write_result(
    replica, eval_task, span, config, user, status=EvalEntryStatus.COMPLETED
):
    """The previous result and the user's feedback; written after the
    replica's snapshot, so only the primary has them."""
    if replica is not None:
        replica.freeze()
    previous = _task_result(eval_task, span, config, status=status)
    feedback = _feedback(user, span, config)
    if replica is not None:
        assert not EvalLogger.objects.filter(pk=previous.pk).exists()
    return previous, feedback


@pytest.mark.parametrize("previous_status", TERMINAL)
def test_recalculate_recomputes_a_task_result(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    monkeypatch,
    previous_status,
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
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
        user,
        status=previous_status,
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


@pytest.mark.parametrize("previous_status", TERMINAL)
def test_failed_recompute_of_a_task_result_keeps_it(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_cost_log,
    monkeypatch,
    previous_status,
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
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
        user,
        status=previous_status,
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "previous result is kept" in str(response.data)
    live = _live(observation_span, custom_eval_config)
    assert [row.pk for row in live] == [previous.pk]
    assert (live.get().status, live.get().eval_explanation) == (
        previous_status,
        "the previous answer",
    )


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


# -- round 9: results the task will never finish -----------------------------


def _set_task_status(eval_task, status):
    from tracer.models.eval_task import EvalTask

    EvalTask.objects.filter(pk=eval_task.pk).update(status=status)


def _not_draining():
    from tracer.models.eval_task import EvalTaskStatus

    return [EvalTaskStatus.PAUSED, EvalTaskStatus.FAILED, EvalTaskStatus.COMPLETED]


@pytest.mark.parametrize("task_status", _not_draining())
@pytest.mark.parametrize("status", [EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING])
def test_unfinished_result_of_a_task_that_will_not_finish_it_is_recalculated(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
    task_status,
    status,
):
    """A paused, failed or finished task never claims this entry again (the
    reconciler requeued it after a config edit, then the run stopped). Its
    result is recalculated instead of refused for good."""
    previous, feedback = _write_result(
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
        user,
        status=status,
    )
    _set_task_status(eval_task, task_status)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 200, response.data
    assert _stored(previous.pk).deleted is True
    (new,) = _live(observation_span, custom_eval_config)
    assert new.pk != previous.pk
    assert (new.status, new.eval_explanation) == (
        EvalEntryStatus.COMPLETED,
        "stubbed pass",
    )
    assert not _recalculation_rows().exists()
    assert Feedback.objects.get(pk=feedback.pk).action_type == "recalculate"


@pytest.mark.parametrize("task_status", _not_draining())
def test_failed_recompute_of_an_unfinished_result_says_there_was_none_to_keep(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_cost_log,
    monkeypatch,
    task_status,
):
    """An unfinished entry is never put back: there was no earlier result.
    The row is left with no live result (never two), and the user is told
    why."""
    _engine_down(monkeypatch)
    previous, feedback = _write_result(
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
        user,
        status=EvalEntryStatus.PENDING,
    )
    _set_task_status(eval_task, task_status)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert (
        "This result had not finished evaluating, so there was no earlier result "
        "to keep."
    ) in str(response.data)
    assert _stored(previous.pk).deleted is True
    assert _live(observation_span, custom_eval_config).count() == 0
    assert not _recalculation_rows().exists()


@pytest.mark.parametrize(
    "task_status", ["running", "pending"], ids=["task_running", "task_pending"]
)
def test_pending_result_of_a_draining_task_is_still_refused(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
    task_status,
):
    previous, feedback = _write_result(
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
        user,
        status=EvalEntryStatus.PENDING,
    )
    _set_task_status(eval_task, task_status)
    before = _snapshot(observation_span, custom_eval_config)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "still being evaluated" in str(response.data)
    assert _snapshot(observation_span, custom_eval_config) == before
    assert stub_run_eval.requests == []


@pytest.mark.parametrize("change", ["claimed", "requeued"])
def test_unfinished_result_changed_between_the_check_and_the_retirement_is_kept(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
    change,
):
    """The fence is the status and stamp the request read, not "terminal":
    a pending entry of a paused task that is claimed (resumed task) or
    re-stamped meanwhile is not replaced."""
    from django.utils import timezone

    import tracer.views.observation_span as view

    previous, feedback = _write_result(
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
        user,
        status=EvalEntryStatus.PENDING,
    )
    _set_task_status(eval_task, "paused")
    real_properties = view.get_mixpanel_properties

    def _changed_meanwhile(*args, **kwargs):
        update = {"updated_at": timezone.now()}
        if change == "claimed":
            update["status"] = EvalEntryStatus.RUNNING
        EvalLogger.objects.using("default").filter(pk=previous.pk).update(**update)
        return real_properties(*args, **kwargs)

    monkeypatch.setattr(view, "get_mixpanel_properties", _changed_meanwhile)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "has changed" in str(response.data)
    assert [
        (row.pk, row.deleted)
        for row in EvalLogger.all_objects.using("default").filter(
            observation_span=observation_span
        )
    ] == [(previous.pk, False)]
    assert stub_run_eval.requests == []
    assert not _recalculation_rows().exists()


# -- round 9: the engine's fence on the replacement ----------------------------


def test_run_that_raises_after_the_engine_took_the_replacement_hands_it_over(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """The request's run raised, but the engine had claimed the replacement
    meanwhile (RUNNING, attempts 1): it is the engine's run now, so the
    request leaves it live and the recalculation to the sweep."""
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    def _claimed_by_the_engine_then_raised(entry, **_kwargs):
        EvalLogger.objects.using("default").filter(pk=entry.pk).update(
            status=EvalEntryStatus.RUNNING, attempts=1
        )
        raise RuntimeError("worker lost its claim")

    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry",
        _claimed_by_the_engine_then_raised,
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    (replacement,) = _live(observation_span, custom_eval_config)
    assert (replacement.status, replacement.attempts) == (EvalEntryStatus.RUNNING, 1)
    assert _stored(previous.pk).deleted is True
    assert list(_recalculation_rows().values_list("replacement_id", flat=True)) == [
        replacement.pk
    ]


def test_errored_answer_that_did_not_land_hands_the_replacement_over(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    """The run answered ERRORED, but the row is PENDING (the engine requeued
    it before the terminal write): it is not this run's to retire."""
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    def _requeued_before_the_write(entry, **_kwargs):
        EvalLogger.objects.using("default").filter(pk=entry.pk).update(
            status=EvalEntryStatus.PENDING
        )
        return EvalEntryStatus.ERRORED

    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry", _requeued_before_the_write
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "task is evaluating this result again" in str(response.data)
    (replacement,) = _live(observation_span, custom_eval_config)
    assert (replacement.status, replacement.attempts) == (EvalEntryStatus.PENDING, 0)
    assert _recalculation_rows().count() == 1


def test_skipped_recompute_keeps_the_previous_result(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )

    def _skipped(entry, **_kwargs):
        EvalLogger.objects.using("default").filter(pk=entry.pk).update(
            status=EvalEntryStatus.SKIPPED
        )
        return EvalEntryStatus.SKIPPED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _skipped)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert "previous result is kept" in str(response.data)
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        previous.pk
    ]
    assert not _recalculation_rows().exists()


# -- round 9: the sweep ----------------------------------------------------------


def test_sweep_restores_after_a_crashed_replacement_errored(
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
    EvalLogger.objects.using("default").filter(pk=replacement_id).update(
        status=EvalEntryStatus.ERRORED
    )

    counts = _sweep()

    assert counts["restored"] == 1
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        previous.pk
    ]
    assert _stored(replacement_id).deleted is True
    assert _membership(member).deleted is False
    assert not _recalculation_rows().exists()


@pytest.mark.parametrize(
    "status,outcome",
    [
        (EvalEntryStatus.COMPLETED, "kept"),
        (EvalEntryStatus.ERRORED, "restored"),
        (EvalEntryStatus.SKIPPED, "restored"),
    ],
)
def test_sweep_finishes_an_answered_replacement_without_the_long_wait(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
    status,
    outcome,
):
    """A replacement that reached a terminal status has nothing left to
    wait for: a few minutes on, the sweep finishes it, so the previous
    result's Feed membership does not linger for 90 minutes."""
    from tracer.services.eval_tasks.recalculate import RECOVER_ANSWERED_AFTER_SECONDS

    previous, replacement_id, issue, member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    _age_recalculation(RECOVER_ANSWERED_AFTER_SECONDS + 30)
    EvalLogger.objects.using("default").filter(pk=replacement_id).update(status=status)

    counts = _sweep()

    assert counts[outcome] == 1
    assert _membership(member).deleted is (outcome == "kept")
    assert not _recalculation_rows().exists()


def test_sweep_waits_for_a_young_answered_replacement(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    previous, replacement_id, _issue, member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    _age_recalculation(10)
    EvalLogger.objects.using("default").filter(pk=replacement_id).update(
        status=EvalEntryStatus.COMPLETED
    )

    counts = _sweep()

    assert counts["kept"] == counts["waiting"] == 0
    assert _recalculation_rows().count() == 1
    assert _membership(member).deleted is False


@pytest.mark.parametrize(
    "task_status,outcome", [("running", "waiting"), ("paused", "restored")]
)
def test_sweep_leaves_an_engine_run_alone_while_its_task_drains(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
    task_status,
    outcome,
):
    """A replacement the engine claimed (RUNNING, attempts > 0) in a draining
    task may be a batch tail waiting on Temporal: it is not judged by age.
    Once the task stops draining, the bound applies."""
    previous, replacement_id, _issue, member = _crashed_recalculation(
        monkeypatch,
        auth_client,
        user,
        replica,
        eval_task,
        observation_span,
        custom_eval_config,
    )
    EvalLogger.objects.using("default").filter(pk=replacement_id).update(attempts=1)
    _set_task_status(eval_task, task_status)

    counts = _sweep()

    if outcome == "waiting":
        assert counts["restored"] == counts["kept"] == counts["left_retired"] == 0
        assert _stored(replacement_id).deleted is False
        assert _recalculation_rows().count() == 1
    else:
        assert counts["restored"] == 1
        assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
            previous.pk
        ]
        assert not _recalculation_rows().exists()


def _second_config(config, name="Second eval"):
    from tracer.models.custom_eval_config import CustomEvalConfig

    return CustomEvalConfig.objects.create(
        name=name,
        project=config.project,
        eval_template=config.eval_template,
        config=config.config,
        mapping=config.mapping,
        filters={},
    )


def test_sweep_batch_is_not_filled_by_recalculations_the_engine_holds(
    replica,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
):
    """Oldest first with a cap: a waiting row (PENDING in a draining task)
    must not take the slot of one the tick can finish."""
    from tracer.services.eval_tasks.recalculate import (
        RECOVER_AFTER_SECONDS,
        begin_recalculation,
        recover_stale_recalculations,
    )

    other_config = _second_config(custom_eval_config)
    eval_task.evals.add(other_config)
    held_previous = _task_result(eval_task, observation_span, custom_eval_config)
    dead_previous = _task_result(eval_task, observation_span, other_config)
    held = begin_recalculation(held_previous)
    dead = begin_recalculation(dead_previous)
    _age_recalculation(RECOVER_AFTER_SECONDS + 60)
    from datetime import timedelta

    from django.utils import timezone

    # The engine holds the older one: requeued, in a draining task.
    _recalculation_rows().filter(replacement_id=held.pk).update(
        started_at=timezone.now() - timedelta(days=1)
    )
    EvalLogger.objects.using("default").filter(pk=held.pk).update(
        status=EvalEntryStatus.PENDING, attempts=1
    )

    counts = recover_stale_recalculations(limit=1)

    assert counts["restored"] == 1
    assert _stored(dead.pk).deleted is True
    assert _stored(dead_previous.pk).deleted is False
    assert _stored(held.pk).deleted is False
    assert list(_recalculation_rows().values_list("replacement_id", flat=True)) == [
        held.pk
    ]


# -- round 9: a refused duplicate and the feedback's action type ---------------


def test_refused_duplicate_does_not_overwrite_the_winners_action_type(
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
    """B read the feedback, then A's whole Recalculate ran and won. B is
    refused; the feedback keeps A's "recalculate"."""
    import tracer.views.observation_span as view

    previous, feedback = _write_result(
        replica, eval_task, observation_span, custom_eval_config, user
    )
    Feedback.objects.filter(pk=feedback.pk).update(action_type="retune")
    real_properties = view.get_mixpanel_properties
    seen = {}

    def _a_runs_meanwhile(*args, **kwargs):
        if "a" not in seen:
            seen["a"] = None
            seen["a"] = _recalculate(
                auth_client, observation_span, custom_eval_config, feedback
            )
        return real_properties(*args, **kwargs)

    monkeypatch.setattr(view, "get_mixpanel_properties", _a_runs_meanwhile)

    b = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert seen["a"].status_code == 200, seen["a"].data
    assert b.status_code == 400
    assert Feedback.objects.get(pk=feedback.pk).action_type == "recalculate"
    assert _live(observation_span, custom_eval_config).count() == 1


# -- round 9: the reconciler drops a replacement the engine took over ----------


def _reconcile_dropping(eval_task):
    """A reconcile pass whose proof matched no row (every pending entry is
    out of scope); only the drop half of the pass matters here."""
    from django.db import transaction

    from tracer.models.eval_task import EvalTask
    from tracer.selectors.eval_tasks.row_resolver import ResolvedRowSet
    from tracer.services.eval_tasks.reconciler import _requeue_and_drop

    with transaction.atomic():
        task = EvalTask.objects.select_for_update().get(pk=eval_task.pk)
        return _requeue_and_drop(task, resolved=ResolvedRowSet((), (), True))


@pytest.mark.parametrize("reason", ["config_removed", "row_out_of_scope"])
def test_reconcile_dropping_a_requeued_replacement_puts_the_previous_back(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
    reason,
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
    EvalLogger.objects.using("default").filter(pk=replacement_id).update(
        status=EvalEntryStatus.PENDING, attempts=1
    )
    if reason == "config_removed":
        eval_task.evals.remove(custom_eval_config)

    _requeued, dropped = _reconcile_dropping(eval_task)

    assert dropped == 1
    assert _stored(replacement_id).deleted is True
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        previous.pk
    ]
    assert _membership(member).deleted is False
    assert not _recalculation_rows().exists()


def test_reconcile_dropping_a_replacement_of_a_deleted_config_keeps_it_retired(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    eval_task,
    monkeypatch,
):
    from tracer.models.custom_eval_config import CustomEvalConfig

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
    eval_task.evals.remove(custom_eval_config)
    CustomEvalConfig.all_objects.filter(pk=custom_eval_config.pk).update(deleted=True)

    _requeued, dropped = _reconcile_dropping(eval_task)

    assert dropped == 1
    assert not _live(observation_span, custom_eval_config).exists()
    assert _membership(member).deleted is True
    assert not _recalculation_rows().exists()


# -- round 9: Recalculate on a result with no eval task -------------------------


def _inline_result(span, config, **fields):
    """An inline Observe eval's result: no eval task."""
    return EvalLogger.objects.create(
        trace=span.trace,
        observation_span=span,
        custom_eval_config=config,
        eval_task_id=None,
        status=EvalEntryStatus.COMPLETED,
        output_bool=False,
        eval_explanation="the previous answer",
        **fields,
    )


def _write_inline_result(replica, span, config, user):
    if replica is not None:
        replica.freeze()
    previous = _inline_result(span, config)
    feedback = _feedback(user, span, config)
    return previous, feedback


def _new_inline_result(span, config, explanation="the new answer"):
    return EvalLogger.objects.using("default").create(
        trace=span.trace,
        observation_span=span,
        custom_eval_config=config,
        eval_task_id=None,
        status=EvalEntryStatus.COMPLETED,
        output_bool=True,
        eval_explanation=explanation,
    )


def _per_span_eval(monkeypatch, body):
    """Replace the per-span activity the view calls for results with no
    task; ``body`` gets (span_id, config_id) and returns its status."""

    def _activity(span_id, config_id, _task_id=None, _feedback_id=None):
        return body(span_id, config_id)

    monkeypatch.setattr(
        "tracer.views.observation_span.evaluate_observation_span_observe", _activity
    )


def test_untasked_recalculate_replaces_the_result(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    monkeypatch,
):
    previous, feedback = _write_inline_result(
        replica, observation_span, custom_eval_config, user
    )
    _issue, member = _issue_with(previous)
    _per_span_eval(
        monkeypatch,
        lambda *_: bool(_new_inline_result(observation_span, custom_eval_config)),
    )

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 200, response.data
    (new,) = _live(observation_span, custom_eval_config)
    assert new.eval_explanation == "the new answer"
    assert _stored(previous.pk).deleted is True
    assert _membership(member).deleted is True
    assert not _recalculation_rows().exists()


def test_untasked_double_submit_leaves_one_live_result(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    monkeypatch,
):
    """A is evaluating when B, a double-submit that read the same result,
    arrives. B's retirement is conditional on the result still being live,
    so B is refused and never evaluates; A's result is the only live one."""
    previous, feedback = _write_inline_result(
        replica, observation_span, custom_eval_config, user
    )
    seen = {"evaluations": 0}

    def _a_evaluates(*_args):
        seen["evaluations"] += 1
        if "b" not in seen:
            seen["b"] = None
            seen["b"] = _recalculate(
                auth_client, observation_span, custom_eval_config, feedback
            )
        return bool(_new_inline_result(observation_span, custom_eval_config))

    _per_span_eval(monkeypatch, _a_evaluates)

    a = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert a.status_code == 200, a.data
    assert seen["b"].status_code == 400
    assert "already running" in str(seen["b"].data)
    assert seen["evaluations"] == 1
    (new,) = _live(observation_span, custom_eval_config)
    assert new.eval_explanation == "the new answer"
    assert _stored(previous.pk).deleted is True
    assert not _recalculation_rows().exists()


def test_untasked_double_submit_read_before_the_retirement_is_refused(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    monkeypatch,
):
    """B read the live result before A retired it (the view's own read),
    then A ran to the end: B's conditional retirement changes nothing."""
    import tracer.views.observation_span as view

    previous, feedback = _write_inline_result(
        replica, observation_span, custom_eval_config, user
    )
    real_properties = view.get_mixpanel_properties
    seen = {}

    def _a_runs_meanwhile(*args, **kwargs):
        if "a" not in seen:
            seen["a"] = None
            seen["a"] = _recalculate(
                auth_client, observation_span, custom_eval_config, feedback
            )
        return real_properties(*args, **kwargs)

    monkeypatch.setattr(view, "get_mixpanel_properties", _a_runs_meanwhile)
    _per_span_eval(
        monkeypatch,
        lambda *_: bool(_new_inline_result(observation_span, custom_eval_config)),
    )

    b = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert seen["a"].status_code == 200, seen["a"].data
    assert b.status_code == 400
    assert _live(observation_span, custom_eval_config).count() == 1
    assert _stored(previous.pk).deleted is True


def test_untasked_failure_after_another_result_landed_does_not_bring_back_two(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    monkeypatch,
):
    """A's eval fails after another result for the span and config was
    written (another writer). Putting the previous result back would leave
    two live results: it stays retired, and leaves its Feed issue."""
    previous, feedback = _write_inline_result(
        replica, observation_span, custom_eval_config, user
    )
    _issue, member = _issue_with(previous)
    landed = {}

    def _other_lands_then_a_fails(*_args):
        landed["other"] = _new_inline_result(
            observation_span, custom_eval_config, "another writer"
        )
        raise RuntimeError("model provider unavailable")

    _per_span_eval(monkeypatch, _other_lands_then_a_fails)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        landed["other"].pk
    ]
    assert _stored(previous.pk).deleted is True
    assert _membership(member).deleted is True
    assert not _recalculation_rows().exists()


@pytest.mark.parametrize("how", ["raised", "returned"])
def test_untasked_recompute_that_writes_nothing_keeps_the_result(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    monkeypatch,
    how,
):
    previous, feedback = _write_inline_result(
        replica, observation_span, custom_eval_config, user
    )
    _issue, member = _issue_with(previous)

    def _nothing(*_args):
        if how == "raised":
            raise RuntimeError("model provider unavailable")
        return False

    _per_span_eval(monkeypatch, _nothing)

    response = _recalculate(auth_client, observation_span, custom_eval_config, feedback)

    assert response.status_code == 400
    if how == "returned":
        assert "previous result is kept" in str(response.data)
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        previous.pk
    ]
    assert _membership(member).deleted is False
    assert not _recalculation_rows().exists()


def _crashed_untasked(monkeypatch, auth_client, user, replica, span, config, *, wrote):
    previous, feedback = _write_inline_result(replica, span, config, user)
    issue, member = _issue_with(previous)
    written = {}

    def _killed(*_args):
        if wrote:
            written["new"] = _new_inline_result(span, config)
        raise _WorkerKilled()

    _per_span_eval(monkeypatch, _killed)
    with pytest.raises(_WorkerKilled):
        _recalculate(auth_client, span, config, feedback)
    (row,) = _recalculation_rows()
    assert row.replacement_id is None
    assert row.previous_id == previous.pk
    assert _stored(previous.pk).deleted is True
    assert _membership(member).deleted is False
    return previous, written.get("new"), issue, member


def _age_untasked(seconds):
    from datetime import timedelta

    from django.utils import timezone

    _recalculation_rows().update(started_at=timezone.now() - timedelta(seconds=seconds))


def test_sweep_restores_an_untasked_result_whose_recalculate_was_killed(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    monkeypatch,
):
    from tracer.services.eval_tasks.recalculate import RECOVER_AFTER_SECONDS

    previous, _new, issue, member = _crashed_untasked(
        monkeypatch,
        auth_client,
        user,
        replica,
        observation_span,
        custom_eval_config,
        wrote=False,
    )
    # Too young: the request may still be evaluating.
    assert _sweep()["restored"] == 0
    assert _recalculation_rows().count() == 1
    # Meanwhile a Recalculate says one is running, not that the span has no
    # eval.
    again = _recalculate(
        auth_client,
        observation_span,
        custom_eval_config,
        _feedback(user, observation_span, custom_eval_config),
    )
    assert again.status_code == 400
    assert "already running" in str(again.data)
    _age_untasked(RECOVER_AFTER_SECONDS + 60)

    counts = _sweep()

    assert counts["restored"] == 1
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [
        previous.pk
    ]
    assert _membership(member).deleted is False
    issue.refresh_from_db()
    assert (issue.deleted, issue.error_count) == (False, 1)
    assert not _recalculation_rows().exists()
    # The next Recalculate finds a result again.
    assert _sweep()["restored"] == 0


def test_sweep_keeps_an_untasked_result_written_before_the_crash(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    monkeypatch,
):
    from tracer.services.eval_tasks.recalculate import RECOVER_ANSWERED_AFTER_SECONDS

    previous, new, issue, member = _crashed_untasked(
        monkeypatch,
        auth_client,
        user,
        replica,
        observation_span,
        custom_eval_config,
        wrote=True,
    )
    _age_untasked(RECOVER_ANSWERED_AFTER_SECONDS + 30)

    counts = _sweep()

    assert counts["kept"] == 1
    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [new.pk]
    assert _stored(previous.pk).deleted is True
    assert _membership(member).deleted is True
    issue.refresh_from_db()
    assert issue.error_count == 0
    assert not _recalculation_rows().exists()


def test_sweep_does_not_restore_an_untasked_result_over_a_newer_one(
    replica,
    auth_client,
    user,
    observation_span,
    custom_eval_config,
    monkeypatch,
):
    """Crashed with nothing written, then another writer's result landed:
    the sweep keeps that one and retires the previous result for good."""
    from tracer.services.eval_tasks.recalculate import RECOVER_AFTER_SECONDS

    previous, _new, _issue, member = _crashed_untasked(
        monkeypatch,
        auth_client,
        user,
        replica,
        observation_span,
        custom_eval_config,
        wrote=False,
    )
    other = _new_inline_result(observation_span, custom_eval_config, "another")
    _age_untasked(RECOVER_AFTER_SECONDS + 60)

    _sweep()

    assert [row.pk for row in _live(observation_span, custom_eval_config)] == [other.pk]
    assert _membership(member).deleted is True
    assert not _recalculation_rows().exists()
