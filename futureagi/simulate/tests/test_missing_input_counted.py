"""An eval whose input is empty on a call is stored and counted as missing.

The shared evaluator refuses an empty required input with
``MissingEvalInputError``. Both simulation writers (the native executor and
the Temporal activity) store that refusal as a skipped result with the fixed
reason code ``input_missing`` and the names of the empty fields, instead of
an evaluator error. The run page counts it on its own: not evaluated, not
errored, and the per-eval counts still add up to the run's calls.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from model_hub.models.choices import DatasetSourceChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.eval_input_validation import (
    MissingEvalInputError,
    validate_eval_inputs,
)
from simulate.models import AgentDefinition, Scenarios
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution

INPUT_MISSING = "input_missing"

# --- fixtures (copied, not imported, from the other simulate tests) ------------


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Agent under test",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Simulator",
        prompt="You are a caller.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def scenario(db, organization, user, workspace, agent_definition):
    dataset = Dataset.no_workspace_objects.create(
        name="Scenario rows",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    return Scenarios.objects.create(
        name="Scenario",
        description="Scenario under test",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    run_test = RunTest.objects.create(
        name="Run",
        description="Run under test",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return run_test


@pytest.fixture
def test_execution(db, run_test, simulator_agent, agent_definition, scenario):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        scenario_ids=[str(scenario.id)],
    )


@pytest.fixture
def call_execution(db, test_execution, scenario):
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+15551230000",
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        call_summary="",
    )


@pytest.fixture
def eval_template(db, organization):
    return EvalTemplate.objects.create(
        name="Summary is grounded",
        config={"prompt": "Judge the summary", "required_keys": ["summary"]},
        organization=organization,
    )


@pytest.fixture
def eval_config(db, run_test, eval_template):
    return SimulateEvalConfig.objects.create(
        name=f"Eval {uuid.uuid4().hex[:6]}",
        eval_template=eval_template,
        run_test=run_test,
        mapping={"summary": "call.call_summary"},
        config={},
        status=StatusType.COMPLETED.value,
    )


TRANSCRIPT_DATA = {
    "transcript": "Hello.",
    "voice_recording": "",
    "assistant_recording": "",
    "customer_recording": "",
    "stereo_recording": "",
    "user_chat_transcript": "",
    "assistant_chat_transcript": "",
}


# --- the error names the empty fields ----------------------------------------


def _template(config):
    return EvalTemplate(name="t", config=config)


@pytest.mark.parametrize(
    ("config", "values", "mapped", "fields"),
    [
        pytest.param(
            {"required_keys": ["summary", "transcript"]},
            {"summary": "", "transcript": "Hello."},
            None,
            ("summary",),
            id="one-required-input-empty",
        ),
        pytest.param(
            {"required_keys": ["a", "b"], "custom_eval": True},
            {"a": "", "b": None},
            None,
            ("a", "b"),
            id="custom-eval-every-input-empty",
        ),
        pytest.param(
            {"required_keys": ["b", "a"], "custom_eval": True},
            {"a": "x"},
            [],
            ("a", "b"),
            id="custom-eval-nothing-mapped",
        ),
    ],
)
def test_missing_input_error_names_the_empty_fields(config, values, mapped, fields):
    with pytest.raises(MissingEvalInputError) as raised:
        validate_eval_inputs(_template(config), values, mapped_keys=mapped)
    assert raised.value.fields == fields


# --- both writers store it as missing, not as an error ------------------------


def _native(eval_config, call_execution):
    from simulate.services.test_executor import TestExecutor

    with (
        patch("simulate.services.test_executor.close_old_connections", lambda: None),
        patch(
            "simulate.services.test_executor.run_eval_func",
            side_effect=_refuse_empty_summary,
        ) as evaluator,
    ):
        TestExecutor()._run_single_simulate_evaluation(
            eval_config, call_execution, dict(TRANSCRIPT_DATA)
        )
    return evaluator


def _activity(eval_config, call_execution):
    from simulate.temporal.activities.xl import _run_single_evaluation

    # The activity imports the evaluator inside the function.
    with (
        patch("simulate.temporal.activities.xl.close_old_connections", lambda: None),
        patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=_refuse_empty_summary,
        ) as evaluator,
    ):
        _run_single_evaluation(eval_config, call_execution, dict(TRANSCRIPT_DATA))
    return evaluator


def _refuse_empty_summary(*, template, mappings, **_):
    """The shared evaluator's own check, on the inputs the writer sent."""
    validate_eval_inputs(template, mappings)
    raise AssertionError("the empty input should have been refused")


