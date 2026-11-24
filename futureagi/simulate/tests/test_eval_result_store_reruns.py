"""Every other path that replaces a run's own verdict keeps it too.

An eval-only rerun empties a call's ``eval_outputs``, and editing an eval with
``run`` set replaces that eval's verdict on every call of a run. Each keeps
the verdicts it replaces in ``SimulateEvalResult``, the same way ``run-new-evals`` does.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from simulate.models import SimulateEvalResult, TestExecution
from simulate.tests import test_scoring_status as scoring
from simulate.tests.test_scoring_status import CLOSED, _call, _config, _run
from simulate.views.run_test import _rerun_call_executions

# The scoring tests' run test, eval template and hand-off spies.
agent_definition = scoring.agent_definition
simulator_agent = scoring.simulator_agent
scenario = scoring.scenario
run_test = scoring.run_test
eval_template = scoring.eval_template
dispatch = scoring.dispatch
_capturable_test_executor_logger = scoring._capturable_test_executor_logger

VERDICT = {
    "name": "Politeness",
    "output": "Passed",
    "output_type": "Pass/Fail",
    "reason": "the agent stayed polite",
    "status": "Completed",
}


def _kept_rows(call):
    return list(
        SimulateEvalResult.objects.filter(
            call_execution=call, origin=SimulateEvalResult.Origin.REPLACED_OWN_SCORE
        ).order_by("question_key")
    )


def _graded_call(run_test, eval_template):
    config = _config(run_test, eval_template, name="Politeness")
    other = _config(run_test, eval_template, name="Accuracy")
    execution = _run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    call = _call(
        execution,
        metadata=dict(CLOSED),
        eval_outputs={
            str(config.id): dict(VERDICT),
            str(other.id): dict(VERDICT, name="Accuracy"),
            "not-a-config-id": {"name": "Legacy", "output": 1.0},
        },
    )
    return config, other, execution, call


@pytest.mark.django_db
def test_an_eval_only_rerun_of_chosen_calls_keeps_every_verdict_it_clears(
    auth_client, user, run_test, eval_template
):
    config, other, execution, call = _graded_call(run_test, eval_template)

    with patch(
        "simulate.temporal.client.rerun_call_executions",
        return_value={"workflow_id": "kept-rerun", "merged": False},
    ):
        response = auth_client.post(
            f"/simulate/test-executions/{execution.id}/rerun-calls/",
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

    assert response.status_code == 200, response.content
    call.refresh_from_db()
    assert call.eval_outputs == {}
    rows = _kept_rows(call)
    by_key = {row.question_key: row for row in rows}
    assert set(by_key) == {str(config.id), str(other.id), "not-a-config-id"}
    assert by_key[str(config.id)].replaced_verdict == VERDICT
    assert by_key[str(config.id)].eval_config_id == config.id
    # A slot key that names no eval of this run is kept, with no eval and no definition.
    assert by_key["not-a-config-id"].eval_config_id is None
    assert by_key["not-a-config-id"].question_definition is None
    assert by_key["not-a-config-id"].score == 1.0
    assert {row.replaced_by_id for row in rows} == {user.id}
    assert len({row.replacement_id for row in rows}) == 1
    assert {row.organization_id for row in rows} == {run_test.organization_id}


@pytest.mark.django_db
def test_an_eval_only_rerun_of_whole_runs_keeps_every_verdict_it_clears(
    auth_client, user, run_test, eval_template
):
    config, other, execution, call = _graded_call(run_test, eval_template)

    with patch(
        "simulate.temporal.client.rerun_call_executions",
        return_value={"workflow_id": "kept-bulk", "merged": False},
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/rerun-test-executions/",
            {"rerun_type": "eval_only", "test_execution_ids": [str(execution.id)]},
            format="json",
        )

    assert response.status_code == 200, response.content
    call.refresh_from_db()
    assert call.eval_outputs == {}
    rows = _kept_rows(call)
    assert {row.question_key for row in rows} == {
        str(config.id),
        str(other.id),
        "not-a-config-id",
    }
    assert {row.replaced_by_id for row in rows} == {user.id}


@pytest.mark.django_db
def test_the_module_rerun_keeps_what_it_clears_with_nobody_named(
    run_test, eval_template
):
    config, _, _, call = _graded_call(run_test, eval_template)

    with patch("simulate.views.run_test._run_simulate_evaluations_task.apply_async"):
        successful, failed, _, has_pending_evals = _rerun_call_executions(
            [call], "eval_only"
        )

    assert failed == []
    assert successful == [str(call.id)]
    assert has_pending_evals is True
    rows = _kept_rows(call)
    assert len(rows) == 3
    assert {row.replaced_by_id for row in rows} == {None}


@pytest.mark.django_db
def test_a_call_and_eval_rerun_keeps_no_row(auth_client, run_test, eval_template):
    """A call rerun replaces the evidence itself: its history is the call's own."""
    _, _, execution, call = _graded_call(run_test, eval_template)

    with (
        patch(
            "simulate.views.run_test._create_rerun_call_execution",
            lambda call_execution: None,
        ),
        patch(
            "simulate.temporal.client.rerun_call_executions",
            return_value={"workflow_id": "kept-call-rerun", "merged": False},
        ),
    ):
        response = auth_client.post(
            f"/simulate/test-executions/{execution.id}/rerun-calls/",
            {"rerun_type": "call_and_eval", "select_all": True},
            format="json",
        )

    assert response.status_code == 200, response.content
    call.refresh_from_db()
    assert call.eval_outputs == {}
    assert _kept_rows(call) == []


