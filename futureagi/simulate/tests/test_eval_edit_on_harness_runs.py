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
from simulate.services.hosted_harness import create_hosted_job
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
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "SDK runs count as harness runs only once is_harness_run_test also "
        "reads the calls' harness_evaluations"
    ),
)
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
def test_a_bare_rerun_of_a_harness_result_column_is_refused(
    auth_client, harness_run, suite_template, grading
):
    config = _harness_column(harness_run, suite_template)
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
    assert response.json()["result"] == REFUSED
    assert _row(config) == before
    assert _call_state(call) == cell
    grading.assert_not_called()


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