@pytest.mark.django_db
@pytest.mark.parametrize("writer", [_native, _activity], ids=["native", "activity"])
def test_an_empty_input_is_stored_as_missing_not_as_an_error(
    writer, eval_config, call_execution
):
    """The call's summary is empty, so the eval's only input is empty. The
    writer stores a skipped result with the reason code and the field, does
    not raise, and does not mark the eval itself as failed."""
    evaluator = writer(eval_config, call_execution)

    evaluator.assert_called_once()
    call_execution.refresh_from_db()
    stored = call_execution.eval_outputs[str(eval_config.id)]
    assert stored["status"] == "skipped"
    assert stored["reason_code"] == INPUT_MISSING
    assert stored["missing_fields"] == ["summary"]
    assert "summary" in stored["reason"]
    assert stored["output"] is None
    assert "error" not in stored
    assert stored["name"] == eval_config.name
    eval_config.refresh_from_db()
    assert eval_config.status == StatusType.COMPLETED.value


@pytest.mark.django_db
@pytest.mark.parametrize("writer", ["native", "activity"])
def test_another_evaluator_error_is_still_an_error(writer, eval_config, call_execution):
    """Only an empty input is missing. Any other refusal stays an error."""
    target = (
        "simulate.services.test_executor.run_eval_func"
        if writer == "native"
        else "model_hub.views.utils.evals.run_eval_func"
    )
    with (
        patch("simulate.services.test_executor.close_old_connections", lambda: None),
        patch("simulate.temporal.activities.xl.close_old_connections", lambda: None),
        patch(target, side_effect=RuntimeError("model timed out")),
        pytest.raises(RuntimeError),
    ):
        if writer == "native":
            from simulate.services.test_executor import TestExecutor

            TestExecutor()._run_single_simulate_evaluation(
                eval_config, call_execution, dict(TRANSCRIPT_DATA)
            )
        else:
            from simulate.temporal.activities.xl import _run_single_evaluation

            _run_single_evaluation(eval_config, call_execution, dict(TRANSCRIPT_DATA))

    call_execution.refresh_from_db()
    stored = call_execution.eval_outputs[str(eval_config.id)]
    assert stored["status"] == StatusType.FAILED.value
    assert stored["error"] == "error"
    assert "reason_code" not in stored


# --- the run page counts it on its own ----------------------------------------


@pytest.mark.django_db
def test_run_analytics_count_missing_inputs_apart_and_the_counts_add_up(
    test_execution, scenario, eval_config
):
    """Six calls: one passed, one failed, one errored, two with the input
    missing, one never graded. The eval's row shows each on its own, keeps
    the missing inputs out of the evaluated and errored counts, and the
    counts add up to the six calls."""
    from simulate.services.run_results_v3_queries import build_run_analytics

    key = str(eval_config.id)
    missing = {
        "status": "skipped",
        "reason_code": INPUT_MISSING,
        "missing_fields": ["summary"],
        "reason": "No input received for 'summary'. Please check your input.",
        "output": None,
        "output_type": "Pass/Fail",
        "name": eval_config.name,
    }
    stored = [
        {key: {"status": "completed", "output": "Passed", "output_type": "Pass/Fail"}},
        {key: {"status": "completed", "output": "Failed", "output_type": "Pass/Fail"}},
        {key: {"status": "failed", "error": "error", "output": None}},
        {key: missing},
        {key: dict(missing)},
        {},
    ]
    for index, outputs in enumerate(stored):
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number=f"+1555000000{index}",
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            eval_outputs=outputs,
            call_metadata={"eval_started": True, "eval_completed": True},
        )

    analytics = build_run_analytics(test_execution)

    (row,) = [item for item in analytics["evaluations"] if item["id"] == key]
    assert (row["passed"], row["failed"], row["measured"]) == (1, 1, 2)
    assert row["errored"] == 1
    assert row["input_missing"] == 2
    assert row["missing"] == 1
    assert row["measured"] + row["errored"] + row["input_missing"] + row[
        "missing"
    ] == len(stored)
