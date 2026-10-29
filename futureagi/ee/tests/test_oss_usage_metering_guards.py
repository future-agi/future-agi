"""
Tests for OSS-mode guards around EE usage metering fallbacks.

When the ee.usage services can't be imported (OSS deployments), the
synthetic-data generation and prompt-optimizer paths must skip metering
instead of crashing with AttributeError on the None fallbacks
(see issue #727). The EE path must keep enforcing usage limits.
"""

import sys
from unittest.mock import Mock, patch

import pandas as pd
import pytest

# Setting a sys.modules entry to None makes `import` raise ImportError,
# which is exactly what happens when ee.usage isn't shipped.
_METERING_ABSENT = {"ee.usage.services.metering": None}
_USAGE_ENTRIES_ABSENT = {"ee.usage.utils.usage_entries": None}


class TestCaseGeneratorUsagePrecheck:
    """generate_cases_for_intent must not crash when metering is unavailable."""

    def _run_generate(self):
        from ee.agenthub.scenario_graph.services import case_generator

        generated = pd.DataFrame({"scenario": ["a", "b"]})
        with (
            patch.object(
                case_generator,
                "_generate_raw_data_from_sda",
                return_value=(generated, 0.0),
            ),
            patch.object(
                case_generator,
                "convert_sda_data_to_cases",
                return_value=[{"scenario": "a"}, {"scenario": "b"}],
            ),
        ):
            return case_generator.generate_cases_for_intent(
                intent_id="intent-1",
                intent_value="test intent",
                branches_metadata=[{"branch_name": "b1"}],
                batch_size=2,
                agent_context={"organization_id": "org-1"},
            )

    def test_skips_precheck_when_metering_unavailable(self):
        """OSS mode: generation proceeds instead of raising AttributeError."""
        with patch.dict(sys.modules, {**_METERING_ABSENT, **_USAGE_ENTRIES_ABSENT}):
            cases = self._run_generate()

        assert [c["intent_id"] for c in cases] == ["intent-1", "intent-1"]

    def test_enforces_limit_when_metering_available(self):
        """EE mode: a denied usage check still raises ValueError."""
        metering_stub = Mock()
        metering_stub.check_usage.return_value = Mock(
            allowed=False, reason="Usage limit exceeded"
        )
        with patch.dict(sys.modules, {"ee.usage.services.metering": metering_stub}):
            with pytest.raises(ValueError, match="Usage limit exceeded"):
                self._run_generate()

    def test_allowed_check_proceeds_to_generation(self):
        """EE mode: an allowed usage check generates cases as before."""
        metering_stub = Mock()
        metering_stub.check_usage.return_value = Mock(allowed=True)
        with patch.dict(sys.modules, {"ee.usage.services.metering": metering_stub}):
            cases = self._run_generate()

        metering_stub.check_usage.assert_called_once()
        assert len(cases) == 2


class TestCaseGeneratorCostLogging:
    """_log_generation_cost must be a no-op when usage entries are unavailable."""

    def test_returns_quietly_when_usage_entries_unavailable(self):
        from ee.agenthub.scenario_graph.services import case_generator

        generated = pd.DataFrame({"scenario": ["a"]})
        with (
            patch.dict(sys.modules, _USAGE_ENTRIES_ABSENT),
            patch.object(case_generator, "logger") as mock_logger,
        ):
            case_generator._log_generation_cost(generated, {"organization_id": "org-1"})

        mock_logger.exception.assert_not_called()


class TestEnhancedScenariosAgentUsagePrecheck:
    """ESA._generate_raw_cases_from_sda must skip the pre-check in OSS mode."""

    def _build_agent(self):
        from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
            EnhancedScenariosAgent,
        )

        with patch.object(
            EnhancedScenariosAgent, "__init__", lambda self, **kwargs: None
        ):
            agent = EnhancedScenariosAgent()
        agent.agent_definition = Mock()
        agent.agent_definition.organization.id = "org-1"
        return agent

    def test_skips_precheck_when_metering_unavailable(self):
        """OSS mode: reaches the empty-branch early return instead of crashing."""
        agent = self._build_agent()
        with patch.dict(sys.modules, _METERING_ABSENT):
            result = agent._generate_raw_cases_from_sda(
                template_branch={},
                sda=Mock(),
                user_requirements={},
                rows=1,
            )

        assert result is None

    def test_enforces_limit_when_metering_available(self):
        """EE mode: a denied usage check still raises ValueError."""
        agent = self._build_agent()
        metering_stub = Mock()
        metering_stub.check_usage.return_value = Mock(
            allowed=False, reason="Usage limit exceeded"
        )
        with patch.dict(sys.modules, {"ee.usage.services.metering": metering_stub}):
            with pytest.raises(ValueError, match="Usage limit exceeded"):
                agent._generate_raw_cases_from_sda(
                    template_branch={},
                    sda=Mock(),
                    user_requirements={},
                    rows=1,
                )


