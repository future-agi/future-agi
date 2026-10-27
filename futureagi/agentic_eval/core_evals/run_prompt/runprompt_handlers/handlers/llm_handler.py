"""
LLM Handler for chat completion models.

This handler implements sync and async execution for LLM completions:
- GPT-4, GPT-5, o1/o3 reasoning models
- Claude models
- Llama, Mistral, and other LiteLLM-supported models

Features:
- Regular (non-streaming) completion
- Streaming with WebSocket support and chunk buffering
- Tool call handling
- Stop streaming signal handling
- Custom model delegation
"""

import json
import time
from typing import Any, Dict, List, Optional

import litellm
import structlog
from asgiref.sync import sync_to_async

from model_hub.utils.websocket_manager import WebSocketManager
from agentic_eval.core_evals.run_prompt.error_handler import (
    ErrorContext,
    handle_api_error,
    litellm_try_except,
)

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

logger = structlog.get_logger(__name__)


# Buffer configuration for streaming
MAX_BUFFER_SIZE = 60  # Characters to buffer before sending WebSocket message


class LLMHandler(BaseModelHandler):
    """
    Handler for LLM chat completion models.

    Supports both regular and streaming completions with WebSocket integration
    for real-time updates. Handles tool calling, custom models, and all
    LiteLLM-supported providers.
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize LLM handler.

        Args:
            context: ModelHandlerContext with model configuration
        """
        super().__init__(context)
        self._validate_context()

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute LLM completion synchronously.

        Args:
            streaming: Whether to stream the response via WebSocket

        Returns:
            HandlerResponse with completion results
        """
        start_time = time.time()

        # Build payload using utility
        payload = PayloadBuilder.build_llm_payload(
            self.context,
            self.context.provider or "openai",
            self.context.api_key,
        )

        if streaming:
            return self._streaming_execution(payload, start_time)
        else:
            return self._regular_execution(payload, start_time)

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute LLM completion asynchronously.

        Args:
            streaming: Whether to stream the response via WebSocket

        Returns:
            HandlerResponse with completion results
        """
        start_time = time.time()

        # Build payload using utility
        payload = PayloadBuilder.build_llm_payload(
            self.context,
            self.context.provider or "openai",
            self.context.api_key,
        )

        if streaming:
            return await self._streaming_execution_async(payload, start_time)
        else:
            return await self._regular_execution_async(payload, start_time)

    # -------------------------------------------------------------------------
    # Regular (Non-Streaming) Execution
    # -------------------------------------------------------------------------

    def _regular_execution(
        self, payload: Dict[str, Any], start_time: float
    ) -> HandlerResponse:
        """
        Execute non-streaming LLM completion.

        Args:
            payload: LiteLLM payload
            start_time: Request start time

        Returns:
            HandlerResponse with completion results
        """
        # Check for custom model
        if self._is_custom_model(payload):
            return self._execute_custom_model(payload, start_time)

        response = None
        with litellm_try_except():
            response = litellm.completion(**payload, drop_params=True)

        # Validate response is not None
        self._validate_response_not_empty(response, response_type="LLM")

        # Check for tool calls (allows empty content)
        has_tool_calls = bool(
            self.context.tools and response.choices[0].message.tool_calls
        )

        # Validate response content is not empty (unless tool calls are present)
        response_content = response.choices[0].message.content
        self._validate_response_not_empty(
            response_content,
            has_tool_calls=has_tool_calls,
            response_type="LLM",
        )

        # Format response
        formatted_response, value_info = ResponseFormatter.format_llm_response(
            response=response,
            model=self.context.model,
            start_time=start_time,
            tools=self.context.tools,
            output_format=self.context.output_format,
            show_reasoning_process=self.context.show_reasoning_process,
        )

        return HandlerResponse(
            response=formatted_response,
            start_time=start_time,
            end_time=time.time(),
            model=response.model,
            metadata=value_info.get("metadata", {}),
            data_response=value_info["data"]["response"],
        )

    async def _regular_execution_async(
        self, payload: Dict[str, Any], start_time: float
    ) -> HandlerResponse:
        """
        Execute non-streaming LLM completion asynchronously.

        Args:
            payload: LiteLLM payload
            start_time: Request start time

        Returns:
            HandlerResponse with completion results
        """
        # Check for custom model
        if self._is_custom_model(payload):
            return await sync_to_async(self._execute_custom_model)(payload, start_time)

        # Note: litellm_try_except is a sync context manager, but we wrap just the call
        # since acompletion is atomic. For proper error handling, catch litellm exceptions.
        with litellm_try_except():
            response = await litellm.acompletion(**payload, drop_params=True)

        # Validate response is not None
        self._validate_response_not_empty(response, response_type="LLM")

        # Check for tool calls (allows empty content)
        has_tool_calls = bool(
            self.context.tools and response.choices[0].message.tool_calls
        )

        # Validate response content is not empty (unless tool calls are present)
        response_content = response.choices[0].message.content
        self._validate_response_not_empty(
            response_content,
            has_tool_calls=has_tool_calls,
            response_type="LLM",
        )

        # Format response
        formatted_response, value_info = ResponseFormatter.format_llm_response(
            response=response,
            model=self.context.model,
            start_time=start_time,
            tools=self.context.tools,
            output_format=self.context.output_format,
            show_reasoning_process=self.context.show_reasoning_process,
        )

        return HandlerResponse(
            response=formatted_response,
            start_time=start_time,
            end_time=time.time(),
            model=response.model,
            metadata=value_info.get("metadata", {}),
            data_response=value_info["data"]["response"],
        )

    # -------------------------------------------------------------------------
    # Streaming Execution
    # -------------------------------------------------------------------------

    def _streaming_execution(
        self, payload: Dict[str, Any], start_time: float
    ) -> HandlerResponse:
        """
        Execute streaming LLM completion with WebSocket updates.

        Args:
            payload: LiteLLM payload
            start_time: Request start time

        Returns:
            HandlerResponse with completion results
        """
        response_chunks: List[str] = []  # Use list for efficient string accumulation
        ws_manager = self._get_ws_manager()

        # Send started message
        self._ws_send_started(ws_manager)

        # Handle custom model
        if self._is_custom_model(payload):
            return self._handle_custom_model_streaming(ws_manager, payload, start_time)

        # Enable streaming
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        # Buffer for accumulating chunks
        chunk_buffer = ""
        buffer_size = 0
        last_sent_chunk_pos = -1
        tool_calls: List[Dict[str, Any]] = []
        chunk = None

        # Thinking state tracking
        thinking_started = False
        thinking_finished = False

        try:
            response = None
            with litellm_try_except():
                response = litellm.completion(**payload, drop_params=True)

            for i, chunk in enumerate(response):
                # Check for stop signal
                if self._check_stop_streaming(ws_manager):
                    response_content = "".join(response_chunks)
                    return self._handle_streaming_stopped(
                        ws_manager, response_content, chunk_buffer, i, chunk, start_time
                    )

                # Process chunk
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta

                    # Handle reasoning/thinking content (o1/o3 models)
                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        if self.context.show_reasoning_process:
                            if not thinking_started:
                                thinking_tag = "<thinking>\n"
                                response_chunks.append(thinking_tag)
                                self._ws_send_running(ws_manager, thinking_tag, i)
                                thinking_started = True
                            response_chunks.append(delta.reasoning_content)
                            self._ws_send_running(
                                ws_manager, delta.reasoning_content, i
                            )

                    # Handle text content
                    if delta.content:
                        # Close thinking tag when regular content starts
                        if (
                            thinking_started
                            and not thinking_finished
                            and self.context.show_reasoning_process
                        ):
                            closing_tag = "\n</thinking>\n\n"
                            response_chunks.append(closing_tag)
                            self._ws_send_running(ws_manager, closing_tag, i)
                            thinking_finished = True

                        chunk_message = delta.content
                        response_chunks.append(chunk_message)  # Efficient append
                        chunk_buffer += chunk_message
                        buffer_size += len(chunk_message)

                        # Send buffered chunks when buffer is full
                        if buffer_size >= MAX_BUFFER_SIZE:
                            self._ws_send_running(ws_manager, chunk_buffer, i)
                            # Join chunks efficiently for caching
                            current_response = "".join(response_chunks)
                            self._ws_cache_response(ws_manager, current_response, i)
                            chunk_buffer = ""
                            buffer_size = 0
                            last_sent_chunk_pos = i

                    # Handle tool calls
                    elif self.context.tools and delta.tool_calls:
                        tool_calls = ResponseFormatter.accumulate_tool_calls(
                            tool_calls, delta.tool_calls
                        )

        except Exception as e:
            response_content = "".join(response_chunks)
            return self._handle_streaming_error(
                ws_manager,
                e,
                response_content,
                chunk_buffer,
                last_sent_chunk_pos,
                start_time,
            )

        # Join all chunks efficiently at the end
        response_content = "".join(response_chunks)

        # Send any remaining buffered chunks
        if chunk_buffer:
            self._ws_send_running(ws_manager, chunk_buffer, last_sent_chunk_pos + 1)

        # Handle tool calls
        if tool_calls:
            tool_calls_str = json.dumps(tool_calls)
            response_content += tool_calls_str
            self._ws_send_running(ws_manager, tool_calls_str, last_sent_chunk_pos + 2)

        # Check for stop before sending completed
        if self._check_stop_streaming(ws_manager):
            return self._build_stopped_response(response_content, chunk, start_time)

        # Build metadata from final chunk
        metadata = ResponseFormatter.build_streaming_metadata(chunk, start_time)

        # Send completed message
        self._ws_send_completed(ws_manager, metadata)

        return HandlerResponse(
            response=response_content,
            start_time=start_time,
            end_time=time.time(),
            model=chunk.model if chunk else self.context.model,
            metadata=metadata,
        )

    async def _streaming_execution_async(
        self, payload: Dict[str, Any], start_time: float
    ) -> HandlerResponse:
        """
        Execute streaming LLM completion asynchronously with WebSocket updates.

        Args:
            payload: LiteLLM payload
            start_time: Request start time

        Returns:
            HandlerResponse with completion results
        """
        response_chunks: List[str] = []  # Use list for efficient string accumulation
        ws_manager = self.context.ws_manager

        # Send started message
        await self._ws_send_started_async(ws_manager)

        # Handle custom model
        if self._is_custom_model(payload):
            return await self._handle_custom_model_streaming_async(
                ws_manager, payload, start_time
            )

        # Enable streaming
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        # Buffer for accumulating chunks
        chunk_buffer = ""
        buffer_size = 0
        last_sent_chunk_pos = -1
        tool_calls: List[Dict[str, Any]] = []
        chunk = None
        i = -1  # Start at -1 so first chunk is at position 0 (consistent with sync)

        # Thinking state tracking
        thinking_started = False
        thinking_finished = False

        try:
            response = await litellm.acompletion(**payload, drop_params=True)

            async for chunk in response:
                i += 1

                # Check for stop signal
                if await self._check_stop_streaming_async(ws_manager):
                    response_content = "".join(response_chunks)
                    return await self._handle_streaming_stopped_async(
                        ws_manager, response_content, chunk_buffer, i, chunk, start_time
                    )

                # Process chunk
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta

                    # Handle reasoning/thinking content (o1/o3 models)
                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        if self.context.show_reasoning_process:
                            if not thinking_started:
                                thinking_tag = "<thinking>\n"
                                response_chunks.append(thinking_tag)
                                await self._ws_send_running_async(
                                    ws_manager, thinking_tag, i
                                )
                                thinking_started = True
                            response_chunks.append(delta.reasoning_content)
                            await self._ws_send_running_async(
                                ws_manager, delta.reasoning_content, i
                            )

                    # Handle text content
                    if delta.content:
                        # Close thinking tag when regular content starts
                        if (
                            thinking_started
                            and not thinking_finished
                            and self.context.show_reasoning_process
                        ):
                            closing_tag = "\n</thinking>\n\n"
                            response_chunks.append(closing_tag)
                            await self._ws_send_running_async(
                                ws_manager, closing_tag, i
                            )
                            thinking_finished = True

                        chunk_message = delta.content
                        response_chunks.append(chunk_message)  # Efficient append
                        chunk_buffer += chunk_message
                        buffer_size += len(chunk_message)

                        # Send buffered chunks when buffer is full
                        if buffer_size >= MAX_BUFFER_SIZE:
                            await self._ws_send_running_async(
                                ws_manager, chunk_buffer, i
                            )
                            # Join chunks efficiently for caching
                            current_response = "".join(response_chunks)
                            await self._ws_cache_response_async(
                                ws_manager, current_response, i
                            )
                            chunk_buffer = ""
                            buffer_size = 0
                            last_sent_chunk_pos = i

                    # Handle tool calls
                    elif self.context.tools and delta.tool_calls:
                        tool_calls = ResponseFormatter.accumulate_tool_calls(
                            tool_calls, delta.tool_calls
                        )

        except Exception as e:
            response_content = "".join(response_chunks)
            return await self._handle_streaming_error_async(
                ws_manager,
                e,
                response_content,
                chunk_buffer,
                last_sent_chunk_pos,
                start_time,
            )

        # Join all chunks efficiently at the end
        response_content = "".join(response_chunks)

        # Send any remaining buffered chunks
        if chunk_buffer:
            await self._ws_send_running_async(
                ws_manager, chunk_buffer, last_sent_chunk_pos + 1
            )

        # Handle tool calls
        if tool_calls:
            tool_calls_str = json.dumps(tool_calls)
            response_content += tool_calls_str
            await self._ws_send_running_async(
                ws_manager, tool_calls_str, last_sent_chunk_pos + 2
            )

        # Check for stop before sending completed
        if await self._check_stop_streaming_async(ws_manager):
            return self._build_stopped_response(response_content, chunk, start_time)

        # Build metadata from final chunk
        metadata = ResponseFormatter.build_streaming_metadata(chunk, start_time)

        # Send completed message
        await self._ws_send_completed_async(ws_manager, metadata)

        return HandlerResponse(
            response=response_content,
            start_time=start_time,
            end_time=time.time(),
            model=chunk.model if chunk else self.context.model,
            metadata=metadata,
        )

    # -------------------------------------------------------------------------
    # WebSocket Helpers (Sync) - Uses base class methods for basic operations
    # -------------------------------------------------------------------------

    def _ws_send_stopped(
        self, ws_manager: WebSocketManager, partial_response: str
    ) -> None:
        """Send streaming stopped message."""
        ws_manager.send_stopped_message(
            template_id=str(self.context.template_id),
            version=self.context.version,
            partial_response=partial_response,
            result_index=self.context.result_index,
            num_results=self.context.num_results,
        )

    def _ws_cache_response(
        self, ws_manager: WebSocketManager, response: str, chunk_pos: int
    ) -> None:
        """Cache current response state."""
        ws_manager.set_cached_response(
            template_id=str(self.context.template_id),
            version=self.context.version,
            index=self.context.result_index,
            response_data={
                "response": response,
                "error": None,
                "last_chunk_pos": chunk_pos,
            },
        )

    def _check_stop_streaming(self, ws_manager: WebSocketManager) -> bool:
        """Check if streaming should be stopped."""
        return ws_manager.is_streaming_stopped(
            str(self.context.template_id), self.context.version
        )

    # -------------------------------------------------------------------------
    # WebSocket Helpers (Async) - Uses base class methods for basic operations
    # -------------------------------------------------------------------------

    async def _ws_send_stopped_async(self, ws_manager, partial_response: str) -> None:
        """Send streaming stopped message (async)."""
        await ws_manager.send_stopped_message(
            template_id=str(self.context.template_id),
            version=self.context.version,
            partial_response=partial_response,
            result_index=self.context.result_index,
            num_results=self.context.num_results,
        )

    async def _ws_cache_response_async(
        self, ws_manager, response: str, chunk_pos: int
    ) -> None:
        """Cache current response state (async)."""
        await ws_manager.set_cached_response(
            template_id=str(self.context.template_id),
            version=self.context.version,
            index=self.context.result_index,
            response_data={
                "response": response,
                "error": None,
                "last_chunk_pos": chunk_pos,
            },
        )

    async def _check_stop_streaming_async(self, ws_manager) -> bool:
        """Check if streaming should be stopped (async)."""
        return await ws_manager.is_streaming_stopped(
            str(self.context.template_id), self.context.version
        )

    # -------------------------------------------------------------------------
    # Streaming Error and Stop Handlers
    # -------------------------------------------------------------------------

    def _handle_streaming_stopped(
        self,
        ws_manager: WebSocketManager,
        response_content: str,
        chunk_buffer: str,
        chunk_pos: int,
        chunk: Any,
        start_time: float,
    ) -> HandlerResponse:
        """Handle streaming stopped by user."""
        self.logger.info(
            f"Streaming stopped for template {self.context.template_id}, "
            f"version {self.context.version}"
        )

        # Send remaining buffer
        if chunk_buffer:
            self._ws_send_running(ws_manager, chunk_buffer, chunk_pos)

        # Send stopped message
        self._ws_send_stopped(ws_manager, response_content)

        # Cleanup
        ws_manager.cleanup_streaming_data(
            str(self.context.template_id), self.context.version
        )

        return self._build_stopped_response(response_content, chunk, start_time)

    async def _handle_streaming_stopped_async(
        self,
        ws_manager,
        response_content: str,
        chunk_buffer: str,
        chunk_pos: int,
        chunk: Any,
        start_time: float,
    ) -> HandlerResponse:
        """Handle streaming stopped by user (async)."""
        self.logger.info(
            f"Streaming stopped for template {self.context.template_id}, "
            f"version {self.context.version}"
        )

        # Send remaining buffer
        if chunk_buffer:
            await self._ws_send_running_async(ws_manager, chunk_buffer, chunk_pos)

        # Send stopped message
        await self._ws_send_stopped_async(ws_manager, response_content)

        # Cleanup
        await ws_manager.cleanup_streaming_data(
            str(self.context.template_id), self.context.version
        )

        return self._build_stopped_response(response_content, chunk, start_time)

    def _handle_streaming_error(
        self,
        ws_manager: WebSocketManager,
        error: Exception,
        response_content: str,
        chunk_buffer: str,
        last_sent_chunk_pos: int,
        start_time: float,
    ) -> HandlerResponse:
        """Handle streaming error."""
        self.logger.exception(f"Streaming error: {error}")

        # Re-raise specific errors
        if "value must be a string" in str(error):
            raise Exception(str(error))

        # Send remaining buffer
        if chunk_buffer:
            try:
                self._ws_send_running(ws_manager, chunk_buffer, last_sent_chunk_pos + 1)
            except Exception as buffer_error:
                self.logger.exception(
                    f"Failed to send remaining buffer: {buffer_error}"
                )

        context = self._error_context()
        concise_error = handle_api_error(error, self.logger, context)

        # Send error message
        self._ws_send_error(ws_manager, concise_error)

        # Cache error
        try:
            ws_manager.set_cached_response(
                template_id=str(self.context.template_id),
                version=self.context.version,
                index=self.context.result_index,
                response_data={"response": response_content, "error": concise_error},
            )
        except Exception as cache_error:
            self.logger.exception(f"Failed to cache error response: {cache_error}")

        raise Exception(concise_error) from error

    async def _handle_streaming_error_async(
        self,
        ws_manager,
        error: Exception,
        response_content: str,
        chunk_buffer: str,
        last_sent_chunk_pos: int,
        start_time: float,
    ) -> HandlerResponse:
        """Handle streaming error (async)."""
        self.logger.exception(f"Streaming error: {error}")

        # Re-raise specific errors
        if "value must be a string" in str(error):
            raise Exception(str(error))

        # Send remaining buffer
        if chunk_buffer:
            try:
                await self._ws_send_running_async(
                    ws_manager, chunk_buffer, last_sent_chunk_pos + 1
                )
            except Exception as buffer_error:
                self.logger.exception(
                    f"Failed to send remaining buffer: {buffer_error}"
                )

        context = self._error_context()
        concise_error = handle_api_error(error, self.logger, context)

        # Send error message
        await self._ws_send_error_async(ws_manager, concise_error)

        # Cache error
        try:
            await ws_manager.set_cached_response(
                template_id=str(self.context.template_id),
                version=self.context.version,
                index=self.context.result_index,
                response_data={"response": response_content, "error": concise_error},
            )
        except Exception as cache_error:
            self.logger.exception(f"Failed to cache error response: {cache_error}")

        raise Exception(concise_error) from error

    def _error_context(self) -> ErrorContext:
        return ErrorContext(
            model=self.context.model,
            temperature=self.context.temperature,
            max_tokens=self.context.max_tokens,
            message_count=len(self.context.messages) if self.context.messages else 0,
            output_format=self.context.output_format,
            organization_id=self.context.organization_id,
            workspace_id=self.context.workspace_id,
            template_id=self.context.template_id,
        )

    def _build_stopped_response(
        self, response_content: str, chunk: Any, start_time: float
    ) -> HandlerResponse:
        """Build response for stopped streaming."""
        return HandlerResponse(
            response=response_content,
            start_time=start_time,
            end_time=time.time(),
            model=chunk.model
            if chunk and hasattr(chunk, "model")
            else self.context.model,
            metadata={},
        )

    # -------------------------------------------------------------------------
    # Custom Model Handling
    # -------------------------------------------------------------------------

    def _is_custom_model(self, payload: Dict[str, Any]) -> bool:
        """Check if payload is for a custom model."""
        return payload.get("custom_llm_provider") == "custom"

    def _execute_custom_model(
        self, payload: Dict[str, Any], start_time: float
    ) -> HandlerResponse:
        """
        Execute custom model request by delegating to CustomModelHandler.

        This path is reached when a model is marked as custom in the payload
        but wasn't caught by the factory routing. This delegates to
        CustomModelHandler for proper handling.

        Args:
            payload: LiteLLM payload (unused - CustomModelHandler builds its own)
            start_time: Request start time (unused - CustomModelHandler tracks its own)

        Returns:
            HandlerResponse from CustomModelHandler
        """
        self.logger.info(
            "Delegating custom model to CustomModelHandler",
            model=self.context.model,
        )

        from .custom_model_handler import CustomModelHandler

        custom_handler = CustomModelHandler(self.context)
        return custom_handler.execute_sync(streaming=False)

    def _handle_custom_model_streaming(
        self,
        ws_manager: WebSocketManager,
        payload: Dict[str, Any],
        start_time: float,
    ) -> HandlerResponse:
        """
        Handle custom model in streaming mode.

        Custom models don't support true streaming, so we execute normally
        via CustomModelHandler and send the full response as a single chunk.

        Args:
            ws_manager: WebSocket manager for sending messages
            payload: LiteLLM payload (unused - CustomModelHandler builds its own)
            start_time: Request start time

        Returns:
            HandlerResponse from CustomModelHandler
        """
        self.logger.info(
            "Delegating custom model (streaming mode) to CustomModelHandler",
            model=self.context.model,
        )

        from .custom_model_handler import CustomModelHandler

        custom_handler = CustomModelHandler(self.context)
        response = custom_handler.execute_sync(streaming=False)

        # Send the full response as a single chunk since custom models
        # don't support true streaming
        if response.response:
            self._ws_send_running(ws_manager, response.response, 0)

        # Send completed message
        self._ws_send_completed(ws_manager, response.metadata)

        return response

    async def _handle_custom_model_streaming_async(
        self,
        ws_manager,
        payload: Dict[str, Any],
        start_time: float,
    ) -> HandlerResponse:
        """
        Handle custom model in streaming mode (async).

        Custom models don't support true streaming, so we execute normally
        via CustomModelHandler and send the full response as a single chunk.

        Args:
            ws_manager: WebSocket manager for sending messages
            payload: LiteLLM payload (unused - CustomModelHandler builds its own)
            start_time: Request start time

        Returns:
            HandlerResponse from CustomModelHandler
        """
        self.logger.info(
            "Delegating custom model (async streaming mode) to CustomModelHandler",
            model=self.context.model,
        )

        from .custom_model_handler import CustomModelHandler

        custom_handler = CustomModelHandler(self.context)
        response = await custom_handler.execute_async(streaming=False)

        # Send the full response as a single chunk since custom models
        # don't support true streaming
        if response.response:
            await self._ws_send_running_async(ws_manager, response.response, 0)

        # Send completed message
        await self._ws_send_completed_async(ws_manager, response.metadata)

        return response
