"""
PromptBasedAgentAdapter - Mimics user-defined agents using prompts and LiteLLM.

This adapter allows prompts from the Prompt Workbench to be used as the "agent"
in chat simulations, replacing the SDK-based agent definition approach.
"""

import re
import time
from typing import Any
from uuid import UUID

import structlog
from retrying import retry

from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from model_hub.models.run_prompt import PromptVersion

logger = structlog.get_logger(__name__)


class PromptBasedAgentAdapter:
    """
    Adapter that uses a PromptVersion to respond to simulation messages.

    This mimics the behavior of an SDK-based agent but uses the prompt template's
    configuration (messages, model, temperature, etc.) to generate responses.
    """

    def __init__(
        self,
        prompt_version: PromptVersion,
        organization_id: UUID,
        workspace_id: UUID | None = None,
        variable_values: dict[str, Any] | None = None,
    ):
        """
        Initialize the adapter with a prompt version.

        Args:
            prompt_version: The PromptVersion to use for generating responses
            organization_id: The organization ID for API key lookup
            workspace_id: Optional workspace ID for API key lookup
            variable_values: Optional dict of variable values to inject into the prompt
        """
        self.prompt_version = prompt_version
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.variable_values = variable_values or {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from the prompt version snapshot."""
        config_snapshot = self.prompt_version.prompt_config_snapshot

        if not config_snapshot:
            raise ValueError(
                f"PromptVersion {self.prompt_version.id} has no prompt_config_snapshot"
            )

        # Handle both list and single config formats
        if isinstance(config_snapshot, list) and len(config_snapshot) > 0:
            config = config_snapshot[0]
        else:
            config = config_snapshot

        if not isinstance(config, dict):
            raise ValueError(
                f"PromptVersion {self.prompt_version.id} has invalid prompt_config_snapshot format"
            )

        # Extract configuration parameters first (model lives here)
        model_config = config.get("configuration", {})

        # Extract model from configuration (not top level)
        self.model = model_config.get("model")
        if not self.model:
            raise ValueError(
                f"PromptVersion {self.prompt_version.id} has no model specified in configuration"
            )
        self.base_messages = config.get("messages", [])

        if not self.base_messages:
            raise ValueError(
                f"PromptVersion {self.prompt_version.id} has no messages in prompt_config_snapshot"
            )
        self.temperature = model_config.get("temperature", 0.7)
        self.max_tokens = model_config.get("max_tokens", 1000)
        self.top_p = model_config.get("top_p", 1.0)
        self.frequency_penalty = model_config.get("frequency_penalty", 0.0)
        self.presence_penalty = model_config.get("presence_penalty", 0.0)

        # Tool configuration (if any)
        self.tools = model_config.get("tools", [])
        self.tool_choice = model_config.get("tool_choice")

    def _inject_variables(self, content):
        """
        Inject variable values into the content using {{variable_name}} syntax.

        Args:
            content: The content containing variable placeholders (can be string, list, or dict)

        Returns:
            Content with variables replaced with their values (preserves original type)
        """
        # Handle None
        if content is None:
            return ""

        # Handle list content (e.g., multimodal content parts)
        if isinstance(content, list):
            # Process each item in the list, preserving the list structure
            processed_items = []
            for item in content:
                processed_items.append(self._inject_variables(item))
            return processed_items

        # Handle dict content (e.g., {"type": "text", "text": "..."} or {"type": "image_url", ...})
        if isinstance(content, dict):
            processed_dict = {}
            for key, value in content.items():
                if key in ("text", "content") and isinstance(value, str):
                    # Inject variables into text fields
                    processed_dict[key] = self._inject_variables_in_string(value)
                elif isinstance(value, (list, dict)):
                    # Recursively process nested structures
                    processed_dict[key] = self._inject_variables(value)
                else:
                    processed_dict[key] = value
            return processed_dict

        # Handle string content
        if isinstance(content, str):
            return self._inject_variables_in_string(content)

        # Handle other types by converting to string
        return str(content)

    def _inject_variables_in_string(self, text: str) -> str:
        """
        Inject variable values into a string using {{variable_name}} syntax.

        Args:
            text: The string containing variable placeholders

        Returns:
            String with variables replaced with their values
        """
        if not isinstance(text, str):
            text = str(text) if text is not None else ""

        if not self.variable_values:
            return text

        result = text
        for var_name, var_value in self.variable_values.items():
            # Handle {{variable_name}} syntax
            pattern = r"\{\{\s*" + re.escape(var_name) + r"\s*\}\}"
            # Ensure var_value is a string
            str_value = str(var_value) if not isinstance(var_value, str) else var_value
            result = re.sub(pattern, str_value, result)
        return result

    def _prepare_messages(
        self, conversation_history: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """
        Prepare the full message list for the LLM call.

        Combines:
        1. System message from prompt template (with variables injected)
        2. Conversation history from simulation

        Args:
            conversation_history: List of messages from the simulation

        Returns:
            Full message list for LLM call
        """
        messages = []

        # Add base messages from prompt template (system prompt, etc.)
        for msg in self.base_messages:
            content = msg.get("content", "")
            # Inject variables into the content
            processed_content = self._inject_variables(content)
            messages.append(
                {
                    "role": msg.get("role", "system"),
                    "content": processed_content,
                }
            )

        # Add conversation history
        for msg in conversation_history:
            messages.append(
                {
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                }
            )

        return messages

    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def generate_response(
        self,
        conversation_history: list[dict[str, str]],
        additional_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a response using the prompt template and RunPrompt wrapper.

        Args:
            conversation_history: List of messages from the simulation
            additional_context: Optional additional context for the response

        Returns:
            Dict containing the response content and metadata
        """
        try:
            messages = self._prepare_messages(conversation_history)

            logger.info(
                "prompt_based_agent_generating_response",
                model=self.model,
                message_count=len(messages),
                prompt_version_id=str(self.prompt_version.id),
            )

            run_prompt = RunPrompt(
                model=self.model,
                messages=messages,
                organization_id=str(self.organization_id),
                output_format=None,
                temperature=self.temperature,
                frequency_penalty=self.frequency_penalty,
                presence_penalty=self.presence_penalty,
                max_tokens=self.max_tokens,
                top_p=self.top_p,
                response_format=None,
                tool_choice=self.tool_choice,
                tools=self.tools,
                workspace_id=str(self.workspace_id) if self.workspace_id else None,
            )

            start_time = time.perf_counter()
            response_text, value_info = run_prompt.litellm_response()
            latency_ms = int((time.perf_counter() - start_time) * 1000)

            content = response_text or ""
            usage = value_info.get("metadata", {}).get("usage", {})

            result = {
                "content": content,
                "role": "assistant",
                "finish_reason": "stop",
                "model": value_info.get("model", self.model),
                "latency_ms": latency_ms,
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }

            logger.info(
                "prompt_based_agent_response_generated",
                prompt_version_id=str(self.prompt_version.id),
                tokens_used=result["usage"]["total_tokens"],
            )

            return result

        except Exception as e:
            logger.exception(
                "prompt_based_agent_error",
                prompt_version_id=str(self.prompt_version.id),
                error=str(e),
            )
            raise

    def _content_to_string(self, content) -> str:
        """
        Convert content (which may be a string, list, or dict) to a plain string.

        Args:
            content: The content to convert

        Returns:
            String representation of the content
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Extract text from list of content parts
            text_parts = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict):
                    # Extract text from content part dict
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif "text" in item:
                        text_parts.append(item.get("text", ""))
                    elif "content" in item:
                        text_parts.append(str(item.get("content", "")))
            return "\n".join(text_parts)
        if isinstance(content, dict):
            # Extract text from dict
            if content.get("type") == "text":
                return content.get("text", "")
            if "text" in content:
                return content.get("text", "")
            if "content" in content:
                return str(content.get("content", ""))
            return str(content)
        return str(content)

    def get_system_prompt(self) -> str:
        """
        Get the system prompt from the prompt template.

        Returns:
            The system prompt with variables injected
        """
        for msg in self.base_messages:
            if msg.get("role") == "system":
                processed = self._inject_variables(msg.get("content", ""))
                return self._content_to_string(processed)
        return ""

    def get_initial_message(self) -> str | None:
        """
        Get the initial assistant message from the prompt template if present.

        Returns:
            The initial assistant message or None
        """
        for msg in self.base_messages:
            if msg.get("role") == "assistant":
                processed = self._inject_variables(msg.get("content", ""))
                return self._content_to_string(processed)
        return None


def create_adapter_from_run_test(
    run_test,
    organization_id: UUID,
    workspace_id: UUID | None = None,
    variable_values: dict[str, Any] | None = None,
) -> PromptBasedAgentAdapter:
    """
    Factory function to create a PromptBasedAgentAdapter from a RunTest.

    Args:
        run_test: The RunTest instance (must have source_type='prompt')
        organization_id: The organization ID for API key lookup
        workspace_id: Optional workspace ID for API key lookup
        variable_values: Optional variable values to inject

    Returns:
        PromptBasedAgentAdapter instance

    Raises:
        ValueError: If run_test is not prompt-based or missing required fields
    """
    if run_test.source_type != "prompt":
        raise ValueError(
            f"RunTest {run_test.id} is not a prompt-based test (source_type={run_test.source_type})"
        )

    if not run_test.prompt_version:
        raise ValueError(f"RunTest {run_test.id} has no prompt_version set")

    return PromptBasedAgentAdapter(
        prompt_version=run_test.prompt_version,
        organization_id=organization_id,
        workspace_id=workspace_id,
        variable_values=variable_values,
    )


def create_adapter_from_scenario(
    scenario,
    organization_id: UUID,
    workspace_id: UUID | None = None,
    variable_values: dict[str, Any] | None = None,
) -> PromptBasedAgentAdapter:
    """
    Factory function to create a PromptBasedAgentAdapter from a Scenario.

    Args:
        scenario: The Scenarios instance (must have source_type='prompt')
        organization_id: The organization ID for API key lookup
        workspace_id: Optional workspace ID for API key lookup
        variable_values: Optional variable values to inject

    Returns:
        PromptBasedAgentAdapter instance

    Raises:
        ValueError: If scenario is not prompt-based or missing required fields
    """
    if scenario.source_type != "prompt":
        raise ValueError(
            f"Scenario {scenario.id} is not a prompt-based scenario (source_type={scenario.source_type})"
        )

    if not scenario.prompt_version:
        raise ValueError(f"Scenario {scenario.id} has no prompt_version set")

    return PromptBasedAgentAdapter(
        prompt_version=scenario.prompt_version,
        organization_id=organization_id,
        workspace_id=workspace_id,
        variable_values=variable_values,
    )
