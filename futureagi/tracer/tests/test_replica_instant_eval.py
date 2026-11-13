"""Instant eval summaries, row backfill and retries against a lagging replica.

Every test opts the nine TH-3119 models in to the real read router and uses
``tfc.tests.lagging_replica``: a second PostgreSQL connection frozen in a
REPEATABLE READ snapshot. Rows written after the freeze are on the primary
only, as on a streaming standby that has not applied them yet. The run, its
questions and their links are written before the freeze, as they replicate
long before a worker settles an answer.

Covered: a finished run's failure reason (D15), the rows a plain eval task's
first Instant read stores (D24), and retrying failed answers while the run
runs (D25) or with a failure only the primary has (D26), with the tenant,
entitlement and credit checks in front of them.

Only external boundaries are replaced: the entitlement decision, billing's
credit check, the ClickHouse view and span reader, and Temporal (workflow
start, pause signal and whether a workflow still drains the run). ORM
routing, transactions, the answers projection and the stored rows are real.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from rest_framework import status

from accounts.models.organization import Organization
from model_hub.models.ai_model import AIModel
from tfc.capabilities import service as capability_service
from tfc.capabilities.service import CapabilityDecision
from tfc.routers import force_primary
from tfc.tests.lagging_replica import lagging_replica  # noqa: F401  (fixture)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.instant_eval import InstantEvalAnswer, InstantEvalRow, InstantEvalRun
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, EvalTargetType
from tracer.models.project import Project
from tracer.selectors.eval_tasks.row_resolver import resolve_desired_rows
from tracer.services.eval_tasks import entries as entries_module
from tracer.services.eval_tasks.entries import mark_terminal, materialize_pending
from tracer.tests.test_instant_eval_scale import (
    SUMMARY,
    _control,
    _credit_cap,
    _question,
    _start,
    _summary,
)
from tracer.tests.test_instant_eval_scale import billed as billed
from tracer.tests.test_instant_eval_scale import fake_view as fake_view
from tracer.tests.test_instant_eval_scale import (
    instant_eval_enabled as instant_eval_enabled,
)
from tracer.tests.test_instant_eval_scale import started as started
from tracer.tests.test_instant_eval_scale import yes_no as yes_no
from tracer.views import instant_eval_scale

pytestmark = pytest.mark.django_db(transaction=True)

TASK_URL = "/tracer/eval-task/"
TIMED_OUT = "TypeSafe didn't respond in time. Try again."


@pytest.fixture(autouse=True)
def disable_external_mirrors(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")


@pytest.fixture(autouse=True)
def fresh_cache():
    """Summaries are cached by task id; start and end every test empty."""
    cache.clear()
    yield
    cache.clear()


def _instant_run(auth_client, project, configs, rows):
    """An Ask-page run of ``rows`` sessions whose rows are stored, as its
    workflow's first step stores them."""
    response = _start(auth_client, project, configs, sample_size=rows)
    assert response.status_code == status.HTTP_200_OK, response.content
    task = EvalTask.objects.get(id=response.json()["result"]["task_id"])
    ids = list(resolve_desired_rows(task).matched_ids)
    assert len(ids) == rows
    assert InstantEvalRow.objects.filter(task_id=task.id).count() == rows
    return task, ids


def _claimed(task, config, session_id):
    """An entry a worker has claimed and is answering."""
    return EvalLogger.objects.create(
        target_type=EvalTargetType.SESSION,
        trace_session_id=session_id,
        eval_task_id=str(task.id),
        custom_eval_config=config,
        status=EvalEntryStatus.RUNNING,
    )


def _settle(entry, entry_status, **fields):
    """The worker's terminal write: the entry on the primary, and its answer
    in the run's answers table (``mark_terminal`` copies it over)."""
    assert mark_terminal(
        entry, entry_status, config_hash="h", epoch=entry.updated_at, **fields
    )


def _primary(entry):
    return EvalLogger.all_objects.using("default").get(pk=entry.pk)


def _replica(replica, entry):
    return EvalLogger.all_objects.using(replica.alias).get(pk=entry.pk)


def _answers(task):
    return InstantEvalAnswer.objects.filter(task_id=task.id).count()


def _running(task):
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.RUNNING)


def _workflow_drains(monkeypatch):
    """The run's Temporal workflow is alive and draining it."""
    monkeypatch.setattr(instant_eval_scale, "_workflow_gone", lambda task: False)


