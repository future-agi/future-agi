"""
Custom Model Handler - Handles custom AI models registered in the database.

This handler manages models that are registered in the CustomAIModel table,
which have custom API endpoints and pricing configurations.

Features:
- Custom endpoint URLs
- Custom authentication (API key, bearer token)
- Custom token cost calculations
- Non-streaming execution (custom models don't support streaming)

The endpoint is a URL a workspace member typed, so every call goes through
``tfc.utils.ssrf_guard.guarded_post``: the address is checked against the
deployment's policy on every call (rows saved before the stored config was
normalized included), redirects are refused, metadata headers are dropped,
the body is capped and errors are generic. Logs carry no URL or headers.
"""

import time
from typing import Any

import structlog

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    BaseModelHandler,
    HandlerResponse,
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
    PayloadBuilder,
)
from tfc.utils.ssrf_guard import (
    EndpointBlocked,
    GuardedRequestError,
    endpoint_status_message,
    guarded_post,
)

# Five minutes: some self-hosted models are slow, as before.
_REQUEST_TIMEOUT_SECONDS = 300

logger = structlog.get_logger(__name__)


class CustomModelHandler(BaseModelHandler):
    """
    Handler for custom AI models registered in the database.

    Custom models are defined in the model_hub.CustomAIModel table with:
    - user_model_id: The model identifier used in requests
    - key_config: JSON containing the endpoint (api_base; endpoint_url on rows
      saved before it was normalized) and authentication headers
    - input_token_cost: Cost per million input tokens
    - output_token_cost: Cost per million output tokens
    - provider: Set to "custom" for custom models
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize custom model handler.

        Args:
            context: ModelHandlerContext with model configuration
        """
        super().__init__(context)
        self._validate_context()
        self._custom_model_config: dict[str, Any] | None = None
        self._load_custom_model_config()

    def _load_custom_model_config(self) -> None:
        """
        Load custom model configuration from database.

        Raises:
            ValueError: If custom model not found or invalid config
        """
        try:
            from model_hub.models.custom_models import CustomAIModel
            from model_hub.utils.model_endpoints import custom_endpoint_from_config

            custom_model = CustomAIModel.objects.get(
                organization_id=self.context.organization_id,
                user_model_id=self.context.model,
                deleted=False,
            )

            # Get decrypted key_config
            key_config = custom_model.actual_json

            # Validate required fields
            if not key_config:
                raise ValueError(f"Custom model {self.context.model} has no key_config")

            endpoint_url, headers = custom_endpoint_from_config(key_config)

            self._custom_model_config = {
                "endpoint_url": endpoint_url,
                "headers": headers,
                "input_token_cost": custom_model.input_token_cost,
                "output_token_cost": custom_model.output_token_cost,
                "provider": custom_model.provider,
            }

            logger.info(
                f"[CustomModel] Loaded config for {self.context.model}",
                has_headers=bool(headers),
            )

        except Exception as e:
            logger.exception(
                f"[CustomModel] Failed to load config for {self.context.model}: {e}",
            )
            raise ValueError(
                f"Custom model {self.context.model} not found or invalid: {e}"
            ) from e

    def _build_request_payload(self) -> dict[str, Any]:
        """
        Build request payload for custom model API.

        Uses PayloadBuilder.build_llm_payload as base, then removes provider-specific
        fields and adds stream=False for custom model endpoints.

        Returns:
            Dictionary with request payload (OpenAI-compatible format)
        """
        # Use build_llm_payload as base - it builds standard OpenAI-compatible payload
        # Pass provider="custom" and dummy api_key since we'll remove them anyway
        payload = PayloadBuilder.build_llm_payload(
            context=self.context,
            provider="custom",
            api_key="",  # Not used in final payload
        )

        # Remove provider-specific fields that build_llm_payload might have added
        payload.pop("custom_llm_provider", None)
        payload.pop("api_key", None)
        payload.pop("vertex_credentials", None)

        # Custom models don't support streaming
        payload["stream"] = False

        return payload

    def _calculate_token_costs(
        self, prompt_text: str, completion_text: str, image_urls: list
    ) -> tuple[int, int, dict[str, float]]:
        """
        Calculate token counts and costs for custom model.

        Args:
            prompt_text: Input text for token counting
            completion_text: Output text for token counting
            image_urls: List of image URLs in the prompt

        Returns:
            Tuple of (prompt_tokens, completion_tokens, cost_dict)
        """
        try:
            from ee.usage.utils.usage_entries import count_tiktoken_tokens
        except ImportError:
            count_tiktoken_tokens = None
        from agentic_eval.core_evals.fi_utils.token_count_helper import (
            calculate_total_cost,
        )

        # Count tokens
        estimated_prompt_tokens = (
            count_tiktoken_tokens(prompt_text, image_urls)
            if count_tiktoken_tokens
            else 0
        )
        estimated_completion_tokens = (
            count_tiktoken_tokens(completion_text) if count_tiktoken_tokens else 0
        )

        # Prepare usage payload for cost calculation
        token_usage = {
            "prompt_tokens": estimated_prompt_tokens,
            "completion_tokens": estimated_completion_tokens,
        }

        # Use custom model pricing as fallback_pricing
        # Default to 0.0 if cost is not set
        input_cost = self._custom_model_config.get("input_token_cost") or 0.0
        output_cost = self._custom_model_config.get("output_token_cost") or 0.0

        fallback_pricing = {
            "input_per_1M_tokens": input_cost,
            "output_per_1M_tokens": output_cost,
        }

        # Use calculate_total_cost for consistent cost calculation
        cost_dict = calculate_total_cost(
            model_name=self.context.model,
            token_usage=token_usage,
            fallback_pricing=fallback_pricing,
        )

        return estimated_prompt_tokens, estimated_completion_tokens, cost_dict

    def _extract_message_content_for_token_counting(
        self, messages: list
    ) -> tuple[str, list]:
        """
        Extract text and image URLs from messages for token counting.

        Args:
            messages: List of message dictionaries

        Returns:
            Tuple of (concatenated_text, list_of_image_urls)
        """
        text_parts = []
        image_urls = []

        for msg in messages:
            content = msg.get("content", "")

            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif part.get("type") == "image_url":
                            url = part.get("image_url", {}).get("url", "")
                            if url:
                                image_urls.append(url)

        return " ".join(text_parts), image_urls

    def _make_custom_api_request(
        self, payload: dict[str, Any], start_time: float
    ) -> HandlerResponse:
        """
        Make HTTP request to custom model endpoint.

        Args:
            payload: Request payload
            start_time: Request start time

        Returns:
            HandlerResponse with model output
        """
        endpoint_url = self._custom_model_config["endpoint_url"]
        headers = self._custom_model_config["headers"]

        try:
            logger.info(
                "[CustomModel] Making request",
                model=self.context.model,
                message_count=len(payload.get("messages", [])),
            )

            # Address-checked, pinned POST; the policy is applied on every
            # call, so rows saved before the fix are covered too.
            response = guarded_post(
                endpoint_url,
                json=payload,
                headers=headers,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                logger.warning(
                    "[CustomModel] Endpoint answered an error status",
                    model=self.context.model,
                    status_code=response.status_code,
                )
                raise RuntimeError(
                    "Custom model API request failed: "
                    + endpoint_status_message(response.status_code)
                )
            response_content = response.text

            end_time = time.time()
            completion_time = (end_time - start_time) * 1000

            # Extract message content for token counting
            prompt_text, image_urls = self._extract_message_content_for_token_counting(
                payload.get("messages", [])
            )

            # Calculate token costs
            prompt_tokens, completion_tokens, cost_dict = self._calculate_token_costs(
                prompt_text=prompt_text,
                completion_text=response_content,
                image_urls=image_urls,
            )

            # Build metadata
            metadata = {
                "usage": {
                    "completion_tokens": completion_tokens,
                    "prompt_tokens": prompt_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
                "cost": cost_dict,
                "response_time": completion_time,
            }

            logger.info(
                "[CustomModel] Request completed",
                model=self.context.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost_dict["total_cost"],
            )

            return HandlerResponse(
                response=response_content,
                start_time=start_time,
                end_time=end_time,
                model=self.context.model,
                metadata=metadata,
            )

        except (EndpointBlocked, GuardedRequestError) as e:
            # Value-free: the category, never the URL, address or headers.
            logger.warning(
                "[CustomModel] Request refused or failed",
                model=self.context.model,
                category=e.category,
            )
            if e.category == "timeout":
                raise TimeoutError(
                    f"Custom model request timed out after "
                    f"{_REQUEST_TIMEOUT_SECONDS} seconds: {e}"
                ) from None
            raise RuntimeError(f"Custom model API request failed: {e}") from None

        except Exception as e:
            logger.exception(
                f"[CustomModel] Unexpected error for {self.context.model}: {e}",
            )
            raise

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute custom model request (synchronous).

        Custom models do not support streaming, so the streaming parameter is ignored.

        Args:
            streaming: Ignored - custom models don't support streaming

        Returns:
            HandlerResponse with model output
        """
        start_time = time.time()

        try:
            logger.info(
                "[CustomModel] Executing custom model request",
                model=self.context.model,
                organization_id=self.context.organization_id,
            )

            # Build request payload
            payload = self._build_request_payload()

            # Make API request (streaming is not supported for custom models)
            if streaming:
                logger.warning(
                    "[CustomModel] Streaming not supported for custom models, executing regular request"
                )
            return self._make_custom_api_request(payload, start_time)

        except Exception as e:
            logger.exception(
                f"[CustomModel] Execution failed: {e}",
                model=self.context.model,
            )
            raise

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute custom model request (asynchronous).

        Note: Currently wraps synchronous execution.
        The guarded POST custom models use is synchronous.

        Args:
            streaming: Ignored - custom models don't support streaming

        Returns:
            HandlerResponse with model output
        """
        from asgiref.sync import sync_to_async

        logger.info(
            "[CustomModel] Executing custom model request (async wrapper)",
            model=self.context.model,
        )

        # Wrap synchronous execution in async
        return await sync_to_async(self.execute_sync)(streaming)
