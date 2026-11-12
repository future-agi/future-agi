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

The answer is the assistant message of the OpenAI-compatible body
(``choices[0].message``: its text, tool calls and reasoning, read by
``ResponseFormatter.parse_chat_completion_body``), not the envelope. A body in
any other shape is the answer as it is, as before, so the call still succeeds;
a text-generation ``generated_text`` body is read as its text.
"""

import json
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
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.response_formatter import (
    ResponseFormatter,
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


def _provider_token_counts(usage: Any) -> tuple[int, int] | None:
    """(prompt_tokens, completion_tokens) from the endpoint's own ``usage``
    when it gives both as whole numbers, else None."""
    if not isinstance(usage, dict):
        return None
    counts = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
    if all(
        isinstance(count, int) and not isinstance(count, bool) and count >= 0
        for count in counts
    ):
        return counts
    return None


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
        self,
        prompt_text: str,
        completion_text: str,
        image_urls: list,
        usage: dict[str, Any] | None = None,
    ) -> tuple[int, int, dict[str, float]]:
        """
        Calculate token counts and costs for custom model.

        Args:
            prompt_text: Input text for token counting
            completion_text: Output text (the answer) for token counting
            image_urls: List of image URLs in the prompt
            usage: The endpoint's own ``usage``; used instead of counting
                when it gives both token counts

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

        provider_counts = _provider_token_counts(usage)
        if provider_counts is not None:
            estimated_prompt_tokens, estimated_completion_tokens = provider_counts
        else:
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

        # The saved price, by the one rule every path uses
        # (pricing_source "custom_model"); the loaded price stays the
        # fallback if the model cannot be read again.
        cost_dict = calculate_total_cost(
            model_name=self.context.model,
            token_usage=token_usage,
            fallback_pricing=fallback_pricing,
            organization_id=self.context.organization_id,
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
        self, payload: dict[str, Any], start_time: float, for_stream: bool = False
    ) -> HandlerResponse:
        """
        Make HTTP request to custom model endpoint.

        Args:
            payload: Request payload
            start_time: Request start time
            for_stream: Shape the result for a Playground (WebSocket) run, as
                LLM streaming does: the answer as text (thinking first when
                shown, then any tool calls as JSON, output_format not parsed).
                Otherwise the response is formatted like format_llm_response.
                The response time is in milliseconds either way.

        Returns:
            HandlerResponse with the assistant's answer
        """
        endpoint_url = self._custom_model_config["endpoint_url"]
        headers = self._custom_model_config["headers"]
        # The guard's category and fixed copy when it refuses or fails the
        # call; the error is raised after the except clause (see below).
        failure_category = failure_copy = None

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
            body_text = response.text

            end_time = time.time()
            elapsed = end_time - start_time

            # The answer is choices[0].message, never the envelope; a body
            # that is not JSON is the answer as it is.
            parsed = ResponseFormatter.parse_chat_completion_body(body_text)
            answer = parsed.content if parsed is not None else body_text
            tool_calls = (parsed.tool_calls if parsed is not None else None) or []
            has_tool_calls = bool(self.context.tools and tool_calls)
            # No emptiness check: an empty answer is an empty result, as it
            # was before the answer was read out of the body (never a failure
            # after the provider has answered).
            tool_calls_text = json.dumps(tool_calls) if has_tool_calls else ""

            reasoning = parsed.reasoning if parsed is not None else None
            answer_with_thinking = answer
            if reasoning and self.context.show_reasoning_process:
                answer_with_thinking = (
                    f"<thinking>\n{reasoning}\n</thinking>\n\n{answer or ''}"
                )

            if for_stream:
                formatted_response = (answer_with_thinking or "") + tool_calls_text
            elif has_tool_calls:
                formatted_response = tool_calls_text
            elif self.context.output_format:
                formatted_response = ResponseFormatter.format_output_text(
                    answer, self.context.output_format
                )
            else:
                formatted_response = answer

            # Extract message content for token counting
            prompt_text, image_urls = self._extract_message_content_for_token_counting(
                payload.get("messages", [])
            )

            # Calculate token costs on the answer, not the envelope
            prompt_tokens, completion_tokens, cost_dict = self._calculate_token_costs(
                prompt_text=prompt_text,
                completion_text=(answer or "") + tool_calls_text,
                image_urls=image_urls,
                usage=parsed.usage if parsed is not None else None,
            )

            # Build metadata
            metadata = {
                "usage": {
                    "completion_tokens": completion_tokens,
                    "prompt_tokens": prompt_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
                "cost": cost_dict,
                # Milliseconds on every path, Playground runs included, as
                # dataset cells, the row footer and LLM streaming record it;
                # response_time_unit tells the Prompts time chip so.
                "response_time": elapsed * 1000,
                "response_time_unit": "ms",
            }

            logger.info(
                "[CustomModel] Request completed",
                model=self.context.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost_dict["total_cost"],
            )

            return HandlerResponse(
                response=formatted_response,
                start_time=start_time,
                end_time=end_time,
                model=self.context.model,
                metadata=metadata,
                # The text (with thinking when shown) for value_info's
                # data.response, as format_llm_response; None falls back to
                # the response (a tool-call-only answer).
                data_response=None if for_stream else answer_with_thinking,
            )

        except (EndpointBlocked, GuardedRequestError) as e:
            # Value-free: the category, never the URL, address or headers.
            logger.warning(
                "[CustomModel] Request refused or failed",
                model=self.context.model,
                category=e.category,
            )
            failure_category, failure_copy = e.category, str(e)

        except Exception as e:
            logger.exception(
                f"[CustomModel] Unexpected error for {self.context.model}: {e}",
            )
            raise

        # Raised after the except clause, so the error has no __context__
        # (as model_endpoints.endpoint_call_error asks). The run-prompt error
        # handler's root pick walks __context__ even when "from None" hides
        # it, and urllib3's text kept there (a read timeout's pinned address
        # and port 443, a dropped connection, a certificate name mismatch)
        # would outscore this copy, so the answer showed the generic
        # sentence instead.
        if failure_category == "timeout":
            raise TimeoutError(
                f"Custom model request timed out after "
                f"{_REQUEST_TIMEOUT_SECONDS} seconds: {failure_copy}"
            )
        raise RuntimeError(f"Custom model API request failed: {failure_copy}")

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute custom model request (synchronous).

        Custom models do not support streaming, so the streaming parameter is ignored.

        Args:
            streaming: Ignored - custom models don't support streaming

        Returns:
            HandlerResponse with model output
        """
        # Make API request (streaming is not supported for custom models)
        if streaming:
            logger.warning(
                "[CustomModel] Streaming not supported for custom models, executing regular request"
            )
        return self._execute()

    def _execute(self, for_stream: bool = False) -> HandlerResponse:
        """One request to the endpoint; ``for_stream`` as in
        _make_custom_api_request."""
        start_time = time.time()

        try:
            logger.info(
                "[CustomModel] Executing custom model request",
                model=self.context.model,
                organization_id=self.context.organization_id,
            )

            # Build request payload
            payload = self._build_request_payload()

            return self._make_custom_api_request(
                payload, start_time, for_stream=for_stream
            )

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

    # -------------------------------------------------------------------------
    # Playground (WebSocket) runs
    # -------------------------------------------------------------------------

    def execute_with_ws_lifecycle_sync(self) -> HandlerResponse:
        """started -> the answer text as one chunk -> completed (or error)."""
        return self.run_with_ws(self._get_ws_manager())

    async def execute_with_ws_lifecycle_async(self) -> HandlerResponse:
        """Async version of the above."""
        # Async callers always have ws_manager, as in the base template.
        return await self.run_with_ws_async(self.context.ws_manager)

    def run_with_ws(self, ws_manager, owns_lifecycle: bool = True) -> HandlerResponse:
        """
        Run once and send the answer over the WebSocket.

        Custom endpoints do not stream, so the Playground gets the whole
        answer text (tool calls as their JSON) in one 'running' frame, then
        'completed' with the response time in milliseconds
        (response_time_unit "ms"), as LLM streaming sends them; never the
        endpoint's body.

        Args:
            ws_manager: The run's WebSocket manager
            owns_lifecycle: Send 'started' first and 'error' on a failure.
                False for LLMHandler's custom-model fallback, which sent
                'started' itself and leaves errors to its caller.

        Returns:
            HandlerResponse whose response is the text that was sent
        """
        if owns_lifecycle:
            self._ws_send_started(ws_manager)
        try:
            response = self._execute(for_stream=True)
            if response.response:
                self._ws_send_running(ws_manager, response.response, 0)
            self._ws_send_completed(ws_manager, response.metadata)
            return response
        except Exception as e:
            if owns_lifecycle:
                self._ws_send_error(ws_manager, str(e))
            raise

    async def run_with_ws_async(
        self, ws_manager, owns_lifecycle: bool = True
    ) -> HandlerResponse:
        """Async version of run_with_ws (the request runs in a worker thread)."""
        from asgiref.sync import sync_to_async

        if owns_lifecycle:
            await self._ws_send_started_async(ws_manager)
        try:
            response = await sync_to_async(self._execute)(for_stream=True)
            if response.response:
                await self._ws_send_running_async(ws_manager, response.response, 0)
            await self._ws_send_completed_async(ws_manager, response.metadata)
            return response
        except Exception as e:
            if owns_lifecycle:
                await self._ws_send_error_async(ws_manager, str(e))
            raise
