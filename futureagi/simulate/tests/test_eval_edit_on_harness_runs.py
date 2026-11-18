"""Editing an eval on the run-test page of a harness run, over HTTP.

``POST run-tests/{id}/eval-configs/{cfg}/update/`` refuses a result column the
harness fills, as the environments route does, so ``run=true`` can't write
"pending" over the harness's verdict. A refusal saves nothing, writes no
placeholder and grades nothing. Evals a person mapped, and runs no harness
touched, are edited and graded again as before.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import HostedHarnessJob, SimulateEvalConfig
from simulate.models.test_execution import TestExecution
from simulate.services.eval_config_edit import (
    EvalConfigEditRefused,
    harness_column_refusal,
    keeps_own_mapping,
)
from simulate.services.harness_evals import regrade_mapping
from simulate.services.hosted_harness import create_hosted_job
from simulate.services.kept_harness_entry import (
    KEPT_KEY,
    end_without_verdict,
    pending_placeholder,
)
from simulate.tests import test_scoring_status as scoring

from .test_hosted_harness_channels import _payload

# A run test, its agent and scenario, exactly as the scoring tests that post
# to this route build them.
agent_definition = scoring.agent_definition
simulator_agent = scoring.simulator_agent
scenario = scoring.scenario
run_test = scoring.run_test
eval_template = scoring.eval_template

REFUSED = "Task completion is set by the harness and can't be edited here."
HARNESS_VERDICT = {
    "status": "completed",
    "source": "harness",
    "output": "Failed",
    "name": "Task completion",
}


@pytest.fixture
def grading():
    """Spies on the re-grade this route dispatches."""
    with patch(
        "simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async"
    ) as apply_async:
        yield apply_async


@pytest.fixture
def harness_run(organization, workspace, run_test):
    """A run test a hosted harness environment owns."""
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="eval-edit-on-harness-run",
        workspace=workspace,
    )
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(run_test=run_test)
    return run_test


@pytest.fixture
def suite_template(db):
    return EvalTemplate.objects.create(
        name="suite_edit_on_run_test",
        config={"required_keys": ["conversation"], "output": "Pass/Fail"},
        owner="system",
    )


def _harness_column(run_test, template):
    """A result column the harness fills: no mapping of its own."""
    return SimulateEvalConfig.objects.create(
        name="Task completion",
        eval_template=template,
        run_test=run_test,
        mapping={},
    )


def _finished_run(run_test, config, *, metadata=None):
    """A completed run whose one call holds the harness's verdict for ``config``."""
    execution = scoring._run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    call = scoring._call(
        execution,
        metadata=metadata,
        eval_outputs={str(config.id): dict(HARNESS_VERDICT)},
    )
    return execution, call


def _update(client, run_test, config, body):
    return client.post(
        f"/simulate/run-tests/{run_test.id}/eval-configs/{config.id}/update/",
        body,
        format="json",
    )


def _row(config):
    """Every stored field an edit could change."""
    config.refresh_from_db()
    return {
        field: getattr(config, field)
        for field in (
            "name",
            "config",
            "mapping",
            "filters",
            "model",
            "error_localizer",
            "kb_id",
            "eval_template_id",
            "updated_at",
        )
    }


def _call_state(call):
    call.refresh_from_db()
    return call.eval_outputs, call.call_metadata


@pytest.mark.parametrize(
    ("harness_run_flag", "name", "mapping", "expected"),
    [
        pytest.param(True, "Task completion", {}, REFUSED, id="harness-empty"),
        pytest.param(True, "Task completion", None, REFUSED, id="harness-null"),
        pytest.param(
            True, "Task completion", ["transcript"], REFUSED, id="harness-not-a-dict"
        ),
        pytest.param(
            True,
            None,
            {},
            "This evaluation is set by the harness and can't be edited here.",
            id="harness-unnamed",
        ),
        pytest.param(
            True, "Politeness", {"text": "transcript"}, None, id="harness-mapped"
        ),
        pytest.param(False, "Task completion", {}, None, id="no-harness"),
    ],
)
def test_harness_column_refusal(harness_run_flag, name, mapping, expected):
    config = SimpleNamespace(name=name, mapping=mapping)

    assert harness_column_refusal(config, harness_run=harness_run_flag) == expected