def _foreign_project():
    other = Organization.objects.create(name="Elsewhere")
    return getattr(Project, "no_workspace_objects", Project.objects).create(
        name="Elsewhere",
        organization=other,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@contextmanager
def _instant_eval_denied(monkeypatch):
    """A plan or license without Instant eval, for the requests inside."""
    allowed = capability_service.check

    def check(feature_id, **kwargs):
        if feature_id == "instant_eval":
            return CapabilityDecision(
                allowed=False, feature_id=feature_id, reason_code="NOT_IN_PLAN"
            )
        return allowed(feature_id, **kwargs)

    monkeypatch.setattr(capability_service, "check", check)
    try:
        yield
    finally:
        monkeypatch.setattr(capability_service, "check", allowed)


# -- D15: a finished run's failure reason --------------------------------------


def test_d15_finished_run_names_the_failure_settled_after_freeze(
    lagging_replica,  # noqa: F811
    auth_client,
    observe_project,
    yes_no,  # noqa: F811
    fake_view,  # noqa: F811
    started,  # noqa: F811
):
    """Every row was skipped for missing input, and the run was marked
    completed through the eval-task API before the replica applied those
    writes. The summary counts the failures on the primary; it must also say
    why, and must not keep a reason-less summary for a day."""
    task, ids = _instant_run(auth_client, observe_project, [yes_no], 2)
    entries = [_claimed(task, yes_no, session_id) for session_id in ids]
    lagging_replica.freeze()

    for entry in entries:
        _settle(entry, EvalEntryStatus.SKIPPED, skipped_reason="missing input")
    completed = auth_client.patch(
        f"{TASK_URL}{task.id}/", {"status": EvalTaskStatus.COMPLETED}, format="json"
    )
    assert completed.status_code == status.HTTP_200_OK, completed.content
    assert EvalTask.objects.get(id=task.id).status == EvalTaskStatus.COMPLETED
    assert _answers(task) == 2
    assert _replica(lagging_replica, entries[0]).status == EvalEntryStatus.RUNNING

    summary = _summary(auth_client, observe_project, task)
    assert (summary["status"], summary["answered"], summary["failed"]) == (
        "completed",
        0,
        2,
    )
    assert summary["failure"] == {
        "message": "missing input",
        "every": True,
        "ai_providers": False,
    }

    # Caught up: the summary kept for the finished run still says why.
    lagging_replica.thaw()
    assert _summary(auth_client, observe_project, task) == summary


# -- D24: the rows a plain eval task's first Instant read stores ---------------


def test_d24_first_read_of_a_plain_task_stores_rows_materialized_after_freeze(
    lagging_replica,  # noqa: F811
    auth_client,
    observe_project,
    yes_no,  # noqa: F811
    fake_view,  # noqa: F811
    started,  # noqa: F811
    monkeypatch,
):
    """A historical task asked through the plain eval-task API with Instant
    questions, no run marker and no row ids: its first Instant read stores
    the rows its entries name and prepares its run from them, once."""
    created = auth_client.post(
        TASK_URL,
        {
            "project": str(observe_project.id),
            "name": "Plain Instant task",
            "run_type": "historical",
            "row_type": "sessions",
            "spans_limit": 100,
            "sampling_rate": 100.0,
            "evals": [str(yes_no.id)],
        },
        format="json",
    )
    assert created.status_code == status.HTTP_200_OK, created.content
    task = EvalTask.objects.get(id=created.data["result"]["id"])
    assert not (task.filters or {}).get("instant_eval_run")
    assert not InstantEvalRun.objects.filter(eval_task=task).exists()
    lagging_replica.freeze()

    # The task's workflow materializes its two sessions on the primary.
    monkeypatch.setattr(
        entries_module, "get_reader", lambda: SimpleNamespace(close=lambda: None)
    )
    sessions = fake_view.ids[:2]
    assert materialize_pending(task, resolved_row_ids=sessions) == 2
    assert not EvalLogger.objects.using(lagging_replica.alias).filter(
        eval_task_id=str(task.id)
    )

    # Another organization's project can't read (and so prepare) the run.
    foreign = auth_client.get(
        SUMMARY,
        {
            "project_id": str(_foreign_project().id),
            "eval_task_id": str(task.id),
            "tz": "UTC",
        },
    )
    assert foreign.status_code == status.HTTP_404_NOT_FOUND
    assert not InstantEvalRun.objects.filter(eval_task=task).exists()

    def prepared():
        summary = _summary(auth_client, observe_project, task)
        run = InstantEvalRun.objects.get(eval_task=task)
        rows = set(
            InstantEvalRow.objects.filter(task_id=task.id).values_list(
                "row_id", flat=True
            )
        )
        return (
            rows,
            run.population,
            run.rows_requested,
            summary["total"],
            summary["pending"],
        )

    expected = (set(sessions), 2, 2, 2, 2)
    assert prepared() == expected

    # Caught up and counted again: the run was prepared from both rows.
    lagging_replica.thaw()
    cache.clear()
    assert prepared() == expected


# -- R4-3: a plain Instant task read seconds after it was created -------------


def test_r4_3_plain_instant_task_created_after_freeze_loads_with_its_question(
    lagging_replica,  # noqa: F811
    auth_client,
    observe_project,
    yes_no,  # noqa: F811
    fake_view,  # noqa: F811
    started,  # noqa: F811
    monkeypatch,
):
    """A historical task asked through the plain eval-task API with an
    Instant question, read before the replica has its question link: the
    summary loads (no 404) and counts that question from the first poll."""
    lagging_replica.freeze()
    created = auth_client.post(
        TASK_URL,
        {
            "project": str(observe_project.id),
            "name": "Plain Instant task",
            "run_type": "historical",
            "row_type": "sessions",
            "spans_limit": 100,
            "sampling_rate": 100.0,
            "evals": [str(yes_no.id)],
        },
        format="json",
    )
    assert created.status_code == status.HTTP_200_OK, created.content
    task = EvalTask.objects.get(id=created.data["result"]["id"])
    assert not (task.filters or {}).get("instant_eval_run")
    assert not CustomEvalConfig.objects.filter(eval_tasks=task).exists()

    # The task's workflow materializes its two sessions on the primary, as
    # its reconcile pass does.
    monkeypatch.setattr(
        entries_module, "get_reader", lambda: SimpleNamespace(close=lambda: None)
    )
    sessions = fake_view.ids[:2]
    with force_primary():
        assert materialize_pending(task, resolved_row_ids=sessions) == 2

    summary = _summary(auth_client, observe_project, task)
    assert list(summary["counts"]) == [str(yes_no.id)]
    run = InstantEvalRun.objects.get(eval_task=task)
    assert (run.questions, summary["total"]) == (1, 2)


# -- R5: starting a run with a question saved a moment ago --------------------


def test_r5_start_with_a_question_saved_after_freeze(
    lagging_replica,  # noqa: F811
    auth_client,
    observe_project,
    eval_template,
    fake_view,  # noqa: F811
    started,  # noqa: F811
):
    """The Ask page saves a new question and starts the run straight away:
    the start finds it, not 'Those questions aren't saved in this project.'"""
    lagging_replica.freeze()
    question = _question(observe_project, eval_template, "Was the refund issued?")
    assert not CustomEvalConfig.objects.filter(pk=question.pk).exists()

    response = _start(auth_client, observe_project, [question], sample_size=5)

    assert response.status_code == status.HTTP_200_OK, response.content
    task = EvalTask.objects.get(id=response.json()["result"]["task_id"])
    assert list(
        CustomEvalConfig.objects.using("default")
        .filter(eval_tasks=task)
        .values_list("id", flat=True)
    ) == [question.id]
    assert len(started) == 1


# -- D25: retrying a failure the replica has not applied -----------------------


def test_d25_retry_requeues_a_failure_settled_after_freeze_while_the_run_runs(
    lagging_replica,  # noqa: F811
    auth_client,
    observe_project,
    yes_no,  # noqa: F811
    fake_view,  # noqa: F811
    started,  # noqa: F811
    monkeypatch,
):
    task, ids = _instant_run(auth_client, observe_project, [yes_no], 1)
    _running(task)
    entry = _claimed(task, yes_no, ids[0])
    _workflow_drains(monkeypatch)
    credits = _credit_cap(monkeypatch, left=10.0)
    lagging_replica.freeze()

    _settle(entry, EvalEntryStatus.ERRORED, error=True, error_message=TIMED_OUT)
    assert _replica(lagging_replica, entry).status == EvalEntryStatus.RUNNING
    assert _answers(task) == 1

    # Neither another organization's project nor an org without Instant eval
    # reaches the failure.
    foreign = _control(auth_client, _foreign_project(), task, "retry_failed")
    assert foreign.status_code == status.HTTP_404_NOT_FOUND, foreign.content
    with _instant_eval_denied(monkeypatch):
        denied = _control(auth_client, observe_project, task, "retry_failed")
    assert denied.status_code == status.HTTP_402_PAYMENT_REQUIRED, denied.content
    assert _primary(entry).status == EvalEntryStatus.ERRORED
    assert credits == []

    before = len(started)
    retried = _control(auth_client, observe_project, task, "retry_failed")
    assert retried.status_code == status.HTTP_200_OK, retried.content
    assert retried.json()["result"]["retried"] == 1
    stored = _primary(entry)
    assert (stored.status, stored.error, stored.error_message) == (
        EvalEntryStatus.PENDING,
        False,
        None,
    )
    assert credits == [("jev_evaluator", 0.06)]
    assert _answers(task) == 0
    # The workflow still draining the run claims the requeued answer itself.
    assert len(started) == before
    assert EvalTask.objects.get(id=task.id).status == EvalTaskStatus.RUNNING


# -- D26: a retry that prices, requeues and forgets every failure --------------


def _two_failures_one_replicated(replica, auth_client, project, config):
    """A running two-row run: row A's failure has replicated; row B's worker
    failed it after the freeze, so only the primary has it. Both answers are
    in the run's answers table."""
    task, ids = _instant_run(auth_client, project, [config], 2)
    _running(task)
    a, b = (_claimed(task, config, session_id) for session_id in ids)
    _settle(a, EvalEntryStatus.ERRORED, error=True, error_message=TIMED_OUT)
    replica.freeze()

    _settle(b, EvalEntryStatus.ERRORED, error=True, error_message=TIMED_OUT)
    assert [_replica(replica, e).status for e in (a, b)] == [
        EvalEntryStatus.ERRORED,
        EvalEntryStatus.RUNNING,
    ]
    assert _answers(task) == 2
    return task, (a, b)


def test_d26_retry_is_priced_for_a_failure_only_the_primary_has(
    lagging_replica,  # noqa: F811
    auth_client,
    observe_project,
    yes_no,  # noqa: F811
    fake_view,  # noqa: F811
    started,  # noqa: F811
    monkeypatch,
):
    """The retry is two answers, so 0.1 credits (one answer is 0.06) refuse
    it, and nothing is requeued or forgotten."""
    _workflow_drains(monkeypatch)
    task, entries = _two_failures_one_replicated(
        lagging_replica, auth_client, observe_project, yes_no
    )

    credits = _credit_cap(monkeypatch, left=0.1)
    refused = _control(auth_client, observe_project, task, "retry_failed")
    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.content
    assert credits == [("jev_evaluator", 0.12)]
    assert [_primary(e).status for e in entries] == [EvalEntryStatus.ERRORED] * 2
    assert _answers(task) == 2


def test_d26_retry_forgets_both_answers_and_resume_prices_both_rows(
    lagging_replica,  # noqa: F811
    auth_client,
    observe_project,
    yes_no,  # noqa: F811
    fake_view,  # noqa: F811
    started,  # noqa: F811
    monkeypatch,
):
    """Both failures are requeued and both settled answers dropped, so
    pausing and resuming before any summary repairs the answers prices both
    rows again."""
    _workflow_drains(monkeypatch)
    task, entries = _two_failures_one_replicated(
        lagging_replica, auth_client, observe_project, yes_no
    )

    credits = _credit_cap(monkeypatch, left=10.0)
    retried = _control(auth_client, observe_project, task, "retry_failed")
    assert retried.status_code == status.HTTP_200_OK, retried.content
    assert retried.json()["result"]["retried"] == 2
    assert [_primary(e).status for e in entries] == [EvalEntryStatus.PENDING] * 2
    assert _answers(task) == 0
    assert credits == [("jev_evaluator", 0.12)]

    # Paused and resumed before a summary could repair the answers: resuming
    # prices both rows still waiting for an answer.
    paused = _control(auth_client, observe_project, task, "pause")
    assert paused.status_code == status.HTTP_200_OK, paused.content
    credits = _credit_cap(monkeypatch, left=0.1)
    short = _control(auth_client, observe_project, task, "resume")
    assert short.status_code == status.HTTP_403_FORBIDDEN, short.content
    assert credits == [("jev_evaluator", 0.12)]
    assert EvalTask.objects.get(id=task.id).status == EvalTaskStatus.PAUSED

    credits = _credit_cap(monkeypatch, left=10.0)
    before = len(started)
    resumed = _control(auth_client, observe_project, task, "resume")
    assert resumed.status_code == status.HTTP_200_OK, resumed.content
    assert credits == [("jev_evaluator", 0.12)]
    assert len(started) == before + 1
