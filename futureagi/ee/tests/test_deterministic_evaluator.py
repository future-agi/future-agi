"""
Comprehensive Test Suite for DeterministicEvaluator and DeterministicAgent

Tests all refactored functionality including:
- ModelConfig resolution
- Single LLM architecture
- Modality validation
- Input type detection
- Evaluator → Agent flow
- Runtime kwargs handling
- Real LLM calls (marked with @pytest.mark.live_llm)
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfig, ModelConfigs
from ee.agenthub.deterministic_agent.deterministic_agent import (
    DeterministicAgent,
)
from ee.evals.futureagi.eval_deterministic.evaluator import (
    DeterministicEvaluator,
)
from model_hub.utils.evals import evals_template

pytestmark = pytest.mark.skip(
    reason="DeterministicEvaluator being retired; migrating to AgentEvaluator"
)

# =============================================================================
# Unit Tests - ModelConfig Resolution
# =============================================================================


@pytest.mark.unit
class TestModelConfigResolution:
    """Test ModelConfig resolution logic."""

    def test_get_config_turing_large(self):
        """TURING_LARGE config resolves correctly."""
        config = ModelConfigs.get_config(ModelConfigs.TURING_LARGE.model_name)
        assert config is not None
        assert config.model_name == ModelConfigs.TURING_LARGE.model_name
        assert config.provider == ModelConfigs.TURING_LARGE.provider
        assert config.temperature == 0.2
        assert config.supports_audio == False
        assert config.supports_pdf == False

    def test_get_config_turing_small(self):
        """TURING_SMALL config resolves correctly."""
        config = ModelConfigs.get_config(ModelConfigs.TURING_SMALL.model_name)
        assert config is not None
        assert config.supports_audio == False
        assert config.supports_pdf == False

    def test_get_config_invalid_model(self):
        """Invalid model returns None."""
        config = ModelConfigs.get_config("invalid-model")
        assert config is None

    def test_get_temperature(self):
        """get_temperature helper works."""
        temp = ModelConfigs.get_temperature(ModelConfigs.TURING_LARGE.model_name)
        assert temp == 0.2

    def test_get_max_tokens(self):
        """get_max_tokens helper works."""
        max_tokens = ModelConfigs.get_max_tokens(ModelConfigs.TURING_LARGE.model_name)
        assert max_tokens == 50000


# =============================================================================
# Unit Tests - DeterministicEvaluator Initialization
# ============================================================================


@pytest.mark.unit
class TestDeterministicEvaluatorInit:
    """Test DeterministicEvaluator initialization."""

    def test_default_initialization(self):
        """Default initialization uses TURING_LARGE."""
        evaluator = DeterministicEvaluator(rule_prompt="Test", choices=["yes", "no"])
        assert evaluator.model_name == ModelConfigs.TURING_LARGE.model_name
        assert evaluator.temperature == ModelConfigs.TURING_LARGE.temperature
        assert evaluator.check_internet == False
        assert evaluator.fewshots is None

    def test_explicit_model(self):
        """Explicit model parameter works."""
        evaluator = DeterministicEvaluator(
            model=ModelConfigs.TURING_SMALL.model_name,
            rule_prompt="Test",
            choices=["yes", "no"],
        )
        assert evaluator.model_name == ModelConfigs.TURING_SMALL.model_name
        assert evaluator.temperature == ModelConfigs.TURING_SMALL.temperature

    def test_kwargs_stored(self):
        """Runtime parameters stored correctly."""
        evaluator = DeterministicEvaluator(
            rule_prompt="Test",
            choices=["yes", "no"],
            check_internet=True,
            few_shots=[{"q": "test", "a": "answer"}],
            knowledge_base_id="kb-123",
        )
        assert evaluator.check_internet == True
        assert evaluator.fewshots is not None
        assert evaluator.knowledge_base_id == "kb-123"

    def test_invalid_model_falls_back(self):
        """Invalid model falls back to TURING_LARGE."""
        evaluator = DeterministicEvaluator(
            model="invalid-model", rule_prompt="Test", choices=["yes", "no"]
        )
        # Should use TURING_LARGE since invalid model returns None
        assert evaluator.model_name == ModelConfigs.TURING_LARGE.model_name


# =============================================================================
# Unit Tests - DeterministicAgent Initialization
# =============================================================================


@pytest.mark.unit
class TestDeterministicAgentInit:
    """Test DeterministicAgent initialization."""

    def test_default_initialization(self):
        """Default initialization uses the direct multimodal model."""
        agent = DeterministicAgent()
        assert agent.llm is not None
        assert agent.llm.model_name == ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
        assert agent.supports_audio == True
        assert agent.supports_pdf == True

    def test_llm_injection(self):
        """LLM injection extracts turing model_name and routes via Turing."""
        mock_llm = Mock(spec=LLM)
        mock_llm.model_name = ModelConfigs.TURING_SMALL.model_name

        agent = DeterministicAgent(llm=mock_llm)
        assert agent.model_name == ModelConfigs.TURING_SMALL.model_name
        assert agent.is_turing_model is True
        assert agent.llm is None
        assert agent.turing_client is not None
        assert agent.supports_audio == False  # TURING_SMALL

    def test_fewshots_initialization(self):
        """Fewshots parameter handled correctly."""
        fewshots = [{"input": "test", "output": "yes"}]
        agent = DeterministicAgent(fewshots=fewshots)
        assert agent.fewshots == fewshots


# =============================================================================
# Unit Tests - Input Validation
# =============================================================================


@pytest.mark.unit
class TestInputValidation:
    """Test input validation in handlers."""

    def test_empty_text_raises_error(self):
        """Empty text input raises ValueError."""
        agent = DeterministicAgent()
        with pytest.raises(ValueError, match=agent.EMPTY_EVAL_RESPONSE_ERROR_MESSAGE):
            agent._build_content_block("", "text", 0)

    def test_none_text_raises_error(self):
        """None text input raises ValueError."""
        agent = DeterministicAgent()
        with pytest.raises(ValueError, match=agent.EMPTY_EVAL_RESPONSE_ERROR_MESSAGE):
            agent._build_content_block(None, "text", 0)

    def test_empty_image_raises_error(self):
        """Empty image input raises ValueError."""
        agent = DeterministicAgent()
        with pytest.raises(ValueError, match=agent.EMPTY_EVAL_RESPONSE_ERROR_MESSAGE):
            agent._build_content_block("", "image", 0)

    def test_valid_text_input(self):
        """Valid text input processes correctly."""
        agent = DeterministicAgent()
        content = agent._build_content_block("test text", "text", 0)
        assert len(content) == 3
        assert content[0]["text"] == "<variable_1>"
        assert content[1]["text"] == "test text"
        assert content[2]["text"] == "</variable_1>"


# =============================================================================
# Unit Tests - Modality Validation
# =============================================================================


@pytest.mark.unit
class TestModalityValidation:
    """Test modality validation enforcement."""

    def test_audio_with_turing_small_raises_error(self):
        """Audio input with a non-audio direct model raises before LLM calls."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_FLASH.model_name
        )

        assert agent.supports_audio == False

        with pytest.raises(ValueError, match=agent.EMPTY_EVAL_RESPONSE_ERROR_MESSAGE):
            agent.evaluate(
                {
                    "inputs": ["test"],
                    "input_type": ["audio"],
                    "rule_prompt": "Test",
                    "choices": ["yes", "no"],
                    "multi_choice": False,
                }
            )

    def test_turing_audio_path_does_not_use_direct_llm(self):
        """Turing models route multimodal requests through the Turing client."""
        agent = DeterministicAgent(model_name=ModelConfigs.TURING_SMALL.model_name)

        assert agent.is_turing_model is True
        assert agent.llm is None
        assert agent.turing_client is not None
        with patch.object(
            agent,
            "_call_llm",
            return_value='{"choices": ["yes"], "explanation": "ok"}',
        ):
            result = agent.evaluate(
                {
                    "inputs": ["test"],
                    "input_type": ["audio"],
                    "rule_prompt": "Test",
                    "choices": ["yes", "no"],
                    "multi_choice": False,
                }
            )

        assert result["choices"] == ["yes"]

    def test_pdf_with_direct_text_model_raises_error(self):
        """PDF input with a non-PDF direct model raises before LLM calls."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_FLASH.model_name
        )

        with pytest.raises(ValueError, match=agent.EMPTY_EVAL_RESPONSE_ERROR_MESSAGE):
            agent.evaluate(
                {
                    "inputs": ["test.pdf"],
                    "input_type": ["pdf"],
                    "rule_prompt": "Test",
                    "choices": ["yes", "no"],
                    "multi_choice": False,
                }
            )

    def test_evaluator_passes_model_type_for_turing_alias(self):
        """Evaluator-created agents preserve turing routing aliases."""
        evaluator = DeterministicEvaluator(
            model=ModelConfigs.TURING_SMALL.model_name,
            rule_prompt="Test",
            choices=["yes", "no"],
        )

        with patch.object(
            evaluator,
            "_format_chat_history",
            return_value={
                "inputs": ["test"],
                "input_type": ["audio"],
                "rule_prompt": "Test",
                "choices": ["yes", "no"],
                "multi_choice": False,
            },
        ):
            agent = DeterministicAgent(
                llm=evaluator,
                check_internet=False,
                fewshots=None,
                knowledge_base_id=None,
            )

        assert agent.is_turing_model is True
        assert agent.routing_model == ModelConfigs.TURING_SMALL.model_name


# =============================================================================
# Integration Tests - Real File Processing
# =============================================================================

# Test file URLs (static but should work)
TEST_AUDIO_URL = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/audio/a16850ec-bbbc-4075-acc8-c1e553d4514d/ee571c8f-26c7-4b20-9b05-2a88c0b9df4b"
TEST_IMAGE_URL = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/dcc00d97-fde8-4721-bd3b-de75e2898f63/2ec84fac-886d-4fd8-be4f-e061ab070104"
TEST_PDF_URL = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/documents/a16850ec-bbbc-4075-acc8-c1e553d4514d/a4bab4d8-da56-4831-95ed-e576cc767739"


def _get_eval_config(eval_name: str) -> dict:
    for eval_config in evals_template:
        if eval_config.get("name") == eval_name:
            return eval_config
    raise ValueError(f"Eval config not found for {eval_name}")


@pytest.mark.integration
class TestRealFileProcessing:
    """Integration tests with real file URLs."""

    def test_audio_url_processing(self):
        """Process real audio URL with TURING_LARGE."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
        )

        # Should process without errors
        content = agent._build_content_block(TEST_AUDIO_URL, "audio", 0)

        assert len(content) == 5  # Variable tags + audio tags + content
        assert content[0]["text"] == "<variable_1>"
        assert content[1]["text"] == "<audio>"
        assert content[2]["type"] == "image_url"
        assert content[3]["text"] == "</audio>"
        assert content[4]["text"] == "</variable_1>"

    def test_image_url_processing(self):
        """Process real image URL."""
        agent = DeterministicAgent()

        # Should process without errors
        content = agent._build_content_block(TEST_IMAGE_URL, "image", 0)

        assert len(content) == 5
        assert content[0]["text"] == "<variable_1>"
        assert content[1]["text"] == "<image_1>"
        assert content[2]["type"] == "image_url"
        assert content[3]["text"] == "</image_1>"
        assert content[4]["text"] == "</variable_1>"

    def test_pdf_url_processing(self):
        """Process real PDF URL with TURING_LARGE."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
        )

        # Should process without errors
        content = agent._build_content_block(TEST_PDF_URL, "pdf", 0)

        assert len(content) == 5
        assert content[0]["text"] == "<variable_1>"
        assert content[1]["text"] == "<pdf>"
        assert content[2]["type"] == "file"
        assert content[3]["text"] == "</pdf>"
        assert content[4]["text"] == "</variable_1>"

    def test_text_with_image_mixed_input(self):
        """Test evaluation with mixed text and image inputs."""
        agent = DeterministicAgent()

        payload = {
            "inputs": ["What's in this image?", TEST_IMAGE_URL],
            "input_type": ["text", "image"],
            "rule_prompt": "Does the query match the image?",
            "choices": ["yes", "no"],
            "multi_choice": False,
        }

        # Should build prompt successfully
        content, criteria_prompt = agent._get_prompt(payload)

        # Verify content includes both variables
        content_str = str(content)
        assert "<variable_1>" in content_str
        assert "<variable_2>" in content_str

    def test_audio_with_turing_small_fails(self):
        """A non-audio direct model should reject audio before LLM calls."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_FLASH.model_name
        )

        with pytest.raises(ValueError, match=agent.EMPTY_EVAL_RESPONSE_ERROR_MESSAGE):
            agent.evaluate(
                {
                    "inputs": [TEST_AUDIO_URL],
                    "input_type": ["audio"],
                    "rule_prompt": "Test",
                    "choices": ["yes", "no"],
                }
            )

    def test_pdf_with_turing_small_fails(self):
        """A non-PDF direct model should reject PDF before LLM calls."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_FLASH.model_name
        )

        with pytest.raises(ValueError, match=agent.EMPTY_EVAL_RESPONSE_ERROR_MESSAGE):
            agent.evaluate(
                {
                    "inputs": [TEST_PDF_URL],
                    "input_type": ["pdf"],
                    "rule_prompt": "Test",
                    "choices": ["yes", "no"],
                }
            )


# =============================================================================
# Integration Tests - Evaluator → Agent Flow
# =============================================================================


@pytest.mark.integration
class TestEvaluatorAgentFlow:
    """Test integration between Evaluator and Agent."""

    def test_evaluator_creates_agent(self):
        """Evaluator correctly initializes Agent."""
        evaluator = DeterministicEvaluator(
            rule_prompt="Test rule",
            choices=["yes", "no"],
            check_internet=True,
            few_shots=[{"q": "test"}],
        )

        # Mock agent evaluate to avoid LLM call
        with patch.object(
            DeterministicAgent,
            "evaluate",
            return_value={"choices": ["yes"], "explanation": "test"},
        ):
            # Trigger _evaluate which creates agent
            result = evaluator._evaluate(input="test input", output="test output")

            # Verify result structure
            assert "name" in result
            assert "data" in result

    def test_runtime_kwargs_override(self):
        """Runtime kwargs override init values."""
        evaluator = DeterministicEvaluator(
            rule_prompt="Test",
            choices=["yes", "no"],
            few_shots=[{"old": "value"}],
            check_internet=False,
        )

        # Before _evaluate
        assert evaluator.fewshots == [{"old": "value"}]
        assert evaluator.check_internet == False

        # Mock to capture agent initialization
        original_init = DeterministicAgent.__init__
        captured_kwargs = {}

        def mock_init(self, **kwargs):
            captured_kwargs.update(kwargs)
            return original_init(self, **kwargs)

        with patch.object(DeterministicAgent, "__init__", mock_init):
            with patch.object(
                DeterministicAgent,
                "evaluate",
                return_value={"choices": ["yes"], "explanation": "test"},
            ):
                evaluator._evaluate(
                    input="test", few_shots=[{"new": "value"}], check_internet=True
                )

        # Runtime values should override
        assert evaluator.fewshots == [{"new": "value"}]
        assert evaluator.check_internet == True


# =============================================================================
# Integration Tests - Input Type Detection
# =============================================================================


@pytest.mark.integration
class TestInputTypeDetection:
    """Test input type detection flow."""

    def test_input_type_detected_in_evaluator(self):
        """Input types detected in evaluator._evaluate."""
        evaluator = DeterministicEvaluator(rule_prompt="Test", choices=["yes", "no"])

        # Mock agent to capture payload
        captured_payload = {}

        def mock_evaluate(self, payload):
            captured_payload.update(payload)
            return {"choices": ["yes"], "explanation": "test"}

        with patch.object(DeterministicAgent, "evaluate", mock_evaluate):
            evaluator._evaluate(input="text", output="more text")

        # Verify input_type was detected and included in payload
        assert "input_type" in captured_payload
        assert len(captured_payload["input_type"]) > 0


# =============================================================================
# E2E Tests - Real LLM Calls
# =============================================================================


@pytest.mark.live_llm
@pytest.mark.slow
class TestRealLLMCalls:
    """E2E tests with real LLM calls (requires API access)."""

    def test_text_only_evaluation(self):
        """Real evaluation with text inputs."""
        evaluator = DeterministicEvaluator(
            model=ModelConfigs.TURING_SMALL.model_name,
            rule_prompt="Is the response positive or negative?",
            choices=["positive", "negative"],
            multi_choice=False,
        )

        result = evaluator._evaluate(
            input="I love this product!", output="The customer seems happy"
        )

        assert "data" in result
        assert result["failure"] == False
        assert len(result["metrics"]) > 0

    def test_multi_choice_evaluation(self):
        """Real evaluation with multi-choice."""
        evaluator = DeterministicEvaluator(
            rule_prompt="Select all that apply: tone, clarity, accuracy",
            choices=["tone", "clarity", "accuracy"],
            multi_choice=True,
        )

        result = evaluator._evaluate(
            input="The sky is blue", output="Accurate and clear statement"
        )

        assert "data" in result
        assert result["failure"] == False

    def test_with_fewshots(self):
        """Real evaluation with few-shot examples."""
        fewshots = [
            {"type": "text", "text": "Example: positive → good"},
            {"type": "text", "text": "Example: negative → bad"},
        ]

        evaluator = DeterministicEvaluator(
            rule_prompt="Classify sentiment",
            choices=["good", "bad"],
            few_shots=fewshots,
        )

        result = evaluator._evaluate(
            input="This is terrible!", output="Negative sentiment detected"
        )

        assert "data" in result


# =============================================================================
# E2E Tests - Protect Evaluations
# =============================================================================


@pytest.mark.integration
class TestProtectEvaluations:
    """Test protect evaluation flow."""

    def test_protect_eval_flow(self):
        """Protect eval uses separate LLM."""
        evaluator = DeterministicEvaluator(
            model="protect/toxicity",
            provider="protect",
            rule_prompt="Check for toxicity",
            choices=["toxic", "non-toxic"],
        )

        # Mock protect LLM call
        with patch.object(
            LLM,
            "_get_completion_content",
            return_value={"choices": ["non-toxic"], "explanation": "No toxic content"},
        ):
            result = evaluator._evaluate(
                input="Hello, how are you?", eval_name="Toxicity", call_type="protect"
            )

            assert result is not None


# =============================================================================
# Additional Comprehensive Tests
# =============================================================================


@pytest.mark.unit
class TestChoicesHandling:
    """Test choices validation and handling."""

    def test_empty_choices_raises_error(self):
        """Empty choices list raises error."""
        with pytest.raises(ValueError):
            DeterministicAgent().evaluate(
                {
                    "inputs": ["test"],
                    "input_type": ["text"],
                    "rule_prompt": "Test",
                    "choices": [],  # Empty choices
                    "multi_choice": False,
                }
            )

    def test_single_choice_multi_choice(self):
        """Single choice with multi_choice=True."""
        # Mock returns JSON string
        mock_response = '{"choices": ["yes"], "explanation": "test"}'

        with patch.object(LLM, "_get_completion_content") as mock_llm:
            mock_llm.return_value = mock_response

            result = DeterministicAgent().evaluate(
                {
                    "inputs": ["test"],
                    "input_type": ["text"],
                    "rule_prompt": "Test",
                    "choices": ["yes"],
                    "multi_choice": True,
                }
            )

            assert result["choices"] == ["yes"]

    def test_multi_choice_evaluation(self):
        """Multi-choice returns multiple selections."""
        # Mock returns JSON string
        mock_response = (
            '{"choices": ["option1", "option3"], "explanation": "Selected multiple"}'
        )

        with patch.object(LLM, "_get_completion_content") as mock_llm:
            mock_llm.return_value = mock_response

            result = DeterministicAgent().evaluate(
                {
                    "inputs": ["test"],
                    "input_type": ["text"],
                    "rule_prompt": "Select all that apply",
                    "choices": ["option1", "option2", "option3"],
                    "multi_choice": True,
                }
            )

            assert len(result["choices"]) == 2
            assert "option1" in result["choices"]
            assert "option3" in result["choices"]


@pytest.mark.unit
class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_missing_rule_prompt(self):
        """Missing rule_prompt raises error."""
        with pytest.raises((KeyError, ValueError)):
            DeterministicAgent().evaluate(
                {
                    "inputs": ["test"],
                    "input_type": ["text"],
                    "choices": ["yes", "no"],
                    "multi_choice": False,
                    # Missing rule_prompt
                }
            )

    def test_mismatched_inputs_and_types_length(self):
        """Mismatched inputs and input_type lengths handled."""
        # Mock returns JSON string
        mock_response = '{"choices": ["yes"], "explanation": "test"}'

        with patch.object(LLM, "_get_completion_content") as mock_llm:
            mock_llm.return_value = mock_response

            # Should auto-detect missing types
            result = DeterministicAgent().evaluate(
                {
                    "inputs": ["text1", "text2"],
                    "input_type": ["text"],  # Only 1 type for 2 inputs
                    "rule_prompt": "Test",
                    "choices": ["yes", "no"],
                    "multi_choice": False,
                }
            )

            assert result is not None

    def test_all_none_inputs_raises_error(self):
        """All None inputs raises ValueError."""
        agent = DeterministicAgent()
        with pytest.raises(ValueError, match=agent.EMPTY_EVAL_RESPONSE_ERROR_MESSAGE):
            agent.evaluate(
                {
                    "inputs": [None, None],
                    "input_type": ["text", "text"],
                    "rule_prompt": "Test",
                    "choices": ["yes", "no"],
                    "multi_choice": False,
                }
            )

    def test_invalid_input_type(self):
        """Unknown input type defaults to text."""
        # Mock returns JSON string
        mock_response = '{"choices": ["yes"], "explanation": "test"}'

        with patch.object(LLM, "_get_completion_content") as mock_llm:
            mock_llm.return_value = mock_response

            result = DeterministicAgent().evaluate(
                {
                    "inputs": ["test"],
                    "input_type": ["unknown_type"],  # Invalid type
                    "rule_prompt": "Test",
                    "choices": ["yes", "no"],
                    "multi_choice": False,
                }
            )

            assert result is not None


@pytest.mark.integration
class TestMultiModalCombinations:
    """Test various multi-modal input combinations."""

    def test_text_plus_audio(self):
        """Text + Audio combination."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
        )

        payload = {
            "inputs": ["Transcribe this", TEST_AUDIO_URL],
            "input_type": ["text", "audio"],
            "rule_prompt": "Does the transcription match?",
            "choices": ["yes", "no"],
            "multi_choice": False,
        }

        content, criteria_prompt = agent._get_prompt(payload)

        # Should have both text and audio content
        content_str = str(content)
        assert "<variable_1>" in content_str
        assert "<variable_2>" in content_str

    def test_image_plus_pdf(self):
        """Image + PDF combination."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
        )

        payload = {
            "inputs": [TEST_IMAGE_URL, TEST_PDF_URL],
            "input_type": ["image", "pdf"],
            "rule_prompt": "Compare image and document",
            "choices": ["match", "no_match"],
            "multi_choice": False,
        }

        content, criteria_prompt = agent._get_prompt(payload)

        # Should process both modalities
        assert len(content) > 0

    def test_three_different_modalities(self):
        """Text + Image + Audio."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
        )

        payload = {
            "inputs": ["Question", TEST_IMAGE_URL, TEST_AUDIO_URL],
            "input_type": ["text", "image", "audio"],
            "rule_prompt": "Analyze all inputs",
            "choices": ["yes", "no"],
            "multi_choice": False,
        }

        content, criteria_prompt = agent._get_prompt(payload)

        content_str = str(content)
        assert "<variable_1>" in content_str
        assert "<variable_2>" in content_str
        assert "<variable_3>" in content_str


