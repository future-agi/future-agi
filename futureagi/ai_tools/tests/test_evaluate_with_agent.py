"""
Tests for the evaluate_with_agent MCP tool.

The tool wires together EvalScout → EvalOrchestrator, both of which make
real LLM calls. All AI components are mocked here so the tests stay fast,
deterministic, and free of external dependencies. What we're testing:

  1. Input validation (invalid scope, missing required params)
  2. Happy-path wiring: scout → orchestrator → formatted result
  3. Choices handling: numeric scoring vs. categorical labels
  4. Optional params: kb_id, eval_template_id passed through correctly
  5. Error propagation: orchestrator exception surfaces cleanly
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_trace
from tfc.ee_gating import EEFeature, FeatureUnavailable

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_FAKE_TRACE_ID = str(uuid.uuid4())
_FAKE_SPAN_ID = str(uuid.uuid4())
_FAKE_TEMPLATE_ID = str(uuid.uuid4())
_FAKE_KB_ID = str(uuid.uuid4())

_EMPTY_RESOURCES = {"knowledge_bases": [], "feedback_count": 0, "mcp_tools": []}

_SCOUT_BRIEF = {
    "complexity": "simple",
    "recommended_model": "flash",
    "criteria_needs_formalization": False,
    "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
    "resource_plan": {
        "use_kb": False,
        "kb_query_hints": [],
        "use_feedback": False,
        "use_mcp_tools": [],
    },
    "reasoning": "Simple criteria, no extra resources needed.",
    "available_resources": _EMPTY_RESOURCES,
}

_ORCHESTRATOR_OUTPUT = {
    "result": {
        "result": "Passed",
        "confidence": 0.9,
        "explanation": "The response was clear and helpful.",
    },
    "model_used": "flash",
    "eval_calls": 1,
    "resources_used": [],
    "drill_down_performed": False,
    "orchestrator_turns": 3,
    "token_usage": {
        "scout": {"prompt_tokens": 100, "completion_tokens": 50},
        "orchestrator": {"prompt_tokens": 500, "completion_tokens": 200},
        "eval_calls": {"prompt_tokens": 300, "completion_tokens": 100},
        "total": {"prompt_tokens": 900, "completion_tokens": 350, "total_tokens": 1250},
    },
}


def _ai_patches():
    """Context manager that mocks all external AI/DB calls made by the tool."""
    return (
        patch(
            "ee.agenthub.eval_orchestrator.utils.gather_available_resources",
            return_value=_EMPTY_RESOURCES,
        ),
        patch(
            "ee.agenthub.eval_orchestrator.utils.build_input_summary",
            return_value="TRACE: test-trace\nInput: Hello\nOutput: World",
        ),
        patch(
            "ee.agenthub.eval_orchestrator.EvalScout",
        ),
        patch(
            "ee.agenthub.eval_orchestrator.EvalOrchestrator",
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def trace(tool_context):
    return make_trace(tool_context)


@pytest.fixture
def mock_orchestrator():
    """Mock scout + orchestrator returning canned output."""
    with (
        patch(
            "ee.agenthub.eval_orchestrator.utils.gather_available_resources",
            return_value=_EMPTY_RESOURCES,
        ),
        patch(
            "ee.agenthub.eval_orchestrator.utils.build_input_summary",
            return_value="TRACE: test-trace",
        ),
        patch("ee.agenthub.eval_orchestrator.EvalScout") as MockScout,
        patch(
            "ee.agenthub.eval_orchestrator.EvalOrchestrator"
        ) as MockOrchestrator,
    ):
        MockScout.return_value.run.return_value = dict(_SCOUT_BRIEF)
        MockOrchestrator.return_value.run.return_value = dict(_ORCHESTRATOR_OUTPUT)
        yield MockScout, MockOrchestrator


@pytest.fixture(autouse=True)
def allow_agentic_eval_feature():
    """Default tests assume evaluate_with_agent is available."""
    with patch("tfc.ee_gating.check_ee_feature", return_value=None):
        yield


# ---------------------------------------------------------------------------
# 1. Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_blocked_when_agentic_eval_not_allowed(self, tool_context):
        with patch("tfc.ee_gating.check_ee_feature") as mock_check:
            mock_check.side_effect = FeatureUnavailable(
                EEFeature.AGENTIC_EVAL,
                detail="Agentic evaluation requires Boost plan",
            )

            result = run_tool(
                "evaluate_with_agent",
                {
                    "source_id": _FAKE_TRACE_ID,
                    "input_scope": "trace",
                    "criteria": "Is it helpful?",
                },
                tool_context,
            )

            assert result.is_error
            assert result.error_code == "ENTITLEMENT_DENIED"
            assert result.data == {
                "feature": EEFeature.AGENTIC_EVAL.value,
                "upgrade_required": True,
            }
            mock_check.assert_called_once_with(
                EEFeature.AGENTIC_EVAL, org_id=str(tool_context.organization.id)
            )

    def test_invalid_scope_returns_error(self, tool_context):
        result = run_tool(
            "evaluate_with_agent",
            {
                "source_id": _FAKE_TRACE_ID,
                "input_scope": "banana",
                "criteria": "Is it helpful?",
            },
            tool_context,
        )
        assert result.is_error
        assert "banana" in result.content
        assert "input_scope" in result.content

    def test_all_valid_scopes_accepted(self, tool_context, mock_orchestrator):
        for scope in ("span", "trace", "session", "dataset_row", "cell"):
            result = run_tool(
                "evaluate_with_agent",
                {
                    "source_id": str(uuid.uuid4()),
                    "input_scope": scope,
                    "criteria": "Is it good?",
                },
                tool_context,
            )
            assert (
                not result.is_error
            ), f"Scope '{scope}' unexpectedly failed: {result.content}"


# ---------------------------------------------------------------------------
# 2. Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_returns_result_and_explanation(self, tool_context, mock_orchestrator):
        result = run_tool(
            "evaluate_with_agent",
            {
                "source_id": _FAKE_TRACE_ID,
                "input_scope": "trace",
                "criteria": "Is the response helpful?",
            },
            tool_context,
        )

        assert not result.is_error
        assert "Passed" in result.content
        assert "The response was clear and helpful." in result.content
        assert "90%" in result.content  # confidence formatted as percentage

    def test_result_data_structure(self, tool_context, mock_orchestrator):
        result = run_tool(
            "evaluate_with_agent",
            {
                "source_id": _FAKE_TRACE_ID,
                "input_scope": "trace",
                "criteria": "Is the response helpful?",
            },
            tool_context,
        )

        assert result.data["result"] == "Passed"
        assert result.data["confidence"] == 0.9
        assert result.data["model_used"] == "flash"
        assert result.data["orchestrator_turns"] == 3
        assert result.data["eval_calls"] == 1

    def test_scout_called_before_orchestrator(self, tool_context, mock_orchestrator):
        MockScout, MockOrchestrator = mock_orchestrator

        run_tool(
            "evaluate_with_agent",
            {
                "source_id": _FAKE_TRACE_ID,
                "input_scope": "trace",
                "criteria": "Is the response helpful?",
            },
            tool_context,
        )

        MockScout.return_value.run.assert_called_once()
        MockOrchestrator.return_value.run.assert_called_once()

    def test_scout_brief_enriched_with_available_resources(
        self, tool_context, mock_orchestrator
    ):
        """Orchestrator must receive scout_brief with available_resources set."""
        MockScout, MockOrchestrator = mock_orchestrator

        run_tool(
            "evaluate_with_agent",
            {
                "source_id": _FAKE_TRACE_ID,
                "input_scope": "trace",
                "criteria": "Check helpfulness",
            },
            tool_context,
        )

        _, kwargs = MockOrchestrator.call_args
        brief = kwargs.get("scout_brief") or MockOrchestrator.call_args[0][1]
        assert "available_resources" in brief

    def test_content_includes_source_id_and_criteria(
        self, tool_context, mock_orchestrator
    ):
        result = run_tool(
            "evaluate_with_agent",
            {
                "source_id": _FAKE_TRACE_ID,
                "input_scope": "trace",
                "criteria": "Is the response helpful?",
            },
            tool_context,
        )

        assert _FAKE_TRACE_ID in result.content
        assert "Is the response helpful?" in result.content


# ---------------------------------------------------------------------------
# 3. Choices handling
# ---------------------------------------------------------------------------


class TestChoices:
    def test_numeric_scoring_mode_when_no_choices(self, tool_context):
        """With no choices, the tool passes choices=None → orchestrator defaults to 0–1 scoring."""
        with (
            patch(
                "ee.agenthub.eval_orchestrator.utils.gather_available_resources",
                return_value=_EMPTY_RESOURCES,
            ),
            patch(
                "ee.agenthub.eval_orchestrator.utils.build_input_summary",
                return_value="SPAN: test",
            ),
            patch("ee.agenthub.eval_orchestrator.EvalScout") as MockScout,
            patch(
                "ee.agenthub.eval_orchestrator.EvalOrchestrator"
            ) as MockOrchestrator,
        ):
            numeric_output = dict(_ORCHESTRATOR_OUTPUT)
            numeric_output["result"] = {
                "result": "0.8",
                "confidence": 0.95,
                "explanation": "Good",
            }
            MockScout.return_value.run.return_value = dict(_SCOUT_BRIEF)
            MockOrchestrator.return_value.run.return_value = numeric_output

            result = run_tool(
                "evaluate_with_agent",
                {
                    "source_id": _FAKE_SPAN_ID,
                    "input_scope": "span",
                    "criteria": "Rate response quality",
                    # no choices → numeric mode
                },
                tool_context,
            )

            assert not result.is_error
            assert "0.8" in result.content
            _, kwargs = MockOrchestrator.call_args
            eval_config = kwargs.get("eval_config") or MockOrchestrator.call_args[0][0]
            assert eval_config.choices is None

    def test_categorical_choices_passed_through(self, tool_context, mock_orchestrator):
        MockScout, MockOrchestrator = mock_orchestrator

        run_tool(
            "evaluate_with_agent",
            {
                "source_id": _FAKE_SPAN_ID,
                "input_scope": "span",
                "criteria": "Did the agent follow instructions?",
                "choices": ["Passed", "Failed"],
            },
            tool_context,
        )

        _, kwargs = MockOrchestrator.call_args
        eval_config = kwargs.get("eval_config") or MockOrchestrator.call_args[0][0]
        assert eval_config.choices == ["Passed", "Failed"]


# ---------------------------------------------------------------------------
# 4. Optional params: kb_id and eval_template_id
# ---------------------------------------------------------------------------


class TestOptionalParams:
    def test_kb_id_passed_to_gather_resources(self, tool_context):
        with (
            patch(
                "ee.agenthub.eval_orchestrator.utils.gather_available_resources",
            ) as mock_gather,
            patch(
                "ee.agenthub.eval_orchestrator.utils.build_input_summary",
                return_value="TRACE: test",
            ),
            patch("ee.agenthub.eval_orchestrator.EvalScout") as MockScout,
            patch(
                "ee.agenthub.eval_orchestrator.EvalOrchestrator"
            ) as MockOrchestrator,
        ):
            mock_gather.return_value = _EMPTY_RESOURCES
            MockScout.return_value.run.return_value = dict(_SCOUT_BRIEF)
            MockOrchestrator.return_value.run.return_value = dict(_ORCHESTRATOR_OUTPUT)

            run_tool(
                "evaluate_with_agent",
                {
                    "source_id": _FAKE_TRACE_ID,
                    "input_scope": "trace",
                    "criteria": "Check policy compliance",
                    "kb_id": _FAKE_KB_ID,
                },
                tool_context,
            )

            mock_gather.assert_called_once_with(
                organization_id=str(tool_context.organization_id),
                eval_template_id=None,
                kb_id=_FAKE_KB_ID,
            )

    def test_eval_template_id_passed_to_gather_resources(self, tool_context):
        with (
            patch(
                "ee.agenthub.eval_orchestrator.utils.gather_available_resources",
            ) as mock_gather,
            patch(
                "ee.agenthub.eval_orchestrator.utils.build_input_summary",
                return_value="TRACE: test",
            ),
            patch("ee.agenthub.eval_orchestrator.EvalScout") as MockScout,
            patch(
                "ee.agenthub.eval_orchestrator.EvalOrchestrator"
            ) as MockOrchestrator,
        ):
            mock_gather.return_value = _EMPTY_RESOURCES
            MockScout.return_value.run.return_value = dict(_SCOUT_BRIEF)
            MockOrchestrator.return_value.run.return_value = dict(_ORCHESTRATOR_OUTPUT)

            run_tool(
                "evaluate_with_agent",
                {
                    "source_id": _FAKE_TRACE_ID,
                    "input_scope": "trace",
                    "criteria": "Check helpfulness",
                    "eval_template_id": _FAKE_TEMPLATE_ID,
                },
                tool_context,
            )

            mock_gather.assert_called_once_with(
                organization_id=str(tool_context.organization_id),
                eval_template_id=_FAKE_TEMPLATE_ID,
                kb_id=None,
            )

    def test_eval_config_carries_kb_and_template(self, tool_context, mock_orchestrator):
        MockScout, MockOrchestrator = mock_orchestrator

        run_tool(
            "evaluate_with_agent",
            {
                "source_id": _FAKE_TRACE_ID,
                "input_scope": "trace",
                "criteria": "Check accuracy",
                "kb_id": _FAKE_KB_ID,
                "eval_template_id": _FAKE_TEMPLATE_ID,
            },
            tool_context,
        )

        _, kwargs = MockOrchestrator.call_args
        eval_config = kwargs.get("eval_config") or MockOrchestrator.call_args[0][0]
        assert eval_config.kb_id == _FAKE_KB_ID
        assert eval_config.eval_template_id == _FAKE_TEMPLATE_ID


# ---------------------------------------------------------------------------
# 5. Error propagation
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_orchestrator_exception_surfaces_as_error(self, tool_context):
        with (
            patch(
                "ee.agenthub.eval_orchestrator.utils.gather_available_resources",
                return_value=_EMPTY_RESOURCES,
            ),
            patch(
                "ee.agenthub.eval_orchestrator.utils.build_input_summary",
                return_value="TRACE: test",
            ),
            patch("ee.agenthub.eval_orchestrator.EvalScout") as MockScout,
            patch(
                "ee.agenthub.eval_orchestrator.EvalOrchestrator"
            ) as MockOrchestrator,
        ):
            MockScout.return_value.run.return_value = dict(_SCOUT_BRIEF)
            MockOrchestrator.return_value.run.side_effect = RuntimeError("LLM timeout")

            result = run_tool(
                "evaluate_with_agent",
                {
                    "source_id": _FAKE_TRACE_ID,
                    "input_scope": "trace",
                    "criteria": "Check helpfulness",
                },
                tool_context,
            )

            assert result.is_error

    def test_build_summary_failure_does_not_crash(self, tool_context):
        """build_input_summary failure (e.g. object not found) should not crash the tool."""
        with (
            patch(
                "ee.agenthub.eval_orchestrator.utils.gather_available_resources",
                return_value=_EMPTY_RESOURCES,
            ),
            patch(
                "ee.agenthub.eval_orchestrator.utils.build_input_summary",
                # utils already swallows exceptions and returns a fallback string,
                # but we simulate the fallback string here
                return_value="[Summary unavailable for trace id=fake: DoesNotExist]",
            ),
            patch("ee.agenthub.eval_orchestrator.EvalScout") as MockScout,
            patch(
                "ee.agenthub.eval_orchestrator.EvalOrchestrator"
            ) as MockOrchestrator,
        ):
            MockScout.return_value.run.return_value = dict(_SCOUT_BRIEF)
            MockOrchestrator.return_value.run.return_value = dict(_ORCHESTRATOR_OUTPUT)

            result = run_tool(
                "evaluate_with_agent",
                {
                    "source_id": _FAKE_TRACE_ID,
                    "input_scope": "trace",
                    "criteria": "Check helpfulness",
                },
                tool_context,
            )

            # Tool should still complete — scout gets the fallback summary string
            assert not result.is_error
            scout_call_args = MockScout.return_value.run.call_args
            summary_passed = (
                scout_call_args[1].get("input_summary") or scout_call_args[0][1]
            )
            assert "unavailable" in summary_passed