@pytest.mark.parametrize(
    "mapping",
    [
        pytest.param({}, id="empty"),
        pytest.param(None, id="null"),
        pytest.param(["transcript"], id="not-a-dict"),
    ],
)
def test_keeps_own_mapping(mapping):
    """The words name the eval as it was before the edit, not as edited."""
    check = keeps_own_mapping("Politeness")

    assert check(SimpleNamespace(name="Renamed", mapping={"text": "x"})) is None
    with pytest.raises(EvalConfigEditRefused) as refused:
        check(SimpleNamespace(name="Renamed", mapping=mapping))
    assert str(refused.value) == "Politeness needs at least one input mapped"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"name": "Renamed"}, id="rename"),
        pytest.param({"mapping": {"conversation": "transcript"}}, id="map-an-input"),
        pytest.param({"model": "x"}, id="model-only"),
    ],
)
@pytest.mark.parametrize("run", [True, False], ids=["run", "no-run"])
def test_a_harness_result_column_is_not_edited(
    auth_client, harness_run, suite_template, grading, body, run
):
    config = _harness_column(harness_run, suite_template)
    execution, call = _finished_run(harness_run, config)
    if run:
        body = {**body, "run": True, "test_execution_id": str(execution.id)}
    before = _row(config)
    cell = _call_state(call)

    response = _update(auth_client, harness_run, config, body)

    assert response.status_code == 400, response.content
    assert response.json()["detail"] == REFUSED
    # The run-test page toasts the envelope's result.
    assert response.json()["result"] == REFUSED
    assert _row(config) == before
    assert _call_state(call) == cell
    execution.refresh_from_db()
    assert execution.status == TestExecution.ExecutionStatus.COMPLETED
    grading.assert_not_called()


@pytest.mark.django_db
def test_an_edit_that_drops_the_mapping_is_refused_on_a_harness_run(
    auth_client, harness_run, eval_template, grading
):
    config = scoring._config(harness_run, eval_template, name="Politeness")
    before = _row(config)

    response = _update(
        auth_client, harness_run, config, {"name": "Renamed", "mapping": {}}
    )

    assert response.status_code == 400, response.content
    assert response.json()["detail"] == "Politeness needs at least one input mapped"
    assert response.json()["result"] == "Politeness needs at least one input mapped"
    assert _row(config) == before
    grading.assert_not_called()


@pytest.mark.django_db
def test_a_mapped_eval_on_a_harness_run_is_edited_and_rerun(
    auth_client, harness_run, eval_template, grading
):
    config = scoring._config(harness_run, eval_template, name="Politeness")
    execution = scoring._run(harness_run, TestExecution.ExecutionStatus.COMPLETED)
    call = scoring._call(execution)

    response = _update(
        auth_client,
        harness_run,
        config,
        {"name": "Politeness v2", "run": True, "test_execution_id": str(execution.id)},
    )

    assert response.status_code == 200, response.content
    config.refresh_from_db()
    assert config.name == "Politeness v2"
    assert config.mapping == {"conversation": "transcript"}
    call.refresh_from_db()
    assert call.eval_outputs[str(config.id)] == {"status": "pending"}
    execution.refresh_from_db()
    assert execution.status == TestExecution.ExecutionStatus.EVALUATING
    grading.assert_called_once_with(args=([str(call.id)], [str(config.id)]))


@pytest.mark.django_db
def test_a_run_no_harness_touched_edits_an_empty_mapping_config_as_before(
    auth_client, run_test, eval_template, grading
):
    config = scoring._config(run_test, eval_template, name="Politeness")

    response = _update(auth_client, run_test, config, {"mapping": {}})

    assert response.status_code == 200, response.content
    config.refresh_from_db()
    assert config.mapping == {}
    grading.assert_not_called()


