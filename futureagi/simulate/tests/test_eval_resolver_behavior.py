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

import copy
import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
from simulate.services.test_executor import (
    TestExecutor,
    _run_simulate_evaluations_task,
)
from simulate.utils.eval_summary import derive_kpi_output_type
from simulate.utils.verdicts import has_stored_verdict


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
        response_time_ms=1500,
        avg_agent_latency_ms=8652,
        avg_stop_time_after_interruption_ms=400,
        user_interruption_count=2,
        user_interruption_rate=0.1,
        user_wpm=120.5,
        bot_wpm=150.2,
        talk_ratio=0.45,
        ai_interruption_count=1,
        ai_interruption_rate=0.05,
        cost_cents=250,
        customer_cost_cents=180,
        conversation_metrics_data={
            "avg_latency_ms": 900.5,
            "total_tokens": 3200,
            "input_tokens": 1800,
            "output_tokens": 1400,
            "turn_count": 12,
            "agent_talk_percentage": 55.5,
            "csat_score": 4.5,
        },
        provider_call_data={"vapi": {"call_id": "vapi-12345"}},
        customer_cost_breakdown={
            "llm": {"cost": 0.039254, "promptTokens": 18667, "completionTokens": 240},
            "stt": {"cost": 0.01405, "minutes": 1.83},
            "tts": {"cost": 0.010813, "characters": 983},
            "vapi": {"cost": 0.0},
        },
        customer_latency_metrics={
            "systemMetrics": {"overall_latency": 850, "p95_latency": 1200},
            "turnLatencies": [120, 340, 780],
        },
        tool_outputs=[
            {"name": "search", "duration_ms": 210, "result": {"status": "ok"}},
            {"name": "fetch", "duration_ms": 88, "result": {"status": "ok"}},
        ],
    )


@pytest.fixture
def chat_call_execution(db, test_execution, scenario, agent_version):
    """Chat-sim shape: voice-only metrics stay null; conversation_metrics_data
    populated."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        agent_version=agent_version,
        call_summary="Chat completed successfully.",
        ended_reason="chat-finished",
        duration_seconds=45,
        overall_score=9,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        conversation_metrics_data={
            "avg_latency_ms": 320.0,
            "total_tokens": 1800,
            "input_tokens": 1100,
            "output_tokens": 700,
            "turn_count": 8,
            "agent_talk_percentage": 50.0,
            "csat_score": 4.0,
        },
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


def _run_xl(eval_config, call_execution, transcript_data):
    """Exercise xl.py's `_run_single_evaluation` (temporal-activity path)."""
    from simulate.temporal.activities.xl import _run_single_evaluation

    return _run_single_evaluation(eval_config, call_execution, transcript_data)


