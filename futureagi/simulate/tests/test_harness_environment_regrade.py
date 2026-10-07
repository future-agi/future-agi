"""Grading a finished environment run again, over HTTP: every refusal of
``runs/{id}/evaluations/run/`` in the order the route checks them, that a
refusal writes nothing, and what a queued grade writes and sends.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    CallExecution,
    HostedHarnessJob,
    RunTest,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.services.harness_evals import is_harness_run_test
from simulate.services.hosted_harness import create_hosted_job
from simulate.tests import test_harness_environment_evals as env_evals

from .test_hosted_harness_channels import _payload

# A built environment, a client in its workspace and one finished run of it,
# exactly as the environment's other eval endpoint tests build them.
environment = env_evals.environment
env_client = env_evals.env_client
finished_run = env_evals.finished_run

ENVIRONMENTS = env_evals.ENVIRONMENTS


@pytest.fixture
def dispatch():
    """Spy on the one grading job this route queues.

    Patches the attribute on the task object itself, which the service's
    module-level import shares.
    """
    with patch(
        "simulate.services.test_executor.run_new_evals_on_call_executions_task.apply_async"
    ) as spy:
        yield spy


def _regrade(client, job_id, execution_id, workspace, body):
    return client.post(
        f"{ENVIRONMENTS}/{job_id}/runs/{execution_id}/evaluations/run/",
        body,
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


def _mapped_eval(job, name="Politeness", template_name="politeness_regrade"):
    template = EvalTemplate.objects.create(
        name=template_name,
        config={"required_keys": ["output"], "output": "Pass/Fail"},
        organization=job.organization,
    )
    return SimulateEvalConfig.objects.create(
        name=name,
        eval_template=template,
        run_test=job.run_test,
        config={},
        mapping={"output": "transcript"},
    )


def _harness_filled_eval(job, name, template_name, *, owner, required_keys):
    template = EvalTemplate.objects.create(
        name=template_name,
        config={"required_keys": list(required_keys), "output": "Pass/Fail"},
        owner=owner,
        organization=job.organization if owner == "user" else None,
    )
    return SimulateEvalConfig.objects.create(
        name=name,
        eval_template=template,
        run_test=job.run_test,
        config={},
        mapping={},
    )


def _live_job_for(execution, state):
    """A harness job that ran this execution, in ``state``."""
    run_test = execution.run_test
    return HostedHarnessJob.no_workspace_objects.create(
        organization=run_test.organization,
        workspace=run_test.workspace,
        run_id=uuid.uuid4(),
        idempotency_key=f"regrade-live-{uuid.uuid4()}",
        request_digest=f"sha256:{'0' * 64}",
        schema_version="1.4",
        payload={},
        state=state,
        seed=1,
        scenario_count=1,
        artifact_level="standard",
        max_artifact_bytes=1,
        deadline_at=timezone.now() + timedelta(hours=1),
        run_test=run_test,
        test_execution=execution,
    )


def _state(job, execution, call=None):
    """Everything a refused request must leave as it was."""
    job.refresh_from_db()
    execution.refresh_from_db()
    run_test = RunTest.objects.get(id=job.run_test_id)
    state = {
        "status": execution.status,
        "execution_metadata": execution.execution_metadata,
        "enable_tool_evaluation": run_test.enable_tool_evaluation,
        "content_updated_at": job.content_updated_at,
    }
    if call is not None:
        call.refresh_from_db()
        state["eval_outputs"] = call.eval_outputs
        state["call_metadata"] = call.call_metadata
    return state


X = "11111111-1111-4111-8111-111111111111"


def _graded_call(execution, eval_config):
    """One graded call holding a verdict for ``eval_config``."""
    return env_evals._call(
        execution,
        metadata=env_evals._graded(),
        eval_outputs={str(eval_config.id): env_evals._verdict()},
    )


def _other_run_test(job):
    return RunTest.objects.create(
        name="Other run test",
        organization=job.organization,
        workspace=job.workspace,
    )


def _eval_column_ids(execution):
    column_order = (execution.execution_metadata or {}).get("column_order", [])
    return [
        column.get("id")
        for column in column_order
        if isinstance(column, dict) and column.get("type") == "evaluation"
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("body", "field", "message"),
    [
        pytest.param({}, "eval_config_ids", "This field is required.", id="missing"),
        pytest.param(
            {"eval_config_ids": []},
            "eval_config_ids",
            "This list may not be empty.",
            id="empty",
        ),
        pytest.param(
            {"eval_config_ids": ["nope"]},
            "eval_config_ids",
            "0: Must be a valid UUID.",
            id="not-a-uuid",
        ),
        pytest.param(
            {"eval_config_ids": [X, X]},
            "eval_config_ids",
            "Each evaluation can be sent only once.",
            id="duplicate",
        ),
        pytest.param(
            {"eval_config_ids": [X], "enable_tool_evaluation": "maybe"},
            "enable_tool_evaluation",
            "Must be a valid boolean.",
            id="wrong-type",
        ),
        pytest.param(
            {"eval_config_ids": [X], "run": True},
            "run",
            "Unknown field.",
            id="unknown-key",
        ),
    ],
)
def test_regrade_refuses_a_body_that_fails_validation(
    env_client, workspace, dispatch, body, field, message
):
    """The body is checked before the environment is looked up, so no such
    environment is needed to see the refusal."""
    response = _regrade(env_client, uuid.uuid4(), uuid.uuid4(), workspace, body)

    assert response.status_code == 400, response.content
    payload = response.json()
    assert payload["status"] is False
    assert payload["details"] == {field: [message]}
    assert payload["detail"] == f"{field}: {message}"
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_regrade_answers_not_found_for_an_environment_outside_this_workspace(
    env_client, user, environment, finished_run, workspace, dispatch
):
    cfg = _mapped_eval(environment)
    call = _graded_call(finished_run, cfg)
    other_workspace = Workspace.objects.create(
        name="Other workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    body = {"eval_config_ids": [str(cfg.id)]}
    before = _state(environment, finished_run, call)

    elsewhere = _regrade(
        env_client, environment.id, finished_run.id, other_workspace, body
    )
    missing = _regrade(env_client, uuid.uuid4(), finished_run.id, workspace, body)

    for response in (elsewhere, missing):
        assert response.status_code == 404, response.content
        assert response.json() == {"detail": "Environment not found"}
    assert _state(environment, finished_run, call) == before
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_regrade_refuses_an_environment_that_is_still_building(
    env_client, user, workspace, dispatch
):
    job, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-regrade-unbuilt",
        workspace=workspace,
    )

    response = _regrade(
        env_client, job.id, uuid.uuid4(), workspace, {"eval_config_ids": [X]}
    )

    assert response.status_code == 409, response.content
    assert response.json() == {
        "detail": "Environment has no evaluations until it finishes building"
    }
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_regrade_refuses_a_run_that_is_not_this_environments(
    env_client, environment, workspace, dispatch
):
    cfg = _mapped_eval(environment)
    other = _other_run_test(environment)
    foreign = TestExecution.objects.create(
        run_test=other,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
    )

    for execution_id in (foreign.id, uuid.uuid4()):
        response = _regrade(
            env_client,
            environment.id,
            execution_id,
            workspace,
            {"eval_config_ids": [str(cfg.id)]},
        )
        assert response.status_code == 404, response.content
        assert response.json() == {"detail": "Run not found"}

    dispatch.assert_not_called()
    foreign.refresh_from_db()
    assert foreign.status == TestExecution.ExecutionStatus.COMPLETED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "run_status",
    [
        pytest.param(TestExecution.ExecutionStatus.COMPLETED, id="finished"),
        pytest.param(
            TestExecution.ExecutionStatus.RUNNING,
            id="over-the-not-finished-refusal",
        ),
    ],
)
def test_regrade_says_still_finishing_while_the_harness_job_has_not_ended(
    env_client, environment, finished_run, workspace, dispatch, run_status
):
    """Teardown would overwrite ``evaluating`` with the job's end state, so a
    live job is refused first, whatever the run reads right now."""
    TestExecution.objects.filter(id=finished_run.id).update(status=run_status)
    _live_job_for(finished_run, HostedHarnessJob.State.CLEANING_UP)
    cfg = _mapped_eval(environment)
    call = _graded_call(finished_run, cfg)
    before = _state(environment, finished_run, call)

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)], "enable_tool_evaluation": True},
    )

    assert response.status_code == 409, response.content
    assert response.json() == {
        "detail": "This run is still finishing. Try again in a moment."
    }
    assert _state(environment, finished_run, call) == before
    dispatch.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "run_status",
    [
        TestExecution.ExecutionStatus.RUNNING,
        TestExecution.ExecutionStatus.EVALUATING,
        TestExecution.ExecutionStatus.CANCELLED,
        TestExecution.ExecutionStatus.FAILED,
    ],
)
def test_regrade_refuses_a_run_that_has_not_finished(
    env_client, environment, finished_run, workspace, dispatch, run_status
):
    TestExecution.objects.filter(id=finished_run.id).update(status=run_status)
    cfg = _mapped_eval(environment)
    call = _graded_call(finished_run, cfg)
    before = _state(environment, finished_run, call)

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)], "enable_tool_evaluation": True},
    )

    assert response.status_code == 409, response.content
    assert response.json() == {"detail": "Only a finished run can be graded again"}
    assert _state(environment, finished_run, call) == before
    dispatch.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["unknown", "removed", "another-run-tests"])
def test_regrade_refuses_an_eval_that_is_not_this_environments(
    env_client, environment, finished_run, workspace, dispatch, kind
):
    cfg = _mapped_eval(environment)
    call = _graded_call(finished_run, cfg)
    if kind == "unknown":
        that_id = str(uuid.uuid4())
    elif kind == "removed":
        removed = _mapped_eval(environment, "Removed", "removed_regrade")
        removed.deleted = True
        removed.deleted_at = timezone.now()
        removed.save()
        that_id = str(removed.id)
    else:
        template = EvalTemplate.objects.create(
            name="other_run_test_regrade",
            config={"required_keys": ["output"], "output": "Pass/Fail"},
            organization=environment.organization,
        )
        foreign = SimulateEvalConfig.objects.create(
            name="Other run test's eval",
            eval_template=template,
            run_test=_other_run_test(environment),
            config={},
            mapping={"output": "transcript"},
        )
        that_id = str(foreign.id)
    before = _state(environment, finished_run, call)

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id), that_id], "enable_tool_evaluation": True},
    )

    assert response.status_code == 404, response.content
    assert response.json() == {"detail": "Evaluation not found"}
    assert _state(environment, finished_run, call) == before
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_regrade_answers_not_found_before_the_harness_refusal(
    env_client, environment, finished_run, workspace, dispatch
):
    """An id that names nothing is answered before any eval is judged, so a
    harness-filled eval in the same request never gets its 400 first."""
    claim = _harness_filled_eval(
        environment,
        "Harness claim",
        "claim_order_regrade",
        owner="user",
        required_keys=["conversation"],
    )
    call = _graded_call(finished_run, claim)
    before = _state(environment, finished_run, call)

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(claim.id), str(uuid.uuid4())]},
    )

    assert response.status_code == 404, response.content
    assert response.json() == {"detail": "Evaluation not found"}
    assert _state(environment, finished_run, call) == before
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_regrade_refuses_a_harness_only_eval_before_writing_anything(
    env_client, environment, finished_run, workspace, dispatch
):
    """A per-scenario claim is judged on a record only the harness has, so
    the whole request is refused, the gradeable eval beside it included."""
    claim = _harness_filled_eval(
        environment,
        "Harness claim",
        "harness_claim_regrade",
        owner="user",
        required_keys=["conversation"],
    )
    cfg = _mapped_eval(environment)
    call = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={str(claim.id): {"source": "harness", "output": "Passed"}},
    )
    before = _state(environment, finished_run, call)

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {
            "eval_config_ids": [str(cfg.id), str(claim.id)],
            "enable_tool_evaluation": True,
        },
    )

    assert response.status_code == 400, response.content
    assert response.json() == {
        "detail": (
            "Harness claim is scored by the harness during the call. "
            "Only rerunning the call refreshes it."
        )
    }
    assert _state(environment, finished_run, call) == before
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_regrade_refuses_a_run_with_no_calls_before_writing_anything(
    env_client, environment, finished_run, workspace, dispatch
):
    cfg = _mapped_eval(environment)
    before = _state(environment, finished_run)

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)], "enable_tool_evaluation": True},
    )

    assert response.status_code == 409, response.content
    assert response.json() == {"detail": "This run has no calls to grade"}
    after = _state(environment, finished_run)
    assert after == before
    assert after["status"] == TestExecution.ExecutionStatus.COMPLETED
    assert str(cfg.id) not in _eval_column_ids(finished_run)
    assert after["enable_tool_evaluation"] is False
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_regrade_refuses_a_run_whose_calls_never_completed(
    env_client, environment, finished_run, workspace, dispatch
):
    """The graders skip a call that did not complete, and the run leaves
    ``evaluating`` only once a completed call is graded, so grading such a
    run would leave it ``evaluating`` for good."""
    cfg = _mapped_eval(environment)
    call = env_evals._call(
        finished_run,
        status="failed",
        metadata=env_evals._graded(),
        eval_outputs={str(cfg.id): env_evals._verdict()},
    )
    before = _state(environment, finished_run, call)

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)], "enable_tool_evaluation": True},
    )

    assert response.status_code == 409, response.content
    assert response.json() == {
        "detail": "Nothing to grade again: no call in this run completed."
    }
    after = _state(environment, finished_run, call)
    assert after == before
    assert after["status"] == TestExecution.ExecutionStatus.COMPLETED
    assert str(cfg.id) not in _eval_column_ids(finished_run)
    assert after["enable_tool_evaluation"] is False
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_regrade_refuses_a_second_request_that_raced_the_first(
    env_client, environment, finished_run, workspace, dispatch
):
    """Both requests read ``completed``; the first moves the run to
    ``evaluating`` before the second writes. The second claims nothing,
    keeps nothing it wrote, and dispatches nothing."""
    cfg = _mapped_eval(environment)
    call = _graded_call(finished_run, cfg)
    before = _state(environment, finished_run, call)
    # The last read before the claim is the completed-call check; the
    # first request's claim lands right after it.
    manager = CallExecution.objects
    real_filter = manager.filter

    def first_request_claims_in_between(*args, **kwargs):
        if kwargs.get("status") == CallExecution.CallStatus.COMPLETED:
            TestExecution.objects.filter(id=finished_run.id).update(
                status=TestExecution.ExecutionStatus.EVALUATING
            )
        return real_filter(*args, **kwargs)

    with patch.object(manager, "filter", side_effect=first_request_claims_in_between):
        response = _regrade(
            env_client,
            environment.id,
            finished_run.id,
            workspace,
            {"eval_config_ids": [str(cfg.id)], "enable_tool_evaluation": True},
        )

    assert response.status_code == 409, response.content
    assert response.json() == {"detail": "Grading is already running on this run."}
    # Only the first request's claim moved anything.
    assert _state(environment, finished_run, call) == {
        **before,
        "status": TestExecution.ExecutionStatus.EVALUATING,
    }
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_run_refuses_a_harness_only_eval_before_looking_for_calls(
    env_client, environment, finished_run, workspace, dispatch
):
    """On a run with no calls, a harness-only claim still answers the harness
    400, not the no-calls 409: the claim is refused first."""
    claim = _harness_filled_eval(
        environment,
        "Harness claim",
        "harness_claim_no_calls_regrade",
        owner="user",
        required_keys=["conversation"],
    )
    before = _state(environment, finished_run)

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(claim.id)], "enable_tool_evaluation": True},
    )

    assert response.status_code == 400, response.content
    assert response.json() == {
        "detail": (
            "Harness claim is scored by the harness during the call. "
            "Only rerunning the call refreshes it."
        )
    }
    after = _state(environment, finished_run)
    assert after == before
    assert after["status"] == TestExecution.ExecutionStatus.COMPLETED
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_regrade_answers_503_and_puts_every_score_back_when_grading_cannot_be_queued(
    env_client, environment, finished_run, workspace, dispatch
):
    dispatch.side_effect = TimeoutError("broker down")
    suite = _harness_filled_eval(
        environment,
        "Task completion",
        "suite_regrade_503",
        owner="system",
        required_keys=["conversation"],
    )
    harness_score = {
        "name": "Task completion",
        "output": "Passed",
        "output_type": "Pass/Fail",
        "status": "completed",
        "source": "harness",
    }
    graded = env_evals._call(
        finished_run,
        metadata=env_evals._graded(),
        eval_outputs={str(suite.id): harness_score},
    )
    bare = env_evals._call(finished_run, metadata={"eval_started": True})

    clock_before = environment.content_updated_at
    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(suite.id)], "enable_tool_evaluation": True},
    )

    assert response.status_code == 503, response.content
    assert response.json() == {"detail": "Grading couldn't be started. Try again."}
    dispatch.assert_called_once()
    finished_run.refresh_from_db()
    graded.refresh_from_db()
    bare.refresh_from_db()
    assert finished_run.status == TestExecution.ExecutionStatus.COMPLETED
    assert graded.eval_outputs == {str(suite.id): harness_score}
    assert bare.eval_outputs == {}
    assert (
        RunTest.objects.get(id=environment.run_test_id).enable_tool_evaluation is True
    )
    assert graded.call_metadata["eval_completed"] is True
    assert "eval_completed" not in bare.call_metadata
    assert graded.call_metadata["eval_started"] is False
    assert bare.call_metadata["eval_started"] is False
    assert str(suite.id) in _eval_column_ids(finished_run)
    # A failed start is not content movement: the list's clock stays put.
    environment.refresh_from_db()
    assert environment.content_updated_at == clock_before


@pytest.mark.django_db
def test_regrade_marks_the_run_evaluating_and_queues_one_grading_job(
    env_client, environment, finished_run, workspace, dispatch
):
    cfg = _mapped_eval(environment)
    call = _graded_call(finished_run, cfg)
    clock = environment.content_updated_at
    assert is_harness_run_test(environment.run_test_id) is True

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(cfg.id)], "enable_tool_evaluation": True},
    )

    assert response.status_code == 202, response.content
    assert response.json() == {"call_execution_count": 1}
    dispatch.assert_called_once_with(args=([str(call.id)], [str(cfg.id)]))
    finished_run.refresh_from_db()
    call.refresh_from_db()
    environment.refresh_from_db()
    assert finished_run.status == TestExecution.ExecutionStatus.EVALUATING
    assert call.eval_outputs[str(cfg.id)] == {"status": "pending"}
    assert (
        RunTest.objects.get(id=environment.run_test_id).enable_tool_evaluation is True
    )
    assert environment.content_updated_at is not None
    if clock is not None:
        assert environment.content_updated_at > clock


@pytest.mark.django_db
def test_regrade_sends_a_suite_eval_by_id_alone(
    env_client, environment, finished_run, workspace, dispatch
):
    """The grader works out a suite eval's inputs itself, so the config keeps
    its empty mapping and later harness runs still treat it as a result
    column the harness fills."""
    suite = _harness_filled_eval(
        environment,
        "Task completion",
        "suite_regrade_one_off",
        owner="system",
        required_keys=["conversation", "agent_prompt"],
    )
    call = env_evals._call(finished_run, metadata=env_evals._graded())

    response = _regrade(
        env_client,
        environment.id,
        finished_run.id,
        workspace,
        {"eval_config_ids": [str(suite.id)]},
    )

    assert response.status_code == 202, response.content
    dispatch.assert_called_once_with(args=([str(call.id)], [str(suite.id)]))
    suite.refresh_from_db()
    call.refresh_from_db()
    finished_run.refresh_from_db()
    assert suite.mapping == {}
    assert call.eval_outputs[str(suite.id)] == {"status": "pending"}
    assert finished_run.status == TestExecution.ExecutionStatus.EVALUATING
    assert (
        RunTest.objects.get(id=environment.run_test_id).enable_tool_evaluation is False
    )