@pytest.mark.django_db
def test_the_route_asks_the_shared_harness_test(
    auth_client, run_test, suite_template, grading
):
    """The route must ask ``is_harness_run_test`` itself, not a narrower
    lookup of its own, so every kind of run that test counts is refused."""
    config = _harness_column(run_test, suite_template)
    before = _row(config)

    with patch("simulate.views.run_test.is_harness_run_test", return_value=True):
        response = _update(auth_client, run_test, config, {"model": "x"})

    assert response.status_code == 400, response.content
    assert response.json()["detail"] == REFUSED
    assert _row(config) == before


@pytest.mark.django_db
def test_a_harness_result_column_is_not_edited_on_an_sdk_run(
    auth_client, run_test, suite_template, grading
):
    config = _harness_column(run_test, suite_template)
    execution, call = _finished_run(
        run_test,
        config,
        metadata={
            "harness_evaluations": [{"name": "Task completion", "passed": False}]
        },
    )
    cell = _call_state(call)

    response = _update(
        auth_client,
        run_test,
        config,
        {"model": "x", "run": True, "test_execution_id": str(execution.id)},
    )

    assert response.status_code == 400, response.content
    assert response.json()["detail"] == REFUSED
    assert _call_state(call) == cell


@pytest.mark.django_db
def test_a_run_no_harness_touched_edits_a_stored_empty_mapping_config_as_before(
    auth_client, run_test, suite_template, grading
):
    config = _harness_column(run_test, suite_template)

    response = _update(auth_client, run_test, config, {"name": "Renamed"})

    assert response.status_code == 200, response.content
    config.refresh_from_db()
    assert config.name == "Renamed"
    assert config.mapping == {}
    grading.assert_not_called()


# --- edit + run on a harness run: the grading #3428's route does -----------
#
# The route answers from the statuses it declares (200, 400, 404, 500). The
# codes are #3428's; the move to 409 for a refusal caused by the run's state
# is a follow-up for this route and the environments route together.


def _mapped_harness_cell(harness_run, eval_template, execution_status):
    """A mapped eval on a harness run whose one call holds a harness verdict."""
    config = scoring._config(harness_run, eval_template, name="Politeness")
    execution = scoring._run(harness_run, execution_status)
    call = scoring._call(
        execution, eval_outputs={str(config.id): dict(HARNESS_VERDICT)}
    )
    return config, execution, call


def _edit_and_run(client, run_test, config, execution, body=None):
    return _update(
        client,
        run_test,
        config,
        {
            **(body or {"model": "x"}),
            "run": True,
            "test_execution_id": str(execution.id),
        },
    )


@pytest.mark.django_db
def test_an_edit_and_run_keeps_the_harness_verdict_under_the_placeholder(
    auth_client, harness_run, eval_template, grading
):
    """A grade that then fails, is skipped or times out hands the harness's
    verdict back, so the placeholder must hold it."""
    config, execution, call = _mapped_harness_cell(
        harness_run, eval_template, TestExecution.ExecutionStatus.COMPLETED
    )

    response = _edit_and_run(auth_client, harness_run, config, execution)

    assert response.status_code == 200, response.content
    call.refresh_from_db()
    cell = call.eval_outputs[str(config.id)]
    assert cell == pending_placeholder(HARNESS_VERDICT)
    assert cell[KEPT_KEY] == HARNESS_VERDICT
    failed = {"status": "failed", "name": "Politeness"}
    assert end_without_verdict(cell, failed) == HARNESS_VERDICT
    grading.assert_called_once_with(args=([str(call.id)], [str(config.id)]))


@pytest.mark.django_db
def test_two_edit_and_run_requests_dispatch_once(
    auth_client, harness_run, eval_template, grading
):
    config, execution, call = _mapped_harness_cell(
        harness_run, eval_template, TestExecution.ExecutionStatus.COMPLETED
    )

    first = _edit_and_run(auth_client, harness_run, config, execution)
    second = _edit_and_run(
        auth_client, harness_run, config, execution, {"model": "second"}
    )

    assert first.status_code == 200, first.content
    assert second.status_code == 400, second.content
    assert second.json()["code"] == "rerun_evals_run_not_completed"
    # Refused before the save: the second edit is not kept.
    assert _row(config)["model"] == "x"
    grading.assert_called_once()
    call.refresh_from_db()
    assert call.eval_outputs[str(config.id)][KEPT_KEY] == HARNESS_VERDICT


