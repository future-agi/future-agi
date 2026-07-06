"""End-to-end behavior tests for the eval-mapping resolver on the rerun /
add-eval / update-mapping / chat-finalize path.

Constructs a real Django model chain against Postgres (via bin/test
docker-compose.test.yml), invokes
`TestExecutor._run_single_simulate_evaluation` directly, and mocks
`run_eval_func` at its import boundary so no LLM call is issued.
Assertions read the `mappings` payload handed to the mock (proving the
resolver produced the correct values) and the persisted
`SimulateEvalConfig.status` (proving the model save happens).
"""

import uuid
from unittest.mock import patch

import pytest

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios
from simulate.models.agent_version import AgentVersion
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services.test_executor import TestExecutor


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+15551230000",
        inbound=True,
        description="Test agent for resolver behavior",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(db, agent_definition, organization, workspace):
    return AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        configuration_snapshot={
            "description": "You are a helpful agent.",
            "assistant_id": "test-assistant-id",
        },
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Test Simulator",
        prompt="You are a test simulator agent.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="row value")
    return dataset


@pytest.fixture
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    return Scenarios.objects.create(
        name="Test Scenario",
        description="Test scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    rt = RunTest.objects.create(
        name="Test Run",
        description="Test run",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(
    db, run_test, simulator_agent, agent_definition, agent_version, scenario
):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        agent_version=agent_version,
        scenario_ids=[str(scenario.id)],
    )


@pytest.fixture
def call_execution(db, test_execution, scenario, agent_version):
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+15551230000",
        status=CallExecution.CallStatus.COMPLETED,
        agent_version=agent_version,
        recording_url="s3://bucket/rec.mp3",
        stereo_recording_url="s3://bucket/stereo.mp3",
        call_summary="Customer called about order 123.",
        ended_reason="customer-ended-call",
        duration_seconds=120,
        overall_score=8,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
    )


@pytest.fixture
def eval_template(db, organization):
    return EvalTemplate.objects.create(
        name="Score Eval",
        config={"prompt": "Score the interaction"},
        organization=organization,
    )


@pytest.fixture
def transcript_data():
    return {
        "transcript": "Hello. Yes, order 123 shipped.",
        "voice_recording": "s3://bucket/rec.mp3",
        "assistant_recording": "s3://bucket/asst.mp3",
        "customer_recording": "s3://bucket/cust.mp3",
        "stereo_recording": "s3://bucket/stereo.mp3",
        "user_chat_transcript": "",
        "assistant_chat_transcript": "",
    }


def _make_eval(mapping, run_test, eval_template, config=None):
    return SimulateEvalConfig.objects.create(
        name=f"Eval {uuid.uuid4().hex[:6]}",
        eval_template=eval_template,
        run_test=run_test,
        mapping=mapping,
        config=config or {},
    )


def _run(eval_config, call_execution, transcript_data):
    return TestExecutor()._run_single_simulate_evaluation(
        eval_config, call_execution, transcript_data
    )


_SUCCESS_STUB = {"output": "8", "reason": "ok", "output_type": "score"}


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestDotFormMappingResolution:
    """The resolver on the rerun path must map every FE-emitted dot-form value
    to the same runtime value the initial-run path (xl.py) maps it to.
    """

    @patch("simulate.services.test_executor.run_eval_func")
    def test_call_transcript_resolves_to_transcript_data_value(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["conversation"] == "Hello. Yes, order 123 shipped."

    @patch("simulate.services.test_executor.run_eval_func")
    def test_call_recording_url_resolves_to_call_execution_field(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"answer": "call.recording_url"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["answer"] == "s3://bucket/rec.mp3"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_call_summary_resolves_to_call_summary_field(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"summary": "call.summary"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["summary"] == "Customer called about order 123."

    @patch("simulate.services.test_executor.run_eval_func")
    def test_call_agent_prompt_resolves_from_snapshot_description(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"system_prompt": "call.agent_prompt"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["system_prompt"] == "You are a helpful agent."

    @patch("simulate.services.test_executor.run_eval_func")
    def test_call_status_resolves_via_context_map(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"s": "call.status"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["s"] == CallExecution.CallStatus.COMPLETED.value

    @patch("simulate.services.test_executor.run_eval_func")
    def test_agent_dot_keys_resolve_via_context_map(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"name": "agent.name", "desc": "agent.description"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["name"] == "Test Agent"
        assert mappings["desc"] == "You are a helpful agent."


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestLegacyUnderscoreCompatibility:
    """Legacy underscore-form mapping values must keep resolving. Old
    persisted eval configs from before the dot-form vocabulary must not
    regress.
    """

    @patch("simulate.services.test_executor.run_eval_func")
    def test_underscore_transcript_still_resolves(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"input": "transcript"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["input"] == "Hello. Yes, order 123 shipped."

    @patch("simulate.services.test_executor.run_eval_func")
    def test_underscore_agent_prompt_still_resolves_from_snapshot(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"sp": "agent_prompt"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["sp"] == "You are a helpful agent."


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestEvalConfigStatusPersistence:
    """`SimulateEvalConfig.status` must be persisted on both success and
    failure so downstream selectors filtering on the model see the
    resolved state.
    """

    @patch("simulate.services.test_executor.run_eval_func")
    def test_status_completed_after_successful_run(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"x": "call.transcript"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        ec.refresh_from_db()
        assert ec.status == StatusType.COMPLETED.value

    @patch("simulate.services.test_executor.run_eval_func")
    def test_status_failed_after_exception(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.side_effect = RuntimeError("boom")
        ec = _make_eval({"x": "call.transcript"}, run_test, eval_template)

        with pytest.raises(RuntimeError):
            _run(ec, call_execution, transcript_data)

        ec.refresh_from_db()
        assert ec.status == StatusType.FAILED.value


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestCallContextPropagation:
    """`call_context` is passed to `run_eval_func` only when the eval
    opts in via `config.data_injection.call_context`. Agent evals that
    read the call payload through `explore_trace` depend on this.
    """

    @patch("simulate.services.test_executor.run_eval_func")
    def test_call_context_is_none_when_data_injection_disabled(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"x": "call.transcript"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["call_context"] is None

    @patch("simulate.services.test_executor.run_eval_func")
    def test_call_context_populated_when_data_injection_enabled(
        self,
        mock_run,
        run_test,
        call_execution,
        transcript_data,
        eval_template,
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"x": "call.transcript"},
            run_test,
            eval_template,
            config={"data_injection": {"call_context": True}},
        )

        _run(ec, call_execution, transcript_data)

        ctx = mock_run.call_args.kwargs["call_context"]
        assert ctx is not None
        assert ctx["id"] == str(call_execution.id)
        assert ctx["recording_url"] == "s3://bucket/rec.mp3"
        assert ctx["call_summary"] == "Customer called about order 123."
        assert ctx["duration_seconds"] == 120
        assert ctx["overall_score"] == 8.0
