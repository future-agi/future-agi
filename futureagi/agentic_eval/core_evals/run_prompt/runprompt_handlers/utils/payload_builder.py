"""
Clean payload construction for LiteLLM API calls.

This module centralizes all payload building logic, eliminating scattered
parameter handling and hardcoded model lists.

NOTE: LiteLLM with drop_params=True handles most model-specific parameter
adjustments automatically:
- max_tokens -> max_completion_tokens for o1/o3/gpt-5 reasoning models
- Temperature dropping for models that only support temperature=1
- Dropping unsupported params like frequency_penalty, presence_penalty

This module focuses on:
- Building the initial payload structure
- Parameter validation (via ParametersValidator)
- Response format handling (json_schema, json_object)
- Provider-specific API key configuration
"""

import copy
import json
from typing import Any, Dict, Optional

import structlog

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.parameter_validator import (
    ParametersValidator,
)
from model_hub.utils.azure_endpoints import normalize_azure_custom_model_config

logger = structlog.get_logger(__name__)


class PayloadBuilder:
    """
    Builder for LiteLLM API payloads.

    Handles:
    - Parameter normalization
    - Provider-specific API key configuration
    - Response format handling (json_schema, json_object, text)

    NOTE: Model-specific parameter adjustments (o1/o3/gpt-5, Claude 4+) are
    handled by LiteLLM automatically when drop_params=True is set.
    """

    @staticmethod
    def build_llm_payload(
        context: ModelHandlerContext,
        provider: str,
        api_key: Any,
    ) -> Dict[str, Any]:
        """
        Build payload for litellm.completion() call.

        Args:
            context: Handler context with model and parameters
            provider: Provider name (openai, anthropic, etc.)
            api_key: API key (string or dict)

        Returns:
            Clean payload dict ready for litellm.completion()

        Note:
            - Validates parameters against model limits before building payload
            - Requires litellm.drop_params=True to be set before calling
              litellm.completion() for proper handling of model-specific
              parameter restrictions
        """
        # Validate parameters against model limits
        validator = ParametersValidator(model=context.model)
        validation_results = validator.validate_all(
            max_tokens=context.max_tokens,
            temperature=context.temperature,
            top_p=context.top_p,
            frequency_penalty=context.frequency_penalty,
            presence_penalty=context.presence_penalty,
            # Only use default if max_tokens is explicitly set to a value
            # If None, let API use its own defaults
            default_max_tokens=None if context.max_tokens is None else 4096,
        )

        # Build response_format
        response_format = PayloadBuilder._build_response_format(context)

        # Get messages (copy to avoid mutation)
        messages = list(context.messages)

        # Ensure JSON instruction is present when using json_object format
        if response_format and response_format.get("type") == "json_object":
            messages = PayloadBuilder._ensure_json_instruction(messages)

        # Build base payload with validated parameters
        # LiteLLM will handle dropping unsupported params when drop_params=True
        # None values are stripped below so litellm uses its own provider defaults
        payload = {
            "messages": messages,
            "model": context.model,
            "temperature": float(context.temperature)
            if context.temperature is not None
            else None,
            "frequency_penalty": float(context.frequency_penalty)
            if context.frequency_penalty is not None
            else None,
            "presence_penalty": float(context.presence_penalty)
            if context.presence_penalty is not None
            else None,
            "max_tokens": int(context.max_tokens)
            if context.max_tokens is not None
            else None,
            "top_p": float(context.top_p) if context.top_p is not None else None,
            "response_format": response_format,
            "tools": context.tools or [],
            "tool_choice": context.tool_choice if context.tools else None,
        }

        # Remove None/empty values to keep payload clean
        payload = {k: v for k, v in payload.items() if v not in [None, [], {}, ""]}

        # Add API key configuration (provider-specific)
        PayloadBuilder._add_api_key(payload, provider, api_key)

        # Add reasoning parameters if present
        if context.reasoning_effort:
            payload["reasoning_effort"] = context.reasoning_effort

        if context.thinking_budget:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": int(context.thinking_budget),
            }

        return payload

    @staticmethod
    def _build_response_format(
        context: ModelHandlerContext,
    ) -> Optional[Dict[str, Any]]:
        """
        Build response_format from context.

        Valid LiteLLM response_format values (per official docs):
        - None: No specific format
        - {"type": "json_object"}: Guarantees valid JSON output
        - {"type": "json_schema", "json_schema": {...}, "strict": true}: Structured outputs
        - {"type": "text"}: Plain text (OpenAI API standard)

        DEPRECATED (backward compatibility only):
        - String values like "json", "text" are NOT part of LiteLLM spec
        - These are converted to dict format with deprecation warnings
        - Will be removed in future version

        Args:
            context: Handler context

        Returns:
            response_format dict or None (following supported response_format conventions)
        """
        if not context.response_format:
            return None

        # Handle json_schema format (structured outputs)
        # Format: {"schema": {...}, "name": "..."}
        if isinstance(context.response_format, dict) and context.response_format.get(
            "schema"
        ):
            # Deep copy to avoid mutating the original context
            schema = copy.deepcopy(context.response_format.get("schema", {}))

            # Convert additional_properties to additionalProperties (snake_case to camelCase)
            if schema and "additional_properties" in schema:
                schema["additionalProperties"] = schema.pop("additional_properties")

            json_schema_obj = {
                "name": context.response_format.get("name"),
                "strict": True,
                "schema": schema,  # Use the deep copied schema
            }

            return {
                "type": "json_schema",
                "json_schema": json_schema_obj,
            }

        # Handle dict with explicit type (LiteLLM standard format)
        # Format: {"type": "text"} or {"type": "json_object"}
        if (
            isinstance(context.response_format, dict)
            and "type" in context.response_format
        ):
            format_type = context.response_format["type"]

            # Validate type is one of the allowed values
            if format_type in ("text", "json_object", "json_schema"):
                return {"type": format_type}
            else:
                # Unknown type - log error and raise exception
                logger.error(
                    "invalid_response_format_type",
                    response_format=context.response_format,
                    format_type=format_type,
                    valid_types=["text", "json_object", "json_schema"],
                    message="Invalid response_format type. Must be 'text', 'json_object', or 'json_schema'",
                )
                raise ValueError(
                    f"Invalid response_format type '{format_type}'. "
                    f"Must be one of: 'text', 'json_object', 'json_schema'"
                )

        # DEPRECATED: Handle string values (backward compatibility only)
        # These are NOT part of LiteLLM spec and will be removed in future version
        if isinstance(context.response_format, str):
            format_lower = context.response_format.lower()

            logger.warning(
                "deprecated_response_format_string",
                response_format=context.response_format,
                message=(
                    "String response_format values are deprecated and not part of LiteLLM spec. "
                    f"Use dict format instead: {{'type': '{format_lower}'}}"
                ),
            )

            if format_lower == "text":
                return {"type": "text"}
            elif format_lower in ("json", "json_object"):
                return {"type": "json_object"}
            else:
                # Unknown string format - log error and raise exception
                logger.error(
                    "invalid_response_format_string",
                    response_format=context.response_format,
                    message="Invalid response_format string. Use {'type': 'text'} or {'type': 'json_object'}",
                )
                raise ValueError(
                    f"Invalid response_format '{context.response_format}'. "
                    f"Must be {{'type': 'text'}} or {{'type': 'json_object'}}"
                )

        # Invalid format type
        logger.error(
            "invalid_response_format",
            response_format=context.response_format,
            type=type(context.response_format).__name__,
            message="response_format must be a dict with 'type' or 'schema' key",
        )
        raise ValueError(
            f"Invalid response_format type {type(context.response_format).__name__}. "
            f"Must be a dict with 'type' or 'schema' key"
        )

    @staticmethod
    def _ensure_json_instruction(messages: list) -> list:
        """
        Ensure messages contain "json" keyword for json_object format.

        If not present, prepend a system message with JSON instruction.
        This is required by OpenAI for json_object response format.

        Args:
            messages: Messages list

        Returns:
            Messages list with JSON instruction added if needed (may be a new list)
        """

        def extract_message_text(msg):
            """Extract text content from message."""
            content = msg.get("content", "")

            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                # Handle multimodal content like [{"type": "text", "text": "..."}]
                return " ".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                )
            return ""

        # Check if "json" appears in any message
        messages_text = " ".join(extract_message_text(msg) for msg in messages).lower()

        if "json" not in messages_text:
            # Create a new list with the JSON instruction prepended (avoid mutating original)
            json_instruction = "You must respond with valid JSON."
            new_messages = [{"role": "system", "content": json_instruction}] + list(
                messages
            )
            logger.debug("Added JSON instruction to messages for json_object format")
            return new_messages

        return messages

    @staticmethod
    def _add_api_key(payload: Dict[str, Any], provider: str, api_key: Any):
        """
        Add API key configuration to payload.

        Handles different provider formats:
        - OpenAI: Direct API key or dict
        - Azure: Dict with credentials
        - Bedrock: Dict with credentials
        - Vertex AI: JSON credentials
        - Others: Direct API key or custom_llm_provider

        Args:
            payload: Payload dict (modified in place)
            provider: Provider name
            api_key: API key (string or dict)
        """
        if isinstance(api_key, dict):
            # Set custom_llm_provider for non-OpenAI providers
            provider_for_payload = provider
            normalized = None
            if provider == "azure":
                normalized = normalize_azure_custom_model_config(api_key)
                if normalized.get("azure_endpoint_type") == "foundry":
                    provider_for_payload = "azure_ai"

            if provider_for_payload != "openai":
                payload["custom_llm_provider"] = provider_for_payload

            # Handle specific providers
            if provider_for_payload in ["bedrock", "azure", "openai", "azure_ai"]:
                if provider_for_payload == "azure":
                    # Normalize and filter Azure config - only pass litellm-supported fields
                    if normalized is None:
                        normalized = normalize_azure_custom_model_config(api_key)
                    payload.update(
                        {
                            "api_base": normalized["api_base"],
                            "api_version": normalized["api_version"],
                            "api_key": normalized["api_key"],
                        }
                    )
                elif provider_for_payload == "azure_ai":
                    if normalized is None:
                        normalized = normalize_azure_custom_model_config(api_key)
                    payload.update(
                        {
                            "api_base": normalized["api_base"],
                            "api_key": normalized["api_key"],
                        }
                    )
                    if isinstance(payload.get("model"), str) and not payload[
                        "model"
                    ].startswith("azure_ai/"):
                        payload["model"] = f"azure_ai/{payload['model']}"
                else:
                    payload.update(api_key)
                # Add openai/ prefix for OpenAI models
                if provider_for_payload == "openai":
                    payload["model"] = "openai/" + payload["model"]

            elif provider_for_payload.startswith("vertex_ai"):
                # Vertex AI uses JSON credentials
                vertex_location = api_key.get("location") if isinstance(api_key, dict) else None
                creds = {k: v for k, v in api_key.items() if k != "location"} if isinstance(api_key, dict) else api_key
                payload["vertex_credentials"] = json.dumps(creds)
                if vertex_location:
                    payload["vertex_location"] = vertex_location

            else:
                # Generic dict API key
                payload["api_key"] = api_key
        else:
            # Simple string API key
            if provider != "openai":
                payload["custom_llm_provider"] = provider
            payload["api_key"] = api_key