@pytest.mark.unit
class TestFewShotsHandling:
    """Test few-shot examples handling."""

    def test_fewshots_empty_list(self):
        """Empty fewshots list handled gracefully."""
        agent = DeterministicAgent(fewshots=[])
        assert agent.fewshots == []

    def test_fewshots_formatting(self):
        """Few-shots correctly formatted in prompt."""
        # Fewshots must follow the content schema: {"type": "text", "text": "..."}
        fewshots = [
            {"type": "text", "text": "Example 1: yes"},
            {"type": "text", "text": "Example 2: no"},
        ]

        agent = DeterministicAgent(fewshots=fewshots)

        # Mock returns JSON string (as real LLM would)
        mock_response = '{"choices": ["yes"], "explanation": "test"}'

        with patch.object(LLM, "_get_completion_content") as mock_llm:
            mock_llm.return_value = mock_response

            result = agent.evaluate(
                {
                    "inputs": ["test"],
                    "input_type": ["text"],
                    "rule_prompt": "Test",
                    "choices": ["yes", "no"],
                    "multi_choice": False,
                }
            )

            # Verify prompt was called and result parsed
            assert mock_llm.called
            assert result["choices"] == ["yes"]


# =============================================================================
# Model Connectivity Tests (Dynamic & Parametrized)
# =============================================================================


