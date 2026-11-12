"""
Base handler classes for RunPrompt model execution.

This module provides the foundational classes for the Strategy Pattern implementation:
- ModelHandlerContext: Immutable context data passed to handlers
- HandlerResponse: Standardized response format
- BaseModelHandler: Abstract base class for all model type handlers
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
import structlog

logger = structlog.get_logger(__name__)


# Retry configuration defaults
DEFAULT_MAX_RETRIES = 10
DEFAULT_INITIAL_DELAY = 1.0
DEFAULT_MAX_DELAY = 10.0

# Patterns that indicate a timeout/retryable error
TIMEOUT_ERROR_PATTERNS = (
    "timeout",
    "timed out",
    "connection timeout",
    "read timeout",
    "too many requests",
)


def with_response_time_unit(metadata: Any) -> Any:
    """
    Mark metadata["response_time"] as milliseconds, the unit every handler
    emits. Prompts runs saved before this marker stored seconds for
    streaming LLM runs with no unit, so readers convert only marked values.
    Returns a copy when it adds the key; anything without a response_time
    (for example a stopped run's empty metadata) is returned unchanged.
    """
    if (
        isinstance(metadata, dict)
        and "response_time" in metadata
        and "response_time_unit" not in metadata
    ):
        return {**metadata, "response_time_unit": "ms"}
    return metadata


@dataclass
class ModelHandlerContext:
    """
    Immutable context containing all data needed for model execution.

    This dataclass is passed to handlers and contains:
    - Model configuration (name, messages, parameters)
    - Organization/workspace context
    - API credentials and provider info
    - WebSocket manager for streaming
    - Template tracking for result indexing
    """

    # Core model configuration
    model: str
    messages: List[Dict[str, Any]]
    organization_id: str
    workspace_id: Optional[str] = None
    output_format: str = "text"

    # LLM parameters
    temperature: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    response_format: Any = None
    tool_choice: Any = None
    tools: List[Dict[str, Any]] = field(default_factory=list)

    # Additional configuration
    run_prompt_config: Dict[str, Any] = field(default_factory=dict)

    # WebSocket and streaming
    ws_manager: Any = None

    # Template tracking (for WebSocket messages and result indexing)
    template_id: Optional[str] = None
    version: Optional[str] = None
    result_index: Optional[int] = None  # index in original param
    num_results: Optional[int] = None  # max_index in original param
    run_type: Optional[str] = None

    # Provider and API configuration
    provider: Optional[str] = None
    api_key: Any = None

    # Reasoning parameters
    reasoning_effort: Optional[str] = None
    thinking_budget: Optional[int] = None
    show_reasoning_process: Optional[bool] = None

    @classmethod
    def from_run_prompt(
        cls,
        run_prompt_instance,
        template_id: Optional[str] = None,
        version: Optional[str] = None,
        result_index: Optional[int] = None,
        num_results: Optional[int] = None,
        run_type: Optional[str] = None,
        provider: Optional[str] = None,
        api_key: Any = None,
    ) -> "ModelHandlerContext":
        """
        Factory method to create context from a RunPrompt instance.

        Args:
            run_prompt_instance: Instance of RunPrompt class
            template_id: Template identifier for WebSocket messages
            version: Template version
            result_index: Index of current result (for batching)
            num_results: Total number of results expected
            run_type: Type of run (for tracking)
            provider: Model provider name
            api_key: API key for the provider

        Returns:
            ModelHandlerContext instance
        """
        return cls(
            model=run_prompt_instance.model,
            messages=run_prompt_instance.messages,
            organization_id=run_prompt_instance.organization_id,
            workspace_id=getattr(run_prompt_instance, "workspace_id", None),
            output_format=run_prompt_instance.output_format,
            temperature=run_prompt_instance.temperature,
            frequency_penalty=run_prompt_instance.frequency_penalty,
            presence_penalty=run_prompt_instance.presence_penalty,
            max_tokens=run_prompt_instance.max_tokens,
            top_p=run_prompt_instance.top_p,
            response_format=run_prompt_instance.response_format,
            tool_choice=run_prompt_instance.tool_choice,
            tools=run_prompt_instance.tools or [],
            run_prompt_config=run_prompt_instance.run_prompt_config or {},
            ws_manager=getattr(run_prompt_instance, "ws_manager", None),
            template_id=template_id,
            version=version,
            result_index=result_index,
            num_results=num_results,
            run_type=run_type,
            provider=provider,
            api_key=api_key,
            reasoning_effort=getattr(run_prompt_instance, "reasoning_effort", None),
            thinking_budget=getattr(run_prompt_instance, "thinking_budget", None),
            show_reasoning_process=getattr(
                run_prompt_instance, "show_reasoning_process", None
            ),
        )

    # Convenience properties for TTS-specific configuration
    @property
    def voice(self) -> Optional[str]:
        """Extract voice parameter from run_prompt_config."""
        return self.run_prompt_config.get("voice")

    @property
    def voice_id(self) -> Optional[str]:
        """Extract voice_id parameter from run_prompt_config."""
        return self.run_prompt_config.get("voice_id")

    @property
    def audio_format(self) -> Optional[str]:
        """Extract audio format from run_prompt_config."""
        return self.run_prompt_config.get("format")

    @property
    def modalities(self) -> Optional[List[str]]:
        """Extract modalities from run_prompt_config."""
        return self.run_prompt_config.get("modalities")


@dataclass
class HandlerResponse:
    """
    Standardized response format from handlers.

    All handlers return this format, which can be converted to the legacy
    (response, value_info) tuple format for backward compatibility.
    """

    # Main response content (clean, without thinking/reasoning)
    response: Any

    # Timing
    start_time: float
    end_time: float

    # Model information
    model: str

    # Metadata (usage, cost, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Optional fields
    failure: Optional[str] = None
    metrics: List[Any] = field(default_factory=list)
    output: Any = None
    name: Optional[str] = None

    # Response with thinking/reasoning content for value_info (old behavior)
    # When set, this is used in value_info["data"]["response"] instead of self.response
    data_response: Any = None

    @property
    def runtime(self) -> float:
        """Runtime in milliseconds."""
        return (self.end_time - self.start_time) * 1000

    def to_value_info(self) -> Tuple[Any, Dict[str, Any]]:
        """
        Convert to legacy (response, value_info) tuple format.

        Returns:
            Tuple of (response, value_info dict)
        """
        # Use data_response if available (for thinking content), otherwise use response
        response_for_value_info = (
            self.data_response if self.data_response is not None else self.response
        )
        value_info = {
            "name": self.name,
            "data": {"response": response_for_value_info},
            "failure": self.failure,
            "runtime": self.runtime,
            "model": self.model,
            "metrics": self.metrics,
            "metadata": with_response_time_unit(self.metadata),
            "output": self.output,
        }
        return self.response, value_info


class BaseModelHandler(ABC):
    """
    Abstract base class for handling different model types.

    Each model type (LLM, TTS, STT, Custom, Image, etc.) implements this
    interface to provide consistent behavior across all model types.

    The Strategy Pattern allows:
    - Easy addition of new model types
    - Clean separation of concerns
    - Consistent error handling and logging
    - Reusable utilities
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize handler with context.

        Args:
            context: ModelHandlerContext containing all execution parameters
        """
        self.context = context
        self.logger = logger.bind(
            model=context.model,
            organization_id=context.organization_id,
            handler=self.__class__.__name__,
        )

    @abstractmethod
    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute model request synchronously.

        Args:
            streaming: Whether to stream the response

        Returns:
            HandlerResponse with results
        """
        pass

    @abstractmethod
    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute model request asynchronously.

        Args:
            streaming: Whether to stream the response

        Returns:
            HandlerResponse with results
        """
        pass

    def _extract_text_from_messages(self) -> str:
        """
        Extract text content from messages list.

        Handles various message formats:
        - String messages
        - Dict messages with 'content' key
        - Multi-part messages (text + images)

        Returns:
            Concatenated text from all messages
        """
        text_parts = []

        for msg in self.context.messages:
            if isinstance(msg, str):
                text_parts.append(msg)
            elif isinstance(msg, dict):
                content = msg.get("content", "")

                # Handle multi-part content (e.g., text + images)
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                elif isinstance(content, str):
                    text_parts.append(content)

        return " ".join(text_parts).strip()

    def _validate_context(self):
        """
        Validate that context has required fields for this handler.

        Override in subclasses to add specific validation.

        Raises:
            ValueError: If required fields are missing or invalid
        """
        if not self.context.model:
            raise ValueError("Model name is required")

        if not self.context.messages:
            raise ValueError("Messages are required")

        if not self.context.organization_id:
            raise ValueError("Organization ID is required")

    def _build_handler_response(
        self,
        response: Any,
        start_time: float,
        metadata: Optional[Dict[str, Any]] = None,
        failure: Optional[str] = None,
    ) -> HandlerResponse:
        """
        Build a standardized HandlerResponse.

        Args:
            response: The main response content
            start_time: Request start time (from time.time())
            metadata: Optional metadata dict
            failure: Optional failure message

        Returns:
            HandlerResponse instance
        """
        end_time = time.time()

        return HandlerResponse(
            response=response,
            start_time=start_time,
            end_time=end_time,
            model=self.context.model,
            metadata=metadata or {},
            failure=failure,
        )

    def _validate_response_not_empty(
        self,
        response: Any,
        has_tool_calls: bool = False,
        response_type: str = "model",
    ) -> None:
        """
        Validate that the response is not empty.

        This method should be called by all handlers before returning a response
        to ensure consistent empty response handling across all model types.

        Args:
            response: The response content to validate
            has_tool_calls: If True, allow empty response (LLM with tool calls)
            response_type: Type of response for error message (e.g., "LLM", "Image", "TTS", "STT")

        Raises:
            Exception: If response is None or empty (unless has_tool_calls=True)
        """
        # Allow empty response if tool calls are present (LLM case)
        if has_tool_calls:
            return

        # Check for None response
        if response is None:
            self.logger.warning(
                f"{response_type} returned None response",
                model=self.context.model,
            )
            raise Exception(
                f"{response_type} returned None response. This may be due to API issues."
            )

        # Check for empty string response
        if isinstance(response, str) and not response.strip():
            self.logger.warning(
                f"{response_type} returned empty response content",
                model=self.context.model,
            )
            raise Exception(
                f"{response_type} returned empty response content. "
                "This may be due to content filtering or API issues."
            )

    def _is_timeout_error(self, error: Exception) -> bool:
        """
        Check if an exception indicates a timeout or rate limit error.

        Args:
            error: The exception to check

        Returns:
            True if the error appears to be a timeout/rate limit error
        """
        error_str = str(error).lower()
        return any(pattern in error_str for pattern in TIMEOUT_ERROR_PATTERNS)

    def _retry_on_timeout(
        self,
        func: Callable,
        *args,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_delay: float = DEFAULT_INITIAL_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        **kwargs,
    ) -> Any:
        """
        Retry wrapper for API calls that may timeout.

        Implements exponential backoff for timeout and rate limit errors.
        Handles both explicit timeout exceptions and generic exceptions with
        timeout-like error messages.

        Args:
            func: The function to call
            *args: Positional arguments for the function
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay in seconds before first retry
            max_delay: Maximum delay in seconds between retries
            **kwargs: Keyword arguments for the function

        Returns:
            The result of the function call

        Raises:
            Exception: If all retries are exhausted or non-retryable error
        """
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)

            # Catch known timeout exception types explicitly
            except (TimeoutError, requests.exceptions.Timeout) as e:
                if attempt < max_retries - 1:
                    delay = min(initial_delay * (2**attempt), max_delay)
                    self.logger.warning(
                        f"Timeout on attempt {attempt + 1}/{max_retries}. "
                        f"Retrying in {delay}s",
                        error=str(e),
                    )
                    time.sleep(delay)
                else:
                    self.logger.error(f"All {max_retries} retry attempts exhausted")
                    raise

            # Fallback: generic exceptions with timeout-like messages
            except Exception as e:
                if self._is_timeout_error(e) and attempt < max_retries - 1:
                    delay = min(initial_delay * (2**attempt), max_delay)
                    self.logger.warning(
                        f"Timeout-like error on attempt {attempt + 1}/{max_retries}. "
                        f"Retrying in {delay}s",
                        error=str(e),
                    )
                    time.sleep(delay)
                else:
                    # Not a timeout - fail immediately
                    raise

    async def _retry_on_timeout_async(
        self,
        func: Callable,
        *args,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_delay: float = DEFAULT_INITIAL_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        **kwargs,
    ) -> Any:
        """
        Async retry wrapper for API calls that may timeout.

        Uses asyncio.sleep instead of time.sleep to avoid blocking the event loop.
        Handles both explicit timeout exceptions and generic exceptions with
        timeout-like error messages.

        Args:
            func: The function to call (will be awaited if coroutine)
            *args: Positional arguments for the function
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay in seconds before first retry
            max_delay: Maximum delay in seconds between retries
            **kwargs: Keyword arguments for the function

        Returns:
            The result of the function call

        Raises:
            Exception: If all retries are exhausted or non-retryable error
        """
        from asgiref.sync import sync_to_async

        for attempt in range(max_retries):
            try:
                # If func is a coroutine function, await it directly
                # Otherwise wrap it with sync_to_async
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return await sync_to_async(func)(*args, **kwargs)

            # Catch known timeout exception types explicitly
            except (
                TimeoutError,
                asyncio.TimeoutError,
                requests.exceptions.Timeout,
            ) as e:
                if attempt < max_retries - 1:
                    delay = min(initial_delay * (2**attempt), max_delay)
                    self.logger.warning(
                        f"Timeout on attempt {attempt + 1}/{max_retries}. "
                        f"Retrying in {delay}s",
                        error=str(e),
                    )
                    await asyncio.sleep(delay)  # Non-blocking sleep
                else:
                    self.logger.error(f"All {max_retries} retry attempts exhausted")
                    raise

            # Fallback: generic exceptions with timeout-like messages
            except Exception as e:
                if self._is_timeout_error(e) and attempt < max_retries - 1:
                    delay = min(initial_delay * (2**attempt), max_delay)
                    self.logger.warning(
                        f"Timeout-like error on attempt {attempt + 1}/{max_retries}. "
                        f"Retrying in {delay}s",
                        error=str(e),
                    )
                    await asyncio.sleep(delay)  # Non-blocking sleep
                else:
                    # Not a timeout - fail immediately
                    raise

    # -------------------------------------------------------------------------
    # WebSocket Helper Methods
    # -------------------------------------------------------------------------

    def _get_ws_manager(self):
        """Get WS manager from context or create from org_id."""
        if self.context.ws_manager:
            return self.context.ws_manager
        from model_hub.utils.websocket_manager import get_websocket_manager

        return get_websocket_manager(self.context.organization_id)

    def _ws_send_started(self, ws_manager):
        """Send streaming started message."""
        ws_manager.send_started_message(
            template_id=str(self.context.template_id),
            version=self.context.version,
            result_index=self.context.result_index,
            num_results=self.context.num_results,
            output_format=self.context.output_format or "string",
        )

    def _ws_send_running(self, ws_manager, chunk, chunk_pos):
        """Send streaming running message with chunk."""
        ws_manager.send_running_message(
            template_id=str(self.context.template_id),
            version=self.context.version,
            chunk=chunk,
            chunk_pos=chunk_pos,
            result_index=self.context.result_index,
            num_results=self.context.num_results,
        )

    def _ws_send_completed(self, ws_manager, metadata):
        """Send streaming completed message."""
        ws_manager.send_completed_message(
            template_id=str(self.context.template_id),
            version=self.context.version,
            result_index=self.context.result_index,
            num_results=self.context.num_results,
            metadata=with_response_time_unit(metadata),
            output_format=self.context.output_format or "string",
        )

    def _ws_send_error(self, ws_manager, error_str):
        """Send streaming error message."""
        try:
            ws_manager.send_error_message(
                template_id=str(self.context.template_id),
                version=self.context.version,
                error=error_str,
                result_index=self.context.result_index,
                num_results=self.context.num_results,
                output_format=self.context.output_format or "string",
            )
        except Exception as ws_error:
            self.logger.exception(f"Failed to send WebSocket error message: {ws_error}")

    async def _ws_send_started_async(self, ws_manager):
        """Send streaming started message (async)."""
        await ws_manager.send_started_message(
            template_id=str(self.context.template_id),
            version=self.context.version,
            result_index=self.context.result_index,
            num_results=self.context.num_results,
            output_format=self.context.output_format or "string",
        )

    async def _ws_send_running_async(self, ws_manager, chunk, chunk_pos):
        """Send streaming running message with chunk (async)."""
        await ws_manager.send_running_message(
            template_id=str(self.context.template_id),
            version=self.context.version,
            chunk=chunk,
            chunk_pos=chunk_pos,
            result_index=self.context.result_index,
            num_results=self.context.num_results,
        )

    async def _ws_send_completed_async(self, ws_manager, metadata):
        """Send streaming completed message (async)."""
        await ws_manager.send_completed_message(
            template_id=str(self.context.template_id),
            version=self.context.version,
            result_index=self.context.result_index,
            num_results=self.context.num_results,
            metadata=with_response_time_unit(metadata),
            output_format=self.context.output_format or "string",
        )

    async def _ws_send_error_async(self, ws_manager, error_str):
        """Send streaming error message (async)."""
        try:
            await ws_manager.send_error_message(
                template_id=str(self.context.template_id),
                version=self.context.version,
                error=error_str,
                result_index=self.context.result_index,
                num_results=self.context.num_results,
                output_format=self.context.output_format or "string",
            )
        except Exception as ws_error:
            self.logger.exception(f"Failed to send WebSocket error message: {ws_error}")

    # -------------------------------------------------------------------------
    # WebSocket Lifecycle Template Methods
    # -------------------------------------------------------------------------

    def execute_with_ws_lifecycle_sync(self) -> "HandlerResponse":
        """Template: started -> execute_sync() -> send result -> completed/error."""
        ws_manager = self._get_ws_manager()
        self._ws_send_started(ws_manager)
        try:
            response = self.execute_sync(streaming=False)
            if response.response:
                self._ws_send_running(ws_manager, response.response, 0)
            self._ws_send_completed(ws_manager, response.metadata)
            return response
        except Exception as e:
            self._ws_send_error(ws_manager, str(e))
            raise

    async def execute_with_ws_lifecycle_async(self) -> "HandlerResponse":
        """Async version of the above."""
        ws_manager = self.context.ws_manager  # async callers always have ws_manager
        await self._ws_send_started_async(ws_manager)
        try:
            response = await self.execute_async(streaming=False)
            if response.response:
                await self._ws_send_running_async(ws_manager, response.response, 0)
            await self._ws_send_completed_async(ws_manager, response.metadata)
            return response
        except Exception as e:
            await self._ws_send_error_async(ws_manager, str(e))
            raise
