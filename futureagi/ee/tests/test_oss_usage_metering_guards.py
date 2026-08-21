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