@pytest.mark.django_db
def test_an_edit_and_run_that_loses_the_claim_dispatches_nothing(
    auth_client, harness_run, eval_template, grading
):
    """Another request moves the run to EVALUATING after this one read it
    COMPLETED: the claim is conditional, so this one dispatches nothing. That can
    only be known after the save, so the answer says the edit was kept."""
    config, execution, call = _mapped_harness_cell(
        harness_run, eval_template, TestExecution.ExecutionStatus.COMPLETED
    )
    real = regrade_mapping

    def other_request_claims_first(eval_config):
        TestExecution.objects.filter(id=execution.id).update(
            status=TestExecution.ExecutionStatus.EVALUATING
        )
        return real(eval_config)

    with patch(
        "simulate.services.run_regrade.regrade_mapping",
        side_effect=other_request_claims_first,
    ):
        response = _edit_and_run(auth_client, harness_run, config, execution)

    assert response.status_code == 200, response.content
    assert response.json()["message"] == (
        "Evaluation config updated. Grading is already running on this run, so "
        "this change grades the next time evals run."
    )
    assert _row(config)["model"] == "x"
    grading.assert_not_called()
    assert _call_state(call)[0] == {str(config.id): HARNESS_VERDICT}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "run_status",
    [
        TestExecution.ExecutionStatus.FAILED,
        TestExecution.ExecutionStatus.CANCELLED,
        TestExecution.ExecutionStatus.RUNNING,
    ],
)
def test_an_edit_and_run_on_a_run_that_did_not_complete_marks_no_call(
    auth_client, harness_run, eval_template, grading, run_status
):
    config, execution, call = _mapped_harness_cell(
        harness_run, eval_template, run_status
    )
    cell = _call_state(call)
    before = _row(config)

    response = _edit_and_run(auth_client, harness_run, config, execution)

    assert response.status_code == 400, response.content
    assert response.json()["code"] == "rerun_evals_run_not_completed"
    # Refused before the save, so the page's error means nothing changed.
    assert _row(config) == before
    assert _call_state(call) == cell
    execution.refresh_from_db()
    assert execution.status == run_status
    grading.assert_not_called()


@pytest.mark.django_db
def test_an_edit_and_run_leaves_a_call_that_did_not_complete_alone(
    auth_client, harness_run, eval_template, grading
):
    config, execution, call = _mapped_harness_cell(
        harness_run, eval_template, TestExecution.ExecutionStatus.COMPLETED
    )
    failed_call = scoring._call(
        execution,
        status="failed",
        eval_outputs={str(config.id): dict(HARNESS_VERDICT)},
    )
    cell = _call_state(failed_call)

    response = _edit_and_run(auth_client, harness_run, config, execution)

    assert response.status_code == 200, response.content
    assert _call_state(failed_call) == cell
    grading.assert_called_once_with(args=([str(call.id)], [str(config.id)]))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "job_state",
    [
        HostedHarnessJob.State.RUNNING,
        HostedHarnessJob.State.FINALIZING,
        HostedHarnessJob.State.CLEANING_UP,
        HostedHarnessJob.State.RETRY_WAIT,
    ],
)
def test_an_edit_and_run_on_a_run_still_finishing_is_refused(
    auth_client, harness_run, eval_template, grading, job_state
):
    """Teardown writes the job's end state onto the run later, overwriting
    EVALUATING mid-grade."""
    config, execution, call = _mapped_harness_cell(
        harness_run, eval_template, TestExecution.ExecutionStatus.COMPLETED
    )
    HostedHarnessJob.no_workspace_objects.filter(run_test=harness_run).update(
        test_execution=execution, state=job_state
    )
    cell = _call_state(call)
    before = _row(config)

    response = _edit_and_run(auth_client, harness_run, config, execution)

    assert response.status_code == 400, response.content
    assert response.json()["code"] == "rerun_evals_still_finishing"
    assert _row(config) == before
    assert _call_state(call) == cell
    execution.refresh_from_db()
    assert execution.status == TestExecution.ExecutionStatus.COMPLETED
    grading.assert_not_called()