class TestPromptOptimizerUsageLogging:
    """_get_evaluation_feedback must aggregate scores without EE usage logging."""

    def _build_optimizer(self, eval_result):
        from ee.agenthub.prompt_optimizer_agent.agent_task_v2 import PromptOptimizer

        eval_template = Mock()
        eval_template.config = {"eval_type_id": "TestEval", "output": "score"}
        eval_metric = Mock()
        eval_metric.template = eval_template
        eval_metric.config = {"mapping": {"input": "col-1"}}

        with patch.object(PromptOptimizer, "__init__", lambda self, **kwargs: None):
            optimizer = PromptOptimizer()
        optimizer.user_eval_metrics = [eval_metric]

        runner_cls = Mock()
        runner_cls.return_value._create_eval_instance.return_value.run.return_value = (
            eval_result
        )
        patches = [
            patch(
                "ee.agenthub.prompt_optimizer_agent.agent_task_v2.EvaluationRunner",
                runner_cls,
            ),
            patch("evaluations.engine.registry.is_registered", return_value=True),
            patch("evaluations.engine.registry.get_eval_class", return_value=Mock()),
            patch.object(PromptOptimizer, "_setup_eval_params", return_value={}),
            patch.object(
                PromptOptimizer, "_process_eval_result", return_value=(0.8, "ok")
            ),
            patch.object(
                PromptOptimizer, "_format_judgements", return_value="aggregated"
            ),
        ]
        return optimizer, patches

    def test_scores_aggregate_when_usage_logging_unavailable(self):
        """OSS mode: evals run and aggregate instead of the old cascade where
        the None log_api_call raised per metric, every metric was skipped, and
        sum(scores) / len(scores) died with ZeroDivisionError."""
        optimizer, patches = self._build_optimizer(eval_result=Mock())
        with patch(
            "ee.agenthub.prompt_optimizer_agent.agent_task_v2.log_api_call", None
        ):
            for p in patches:
                p.start()
            try:
                judgements, score = optimizer._get_evaluation_feedback(
                    [{"role": "user", "content": "hi"}]
                )
            finally:
                for p in patches:
                    p.stop()

        assert judgements == "aggregated"
        assert score == 80.0

    def test_api_call_row_updated_when_usage_logging_available(self):
        """EE mode: the log row is created, enriched, and marked SUCCESS."""
        from tfc.constants.api_calls import APICallStatusChoices

        eval_result = Mock()
        eval_result.eval_results = [{"data": "row-data"}]
        optimizer, patches = self._build_optimizer(eval_result=eval_result)

        log_row = Mock()
        log_row.status = APICallStatusChoices.PROCESSING
        log_row.config = "{}"
        column_cls = Mock()
        column_cls.objects.filter.return_value.values.return_value = []

        with (
            patch(
                "ee.agenthub.prompt_optimizer_agent.agent_task_v2.log_api_call",
                return_value=log_row,
            ) as mock_log,
            patch("ee.agenthub.prompt_optimizer_agent.agent_task_v2.Column", column_cls),
        ):
            for p in patches:
                p.start()
            try:
                judgements, score = optimizer._get_evaluation_feedback(
                    [{"role": "user", "content": "hi"}]
                )
            finally:
                for p in patches:
                    p.stop()

        mock_log.assert_called_once()
        assert log_row.status == APICallStatusChoices.SUCCESS.value
        log_row.save.assert_called_with(update_fields=["config", "status"])
        assert score == 80.0


class TestEnumsResolveWithoutEE:
    """The billing enums must come from tfc.constants, never be None."""

    def test_case_generator_enum_is_canonical(self):
        from ee.agenthub.scenario_graph.services import case_generator
        from tfc.constants.api_calls import APICallTypeChoices

        assert case_generator.APICallTypeChoices is APICallTypeChoices

    def test_enhanced_scenarios_agent_enum_is_canonical(self):
        from ee.agenthub.scenario_graph import enhanced_scenarios_agent
        from tfc.constants.api_calls import APICallTypeChoices

        assert enhanced_scenarios_agent.APICallTypeChoices is APICallTypeChoices

    def test_agent_task_v2_enums_are_canonical(self):
        from ee.agenthub.prompt_optimizer_agent import agent_task_v2
        from tfc.constants.api_calls import (
            APICallStatusChoices,
            APICallTypeChoices,
        )

        assert agent_task_v2.APICallTypeChoices is APICallTypeChoices
        assert agent_task_v2.APICallStatusChoices is APICallStatusChoices