_SUCCESS_STUB = {"output": "8", "reason": "ok", "output_type": "score"}


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestDotFormMappingResolution:
    """Every FE-emitted dot-form value resolves to the same runtime value on both eval-runner paths."""

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
    """Legacy underscore-form mapping values keep resolving (pre-dot-form configs must not regress)."""

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
    """`SimulateEvalConfig.status` is persisted on both success and failure paths."""

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
    """`call_context` reaches `run_eval_func` only when the eval opts in via `config.data_injection.call_context`."""

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


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestVoiceMetricsAndLatencyResolution:
    """Every raw-callData scalar the FE picker exposes resolves on the BE for a voice sim."""

    @patch("simulate.services.test_executor.run_eval_func")
    def test_avg_agent_latency_bare_and_dot_form_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"bare": "avg_agent_latency", "dot": "call.avg_agent_latency"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["bare"] == "8652"
        assert m["dot"] == "8652"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_response_time_ms_and_seconds_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"ms": "response_time_ms", "sec": "response_time"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["ms"] == "1500"
        assert m["sec"] == "1.5"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_voice_interruption_and_wpm_metrics_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "uic": "user_interruption_count",
                "uir": "user_interruption_rate",
                "aic": "ai_interruption_count",
                "air": "ai_interruption_rate",
                "uw": "user_wpm",
                "bw": "bot_wpm",
                "tr": "talk_ratio",
            },
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["uic"] == "2"
        assert m["uir"] == "0.1"
        assert m["aic"] == "1"
        assert m["air"] == "0.05"
        assert m["uw"] == "120.5"
        assert m["bw"] == "150.2"
        assert m["tr"] == "0.45"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_cost_and_provider_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "cost": "cost_cents",
                "customer_cost": "customer_cost_cents",
                "prov": "provider",
                "dot_prov": "call.provider",
            },
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["cost"] == "250"
        assert m["customer_cost"] == "180"
        assert m["prov"] == "vapi"
        assert m["dot_prov"] == "vapi"


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestChatMetricsResolution:
    """Chat metrics from conversation_metrics_data resolve under both bare and dot form."""

    @patch("simulate.services.test_executor.run_eval_func")
    def test_chat_token_and_turn_metrics_resolve_bare(
        self,
        mock_run,
        run_test,
        chat_call_execution,
        transcript_data,
        eval_template,
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "tt": "total_tokens",
                "it": "input_tokens",
                "ot": "output_tokens",
                "tc": "turn_count",
                "atp": "agent_talk_percentage",
                "csat": "csat_score",
                "lat": "avg_latency_ms",
            },
            run_test,
            eval_template,
        )

        _run(ec, chat_call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["tt"] == "1800"
        assert m["it"] == "1100"
        assert m["ot"] == "700"
        assert m["tc"] == "8"
        assert m["atp"] == "50.0"
        assert m["csat"] == "4.0"
        assert m["lat"] == "320.0"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_chat_metrics_resolve_dot_form(
        self,
        mock_run,
        run_test,
        chat_call_execution,
        transcript_data,
        eval_template,
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "tt": "call.total_tokens",
                "csat": "call.csat_score",
                "lat": "call.avg_latency_ms",
            },
            run_test,
            eval_template,
        )

        _run(ec, chat_call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["tt"] == "1800"
        assert m["csat"] == "4.0"
        assert m["lat"] == "320.0"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_voice_only_metrics_without_chat_equivalent_resolve_empty_on_chat_sim(
        self,
        mock_run,
        run_test,
        chat_call_execution,
        transcript_data,
        eval_template,
    ):
        """Metrics with no chat-side equivalent (WPM, interruption counts,
        stop-time-after-interruption) resolve to empty string on chat sims,
        not a mismatch error. Latency and talk metrics DO have chat equivalents
        and fall back separately.
        """
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "uw": "user_wpm",
                "bw": "bot_wpm",
                "uic": "user_interruption_count",
                "stop": "avg_stop_time_after_interruption_ms",
            },
            run_test,
            eval_template,
        )

        _run(ec, chat_call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["uw"] == ""
        assert m["bw"] == ""
        assert m["uic"] == ""
        assert m["stop"] == ""

    @patch("simulate.services.test_executor.run_eval_func")
    def test_chat_only_metrics_resolve_empty_on_voice_sim_with_no_metrics(
        self, mock_run, run_test, transcript_data, eval_template, agent_version,
        test_execution, scenario,
    ):
        """Voice sim with no conversation_metrics_data resolves chat metrics to empty."""
        ce = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+15551230000",
            status=CallExecution.CallStatus.COMPLETED,
            agent_version=agent_version,
            recording_url="s3://bucket/rec.mp3",
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
        )
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"tt": "total_tokens", "csat": "csat_score"},
            run_test,
            eval_template,
        )

        _run(ec, ce, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["tt"] == ""
        assert m["csat"] == ""


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestGenericDottedPathResolution:
    """Arbitrary-depth walker: dicts, list indices, and mismatch fall-through."""

    @patch("simulate.services.test_executor.run_eval_func")
    def test_deep_dict_path_customer_cost_breakdown_llm_cost(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"cost": "customer_cost_breakdown.llm.cost"}, run_test, eval_template
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["cost"] == "0.039254"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_deep_dict_path_with_call_prefix_matches_bare(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"a": "call.customer_cost_breakdown.stt.cost", "b": "customer_cost_breakdown.stt.cost"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["a"] == m["b"] == "0.01405"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_camelcase_leaf_key_customer_cost_breakdown_llm_prompt_tokens(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"pt": "customer_cost_breakdown.llm.promptTokens"}, run_test, eval_template
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["pt"] == "18667"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_nested_dict_path_customer_latency_system_metrics(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"lat": "customer_latency_metrics.systemMetrics.overall_latency"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["lat"] == "850"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_list_index_path_tool_outputs_by_index(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"first": "tool_outputs.0.name", "second_dur": "tool_outputs.1.duration_ms"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["first"] == "search"
        assert m["second_dur"] == "88"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_list_index_then_dict_then_dict_deeper_path(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"status": "tool_outputs.0.result.status"}, run_test, eval_template
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["status"] == "ok"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_arbitrary_depth_walking_stays_correct(
        self, mock_run, run_test, transcript_data, eval_template, test_execution,
        scenario, agent_version,
    ):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": 42}}}}}}}}}}
        ce = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            agent_version=agent_version,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            customer_cost_breakdown=deep,
        )
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"leaf": "customer_cost_breakdown.a.b.c.d.e.f.g.h.i.j"}, run_test, eval_template
        )

        _run(ec, ce, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["leaf"] == "42"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_missing_intermediate_key_resolves_to_empty(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"missing": "customer_cost_breakdown.no_such_key.cost"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["missing"] == ""

    @patch("simulate.services.test_executor.run_eval_func")
    def test_out_of_range_list_index_resolves_to_empty(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"missing": "tool_outputs.99.name"}, run_test, eval_template
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["missing"] == ""

    @patch("simulate.services.test_executor.run_eval_func")
    def test_unrecognised_head_falls_through_to_mismatch_error(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"x": "this_field_does_not_exist_on_model.foo.bar"},
            run_test,
            eval_template,
        )

        with pytest.raises(Exception):
            _run(ec, call_execution, transcript_data)


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestWalkerAttributeSafety:
    """Dunder / private / callable attrs must not resolve; user paths cannot pivot into module globals."""

    @patch("simulate.services.test_executor.run_eval_func")
    def test_dunder_class_globals_settings_secret_key_does_not_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        ec = _make_eval(
            {"x": "call.__class__.__init__.__globals__.settings.SECRET_KEY"},
            run_test,
            eval_template,
        )
        with pytest.raises(Exception):
            _run(ec, call_execution, transcript_data)

    @patch("simulate.services.test_executor.run_eval_func")
    def test_dunder_dict_does_not_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        ec = _make_eval(
            {"x": "call.__dict__"}, run_test, eval_template
        )
        with pytest.raises(Exception):
            _run(ec, call_execution, transcript_data)

    @patch("simulate.services.test_executor.run_eval_func")
    def test_django_private_meta_does_not_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        ec = _make_eval(
            {"x": "call._meta.app_label"}, run_test, eval_template
        )
        with pytest.raises(Exception):
            _run(ec, call_execution, transcript_data)

    @patch("simulate.services.test_executor.run_eval_func")
    def test_django_private_state_does_not_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        ec = _make_eval({"x": "call._state.db"}, run_test, eval_template)
        with pytest.raises(Exception):
            _run(ec, call_execution, transcript_data)

    @patch("simulate.services.test_executor.run_eval_func")
    def test_objects_manager_callable_does_not_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        ec = _make_eval({"x": "call.objects.all"}, run_test, eval_template)
        with pytest.raises(Exception):
            _run(ec, call_execution, transcript_data)

    @patch("simulate.services.test_executor.run_eval_func")
    def test_save_method_does_not_resolve(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        ec = _make_eval({"x": "call.save"}, run_test, eval_template)
        with pytest.raises(Exception):
            _run(ec, call_execution, transcript_data)


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestSubjectDispatchRobustness:
    """Walker resolves against any subject root and coerces snake_case <-> camelCase per segment."""

    @patch("simulate.services.test_executor.run_eval_func")
    def test_agent_version_snapshot_deep_path_via_subject_prefix(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"desc": "agent_version.configuration_snapshot.description"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        assert (
            mock_run.call_args.kwargs["mappings"]["desc"]
            == "You are a helpful agent."
        )

    @patch("simulate.services.test_executor.run_eval_func")
    def test_persona_bare_attribute_via_subject_prefix(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"vp": "persona.voice_provider"}, run_test, eval_template
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["vp"] == "elevenlabs"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_simulation_bare_attribute_via_subject_prefix(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"src": "simulation.source_type"}, run_test, eval_template
        )

        _run(ec, call_execution, transcript_data)

        # source_type is a CharField on RunTest; walker returns its default.
        assert (
            mock_run.call_args.kwargs["mappings"]["src"]
            == str(run_test.source_type)
        )

    @patch("simulate.services.test_executor.run_eval_func")
    def test_bare_head_on_non_call_subject_falls_through_to_agent_version(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        """Bare head unknown to `call` / `agent` falls through to `agent_version` via subject iteration."""
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"desc": "configuration_snapshot.description"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        assert (
            mock_run.call_args.kwargs["mappings"]["desc"]
            == "You are a helpful agent."
        )

    @patch("simulate.services.test_executor.run_eval_func")
    def test_camelcase_head_coerces_to_snake_case_call_attribute(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        """FE-emitted `customerCostBreakdown.llm.cost` resolves the same as
        the snake_case BE field name `customer_cost_breakdown.llm.cost`."""
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"cost": "customerCostBreakdown.llm.cost"}, run_test, eval_template
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["cost"] == "0.039254"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_snake_case_leaf_coerces_to_camelcase_dict_key(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        """Snake-case leaf coerces to camelCase payload key, including under a `call.` prefix."""
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "lat": "customer_latency_metrics.system_metrics.overall_latency",
                "tokens": "call.customer_cost_breakdown.llm.prompt_tokens",
            },
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["lat"] == "850"
        assert mappings["tokens"] == "18667"

    @patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
    def test_walker_branch_runs_on_xl_temporal_activity_path(
        self, run_test, call_execution, transcript_data, eval_template
    ):
        """Regression guard: exercises xl.py's `_run_single_evaluation` (test_executor path is the wider suite)."""
        from model_hub.views.utils import evals as evals_mod

        with patch.object(
            evals_mod, "run_eval_func", return_value=_SUCCESS_STUB
        ) as mock_run:
            ec = _make_eval(
                {"cost": "customer_cost_breakdown.llm.cost"},
                run_test,
                eval_template,
            )

            _run_xl(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["cost"] == "0.039254"


@pytest.mark.django_db
@patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
def test_xl_error_write_stamps_failed_status(
    run_test, call_execution, transcript_data, eval_template
):
    """``xl.py::_run_single_evaluation``'s generic-exception write actually
    runs and stamps ``"status": StatusType.FAILED.value`` on the row it
    writes -- and that row does not seal under ``has_stored_verdict``. This
    drives the real function through its real error path, so the writer and
    the predicate are tied together by a test that can go red if either
    drifts.
    """
    from model_hub.views.utils import evals as evals_mod

    ec = _make_eval({"x": "call.transcript"}, run_test, eval_template)
    with patch.object(evals_mod, "run_eval_func", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            _run_xl(ec, call_execution, transcript_data)

    call_execution.refresh_from_db()
    row = call_execution.eval_outputs[str(ec.id)]
    assert row["status"] == StatusType.FAILED.value
    assert has_stored_verdict(call_execution, ec.id) is False


@pytest.mark.django_db
@patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
def test_xl_mismatch_error_write_stamps_failed_status(
    run_test, call_execution, transcript_data, eval_template
):
    """``xl.py::_run_single_evaluation``'s column-mismatch write also stamps
    ``"status": StatusType.FAILED.value``, but its ``raise ValueError`` is
    inside the same ``try`` the generic-exception ``except`` wraps, so
    whatever it writes is immediately overwritten by that second write on
    the way out -- the final persisted row alone can't show whether the
    first write's stamp existed. A spy on ``call_execution.save`` (wraps the
    real save) captures a deep copy of ``eval_outputs`` at each save call, so
    the first snapshot is exactly what the mismatch branch wrote, before the
    second write clobbers it.

    A mapping value that is a syntactically valid UUID but matches no real
    ``Column`` reaches the "not available in the test scenario(s)" message
    without raising anywhere upstream.
    """
    ec = _make_eval({"x": str(uuid.uuid4())}, run_test, eval_template)

    saved_snapshots = []
    real_save = call_execution.save

    def _spy_save(*args, **kwargs):
        saved_snapshots.append(copy.deepcopy(call_execution.eval_outputs))
        return real_save(*args, **kwargs)

    with patch.object(call_execution, "save", side_effect=_spy_save):
        with pytest.raises(ValueError):
            _run_xl(ec, call_execution, transcript_data)

    assert len(saved_snapshots) == 2, (
        "expected exactly two call_execution.save() calls: the mismatch "
        "branch's write (xl.py:959) and the generic-exception write "
        "(xl.py:1091) that re-raises and overwrites it"
    )
    mismatch_row = saved_snapshots[0][str(ec.id)]
    assert mismatch_row["status"] == StatusType.FAILED.value
    assert (
        has_stored_verdict(SimpleNamespace(eval_outputs=saved_snapshots[0]), ec.id)
        is False
    )

    # The final, persisted row (the second, generic-exception write) also
    # stamps status and also does not seal -- proves xl.py:1091 independently
    # of test_xl_error_write_stamps_failed_status's success-path setup.
    call_execution.refresh_from_db()
    final_row = call_execution.eval_outputs[str(ec.id)]
    assert final_row["status"] == StatusType.FAILED.value
    assert has_stored_verdict(call_execution, ec.id) is False


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestComputedSerializerFieldsInContext:
    """FE-dropdown vs BE-resolver parity for computed / aliased serializer fields."""

    @patch("simulate.services.test_executor.run_eval_func")
    def test_bare_head_call_type_resolves_to_serializer_computed_value(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        """FE dropdown emits `call_type`; must resolve via get_call_type (computed, not a model attr)."""
        mock_run.return_value = _SUCCESS_STUB
        call_execution.call_metadata = {"call_direction": "outbound"}
        call_execution.save(update_fields=["call_metadata"])
        ec = _make_eval({"ct": "call_type"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["ct"] == "Outbound"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_bare_head_call_type_defaults_to_inbound_without_metadata(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"ct": "call_type"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["ct"] == "Inbound"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_bare_head_duration_resolves_from_duration_seconds_for_voice(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        """Voice sim: get_duration returns duration_seconds."""
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"d": "duration"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["d"] == "120"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_bare_head_audio_url_resolves_from_recording_url(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        """FE dropdown emits `audio_url`; ctx must alias to CallExecution.recording_url."""
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"a": "audio_url"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["a"] == "s3://bucket/rec.mp3"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_serializer_alias_bareheads_resolve_via_ctx(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        """Serializer FK-chain aliases resolve via explicit ctx (walker cannot bridge the rename)."""
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "sa": "simulator_agent_name",
                "ad": "agent_definition_used_name",
                "sc": "scenario",
            },
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["sa"] == "Test Simulator"
        assert mappings["ad"] == "Test Agent"
        assert mappings["sc"] == "Test Scenario"

    @patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
    def test_computed_fields_reach_xl_temporal_activity_path(
        self, run_test, call_execution, transcript_data, eval_template
    ):
        """Regression guard: computed-field ctx entries reach the temporal path too."""
        from model_hub.views.utils import evals as evals_mod

        with patch.object(
            evals_mod, "run_eval_func", return_value=_SUCCESS_STUB
        ) as mock_run:
            ec = _make_eval(
                {"ct": "call_type", "d": "duration", "a": "audio_url"},
                run_test,
                eval_template,
            )

            _run_xl(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["ct"] == "Inbound"
        assert mappings["d"] == "120"
        assert mappings["a"] == "s3://bucket/rec.mp3"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_scenario_columns_value_resolves_via_computed_subject(
        self,
        mock_run,
        run_test,
        call_execution,
        transcript_data,
        eval_template,
        dataset_for_scenario,
    ):
        """Walker traversal on the scenario_columns subject; shape drift on get_scenario_columns fails here."""
        mock_run.return_value = _SUCCESS_STUB
        row = Row.objects.filter(dataset=dataset_for_scenario).first()
        call_execution.call_metadata = {"row_id": str(row.id)}
        call_execution.save(update_fields=["call_metadata"])
        ec = _make_eval(
            {"col": "scenario_columns.situation.value"}, run_test, eval_template
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["col"] == "row value"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_scenario_info_dot_paths_resolve_via_alias(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        """`scenario.info.<field>` alias-table resolution (Scenarios has no `info` attr for the walker)."""
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "sn": "scenario.info.name",
                "sd": "scenario.info.description",
                "st": "scenario.info.type",
                "ss": "scenario.info.source",
            },
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        mappings = mock_run.call_args.kwargs["mappings"]
        assert mappings["sn"] == "Test Scenario"
        assert mappings["sd"] == "Test scenario"
        assert mappings["st"] == str(call_execution.scenario.scenario_type)
        assert mappings["ss"] == "Test source"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_context_build_survives_call_metadata_none(
        self, mock_run, run_test, call_execution, transcript_data, eval_template
    ):
        """In-memory `call.call_metadata=None` must not crash ctx build."""
        mock_run.return_value = _SUCCESS_STUB
        # In-memory-only None; DB constraint would reject save.
        call_execution.call_metadata = None
        ec = _make_eval({"s": "call.summary"}, run_test, eval_template)

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["s"] == "Customer called about order 123."

    @patch("simulate.services.test_executor.run_eval_func")
    def test_chat_sim_voice_labeled_metrics_fall_back_to_conv_metrics(
        self, mock_run, run_test, chat_call_execution, transcript_data, eval_template
    ):
        """Chat sim: voice-labeled latency + talk_ratio resolve via conv_metrics fallback."""
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"rt_ms": "response_time_ms", "rt": "response_time", "aal_ms": "avg_agent_latency_ms",
             "aal": "avg_agent_latency", "tr": "talk_ratio", "atp": "agent_talk_percentage"},
            run_test, eval_template,
        )

        _run(ec, chat_call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        # chat_call_execution: conv_metrics.avg_latency_ms=320.0, agent_talk_percentage=50.0
        assert m["rt_ms"] == m["aal_ms"] == m["aal"] == "320.0"
        assert m["rt"] == "0.32"
        assert m["tr"] == "0.5"  # 50.0 / 100
        assert m["atp"] == "50.0"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_voice_sim_chat_labeled_metrics_fall_back_to_model_fields(
        self, mock_run, run_test, transcript_data, eval_template,
        test_execution, scenario, agent_version,
    ):
        """Voice sim without conv_metrics: chat-labeled fields fall back to model."""
        ce = CallExecution.objects.create(
            test_execution=test_execution, scenario=scenario, agent_version=agent_version,
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            avg_agent_latency_ms=8652, talk_ratio=0.62, overall_score=7,
        )
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"lat": "avg_latency_ms", "atp": "agent_talk_percentage", "c": "csat_score"},
            run_test, eval_template,
        )

        _run(ec, ce, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["lat"] == "8652"
        assert m["atp"] == "62.0"  # 0.62 * 100
        assert m["c"] == "7"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_chat_sim_zero_valued_metrics_survive_fallback(
        self, mock_run, run_test, transcript_data, eval_template,
        test_execution, scenario, agent_version,
    ):
        """A legitimate 0 / 0.0 / 0-ms on the primary source must not be
        clobbered by the cross-modality fallback. If someone rewrites
        `is not None` -> `or` this test fails loudly."""
        ce = CallExecution.objects.create(
            test_execution=test_execution, scenario=scenario, agent_version=agent_version,
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            response_time_ms=0,
            avg_agent_latency_ms=0,
            talk_ratio=0.0,
            overall_score=7,
            conversation_metrics_data={
                "avg_latency_ms": 320.0,
                "agent_talk_percentage": 50.0,
                "csat_score": 4.0,
            },
        )
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "rt_ms": "response_time_ms",
                "aal_ms": "avg_agent_latency_ms",
                "tr": "talk_ratio",
            },
            run_test, eval_template,
        )

        _run(ec, ce, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        # Primary source is 0/0.0; must NOT fall through to 320/50.0/0.5.
        assert m["rt_ms"] == "0"
        assert m["aal_ms"] == "0"
        assert m["tr"] == "0.0"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_voice_sim_zero_valued_metrics_survive_fallback(
        self, mock_run, run_test, transcript_data, eval_template,
        test_execution, scenario, agent_version,
    ):
        """Chat-side keys with a legitimate 0 on the conv_metrics side must
        not fall through to the voice-side model field."""
        ce = CallExecution.objects.create(
            test_execution=test_execution, scenario=scenario, agent_version=agent_version,
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            avg_agent_latency_ms=8652, talk_ratio=0.62, overall_score=7,
            conversation_metrics_data={
                "avg_latency_ms": 0.0,
                "agent_talk_percentage": 0.0,
                "csat_score": 0.0,
            },
        )
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "lat": "avg_latency_ms",
                "atp": "agent_talk_percentage",
                "c": "csat_score",
            },
            run_test, eval_template,
        )

        _run(ec, ce, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        # Conv-metrics is 0.0; must NOT fall through to 8652/62.0/7.
        assert m["lat"] == "0.0"
        assert m["atp"] == "0.0"
        assert m["c"] == "0.0"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_scenario_graph_node_resolves_via_computed_subject(
        self, mock_run, run_test, call_execution, transcript_data, eval_template, scenario, organization
    ):
        """`scenario_graph.nodes.<i>.<field>` exercises get_scenario_graph inline call."""
        from simulate.models.scenario_graph import ScenarioGraph

        mock_run.return_value = _SUCCESS_STUB
        ScenarioGraph.objects.create(
            scenario=scenario,
            organization=organization,
            name="Test Graph",
            is_active=True,
            graph_config={
                "graph_data": {
                    "nodes": [{"id": "n1", "type": "intent", "data": {"label": "Greet"}}],
                    "edges": [],
                }
            },
        )
        ec = _make_eval(
            {"nt": "scenario_graph.nodes.0.type"}, run_test, eval_template
        )

        _run(ec, call_execution, transcript_data)

        assert mock_run.call_args.kwargs["mappings"]["nt"] == "intent"

    @patch("simulate.services.test_executor.run_eval_func")
    def test_subject_build_failure_leaves_ctx_usable(
        self, mock_run, run_test, call_execution, transcript_data, eval_template, monkeypatch
    ):
        """Serializer exception in subject builder falls back to {}, ctx stays usable."""
        from simulate.serializers.test_execution import CallExecutionDetailSerializer

        def _boom(self, obj):
            raise RuntimeError("simulated serializer failure")

        monkeypatch.setattr(
            CallExecutionDetailSerializer, "get_scenario_columns", _boom
        )
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {"a": "call.summary", "b": "scenario_columns.persona.value"},
            run_test,
            eval_template,
        )

        _run(ec, call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["a"] == "Customer called about order 123."
        assert m["b"] == ""  # scenario_columns subject fell back to {}


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
class TestRecordingSlotAvailability:
    """An eval variable mapped to a recording the call has no audio for (e.g.
    stereo / per-channel on a combined-only provider like Bland) fails with an
    actionable message naming the available recordings, instead of the eval
    engine's opaque 'No input received for <var>'."""

    # Combined-only shape: only the mono combined recording exists.
    _COMBINED_ONLY = {
        "transcript": "agent: hi\ncustomer: hello",
        "voice_recording": "s3://bucket/combined.mp3",
        "assistant_recording": "",
        "customer_recording": "",
        "stereo_recording": "",
        "user_chat_transcript": "",
        "assistant_chat_transcript": "",
    }

    @patch("simulate.services.test_executor.run_eval_func")
    def test_empty_stereo_slot_raises_actionable_error(
        self, mock_run, run_test, call_execution, eval_template
    ):
        ec = _make_eval({"output": "call.stereo_recording"}, run_test, eval_template)

        with pytest.raises(ValueError, match="combined-only"):
            _run(ec, call_execution, dict(self._COMBINED_ONLY))

        mock_run.assert_not_called()  # never reaches the eval engine
        ec.refresh_from_db()
        assert ec.status == StatusType.FAILED.value
        call_execution.refresh_from_db()
        reason = call_execution.eval_outputs[str(ec.id)]["reason"]
        assert "call.voice_recording" in reason  # actionable hint for the FE

    @patch("simulate.services.test_executor.run_eval_func")
    def test_present_combined_recording_resolves_and_runs(
        self, mock_run, run_test, call_execution, eval_template
    ):
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval({"output": "call.voice_recording"}, run_test, eval_template)

        _run(ec, call_execution, dict(self._COMBINED_ONLY))

        assert (
            mock_run.call_args.kwargs["mappings"]["output"]
            == "s3://bucket/combined.mp3"
        )

    @patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
    def test_empty_stereo_slot_raises_on_xl_temporal_path(
        self, run_test, call_execution, eval_template
    ):
        """Same guard on the temporal-activity path (two-sources-of-truth)."""
        from model_hub.views.utils import evals as evals_mod

        ec = _make_eval({"output": "call.stereo_recording"}, run_test, eval_template)
        with patch.object(evals_mod, "run_eval_func") as mock_run:
            with pytest.raises(ValueError, match="combined-only"):
                _run_xl(ec, call_execution, dict(self._COMBINED_ONLY))
            mock_run.assert_not_called()


@pytest.mark.django_db
class TestLegacyTranscriptRecordingResolution:
    def test_reads_recordings_from_call_row_and_normalized_provider_data(
        self, call_execution
    ):
        from simulate.temporal.activities.xl import _build_transcript_data

        call_execution.provider_call_data = {
            "vapi": {"usage": {"llm": {"total_tokens": 42}}},
            "livekit": {
                "engine": "livekit",
                "recording": {
                    "assistant": "s3://bucket/assistant.mp3",
                    "customer": "s3://bucket/customer.mp3",
                },
            }
        }
        executor = TestExecutor()
        executor.voice_service_manager = Mock()
        executor.voice_service_manager.get_recording_urls.return_value = {}

        transcript_data = executor._get_call_transcript_data(
            call_execution, url_save_only=True
        )

        assert transcript_data["voice_recording"] == call_execution.recording_url
        assert (
            transcript_data["stereo_recording"]
            == call_execution.stereo_recording_url
        )
        assert transcript_data["assistant_recording"] == "s3://bucket/assistant.mp3"
        assert transcript_data["customer_recording"] == "s3://bucket/customer.mp3"

        xl_transcript_data = _build_transcript_data(call_execution)
        assert xl_transcript_data["assistant_recording"] == "s3://bucket/assistant.mp3"
        assert xl_transcript_data["customer_recording"] == "s3://bucket/customer.mp3"


# ---------------------------------------------------------------------------
# TH-8045 -- sealed verdicts.
# ---------------------------------------------------------------------------

_STORED_VERDICT = {
    "name": "Sealed Eval",
    "output": "Passed",
    "output_type": "Pass/Fail",
    "reason": "the judge said so",
    "status": "completed",
}

_SKIP_REASON = "We could not find enough agent conversation to process this call."
_NO_TRANSCRIPT_REASON = "Call transcript is unavailable, so processing was skipped."


class _NoSkip:
    processing_skipped = False
    processing_skip_reason = ""


class _Skip:
    processing_skipped = True
    processing_skip_reason = _SKIP_REASON


def _seal(call_execution, eval_config):
    """Store a verdict for this config on the call and return a deep copy of it."""
    call_execution.eval_outputs = dict(call_execution.eval_outputs or {})
    call_execution.eval_outputs[str(eval_config.id)] = dict(_STORED_VERDICT)
    call_execution.call_metadata = dict(call_execution.call_metadata or {})
    call_execution.call_metadata["eval_completed"] = True
    call_execution.save(update_fields=["eval_outputs", "call_metadata"])
    return copy.deepcopy(call_execution.eval_outputs[str(eval_config.id)])


def _no_tool_eval(run_test):
    run_test.enable_tool_evaluation = False
    run_test.save(update_fields=["enable_tool_evaluation"])


def _graded_config_ids(mock_single):
    """The eval configs handed to the judge, as a set of ids."""
    return {call.args[0].id for call in mock_single.call_args_list}


def _skipped_payload(eval_config, reason):
    """The exact payload simulate/utils/processing_outcomes.py writes."""
    return {
        "output": None,
        "reason": reason,
        "output_type": None,
        "name": eval_config.name,
        "status": "skipped",
        "skipped": True,
    }


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NoSkip)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
def test_task_skip_existing_honours_config_id(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """skip-existing on: the sealed config is not graded and not rewritten;
    a sibling config with no verdict still is."""
    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    sealed = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    fresh = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    before = _seal(call_execution, sealed)

    TestExecutor()._run_simulate_evaluations(call_execution, skip_existing=True)

    assert _graded_config_ids(mock_single) == {fresh.id}
    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(sealed.id)] == before


_HAS_STORED_VERDICT_CFG_ID = "abc12300-0000-0000-0000-000000000000"


@pytest.mark.parametrize(
    "eval_outputs, expected",
    [
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: {"status": "completed", "output": "Passed"}},
            True,
            id="completed_row_seals",
        ),
        pytest.param(
            {
                _HAS_STORED_VERDICT_CFG_ID: {
                    "status": StatusType.COMPLETED.value,
                    "output": "Passed",
                }
            },
            True,
            id="completed_row_seals_real_status_type_value",
        ),
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: {"status": "pending"}},
            False,
            id="pending_placeholder_does_not_seal",
        ),
        pytest.param(
            {
                _HAS_STORED_VERDICT_CFG_ID: {
                    "status": "skipped",
                    "skipped": True,
                    "output": None,
                }
            },
            False,
            id="skipped_payload_does_not_seal",
        ),
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: {"status": StatusType.FAILED.value}},
            False,
            id="failed_row_does_not_seal",
        ),
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: {}},
            False,
            id="empty_dict_row_does_not_seal",
        ),
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: None},
            False,
            id="none_row_does_not_seal",
        ),
        pytest.param({}, False, id="missing_config_key_does_not_seal"),
        pytest.param(None, False, id="eval_outputs_itself_none_does_not_seal"),
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: {"output": "Passed"}},
            True,
            id="status_less_non_empty_row_seals_conservative_form",
        ),
        pytest.param(
            {
                _HAS_STORED_VERDICT_CFG_ID: {
                    "reason": "Column mapping mismatch: ...",
                    "error": "error",
                    "name": "x",
                    "timestamp": "2026-09-23T00:00:00+00:00",
                    "output": None,
                    "output_type": "score",
                    "status": StatusType.FAILED.value,
                }
            },
            False,
            id="xl_shaped_errored_row_with_status_does_not_seal",
        ),
        pytest.param(
            {
                _HAS_STORED_VERDICT_CFG_ID: {
                    "reason": "Evaluation failed. Please contact Future AGI support.",
                    "error": "error",
                    "name": "x",
                    "timestamp": "2026-09-23T00:00:00+00:00",
                    "output": None,
                    "output_type": "score",
                }
            },
            True,
            id="legacy_status_less_errored_row_seals",
        ),
        # The isinstance(row, dict) guard exists to make a truthy non-dict
        # row not crash; these pin what it returns for those shapes -- a
        # non-dict row can never carry a "status" key, so it always seals
        # when truthy.
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: "Passed"},
            True,
            id="non_dict_str_row_seals",
        ),
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: ["x"]},
            True,
            id="non_dict_list_row_seals",
        ),
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: 1},
            True,
            id="non_dict_int_row_seals",
        ),
        pytest.param(
            {_HAS_STORED_VERDICT_CFG_ID: {"status": 123}},
            True,
            id="dict_with_non_string_status_seals",
        ),
    ],
)
def test_has_stored_verdict_edge_shapes(eval_outputs, expected):
    """``has_stored_verdict`` returns False only for a pending placeholder, a
    skipped payload, or a ``Failed`` grading -- any other non-empty row
    seals, including a status-less one. Also covers a legacy status-less
    errored/no-transcript row (still seals, indistinguishable from a real
    completed row) and a truthy non-dict row (always seals, since it can
    never carry a "status" key)."""
    call_execution = SimpleNamespace(eval_outputs=eval_outputs)

    assert has_stored_verdict(call_execution, _HAS_STORED_VERDICT_CFG_ID) is expected


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NoSkip)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
def test_a_pending_placeholder_row_does_not_seal(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """A ``{"status": "pending"}`` placeholder is written by the bulk rerun
    paths before grading starts (not a verdict), so it does not seal under
    skip-existing: the pending config is graded like a fresh one, and grading
    replaces its row with the real verdict.

    The final assertion checks ``call_metadata["eval_completed"]`` rather
    than only re-comparing ``eval_outputs`` to the mock's own write: that
    flag is set by ``_check_and_update_eval_completion``, which re-reads
    ``eval_outputs`` from the database, so this proves the production
    completion check itself saw the row as no longer pending."""
    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    pending_config = _make_eval(
        {"conversation": "call.transcript"}, run_test, eval_template
    )
    call_execution.eval_outputs = {str(pending_config.id): {"status": "pending"}}
    call_execution.save(update_fields=["eval_outputs"])

    def _grade(eval_config, call_exec, _transcript_data):
        call_exec.eval_outputs = dict(call_exec.eval_outputs or {})
        call_exec.eval_outputs[str(eval_config.id)] = dict(_STORED_VERDICT)
        call_exec.save(update_fields=["eval_outputs"])

    mock_single.side_effect = _grade

    TestExecutor()._run_simulate_evaluations(call_execution, skip_existing=True)

    assert pending_config.id in _graded_config_ids(mock_single)
    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(pending_config.id)] == _STORED_VERDICT
    assert call_execution.call_metadata["eval_completed"] is True


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NoSkip)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
def test_a_skipped_payload_row_does_not_seal(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """A stored skipped payload (``build_skipped_eval_output_payload``'s
    shape, ``status`` ``"skipped"``) is not a verdict -- it is what the
    "conversation too short" / "no transcript" branches write before the
    per-eval loop, for a call whose inputs can change later. So the config
    is graded exactly like a fresh one."""
    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    skipped_config = _make_eval(
        {"conversation": "call.transcript"}, run_test, eval_template
    )
    call_execution.eval_outputs = {
        str(skipped_config.id): _skipped_payload(skipped_config, _SKIP_REASON)
    }
    call_execution.save(update_fields=["eval_outputs"])

    TestExecutor()._run_simulate_evaluations(call_execution, skip_existing=True)

    assert skipped_config.id in _graded_config_ids(mock_single)


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NoSkip)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
def test_an_errored_grading_row_does_not_seal(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """An errored grading row (``status`` ``StatusType.FAILED.value``, the
    shape ``_run_single_simulate_evaluation``'s ``except`` branch writes) is
    not a verdict either -- a judge outage during a run-level add must not
    permanently seal the call for this config."""
    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    errored_config = _make_eval(
        {"conversation": "call.transcript"}, run_test, eval_template
    )
    call_execution.eval_outputs = {
        str(errored_config.id): {
            "reason": "Evaluation failed. Please contact Future AGI support.",
            "error": "error",
            "name": errored_config.name,
            "timestamp": "2026-09-23T00:00:00+00:00",
            "output": None,
            "output_type": None,
            "status": StatusType.FAILED.value,
        }
    }
    call_execution.save(update_fields=["eval_outputs"])

    TestExecutor()._run_simulate_evaluations(call_execution, skip_existing=True)

    assert errored_config.id in _graded_config_ids(mock_single)


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NoSkip)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
def test_skip_existing_off_still_regrades_a_stored_verdict(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """skip-existing off is exactly today's behaviour -- the receipt path and
    the eval-only re-run keep overwriting. Documented on purpose so nobody
    "fixes" it silently."""
    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    sealed = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    _seal(call_execution, sealed)

    TestExecutor()._run_simulate_evaluations(call_execution, skip_existing=False)

    assert sealed.id in _graded_config_ids(mock_single)


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _Skip)
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
def test_skipped_branch_never_replaces_a_real_verdict(
    mock_single,
    run_test,
    call_execution,
    eval_template,
):
    """ "Conversation too short" with skip-existing on: the sealed config
    keeps its verdict byte-for-byte, a config with no verdict gets the
    skipped payload."""
    _no_tool_eval(run_test)
    sealed = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    empty = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    before = _seal(call_execution, sealed)

    TestExecutor()._run_simulate_evaluations(call_execution, skip_existing=True)

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(sealed.id)] == before
    assert call_execution.eval_outputs[str(empty.id)] == _skipped_payload(
        empty, _SKIP_REASON
    )
    mock_single.assert_not_called()
    assert call_execution.call_metadata["processing_skipped"] is True
    assert call_execution.call_metadata["eval_completed"] is True


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _Skip)
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
def test_skipped_branch_overwrites_when_skip_existing_is_off(
    mock_single,
    run_test,
    call_execution,
    eval_template,
):
    """With the flag off the skipped payload still lands on every config --
    today's behaviour, unchanged."""
    _no_tool_eval(run_test)
    sealed = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    _seal(call_execution, sealed)

    TestExecutor()._run_simulate_evaluations(call_execution, skip_existing=False)

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(sealed.id)] == _skipped_payload(
        sealed, _SKIP_REASON
    )


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NoSkip)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
def test_no_transcript_branch_never_replaces_a_real_verdict(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """The "no transcript" branch routes through the same helper, so it is
    guarded too -- it swallows any read error into an empty transcript."""
    _no_tool_eval(run_test)
    mock_transcript.return_value = {**transcript_data, "transcript": ""}
    sealed = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    empty = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    before = _seal(call_execution, sealed)

    TestExecutor()._run_simulate_evaluations(call_execution, skip_existing=True)

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(sealed.id)] == before
    assert call_execution.eval_outputs[str(empty.id)] == _skipped_payload(
        empty, _NO_TRANSCRIPT_REASON
    )
    mock_single.assert_not_called()


