from ee.agenthub.prompt_improve_agent.prompt import (
    ANALYSIS_PROMPT,
    FOLLOWUP_PROMPT,
    IMPROVEMENT_PROMPT,
)
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.json_utils import extract_dict_from_string
from agentic_eval.core.utils.model_config import ModelConfigs
import structlog

logger = structlog.get_logger(__name__)


class PromptImprovementAgent:
    def __init__(
        self,
        model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name,
        temperature=ModelConfigs.VERTEX_GEMINI_2_5_PRO.temperature,
        max_tokens=ModelConfigs.VERTEX_GEMINI_2_5_PRO.max_tokens,
        provider=ModelConfigs.VERTEX_GEMINI_2_5_PRO.provider,
        llm=None,
    ):
        self.llm = (
            llm
            if llm
            else LLM(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
            )
        )

    def improve(self, payload):
        """Improves an existing prompt based on improvement requirements"""

        # Input validation
        original_prompt = payload.get("original_prompt", "")
        improvement_requirements = payload.get("improvement_requirements", "")

        if not original_prompt:
            raise ValueError("Original prompt is required")
        if not improvement_requirements:
            raise ValueError("Improvement requirements are required")

        # Analysis phase
        analysis_messages = [
            {
                "role": "user",
                "content": self._get_analysis_prompt(
                    original_prompt, improvement_requirements
                ),
            }
        ]

        analysis_response = self.llm._get_completion_content(messages=analysis_messages)

        # Improvement phase
        improvement_messages = [
            {
                "role": "user",
                "content": self._get_improvement_prompt(
                    original_prompt, improvement_requirements, analysis_response
                ),
            }
        ]

        improved_prompt = self.llm._get_completion_content(
            messages=improvement_messages
        )

        try:
            improved_result = extract_dict_from_string(improved_prompt)

            # Follow-up refinement phase
            followup_messages = [
                {
                    "role": "user",
                    "content": self._get_followup_prompt(
                        original_prompt,
                        improved_result["improved_prompt"],
                        improvement_requirements,
                    ),
                }
            ]

            final_prompt = self.llm._get_completion_content(messages=followup_messages)
            final_result = extract_dict_from_string(final_prompt)
            return {
                "final_prompt": final_result["final_prompt"],
                "merge_explanation": final_result["explanation"],
            }

        except Exception as e:
            logger.exception(f"Failed to extract dictionary from improved prompt: {e}")
            return {
                "improved_prompt": original_prompt,
                "explanation": "Failed to generate valid improvement. Returning original prompt.",
            }

    def _get_analysis_prompt(self, original_prompt, improvement_requirements):
        return ANALYSIS_PROMPT.format(
            original_prompt=original_prompt,
            improvement_requirements=improvement_requirements,
        )

    def _get_improvement_prompt(
        self, original_prompt, improvement_requirements, analysis
    ):
        return IMPROVEMENT_PROMPT.format(
            original_prompt=original_prompt,
            improvement_requirements=improvement_requirements,
            analysis=analysis,
        )

    def _get_followup_prompt(
        self, original_prompt, improved_prompt, improvement_requirements
    ):
        return FOLLOWUP_PROMPT.format(
            original_prompt=original_prompt,
            improved_prompt=improved_prompt,
            improvement_requirements=improvement_requirements,
        )
