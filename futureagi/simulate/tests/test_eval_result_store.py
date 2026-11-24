"""The result store, and grading a run again keeps the verdict it replaces.

``run-new-evals`` puts a placeholder in each call's slot for the evals it grades
again. Before it does, it writes the verdict that was there into the result
table, marked as the run's own score that was replaced, with when and by whom.
So grading again never loses the original.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, transaction

from simulate.models import (
    CallExecution,
    SimulateEvalAsk,
    SimulateEvalResult,
    TestExecution,
)
from simulate.services.eval_result_store import readable_results
from simulate.services.run_regrade import RegradeAlreadyRunning, regrade_run_evals
from simulate.services.scoring_status import REASON_EVAL_RUN_CANCELLED
from simulate.tests import test_harness_environment_evals as env_evals
from simulate.tests import test_harness_environment_regrade as env_regrade
from simulate.tests.test_harness_environment_regrade import _mapped_eval, _regrade

# A built environment, a client in its workspace, one finished run of it, and
# a spy on the grading job, as the environment's regrade tests build them.
environment = env_regrade.environment
env_client = env_regrade.env_client
finished_run = env_regrade.finished_run
dispatch = env_regrade.dispatch

VERDICT = {
    "name": "Politeness",
    "output": "Passed",
    "output_type": "Pass/Fail",
    "reason": "the agent stayed polite",
    "status": "Completed",
}


def _replaced_rows(call):
    return list(
        SimulateEvalResult.objects.filter(
            call_execution=call, origin=SimulateEvalResult.Origin.REPLACED_OWN_SCORE
        ).order_by("created_at")
    )


@pytest.mark.django_db
def test_run_new_evals_keeps_the_verdict_it_replaces(
    env_client, environment, finished_run, workspace, user, dispatch
):
    cfg = _mapped_eval(environment)
    call = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={str(cfg.id): dict(VERDICT)},
    )

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)]},
    )

    assert response.status_code == 202, response.content
    call.refresh_from_db()
    assert call.eval_outputs[str(cfg.id)] == {"status": "pending"}
    [row] = _replaced_rows(call)
    assert row.replaced_verdict == VERDICT
    assert row.state == SimulateEvalResult.State.ANSWERED
    assert row.label == "Passed"
    assert row.score is None
    assert row.reason == "the agent stayed polite"
    assert row.eval_config_id == cfg.id
    assert row.question_key == str(cfg.id)
    assert row.question_definition["name"] == "Politeness"
    assert row.question_definition["eval_template_id"] == str(cfg.eval_template_id)
    assert row.question_definition["mapping"] == {"output": "transcript"}
    assert row.question_definition["as_of"] == "replacement"
    assert row.question_definition["not_the_grader"] is True
    assert row.question_version == ""
    assert row.ask_id is None
    assert row.replacement_id is not None
    assert row.replaced_at is not None
    assert row.replaced_by_id == user.id
    assert row.organization_id == environment.run_test.organization_id
    assert row.workspace_id == environment.run_test.workspace_id


@pytest.mark.django_db
def test_the_run_test_route_keeps_the_verdict_it_replaces_too(
    env_client, environment, finished_run, workspace, user, dispatch
):
    cfg = _mapped_eval(environment)
    call = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={str(cfg.id): dict(VERDICT)},
    )

    response = env_client.post(
        f"/simulate/run-tests/{environment.run_test_id}/run-new-evals/",
        {
            "test_execution_ids": [str(finished_run.id)],
            "eval_config_ids": [str(cfg.id)],
        },
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )

    assert response.status_code == 200, response.content
    [row] = _replaced_rows(call)
    assert row.replaced_verdict == VERDICT
    assert row.replaced_by_id == user.id


@pytest.mark.django_db
def test_an_eval_new_to_the_call_leaves_no_row(
    env_client, environment, finished_run, workspace, dispatch
):
    cfg = _mapped_eval(environment)
    call = env_evals._call(finished_run, metadata=env_evals._graded())

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)]},
    )

    assert response.status_code == 202, response.content
    assert _replaced_rows(call) == []


@pytest.mark.django_db
def test_only_the_evals_graded_again_are_kept(
    env_client, environment, finished_run, workspace, dispatch
):
    graded_again = _mapped_eval(environment)
    untouched = _mapped_eval(environment, name="Accuracy", template_name="acc_regrade")
    call = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={
            str(graded_again.id): dict(VERDICT),
            str(untouched.id): dict(VERDICT, name="Accuracy"),
        },
    )

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(graded_again.id)]},
    )

    assert response.status_code == 202, response.content
    assert [row.eval_config_id for row in _replaced_rows(call)] == [graded_again.id]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "stored,state,cause,label,score",
    [
        pytest.param(dict(VERDICT), "answered", "", "Passed", None, id="completed"),
        pytest.param(
            {
                "name": "Task",
                "output": "Passed",
                "status": "completed",
                "source": "harness",
            },
            "answered",
            "",
            "Passed",
            None,
            id="harness-score",
        ),
        pytest.param(
            {"name": "Tone", "output": 0.75, "reason": "mostly warm"},
            "answered",
            "",
            None,
            0.75,
            id="xl-row-with-no-status",
        ),
        pytest.param(
            {"name": "Tone", "output": None, "status": "Completed", "reason": ""},
            "unmapped",
            "no_output",
            None,
            None,
            id="completed-with-no-output",
        ),
        pytest.param(
            {
                "name": "Tone",
                "output": None,
                "error": "error",
                "status": "Failed",
                "reason": "boom",
            },
            "grading_error",
            "",
            None,
            None,
            id="failed",
        ),
        pytest.param(
            {
                "name": "Tone",
                "output": None,
                "error": "error",
                "status": "Failed",
                "denial": {"code": "insufficient_credits"},
                "reason": "Not enough credits.",
            },
            "cancelled",
            "denied",
            None,
            None,
            id="denied",
        ),
        pytest.param(
            {
                "name": "Tone",
                "status": "timed_out",
                "reason": "Scoring did not finish.",
            },
            "infrastructure_error",
            "timed_out",
            None,
            None,
            id="timed-out",
        ),
        pytest.param(
            {
                "name": "Tone",
                "status": "skipped",
                "skipped": True,
                "reason": REASON_EVAL_RUN_CANCELLED,
            },
            "cancelled",
            "run_cancelled",
            None,
            None,
            id="run-cancelled",
        ),
        pytest.param(
            {
                "name": "Tone",
                "status": "skipped",
                "skipped": True,
                "reason": "No transcript.",
            },
            "unmapped",
            "skipped",
            None,
            None,
            id="other-skip",
        ),
    ],
)
def test_each_kind_of_stored_verdict_is_kept_with_its_state(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    stored,
    state,
    cause,
    label,
    score,
):
    cfg = _mapped_eval(environment)
    call = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={str(cfg.id): dict(stored)},
    )

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)]},
    )

    assert response.status_code == 202, response.content
    [row] = _replaced_rows(call)
    assert row.replaced_verdict == stored
    assert row.state == state
    assert row.cause == cause
    assert row.label == label
    assert row.score == score


@pytest.mark.django_db
def test_a_pending_placeholder_is_not_a_verdict_and_gets_no_row(
    env_client, environment, finished_run, workspace, dispatch
):
    cfg = _mapped_eval(environment)
    call = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={str(cfg.id): {"status": "pending"}},
    )

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)]},
    )

    assert response.status_code == 202, response.content
    assert _replaced_rows(call) == []


def _kept(call, cfg, environment, **fields):
    return SimulateEvalResult.objects.create(
        origin=SimulateEvalResult.Origin.REPLACED_OWN_SCORE,
        replacement_id=fields.pop("replacement_id", uuid.uuid4()),
        call_execution=call,
        eval_config=cfg,
        question_key=str(cfg.id),
        state=SimulateEvalResult.State.ANSWERED,
        replaced_verdict=fields.pop("replaced_verdict", dict(VERDICT)),
        organization=environment.run_test.organization,
        workspace=environment.run_test.workspace,
        **fields,
    )


@pytest.mark.django_db
def test_a_failed_dispatch_keeps_no_row_because_the_verdict_is_back_on_the_call(
    env_client, environment, finished_run, workspace, dispatch
):
    dispatch.side_effect = TimeoutError("broker down")
    cfg = _mapped_eval(environment)
    call = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={str(cfg.id): dict(VERDICT)},
    )
    kept_from_before = _kept(
        call,
        cfg,
        environment,
        replaced_verdict={"name": "Politeness", "output": "Failed"},
    )

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)]},
    )

    assert response.status_code == 503, response.content
    call.refresh_from_db()
    assert call.eval_outputs[str(cfg.id)] == VERDICT
    # Only this request's rows go; an earlier replacement stays.
    assert [row.id for row in _replaced_rows(call)] == [kept_from_before.id]


@pytest.mark.django_db
def test_a_request_that_loses_the_race_writes_no_row(
    environment, finished_run, dispatch
):
    cfg = _mapped_eval(environment)
    call = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={str(cfg.id): dict(VERDICT)},
    )
    # The first request has already moved the run to EVALUATING.
    TestExecution.objects.filter(id=finished_run.id).update(
        status=TestExecution.ExecutionStatus.EVALUATING
    )

    with pytest.raises(RegradeAlreadyRunning):
        regrade_run_evals(
            environment.run_test,
            [str(finished_run.id)],
            {str(finished_run.id): TestExecution.ExecutionStatus.COMPLETED},
            [cfg.id],
            harness_run=True,
        )

    assert _replaced_rows(call) == []
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_grading_twice_keeps_both_replaced_verdicts_in_order(
    environment, finished_run, dispatch
):
    cfg = _mapped_eval(environment)
    call = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={str(cfg.id): dict(VERDICT)},
    )

    def grade_again():
        regrade_run_evals(
            environment.run_test,
            [str(finished_run.id)],
            {str(finished_run.id): TestExecution.ExecutionStatus.COMPLETED},
            [cfg.id],
            harness_run=True,
        )

    grade_again()
    # The grader writes its new verdict and the run finishes again.
    second = dict(VERDICT, output="Failed", reason="the agent was curt")
    CallExecution.objects.filter(id=call.id).update(eval_outputs={str(cfg.id): second})
    TestExecution.objects.filter(id=finished_run.id).update(
        status=TestExecution.ExecutionStatus.COMPLETED
    )
    grade_again()

    rows = _replaced_rows(call)
    assert [row.replaced_verdict for row in rows] == [VERDICT, second]
    assert rows[0].replacement_id != rows[1].replacement_id
    # Called by a job, not a person: nobody is named.
    assert [row.replaced_by_id for row in rows] == [None, None]


@pytest.mark.django_db
def test_results_go_with_their_call(environment, finished_run):
    cfg = _mapped_eval(environment)
    call = env_evals._call(finished_run)
    _kept(call, cfg, environment)

    CallExecution.all_objects.filter(id=call.id).delete()

    assert not SimulateEvalResult.all_objects.filter(call_execution_id=call.id).exists()


@pytest.mark.django_db
def test_an_eval_removed_later_leaves_the_kept_verdict_readable(
    environment, finished_run
):
    cfg = _mapped_eval(environment)
    call = env_evals._call(finished_run)
    row = _kept(call, cfg, environment, question_definition={"name": "Politeness"})

    type(cfg).all_objects.filter(id=cfg.id).delete()

    row.refresh_from_db()
    assert row.eval_config_id is None
    assert row.question_key == str(cfg.id)
    assert row.replaced_verdict == VERDICT


@pytest.mark.django_db
def test_an_ask_keeps_one_row_per_call_and_question(environment, finished_run, user):
    call = env_evals._call(finished_run)
    ask = SimulateEvalAsk.objects.create(
        organization=environment.run_test.organization,
        workspace=environment.run_test.workspace,
        started_by=user,
        selection={"call_ids": [str(call.id)], "description": {"kind": "calls"}},
        questions=[{"key": "q1", "text": "Did the agent confirm the refund?"}],
    )

    def answer():
        return SimulateEvalResult.objects.create(
            origin=SimulateEvalResult.Origin.ASK,
            ask=ask,
            call_execution=call,
            question_key="q1",
            state=SimulateEvalResult.State.PENDING,
            organization=ask.organization,
            workspace=ask.workspace,
        )

    answer()
    with pytest.raises(IntegrityError), transaction.atomic():
        answer()
    assert ask.status == SimulateEvalAsk.Status.PENDING


@pytest.mark.django_db
def test_a_replaced_row_names_no_ask_and_an_ask_row_must(environment, finished_run):
    call = env_evals._call(finished_run)
    with pytest.raises(IntegrityError), transaction.atomic():
        SimulateEvalResult.objects.create(
            origin=SimulateEvalResult.Origin.ASK,
            ask=None,
            call_execution=call,
            question_key="q1",
            state=SimulateEvalResult.State.PENDING,
            organization=environment.run_test.organization,
            workspace=environment.run_test.workspace,
        )


@pytest.mark.django_db
def test_an_ask_row_carries_no_request_id(environment, finished_run, user):
    call = env_evals._call(finished_run)
    ask = SimulateEvalAsk.objects.create(
        organization=environment.run_test.organization,
        workspace=environment.run_test.workspace,
        started_by=user,
        selection={"call_ids": [str(call.id)]},
        questions=[{"key": "q1"}],
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        SimulateEvalResult.objects.create(
            origin=SimulateEvalResult.Origin.ASK,
            ask=ask,
            replacement_id=uuid.uuid4(),
            call_execution=call,
            question_key="q1",
            state=SimulateEvalResult.State.PENDING,
            organization=ask.organization,
            workspace=ask.workspace,
        )


@pytest.mark.django_db
def test_a_replaced_row_must_name_its_request(environment, finished_run):
    cfg = _mapped_eval(environment)
    call = env_evals._call(finished_run)
    with pytest.raises(IntegrityError), transaction.atomic():
        _kept(call, cfg, environment, replacement_id=None)


@pytest.mark.django_db
def test_one_request_keeps_one_row_per_call_and_eval(environment, finished_run):
    cfg = _mapped_eval(environment)
    call = env_evals._call(finished_run)
    request_id = uuid.uuid4()
    _kept(call, cfg, environment, replacement_id=request_id)
    with pytest.raises(IntegrityError), transaction.atomic():
        _kept(call, cfg, environment, replacement_id=request_id)


@pytest.mark.django_db
@pytest.mark.parametrize("deleted", ["call", "run"])
def test_readers_leave_out_rows_of_a_deleted_call_or_run(
    environment, finished_run, deleted
):
    cfg = _mapped_eval(environment)
    call = env_evals._call(finished_run)
    row = _kept(call, cfg, environment)
    assert list(readable_results(SimulateEvalResult.all_objects.all())) == [row]

    if deleted == "call":
        CallExecution.all_objects.filter(id=call.id).update(deleted=True)
    else:
        TestExecution.all_objects.filter(id=finished_run.id).update(deleted=True)

    assert list(readable_results(SimulateEvalResult.all_objects.all())) == []
