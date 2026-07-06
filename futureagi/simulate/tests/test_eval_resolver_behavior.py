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
    def test_voice_only_metrics_resolve_empty_on_chat_sim(
        self,
        mock_run,
        run_test,
        chat_call_execution,
        transcript_data,
        eval_template,
    ):
        """Voice-only metrics on a chat sim resolve to empty string, not mismatch error."""
        mock_run.return_value = _SUCCESS_STUB
        ec = _make_eval(
            {
                "lat": "avg_agent_latency",
                "uw": "user_wpm",
                "tr": "talk_ratio",
            },
            run_test,
            eval_template,
        )

        _run(ec, chat_call_execution, transcript_data)

        m = mock_run.call_args.kwargs["mappings"]
        assert m["lat"] == ""
        assert m["uw"] == ""
        assert m["tr"] == ""

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