@pytest.mark.django_db
def test_a_dispatch_that_fails_puts_the_harness_verdict_back(
    auth_client, harness_run, eval_template, grading
):
    config, execution, call = _mapped_harness_cell(
        harness_run, eval_template, TestExecution.ExecutionStatus.COMPLETED
    )
    grading.side_effect = RuntimeError("broker down")

    response = _edit_and_run(auth_client, harness_run, config, execution)

    assert response.status_code == 200, response.content
    assert "may not have started" in response.json()["message"]
    assert _call_state(call)[0] == {str(config.id): HARNESS_VERDICT}
    execution.refresh_from_db()
    assert execution.status == TestExecution.ExecutionStatus.COMPLETED


@pytest.mark.django_db
def test_an_edit_and_run_on_a_run_no_harness_touched_writes_the_plain_placeholder(
    auth_client, run_test, eval_template, grading
):
    config = scoring._config(run_test, eval_template, name="Politeness")
    execution = scoring._run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    call = scoring._call(
        execution,
        eval_outputs={str(config.id): {"status": "completed", "output": "Passed"}},
    )

    response = _edit_and_run(auth_client, run_test, config, execution)

    assert response.status_code == 200, response.content
    call.refresh_from_db()
    assert call.eval_outputs[str(config.id)] == {"status": "pending"}
    grading.assert_called_once_with(args=([str(call.id)], [str(config.id)]))


# --- a bare re-run of a result column the harness fills --------------------


@pytest.mark.django_db
def test_a_bare_rerun_of_a_suite_result_column_is_graded_as_re_run_evals_does(
    auth_client, harness_run, suite_template, grading
):
    """``suite_template`` is a built-in suite eval: the platform grades it from
    the transcript, as "Re-run evals" does."""
    config = _harness_column(harness_run, suite_template)
    execution, call = _finished_run(harness_run, config)
    before = _row(config)

    response = _update(
        auth_client,
        harness_run,
        config,
        {"run": True, "test_execution_id": str(execution.id)},
    )

    assert response.status_code == 200, response.content
    assert _row(config) == before
    call.refresh_from_db()
    assert call.eval_outputs[str(config.id)] == pending_placeholder(HARNESS_VERDICT)
    execution.refresh_from_db()
    assert execution.status == TestExecution.ExecutionStatus.EVALUATING
    grading.assert_called_once_with(args=([str(call.id)], [str(config.id)]))


@pytest.fixture
def user_template(db, organization):
    """A user's own eval: the platform has no inputs to grade it from."""
    return EvalTemplate.objects.create(
        name="user_eval_on_run_test",
        config={"required_keys": ["conversation"], "output": "Pass/Fail"},
        organization=organization,
        owner="user",
    )


@pytest.mark.django_db
def test_a_bare_rerun_of_a_column_only_the_harness_can_judge_points_to_re_run_evals(
    auth_client, harness_run, user_template, grading
):
    config = _harness_column(harness_run, user_template)
    assert regrade_mapping(config) is None
    execution, call = _finished_run(harness_run, config)
    before = _row(config)
    cell = _call_state(call)

    response = _update(
        auth_client,
        harness_run,
        config,
        {"run": True, "test_execution_id": str(execution.id)},
    )

    assert response.status_code == 400, response.content
    assert 'Use "Re-run evals"' in response.json()["result"]
    assert response.json()["result"] == harness_column_refusal(
        config, harness_run=True, bare_rerun=True
    )
    assert _row(config) == before
    assert _call_state(call) == cell
    grading.assert_not_called()


@pytest.mark.django_db
def test_a_rerun_that_also_edits_a_suite_result_column_is_still_refused(
    auth_client, harness_run, suite_template, grading
):
    config = _harness_column(harness_run, suite_template)
    execution, call = _finished_run(harness_run, config)
    cell = _call_state(call)

    response = _edit_and_run(auth_client, harness_run, config, execution)

    assert response.status_code == 400, response.content
    assert response.json()["result"] == REFUSED
    assert _call_state(call) == cell
    grading.assert_not_called()