# ---------------------------------------------------------------------------
# xl.py's standalone (Temporal) evaluation orchestrator mirrors the same
# skip-existing guards as TestExecutor._run_simulate_evaluations.
# ---------------------------------------------------------------------------


def _xl_graded_config_ids(mock_single):
    """The eval configs handed to the judge via xl.py's ``_run_single_evaluation``."""
    return {call.args[0].id for call in mock_single.call_args_list}


@pytest.mark.django_db
@patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
@patch("simulate.temporal.activities.xl._build_transcript_data")
@patch("simulate.temporal.activities.xl._run_single_evaluation")
def test_xl_standalone_honours_skip_existing(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """skip-existing on: the sealed config is not re-graded by xl.py's
    standalone orchestrator and its row is not rewritten; a sibling config
    with no verdict still is graded."""
    from simulate.temporal.activities.xl import _run_evaluations_standalone

    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    sealed = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    fresh = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    before = _seal(call_execution, sealed)

    _run_evaluations_standalone(call_execution, skip_existing=True)

    assert _xl_graded_config_ids(mock_single) == {fresh.id}
    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(sealed.id)] == before


@pytest.mark.django_db
@patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
@patch("simulate.temporal.activities.xl._build_transcript_data")
@patch("simulate.temporal.activities.xl._run_single_evaluation")
def test_xl_standalone_skip_existing_off_still_regrades_a_stored_verdict(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """skip-existing off is exactly today's behaviour on the xl.py path too --
    every config in the batch is (re)graded."""
    from simulate.temporal.activities.xl import _run_evaluations_standalone

    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    sealed = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    _seal(call_execution, sealed)

    _run_evaluations_standalone(call_execution, skip_existing=False)

    assert sealed.id in _xl_graded_config_ids(mock_single)


@pytest.mark.django_db
@patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
@patch("simulate.temporal.activities.xl._build_transcript_data")
@patch("simulate.temporal.activities.xl._run_single_evaluation")
def test_xl_standalone_no_transcript_branch_never_replaces_a_real_verdict(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """The "no transcript" branch is reachable in isolation on the xl.py path
    (it does not route through a shared helper, so it needed its own guard)
    -- with skip-existing on, the sealed config keeps its verdict
    byte-for-byte and a config with no verdict gets the "no transcript"
    payload, in ``build_skipped_eval_output_payload``'s shape except for
    ``output_type``, which this write restores to the derived KPI type
    instead of the builder's ``None`` (see
    ``test_xl_standalone_no_transcript_write_keeps_derived_output_type``)."""
    from simulate.temporal.activities.xl import _run_evaluations_standalone

    _no_tool_eval(run_test)
    mock_transcript.return_value = {**transcript_data, "transcript": ""}
    sealed = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    empty = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    before = _seal(call_execution, sealed)

    _run_evaluations_standalone(call_execution, skip_existing=True)

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(sealed.id)] == before
    expected_empty = _skipped_payload(empty, "No transcript data available")
    expected_empty["output_type"] = derive_kpi_output_type(eval_template)
    assert call_execution.eval_outputs[str(empty.id)] == expected_empty
    mock_single.assert_not_called()


@pytest.mark.django_db
@patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
@patch("simulate.temporal.activities.xl._build_transcript_data")
@patch("simulate.temporal.activities.xl._run_single_evaluation")
def test_xl_standalone_no_transcript_write_keeps_derived_output_type(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """The no-transcript write in ``_run_evaluations_standalone`` switched to
    ``build_skipped_eval_output_payload``, but that builder always sets
    ``output_type`` to ``None``. ``get_kpi_eval_metrics_query``'s ``WHERE
    output_type IN (...)`` filter needs the derived KPI type to keep the row
    instead of silently dropping the metric, so the write restores that
    derivation on top of the skipped-payload shape."""
    from simulate.temporal.activities.xl import _run_evaluations_standalone

    _no_tool_eval(run_test)
    mock_transcript.return_value = {**transcript_data, "transcript": ""}
    config = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)

    _run_evaluations_standalone(call_execution, skip_existing=True)

    call_execution.refresh_from_db()
    row = call_execution.eval_outputs[str(config.id)]
    assert row["status"] == "skipped"
    assert row["skipped"] is True
    assert row["output_type"] == derive_kpi_output_type(eval_template)
    mock_single.assert_not_called()


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NoSkip)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
def test_task_an_xl_shaped_errored_row_with_status_does_not_seal(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """The exact row shape ``xl.py::_run_single_evaluation`` now writes on an
    error (``status`` ``StatusType.FAILED.value``) does not seal on the
    ``TestExecutor`` loop either -- the two write paths must agree, since
    both go through the same predicate."""
    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    errored_config = _make_eval(
        {"conversation": "call.transcript"}, run_test, eval_template
    )
    call_execution.eval_outputs = {
        str(errored_config.id): {
            "reason": "Evaluation failed. Please contact Future AGI support.",
            "error": "error",
            "name": errored_config.name,
            "timestamp": "2026-09-23T00:00:00+00:00",
            "output": None,
            "output_type": None,
            "status": StatusType.FAILED.value,
        }
    }
    call_execution.save(update_fields=["eval_outputs"])

    TestExecutor()._run_simulate_evaluations(call_execution, skip_existing=True)

    assert errored_config.id in _graded_config_ids(mock_single)


@pytest.mark.django_db
@patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
@patch("simulate.temporal.activities.xl._build_transcript_data")
@patch("simulate.temporal.activities.xl._run_single_evaluation")
def test_xl_standalone_an_errored_grading_row_does_not_seal(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """An errored grading row as ``xl.py::_run_single_evaluation`` now writes
    it (``status`` ``StatusType.FAILED.value``) is not a verdict on the
    standalone/Temporal path either: a judge outage during a run-level add
    must not permanently seal the call for this config."""
    from simulate.temporal.activities.xl import _run_evaluations_standalone

    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    errored_config = _make_eval(
        {"conversation": "call.transcript"}, run_test, eval_template
    )
    call_execution.eval_outputs = {
        str(errored_config.id): {
            "reason": "Evaluation failed. Please contact Future AGI support.",
            "error": "error",
            "name": errored_config.name,
            "timestamp": "2026-09-23T00:00:00+00:00",
            "output": None,
            "output_type": None,
            "status": StatusType.FAILED.value,
        }
    }
    call_execution.save(update_fields=["eval_outputs"])

    _run_evaluations_standalone(call_execution, skip_existing=True)

    assert errored_config.id in _xl_graded_config_ids(mock_single)


@pytest.mark.django_db
@patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
@patch("simulate.temporal.activities.xl._build_transcript_data")
@patch("simulate.temporal.activities.xl._run_single_evaluation")
def test_xl_standalone_legacy_status_less_errored_row_seals(
    mock_single,
    mock_transcript,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    """A row written by the code as it stood before this fix -- an errored
    grading with no ``status`` key at all -- is indistinguishable from any
    other status-less completed row, so it still seals. It is treated as a
    verdict and is not re-graded even with ``skip_existing`` on; the
    eval-only re-run (which wipes ``eval_outputs`` wholesale) is the only
    way to clear it. This is the intended, documented consequence of the
    conservative predicate form, not a bug."""
    from simulate.temporal.activities.xl import _run_evaluations_standalone

    _no_tool_eval(run_test)
    mock_transcript.return_value = transcript_data
    legacy_errored_config = _make_eval(
        {"conversation": "call.transcript"}, run_test, eval_template
    )
    legacy_row = {
        "reason": "Evaluation failed. Please contact Future AGI support.",
        "error": "error",
        "name": legacy_errored_config.name,
        "timestamp": "2026-09-23T00:00:00+00:00",
        "output": None,
        "output_type": None,
    }
    call_execution.eval_outputs = {str(legacy_errored_config.id): dict(legacy_row)}
    call_execution.save(update_fields=["eval_outputs"])

    _run_evaluations_standalone(call_execution, skip_existing=True)

    assert legacy_errored_config.id not in _xl_graded_config_ids(mock_single)
    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(legacy_errored_config.id)] == legacy_row


def test_task_signature_supports_the_run_level_add_kwargs():
    """The task's call id is bound positionally, so ``call_execution_id``
    being the first parameter is load-bearing. Pins that the three names
    exist with the right defaults -- not the whole parameter list, so an
    unrelated new keyword-only parameter doesn't fail this test -- and binds
    through the real signature to assert the id lands on
    ``call_execution_id``, since a membership-only check would stay green
    even if the parameters were reordered and the call id silently bound to
    the wrong name."""
    params = inspect.signature(_run_simulate_evaluations_task).parameters
    assert "call_execution_id" in params
    assert "eval_config_ids" in params
    assert "skip_existing" in params
    assert params["eval_config_ids"].default is None
    assert params["skip_existing"].default is False

    bound = inspect.signature(_run_simulate_evaluations_task).bind(
        "cid", eval_config_ids=["x"], skip_existing=True
    )
    assert bound.arguments["call_execution_id"] == "cid"


@pytest.mark.django_db
def test_receipt_dispatch_binds_skip_existing_false(
    run_test, call_execution, eval_template
):
    """The receipt path regrades, on purpose: whatever shape it calls
    ``apply_async`` with, the effective ``skip_existing`` the task receives is
    False. Bound through the real signature rather than a pinned call shape,
    so a behaviour-identical refactor of the dispatch call (e.g. moving the
    positional args into kwargs) does not fail this test. Documenting today's
    behaviour so nobody "fixes" it silently."""
    from simulate.services.alk_simulate_ingestion import _dispatch_evaluations_once

    config = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    call_execution.call_metadata = {}
    call_execution.save(update_fields=["call_metadata"])

    with patch(
        "simulate.services.test_executor._run_simulate_evaluations_task.apply_async"
    ) as spy:
        assert (
            _dispatch_evaluations_once(call_execution, eval_config_ids=[str(config.id)])
            is True
        )

    call_args = spy.call_args
    task_args = call_args.kwargs.get("args", call_args.args)
    task_kwargs = call_args.kwargs.get("kwargs", {})
    bound = inspect.signature(_run_simulate_evaluations_task).bind(
        *task_args, **task_kwargs
    )
    bound.apply_defaults()
    assert bound.arguments["call_execution_id"] == str(call_execution.id)
    assert bound.arguments["eval_config_ids"] == [str(config.id)]
    assert bound.arguments.get("skip_existing", False) is False
