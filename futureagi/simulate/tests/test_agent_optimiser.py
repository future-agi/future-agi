"""
Tests for simulate/utils/agent_optimiser.py.

Covers: simulation type resolution, eval template building,
chat aggregate metrics, scenario construction, and the top-level
orchestrators (prepare/get_call_executions/get_full).
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    RunTest,
    SimulateEvalConfig,
    TestExecution,
)

from simulate.utils.agent_optimiser import (
    _build_chat_aggregate_metrics,
    _build_fix_your_agent_eval_templates,
    _get_prompt_from_run_test,
    _resolve_simulation_type,
    construct_scenarios_from_calls,
    get_agent_definition_prompt,
    get_call_executions_with_details,
    get_full_test_execution_data,
    get_latest_optimiser_result,
    get_or_create_optimiser_for_test_execution,
    prepare_simulation_analysis_input,
)


class TestResolveSimulationType:
    def test_text_from_scenarios(self):
        te = MagicMock()
        scenarios = [{"simulation_type": "text"}]
        assert _resolve_simulation_type(te, scenarios) == "text"

    def test_voice_from_mixed_scenarios(self):
        te = MagicMock()
        scenarios = [{"simulation_type": "voice"}, {"simulation_type": "text"}]
        assert _resolve_simulation_type(te, scenarios) == "voice"

    def test_fallback_to_agent_type_from_snapshot(self):
        run_test = MagicMock()
        run_test.source_type = "agent"
        run_test.agent_version = MagicMock()
        run_test.agent_version.configuration_snapshot = {"agent_type": "text"}
        run_test.agent_definition = MagicMock()
        te = MagicMock()
        te.run_test = run_test
        assert _resolve_simulation_type(te, []) == "text"

    def test_fallback_to_agent_definition(self):
        run_test = MagicMock()
        run_test.source_type = "agent"
        run_test.agent_version = MagicMock()
        run_test.agent_version.configuration_snapshot = None
        run_test.agent_definition = MagicMock()
        run_test.agent_definition.agent_type = "text"
        te = MagicMock()
        te.run_test = run_test
        assert _resolve_simulation_type(te, []) == "text"


class TestBuildChatAggregateMetrics:
    def test_empty_executions(self):
        assert _build_chat_aggregate_metrics([]) == {}

    def test_skips_voice_calls(self):
        calls = [MagicMock(simulation_call_type="voice", conversation_metrics_data={"latency": 100})]
        assert _build_chat_aggregate_metrics(calls) == {}

    def test_averages_metrics(self):
        calls = [
            MagicMock(
                simulation_call_type="text",
                conversation_metrics_data={"latency_ms": 100, "turn_count": 5},
            ),
            MagicMock(
                simulation_call_type="text",
                conversation_metrics_data={"latency_ms": 200, "turn_count": 7},
            ),
        ]
        result = _build_chat_aggregate_metrics(calls)
        assert result["agg_latency_ms"] == 150.0
        assert result["agg_turn_count"] == 6.0

    def test_skips_non_numeric_values(self):
        calls = [
            MagicMock(
                simulation_call_type="text",
                conversation_metrics_data={"latency_ms": 100, "label": "foo"},
            ),
        ]
        result = _build_chat_aggregate_metrics(calls)
        assert "agg_label" not in result
        assert result["agg_latency_ms"] == 100.0


class TestBuildFixYourAgentEvalTemplates:
    def test_empty_configs(self):
        templates, allowed, mapping = _build_fix_your_agent_eval_templates([])
        assert templates == []
        assert allowed == set()
        assert mapping == {}

    def test_deduplicates_by_template_id(self):
        tmpl = MagicMock()
        tmpl.id = uuid.uuid4()
        tmpl.name = "Eval A"
        tmpl.config = {"output": "pass_fail"}
        tmpl.criteria = "check"
        tmpl.choices = None
        tmpl.multi_choice = None
        cfg1 = MagicMock(id=uuid.uuid4(), eval_template=tmpl)
        cfg2 = MagicMock(id=uuid.uuid4(), eval_template=tmpl)
        templates, allowed, mapping = _build_fix_your_agent_eval_templates([cfg1, cfg2])
        assert len(templates) == 1
        assert len(allowed) == 2

    def test_score_output_includes_failure_threshold(self):
        tmpl = MagicMock()
        tmpl.id = uuid.uuid4()
        tmpl.name = "Score Eval"
        tmpl.config = {"output": "score", "config": {"failure_threshold": 0.3}}
        tmpl.criteria = ""
        tmpl.choices = None
        tmpl.multi_choice = None
        cfg = MagicMock(id=uuid.uuid4(), eval_template=tmpl)
        templates, _, _ = _build_fix_your_agent_eval_templates([cfg])
        assert templates[0]["output_type"] == "score"
        assert templates[0]["failure_threshold"] == 0.3
        assert templates[0]["score_range_hint"] == {"min": 0.0, "max": 1.0}


class TestConstructScenariosFromCalls:
    def test_empty_calls(self):
        qs = MagicMock()
        qs.count.return_value = 0
        qs.__iter__.return_value = iter([])
        assert construct_scenarios_from_calls(qs) == []

    def test_basic_scenario(self):
        call = MagicMock()
        call.id = uuid.uuid4()
        call.simulation_call_type = "voice"
        call.customer_latency_metrics = {}
        call.customer_cost_breakdown = {}
        call.customer_cost_cents = 0
        call.avg_agent_latency_ms = 0
        call.response_time_ms = 0
        call.ai_interruption_count = 0
        call.user_interruption_count = 0
        call.talk_ratio = 0.0
        call.overall_score = 0.0
        call.eval_outputs = {}
        call.tool_outputs = {}
        call.call_metadata = {
            "row_data": {
                "conversation_branch": "main",
                "branch_category": "",
                "outcome": "",
                "situation": "",
            }
        }
        call.conversation_metrics_data = None

        qs = MagicMock()
        qs.count.return_value = 1
        qs.__iter__.return_value = iter([call])
        scenarios = construct_scenarios_from_calls(qs)
        assert len(scenarios) == 1
        assert scenarios[0]["call_execution_id"] == str(call.id)


class TestGetAgentDefinitionPrompt:
    _DNE = type("DoesNotExist", (Exception,), {})

    @patch("simulate.utils.agent_optimiser.AgentDefinition")
    @patch("simulate.models.AgentVersion")
    def test_with_version(self, MockVersion, MockDef):
        agent_def = MagicMock()
        agent_def.id = uuid.uuid4()
        agent_def.agent_type = "text"
        MockDef.objects.get.return_value = agent_def

        version = MagicMock()
        version.id = uuid.uuid4()
        version.version_number = 2
        version.configuration_snapshot = {"agent_type": "chat"}
        MockVersion.objects.get.return_value = version

        result = get_agent_definition_prompt(str(agent_def.id), str(version.id))
        assert result is not None
        assert result["agent_type"] == "chat"
        assert result["version_id"] == str(version.id)

    @patch("simulate.utils.agent_optimiser.AgentDefinition")
    def test_without_version(self, MockDef):
        agent_def = MagicMock()
        agent_def.id = uuid.uuid4()
        agent_def.agent_type = "text"
        agent_def.inbound = True
        agent_def.agent_name = "test-agent"
        agent_def.description = "desc"
        agent_def.provider = "test"
        MockDef.objects.get.return_value = agent_def

        result = get_agent_definition_prompt(str(agent_def.id))
        assert result["agent_type"] == "text"
        assert result["version_id"] is None

    @patch("simulate.utils.agent_optimiser.AgentDefinition")
    def test_not_found(self, MockDef):
        MockDef.DoesNotExist = self._DNE
        MockDef.objects.get.side_effect = self._DNE
        assert get_agent_definition_prompt(str(uuid.uuid4())) is None

    def test_none_id(self):
        assert get_agent_definition_prompt(None) is None


class TestGetOrCreateOptimiser:
    def test_returns_existing(self):
        te = MagicMock()
        optimiser = MagicMock()
        te.agent_optimiser = optimiser
        assert get_or_create_optimiser_for_test_execution(te) is optimiser

    def test_creates_new(self):
        te = MagicMock()
        te.agent_optimiser = None
        te.run_test.name = "test"

        with patch("simulate.utils.agent_optimiser.AgentOptimiser") as MockOpt:
            mock_instance = MagicMock()
            MockOpt.objects.create.return_value = mock_instance
            result = get_or_create_optimiser_for_test_execution(te)
            assert result is mock_instance
            MockOpt.objects.create.assert_called_once()


class TestGetLatestOptimiserResult:
    def test_returns_latest_run_result(self):
        optimiser = MagicMock()
        run = MagicMock()
        run.result = {"agent_level": {}}
        run.status = "completed"
        run.updated_at.isoformat.return_value = "2024-01-01T00:00:00"
        optimiser.latest_run = run
        te = MagicMock()

        result = get_latest_optimiser_result(optimiser, te)
        assert result["response"] == {"agent_level": {}}

    @patch("simulate.utils.agent_optimiser.create_optimiser_run_for_test_execution")
    def test_creates_run_when_none(self, mock_create):
        optimiser = MagicMock()
        optimiser.latest_run = None
        te = MagicMock()
        mock_run = MagicMock()
        mock_run.result = None
        mock_run.status = "pending"
        mock_run.updated_at.isoformat.return_value = "2024-01-01T00:00:00"
        mock_create.return_value = mock_run

        result = get_latest_optimiser_result(optimiser, te)
        assert result["status"] == "pending"


class TestPrepareSimulationAnalysisInput:
    _DNE = type("DoesNotExist", (Exception,), {})

    @patch("simulate.utils.agent_optimiser.TestExecution")
    @patch("simulate.utils.agent_optimiser.CallExecution")
    @patch("simulate.utils.agent_optimiser._get_eval_configs")
    @patch("simulate.utils.agent_optimiser.construct_scenarios_from_calls")
    def test_no_calls_returns_none(self, mock_scenarios, mock_eval, mock_call, mock_te):
        mock_te.DoesNotExist = self._DNE
        te = MagicMock()
        mock_te.objects.get.return_value = te
        mock_call.objects.filter.return_value.count.return_value = 0

        result = prepare_simulation_analysis_input(str(uuid.uuid4()))
        assert result is None

    @patch("simulate.utils.agent_optimiser.TestExecution")
    def test_exception_returns_none(self, mock_te):
        mock_te.DoesNotExist = self._DNE
        mock_te.objects.get.side_effect = RuntimeError("failure")
        assert prepare_simulation_analysis_input(str(uuid.uuid4())) is None


class TestGetCallExecutionsWithDetails:
    _DNE = type("DoesNotExist", (Exception,), {})

    @patch("simulate.utils.agent_optimiser.TestExecution")
    def test_test_execution_not_found(self, mock_te):
        mock_te.DoesNotExist = self._DNE
        mock_te.objects.get.side_effect = self._DNE
        assert get_call_executions_with_details(str(uuid.uuid4())) is None

    @patch("simulate.utils.agent_optimiser.TestExecution")
    @patch("simulate.utils.agent_optimiser.CallExecution")
    @patch("simulate.utils.agent_optimiser.SimulateEvalConfig")
    def test_exception_returns_none(self, mock_eval, mock_call, mock_te):
        mock_te.DoesNotExist = self._DNE
        mock_te.objects.get.side_effect = RuntimeError("failure")
        assert get_call_executions_with_details(str(uuid.uuid4())) is None


class TestGetPromptFromRunTest:
    _DNE = type("DoesNotExist", (Exception,), {})

    @patch("model_hub.models.run_prompt.PromptVersion")
    def test_returns_none_for_non_prompt_source(self, MockVersion):
        run_test = MagicMock()
        run_test.source_type = "agent"
        assert _get_prompt_from_run_test(run_test) is None

    @patch("model_hub.models.run_prompt.PromptVersion")
    def test_prompt_version_not_found(self, MockVersion):
        run_test = MagicMock()
        run_test.source_type = "prompt"
        run_test.prompt_version_id = uuid.uuid4()
        MockVersion.DoesNotExist = self._DNE
        MockVersion.objects.get.side_effect = self._DNE
        assert _get_prompt_from_run_test(run_test) is None

    @patch("model_hub.models.run_prompt.PromptVersion")
    def test_returns_description(self, MockVersion):
        MockVersion.DoesNotExist = self._DNE
        run_test = MagicMock()
        run_test.source_type = "prompt"
        run_test.prompt_version_id = uuid.uuid4()
        version = MagicMock()
        version.prompt_config_snapshot = {
            "messages": [{"content": "Hello"}, {"content": "World"}]
        }
        MockVersion.objects.get.return_value = version
        result = _get_prompt_from_run_test(run_test)
        assert result["description"] == "Hello\nWorld"
        assert result["inbound"] is True

    @patch("model_hub.models.run_prompt.PromptVersion")
    def test_propagates_generic_exception(self, MockVersion):
        """Generic exceptions should propagate (only DoesNotExist is caught)."""
        MockVersion.DoesNotExist = self._DNE
        run_test = MagicMock()
        run_test.source_type = "prompt"
        run_test.prompt_version_id = uuid.uuid4()
        MockVersion.objects.get.side_effect = RuntimeError("db error")
        with pytest.raises(RuntimeError, match="db error"):
            _get_prompt_from_run_test(run_test)


class TestGetFullTestExecutionData:
    _DNE = type("DoesNotExist", (Exception,), {})

    @patch("simulate.utils.agent_optimiser.TestExecution")
    def test_not_found(self, mock_te):
        mock_te.DoesNotExist = self._DNE
        mock_te.objects.select_related.return_value.get.side_effect = self._DNE
        assert get_full_test_execution_data(str(uuid.uuid4())) is None

    @patch("simulate.utils.agent_optimiser.TestExecution")
    def test_exception_returns_none(self, mock_te):
        mock_te.DoesNotExist = self._DNE
        mock_te.objects.select_related.return_value.get.side_effect = (
            RuntimeError("failure")
        )
        assert get_full_test_execution_data(str(uuid.uuid4())) is None


class TestGetCallExecutionsWithDetailsDB:
    """DB-backed tests verifying the N+1 fix holds at runtime."""

    @pytest.mark.django_db(transaction=True)
    def test_no_call_executions_returns_empty(self, organization):
        """With a valid TestExecution but zero calls, returns an empty list."""
        run_test = RunTest.objects.create(
            name="N+1 Test",
            source_type=RunTest.SourceTypes.AGENT_DEFINITION,
            organization=organization,
        )
        test_execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_calls=0,
            completed_calls=0,
            failed_calls=0,
        )

        result = get_call_executions_with_details(str(test_execution.id))
        assert result is not None
        assert result == []

    @pytest.mark.django_db(transaction=True)
    def test_with_eval_configs_fixed_query_count(
        self, organization, django_assert_num_queries
    ):
        """Eval configs are fetched once, not per call (N+1 guard)."""
        run_test = RunTest.objects.create(
            name="N+1 Test",
            source_type=RunTest.SourceTypes.AGENT_DEFINITION,
            organization=organization,
        )
        test_execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_calls=0,
            completed_calls=0,
            failed_calls=0,
        )
        template = EvalTemplate.objects.create(
            name="Test Eval",
            config={},
            organization=organization,
        )
        SimulateEvalConfig.objects.create(
            eval_template=template,
            name="Config A",
            run_test=run_test,
        )

        # get_call_executions_with_details:
        # 1 - TestExecution.objects.get (no select_related("run_test"))
        # 1 - run_test lazy load on test_execution.run_test
        # 1 - eval configs with select_related("eval_template")
        # 1 - call_executions with select_related("scenario")
        # No per-call eval config queries (N+1 guard).
        with django_assert_num_queries(4):
            result = get_call_executions_with_details(str(test_execution.id))

        assert result is not None
        assert result == []