def get_all_model_configs():
    """Helper to get all ModelConfig instances from ModelConfigs class for parametrization."""
    configs = []
    for attr_name in dir(ModelConfigs):
        attr_value = getattr(ModelConfigs, attr_name)
        if isinstance(attr_value, ModelConfig):
            # Skip PROTECT_FLASH as it takes too long
            if attr_name == "PROTECT_FLASH":
                continue
            configs.append((attr_name, attr_value.model_name))
    return configs


@pytest.mark.live_llm
@pytest.mark.slow
class TestModelConnectivity:
    """
    Tests connectivity to ALL models defined in ModelConfigs.
    Uses parametrization so each model failure is reported individually.
    """

    @pytest.mark.parametrize("config_name, model_name", get_all_model_configs())
    def test_model_reachable(self, config_name: str, model_name: str):
        """Test that a specific model is reachable and returns a valid response."""
        print(f"\nTesting {config_name} -> {model_name}...")

        try:
            agent = DeterministicAgent(model_name=model_name)
            result = agent.evaluate(
                {
                    "inputs": ["Hello"],
                    "input_type": ["text"],
                    "rule_prompt": "Is this a greeting?",
                    "choices": ["yes", "no"],
                    "multi_choice": False,
                }
            )

            assert result is not None, f"No response from {model_name}"
            assert "choices" in result, f"No choices in response from {model_name}"
            assert len(result["choices"]) > 0, f"Empty choices from {model_name}"

            print(f"✅ {config_name} ({model_name}): {result['choices']}")

        except Exception as e:
            pytest.fail(f"Model {config_name} ({model_name}) unreachable: {str(e)}")