@pytest.mark.django_db
def test_editing_an_eval_and_running_it_keeps_the_verdict_with_the_definition_before_the_edit(
    auth_client, user, run_test, eval_template
):
    config, other, execution, call = _graded_call(run_test, eval_template)

    with patch(
        "simulate.services.test_executor.run_new_evals_on_call_executions_task.apply_async"
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/eval-configs/{config.id}/update/",
            {
                "run": True,
                "test_execution_id": str(execution.id),
                "name": "Politeness, stricter",
            },
            format="json",
        )

    assert response.status_code == 200, response.content
    config.refresh_from_db()
    assert config.name == "Politeness, stricter"
    call.refresh_from_db()
    assert call.eval_outputs[str(config.id)] == {"status": "pending"}
    [row] = _kept_rows(call)
    assert row.question_key == str(config.id)
    assert row.replaced_verdict == VERDICT
    assert row.question_definition["name"] == "Politeness"
    assert row.replaced_by_id == user.id


@pytest.mark.django_db
def test_a_call_whose_rerun_fails_part_way_keeps_no_row_and_lends_none_to_the_next(
    auth_client, run_test, eval_template
):
    config, _, execution, failing = _graded_call(run_test, eval_template)
    passing = _call(
        execution,
        metadata=dict(CLOSED),
        eval_outputs={str(config.id): dict(VERDICT, output="Failed")},
    )
    from simulate.views import run_test as views

    real_stamp = views.stamp_eval_queued

    def stamp(metadata, *args, **kwargs):
        if metadata is failing_metadata[0]:
            raise RuntimeError("stamp failed")
        return real_stamp(metadata, *args, **kwargs)

    failing_metadata = [None]
    real_save_snapshot = views.CallExecutionRerunView._save_eval_snapshot

    def save_snapshot(self, call_execution):
        if call_execution.id == failing.id:
            call_execution.call_metadata = call_execution.call_metadata or {}
            failing_metadata[0] = call_execution.call_metadata
        return real_save_snapshot(self, call_execution)

    with (
        patch.object(views, "stamp_eval_queued", stamp),
        patch.object(
            views.CallExecutionRerunView, "_save_eval_snapshot", save_snapshot
        ),
        patch(
            "simulate.temporal.client.rerun_call_executions",
            return_value={"workflow_id": "kept-partial", "merged": False},
        ),
    ):
        response = auth_client.post(
            f"/simulate/test-executions/{execution.id}/rerun-calls/",
            {
                "rerun_type": "eval_only",
                "call_execution_ids": [str(failing.id), str(passing.id)],
            },
            format="json",
        )

    assert response.status_code == 200, response.content
    failing.refresh_from_db()
    assert failing.eval_outputs != {}
    assert _kept_rows(failing) == []
    [row] = _kept_rows(passing)
    assert row.replaced_verdict == dict(VERDICT, output="Failed")