# --- Dileep's review of 5e7ffaf48e -------------------------------------------


def _filter():
    return {
        "column_id": "duration",
        "filter_config": {
            "filter_type": "number",
            "filter_op": "greater_than_or_equal",
            "filter_value": 10,
        },
    }


@pytest.mark.django_db
def test_a_filters_only_edit_with_run_saves_the_filters_on_a_harness_run(
    auth_client, harness_run, eval_template, grading
):
    """A body that changes only the filters is an edit, not a bare re-run."""
    config, execution, call = _mapped_harness_cell(
        harness_run, eval_template, TestExecution.ExecutionStatus.COMPLETED
    )

    response = _edit_and_run(
        auth_client, harness_run, config, execution, {"filters": [_filter()]}
    )

    assert response.status_code == 200, response.content
    assert _row(config)["filters"] == [_filter()]
    grading.assert_called_once_with(args=([str(call.id)], [str(config.id)]))


@pytest.mark.django_db
def test_a_filters_only_edit_with_run_saves_the_filters_on_a_run_no_harness_touched(
    auth_client, run_test, eval_template, grading
):
    config = scoring._config(run_test, eval_template, name="Politeness")
    execution = scoring._run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    scoring._call(execution)

    response = _edit_and_run(
        auth_client, run_test, config, execution, {"filters": [_filter()]}
    )

    assert response.status_code == 200, response.content
    assert _row(config)["filters"] == [_filter()]


def _eval_only_snapshot(call):
    from simulate.models.test_execution import CallExecutionSnapshot

    (snapshot,) = CallExecutionSnapshot.objects.filter(
        call_execution=call,
        rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
    )
    return snapshot


@pytest.mark.django_db
def test_an_edit_and_run_snapshots_the_harness_verdict(
    auth_client, harness_run, eval_template, grading
):
    """As "Re-run evals" does, so the verdict the harness can't produce again is
    in the run's history once the grade replaces it."""
    config, execution, call = _mapped_harness_cell(
        harness_run, eval_template, TestExecution.ExecutionStatus.COMPLETED
    )

    response = _edit_and_run(auth_client, harness_run, config, execution)

    assert response.status_code == 200, response.content
    assert _eval_only_snapshot(call).eval_outputs == {str(config.id): HARNESS_VERDICT}


@pytest.mark.django_db
def test_a_bare_rerun_of_a_suite_result_column_snapshots_the_harness_verdict(
    auth_client, harness_run, suite_template, grading
):
    config = _harness_column(harness_run, suite_template)
    execution, call = _finished_run(harness_run, config)

    response = _update(
        auth_client,
        harness_run,
        config,
        {"run": True, "test_execution_id": str(execution.id)},
    )

    assert response.status_code == 200, response.content
    assert _eval_only_snapshot(call).eval_outputs == {str(config.id): HARNESS_VERDICT}


@pytest.mark.django_db
def test_an_edit_and_run_on_a_run_no_harness_touched_that_is_running_saves_nothing(
    auth_client, run_test, eval_template, grading
):
    config = scoring._config(run_test, eval_template, name="Politeness")
    execution = scoring._run(run_test, TestExecution.ExecutionStatus.RUNNING)
    scoring._call(execution)
    before = _row(config)

    response = _edit_and_run(auth_client, run_test, config, execution)

    assert response.status_code == 400, response.content
    assert _row(config) == before
    grading.assert_not_called()


@pytest.mark.django_db
def test_an_edit_and_run_on_a_harness_run_with_no_completed_call_saves_nothing(
    auth_client, harness_run, eval_template, grading
):
    config = scoring._config(harness_run, eval_template, name="Politeness")
    execution = scoring._run(harness_run, TestExecution.ExecutionStatus.COMPLETED)
    scoring._call(execution, status="failed")
    before = _row(config)

    response = _edit_and_run(auth_client, harness_run, config, execution)

    assert response.status_code == 400, response.content
    assert response.json()["code"] == "rerun_evals_no_completed_call"
    assert _row(config) == before
    grading.assert_not_called()