if __name__ == "__main__":
    # Run all tests
    pytest.main([__file__, "-v", "--tb=short"])


# =============================================================================
# Production Bug Tests - MP3 Audio URL Misclassification
# =============================================================================

# =============================================================================
# Multi-Image Input Tests
# =============================================================================


@pytest.mark.unit
class TestMultiImageInputs:
    """
    Test multi-image input handling for PR #537.
    Tests JSON array strings, lists, and _get_prompt with input_type=['image']/['images'].
    """

    def test_detect_input_type_list_of_images(self):
        """List of image URLs should be detected as 'images' type."""
        from agentic_eval.core.utils.functions import detect_input_type

        image_urls = [
            "https://example.com/image1.jpg",
            "https://example.com/image2.png",
        ]

        # Mock the HTTP requests since we're testing detection logic
        with patch("agentic_eval.core.utils.functions.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.content = b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
            mock_response.headers = {"Content-Type": "image/png"}
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_get.return_value = mock_response

            result = detect_input_type(image_urls)

        assert (
            result.get("type") == "images"
        ), f"List of images should return 'images', got {result}"

    def test_detect_input_type_json_array_string(self):
        """JSON array string of image URLs should be detected as 'images' type."""
        from agentic_eval.core.utils.functions import detect_input_type

        json_array = (
            '["https://example.com/image1.jpg", "https://example.com/image2.png"]'
        )

        # Mock the HTTP requests
        with patch("agentic_eval.core.utils.functions.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.content = b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
            mock_response.headers = {"Content-Type": "image/png"}
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_get.return_value = mock_response

            result = detect_input_type(json_array)

        assert (
            result.get("type") == "images"
        ), f"JSON array of images should return 'images', got {result}"

    def test_detect_input_type_empty_list(self):
        """Empty list should return 'text' type."""
        from agentic_eval.core.utils.functions import detect_input_type

        result = detect_input_type([])
        assert result.get("type") == "text"

    def test_detect_input_type_single_item_list(self):
        """Single-item image list should return 'images' type."""
        from agentic_eval.core.utils.functions import detect_input_type

        with patch("agentic_eval.core.utils.functions.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.content = b"\x89PNG\r\n\x1a\n"
            mock_response.headers = {"Content-Type": "image/png"}
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_get.return_value = mock_response

            result = detect_input_type(["https://example.com/image.jpg"])

        assert result.get("type") == "images"

    def test_handle_image_input_with_list(self):
        """_build_content_block should process list of images."""
        agent = DeterministicAgent()

        image_urls = [TEST_IMAGE_URL, TEST_IMAGE_URL]

        content = agent._build_content_block(image_urls, "image", 0)

        # Should have variable tags wrapping multiple image blocks
        assert content[0]["text"] == "<variable_1>"
        assert content[-1]["text"] == "</variable_1>"
        # Each image is wrapped in <image_n> tags inside the variable block.
        assert len(content) == 8
        assert [block["text"] for block in content if block["type"] == "text"] == [
            "<variable_1>",
            "<image_1>",
            "</image_1>",
            "<image_2>",
            "</image_2>",
            "</variable_1>",
        ]

    def test_handle_image_input_with_json_array_string(self):
        """_build_content_block should parse JSON array string of images."""
        agent = DeterministicAgent()

        # JSON array string format
        json_array = f'["{TEST_IMAGE_URL}", "{TEST_IMAGE_URL}"]'

        content = agent._build_content_block(json_array, "image", 0)

        # Should parse JSON and process both images
        assert content[0]["text"] == "<variable_1>"
        assert content[-1]["text"] == "</variable_1>"
        assert len(content) == 8  # variable tags + two tagged image blocks

    def test_get_prompt_with_image_type_for_list(self):
        """_get_prompt should handle input_type=['image'] with list input value."""
        agent = DeterministicAgent()

        payload = {
            "inputs": [
                [TEST_IMAGE_URL, TEST_IMAGE_URL]
            ],  # List of images as single input
            "input_type": ["image"],
            "rule_prompt": "Analyze these images",
        }

        content, rule_prompt = agent._get_prompt(payload)

        # Should process all images within variable_1 tags
        content_str = str(content)
        assert "<variable_1>" in content_str
        assert "</variable_1>" in content_str

    def test_get_prompt_with_images_type(self):
        """_get_prompt should handle input_type=['images'] explicitly."""
        agent = DeterministicAgent()

        payload = {
            "inputs": [[TEST_IMAGE_URL, TEST_IMAGE_URL]],
            "input_type": ["images"],  # Explicit 'images' type
            "rule_prompt": "Compare these images",
        }

        content, rule_prompt = agent._get_prompt(payload)

        content_str = str(content)
        assert "<variable_1>" in content_str
        assert "</variable_1>" in content_str

    def test_get_prompt_auto_detects_image_list(self):
        """_get_prompt should auto-detect list of images when input_type not provided."""
        agent = DeterministicAgent()

        # Mock detect_input_type to return 'images' for our list
        with patch(
            "ee.agenthub.deterministic_agent.deterministic_agent.detect_input_type"
        ) as mock_detect:
            mock_detect.return_value = {0: "images"}

            payload = {
                "inputs": [[TEST_IMAGE_URL, TEST_IMAGE_URL]],
                "input_type": [],  # Empty - should auto-detect
                "rule_prompt": "Analyze",
            }

            content, rule_prompt = agent._get_prompt(payload)

            # Should have called detect_input_type
            mock_detect.assert_called_once()


@pytest.mark.live_llm
@pytest.mark.slow
class TestMultiImageLLMIntegration:
    """Integration tests for multi-image inputs with actual LLM calls."""

    def test_multi_image_evaluation_with_json_array(self):
        """End-to-end test: evaluate multiple images passed as JSON array string."""
        agent = DeterministicAgent(model_name="vertex_ai/gemini-2.5-pro")

        # JSON array string format (common when importing from CSV)
        json_images = f'["{TEST_IMAGE_URL}", "{TEST_IMAGE_URL}"]'

        result = agent.evaluate(
            {
                "inputs": [json_images],
                "input_type": ["images"],
                "rule_prompt": "Are these two images identical or different?",
                "choices": ["identical", "different"],
                "multi_choice": False,
            }
        )

        assert result is not None
        assert "choices" in result
        assert len(result["choices"]) > 0
        assert result["choices"][0] in ["identical", "different"]
        assert "explanation" in result

    def test_multi_image_evaluation_with_list(self):
        """End-to-end test: evaluate multiple images passed as Python list."""
        agent = DeterministicAgent(model_name="vertex_ai/gemini-2.5-pro")

        result = agent.evaluate(
            {
                "inputs": [[TEST_IMAGE_URL, TEST_IMAGE_URL]],
                "input_type": ["images"],
                "rule_prompt": "Do these images show the same content?",
                "choices": ["yes", "no"],
                "multi_choice": False,
            }
        )

        assert result is not None
        assert "choices" in result
        assert len(result["choices"]) > 0
        assert result["choices"][0] in ["yes", "no"]


@pytest.mark.unit
class TestProductionAudioURLBug:
    """
    Test for production MP3 URL bug where audio files were being sent to OpenAI as images.
    Tests the specific URL that failed in production.
    """

    PRODUCTION_AUDIO_URL = "https://fi-content.s3.ap-south-1.amazonaws.com/call-recordings/019bdb58-2210-7116-af45-aff84f8e7616/f7e3cea9-9884-4753-9dbf-fa0fde1d81ce.mp3"
    # PRODUCTION_AUDIO_URL = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/dcc00d97-fde8-4721-bd3b-de75e2898f63/2ec84fac-886d-4fd8-be4f-e061ab070104"

    def test_audio_url_detected_correctly(self):
        """Production MP3 URL should be detected as audio type."""
        from agentic_eval.core.utils.functions import detect_input_type

        result = detect_input_type(self.PRODUCTION_AUDIO_URL)
        detected_type = (
            result if isinstance(result, str) else result.get("type", "unknown")
        )

        assert (
            detected_type == "audio"
        ), f"MP3 URL misclassified as {detected_type}, should be 'audio'"

    def test_audio_block_uses_audio_tags(self):
        """Audio URLs should be wrapped as audio content, not plain image tags."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
        )

        content = agent._build_content_block(self.PRODUCTION_AUDIO_URL, "audio", 0)

        assert content[1]["text"] == "<audio>"
        assert content[2]["type"] == "image_url"
        assert content[2]["image_url"]["url"] == self.PRODUCTION_AUDIO_URL
        assert content[3]["text"] == "</audio>"

    def test_vertex_to_openai_fallback_conversion(self):
        """
        Test that Vertex AI audio format converts correctly to OpenAI format for fallback.

        This tests the critical conversion in handle_vertex_ai_fallback():
        - Vertex format: {"type": "image_url", "image_url": {"url": "data:audio/mp3;base64,..."}}
        - OpenAI format: {"type": "input_audio", "input_audio": {"data": "...", "format": "mp3"}}
        """
        from agentic_eval.core.utils.llm_payloads import build_audio_content

        # Simulate Vertex AI audio payload
        vertex_payload = build_audio_content(
            provider="vertex_ai",
            audio_input=self.PRODUCTION_AUDIO_URL,
            audio_format="mp3",
        )

        # Vertex uses image_url type with data:audio URI
        assert vertex_payload["type"] == "image_url"
        assert "data:audio/" in vertex_payload["image_url"]["url"]

        # Test fallback conversion
        llm = LLM(model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe this"},
                    vertex_payload,
                ],
            }
        ]

        # Convert for OpenAI fallback
        converted = llm.handle_vertex_ai_fallback(messages)

        # Should convert image_url with audio to input_audio
        audio_content = None
        for content_item in converted[0]["content"]:
            if content_item.get("type") == "input_audio":
                audio_content = content_item
                break

        assert audio_content is not None, "Audio not converted to input_audio format"
        assert "input_audio" in audio_content
        assert "data" in audio_content["input_audio"]
        assert "format" in audio_content["input_audio"]

    @pytest.mark.live_llm
    @pytest.mark.slow
    def test_production_audio_url_end_to_end(self):
        """Integration test with production MP3 URL - tests full evaluation flow including auto-detection."""
        agent = DeterministicAgent(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
        )

        # Exact production scenario - NO input_type to test auto-detection
        result = agent.evaluate(
            {
                "inputs": [self.PRODUCTION_AUDIO_URL],
                "input_type": ["audio"],
                "rule_prompt": "Assess the overall satisfaction expressed by the customer. Assign a CSAT score from 1 to 10.",
                "choices": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
                "multi_choice": False,
            }
        )

        assert result is not None
        assert "choices" in result
        assert len(result["choices"]) > 0
        assert result["choices"][0] in [
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
        ]


# =============================================================================
# Param Modality Validation Tests
# =============================================================================


@pytest.mark.unit
class TestParamModalityValidation:
    """Test param_modalities validation in DeterministicEvaluator."""

    @staticmethod
    def _make_evaluator(**kwargs):
        return DeterministicEvaluator(
            rule_prompt="Test prompt",
            choices=["yes", "no"],
            multi_choice=False,
            **kwargs,
        )

    def test_validation_passes_with_correct_modalities(self):
        evaluator = self._make_evaluator(
            input=["text input", "audio input"],
            input_type=["text", "audio"],
        )

        result = evaluator._validate_param_modalities(
            required_keys=["system_prompt", "conversation"],
            param_modalities={
                "system_prompt": ["TEXT", "JSON", "LIST", "NUMBER"],
                "conversation": ["TEXT", "AUDIO", "JSON", "LIST", "NUMBER"],
            },
        )

        assert result is None

    def test_validation_fails_with_incorrect_modality(self):
        evaluator = self._make_evaluator(
            input=["text input", "image input"],
            input_type=["text", "image"],
        )

        with pytest.raises(ValueError) as exc_info:
            evaluator._validate_param_modalities(
                required_keys=["system_prompt", "conversation"],
                param_modalities={
                    "system_prompt": ["TEXT"],
                    "conversation": ["TEXT", "AUDIO"],
                },
            )

    def test_validation_skipped_when_no_param_modalities(self):
        evaluator = self._make_evaluator(
            input=["text input"],
            input_type=["text"],
        )

        result = evaluator._validate_param_modalities(
            required_keys=["input"],
            param_modalities={},
        )

        assert result is None

    def test_validation_case_insensitive(self):
        evaluator = self._make_evaluator(
            input=["text input"],
            input_type=["text"],
        )

        result = evaluator._validate_param_modalities(
            required_keys=["input"],
            param_modalities={
                "input": ["TEXT"],
            },
        )

        assert result is None

    def test_validation_with_json_type(self):
        evaluator = self._make_evaluator(
            input=['{"key": "value"}'],
            input_type=["json"],
        )

        result = evaluator._validate_param_modalities(
            required_keys=["input"],
            param_modalities={
                "input": ["TEXT", "JSON", "LIST", "NUMBER"],
            },
        )

        assert result is None

    def test_validation_multiple_params(self):
        evaluator = self._make_evaluator(
            input=["text prompt", "text conversation", "pdf context"],
            input_type=["text", "text", "pdf"],
        )

        result = evaluator._validate_param_modalities(
            required_keys=["system_prompt", "conversation", "context"],
            param_modalities={
                "system_prompt": ["TEXT"],
                "conversation": ["TEXT", "AUDIO"],
                "context": ["TEXT", "PDF"],
            },
        )

        assert result is None

    def test_validation_uses_required_keys_order(self):
        evaluator = self._make_evaluator(
            input=["context value", "output value"],
            input_type=["pdf", "text"],
        )

        with pytest.raises(ValueError) as exc_info:
            evaluator._validate_param_modalities(
                required_keys=["context", "output"],
                param_modalities={
                    "context": ["TEXT"],
                    "output": ["TEXT"],
                },
            )

        error_message = str(exc_info.value)
        assert "context" in error_message
        assert "Pdf" in error_message

    def test_validation_ignores_missing_param_in_modalities(self):
        evaluator = self._make_evaluator(
            input=["text input"],
            input_type=["text"],
        )

        result = evaluator._validate_param_modalities(
            required_keys=["input"],
            param_modalities={"other": ["TEXT"]},
        )

        assert result is None

    def test_validation_skips_when_required_keys_empty(self):
        evaluator = self._make_evaluator(
            input=["text input"],
            input_type=["text"],
        )

        result = evaluator._validate_param_modalities(
            required_keys=[],
            param_modalities={"input": ["TEXT"]},
        )

        assert result is None

    def test_validation_skips_when_index_out_of_range(self):
        evaluator = self._make_evaluator(
            input=["text input"],
            input_type=["text"],
        )

        result = evaluator._validate_param_modalities(
            required_keys=["input", "context"],
            param_modalities={
                "input": ["TEXT"],
                "context": ["TEXT", "PDF"],
            },
        )

        assert result is None

    def test_evaluate_raises_before_agent_when_modalities_invalid(self):
        evaluator = self._make_evaluator(
            input=["text input", "image input"],
            input_type=["text", "image"],
        )

        with pytest.raises(ValueError, match="Input type mismatch"):
            evaluator._evaluate(
                required_keys=["system_prompt", "conversation"],
                system_prompt="text input",
                conversation="image input",
                param_modalities={
                    "system_prompt": ["TEXT"],
                    "conversation": ["TEXT", "AUDIO"],
                },
                eval_name="test_eval",
            )


# =============================================================================
# Integration Tests - Param Modality Validation (End-to-End)
# =============================================================================


@pytest.mark.integration
class TestParamModalityValidationE2E:
    """End-to-end tests that exercise eval config wiring + validation."""

    def test_invalid_modality_rejected_before_agent(self):
        eval_config = _get_eval_config("completeness")
        param_modalities = eval_config["config"]["param_modalities"]

        evaluator = DeterministicEvaluator(
            model=ModelConfigs.TURING_LARGE.model_name,
            rule_prompt="Check completeness",
            choices=["yes", "no"],
            multi_choice=False,
            input=["text input", TEST_IMAGE_URL],
            input_type=["text", "image"],
        )

        with pytest.raises(ValueError, match="Input type mismatch"):
            evaluator._evaluate(
                required_keys=["input", "output"],
                input="text input",
                output=TEST_IMAGE_URL,
                param_modalities=param_modalities,
                eval_name="completeness",
            )

    @pytest.mark.live_llm
    @pytest.mark.slow
    def test_valid_modalities_text_audio(self):
        eval_config = _get_eval_config("completeness")
        param_modalities = eval_config["config"]["param_modalities"]

        evaluator = DeterministicEvaluator(
            model=ModelConfigs.TURING_LARGE.model_name,
            rule_prompt="Check completeness",
            choices=["yes", "no"],
            multi_choice=False,
            input=["text input", TEST_AUDIO_URL],
            input_type=["text", "audio"],
        )

        result = evaluator._evaluate(
            required_keys=["input", "output"],
            input="text input",
            output=TEST_AUDIO_URL,
            param_modalities=param_modalities,
            eval_name="completeness",
        )

        assert result is not None

    @pytest.mark.live_llm
    @pytest.mark.slow
    def test_valid_modalities_text_pdf(self):
        eval_config = _get_eval_config("chunk_attribution")
        param_modalities = eval_config["config"]["param_modalities"]

        evaluator = DeterministicEvaluator(
            model=ModelConfigs.TURING_LARGE.model_name,
            rule_prompt="Check chunk attribution",
            choices=["Passed", "Failed"],
            multi_choice=False,
            input=[TEST_PDF_URL, "text output"],
            input_type=["pdf", "text"],
        )

        result = evaluator._evaluate(
            required_keys=["context", "output"],
            context=TEST_PDF_URL,
            output="text output",
            param_modalities=param_modalities,
            eval_name="chunk_attribution",
        )

        assert result is not None