@pytest.mark.django_db
def test_an_eval_only_rerun_whose_dispatch_fails_puts_the_verdicts_back_and_keeps_no_row(
    auth_client, run_test, eval_template
):
    config, _, execution, call = _graded_call(run_test, eval_template)

    with patch(
        "simulate.temporal.client.rerun_call_executions",
        side_effect=RuntimeError("temporal is down"),
    ):
        response = auth_client.post(
            f"/simulate/test-executions/{execution.id}/rerun-calls/",
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

    assert response.status_code == 200, response.content
    call.refresh_from_db()
    assert call.eval_outputs[str(config.id)] == VERDICT
    assert _kept_rows(call) == []


@pytest.mark.django_db
def test_a_bulk_rerun_forgets_only_the_rows_of_the_run_it_put_back(
    auth_client, run_test, eval_template
):
    config, _, first_execution, first_call = _graded_call(run_test, eval_template)
    second_execution = _run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    second_call = _call(
        second_execution,
        metadata=dict(CLOSED),
        eval_outputs={str(config.id): dict(VERDICT, output="Failed")},
    )

    def dispatch(*, test_execution_id, **kwargs):
        if test_execution_id == str(second_execution.id):
            raise RuntimeError("temporal is down")
        return {"workflow_id": "kept-first", "merged": False}

    with patch("simulate.temporal.client.rerun_call_executions", side_effect=dispatch):
        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/rerun-test-executions/",
            {
                "rerun_type": "eval_only",
                "test_execution_ids": [
                    str(first_execution.id),
                    str(second_execution.id),
                ],
            },
            format="json",
        )

    assert response.status_code == 200, response.content
    second_call.refresh_from_db()
    assert second_call.eval_outputs[str(config.id)] == dict(VERDICT, output="Failed")
    assert _kept_rows(second_call) == []
    first_call.refresh_from_db()
    assert first_call.eval_outputs == {}
    first_rows = _kept_rows(first_call)
    assert len(first_rows) == 3
    assert len({row.replacement_id for row in first_rows}) == 1


@pytest.mark.django_db
def test_one_bulk_rerun_of_two_runs_keeps_their_verdicts_under_one_replacement(
    auth_client, run_test, eval_template
):
    config, _, first_execution, first_call = _graded_call(run_test, eval_template)
    second_execution = _run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    second_call = _call(
        second_execution,
        metadata=dict(CLOSED),
        eval_outputs={str(config.id): dict(VERDICT, output="Failed")},
    )

    with patch(
        "simulate.temporal.client.rerun_call_executions",
        return_value={"workflow_id": "kept-both", "merged": False},
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{run_test.id}/rerun-test-executions/",
            {
                "rerun_type": "eval_only",
                "test_execution_ids": [
                    str(first_execution.id),
                    str(second_execution.id),
                ],
            },
            format="json",
        )

    assert response.status_code == 200, response.content
    rows = _kept_rows(first_call) + _kept_rows(second_call)
    assert len(rows) == 4
    assert len({row.replacement_id for row in rows}) == 1


@pytest.mark.django_db
def test_a_put_back_forgets_only_the_rows_of_calls_it_restored(run_test, eval_template):
    from simulate.services.eval_result_store import ClearedVerdicts
    from simulate.views import run_test as views

    _, _, execution, restored = _graded_call(run_test, eval_template)
    no_snapshot = _call(execution, metadata=dict(CLOSED), eval_outputs={"k": VERDICT})
    views._save_eval_snapshot(restored)
    cleared = ClearedVerdicts()
    for call in (restored, no_snapshot):
        cleared.add(call)
        call.eval_outputs = {}
        call.save(update_fields=["eval_outputs"])
    cleared.keep()

    views._restore_eval_only_rerun_state(
        [str(restored.id), str(no_snapshot.id)],
        "temporal is down",
        replacement_id=cleared.replacement_id,
    )

    restored.refresh_from_db()
    assert restored.eval_outputs != {}
    assert _kept_rows(restored) == []
    # Nothing was put back on this call, so its kept verdict is the only copy.
    no_snapshot.refresh_from_db()
    assert no_snapshot.eval_outputs == {}
    [row] = _kept_rows(no_snapshot)
    assert row.replaced_verdict == VERDICT
