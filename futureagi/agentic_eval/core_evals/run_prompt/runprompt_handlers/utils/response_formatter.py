"""
Response formatting utilities for model handlers.

This module standardizes response formatting for LLM handlers:
- LLM completion responses
- Streaming responses with tool call accumulation
- Metadata building from streaming chunks

All formatters return consistent metadata and value_info structures.
"""

import ast
import json
import time
from dataclasses import dataclass
from typing import Any

import structlog

from agentic_eval.core.utils.json_utils import strip_code_fence
from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost

logger = structlog.get_logger(__name__)

# What a tenant sees when a custom model's endpoint answers HTTP 200 with
# something other than an answer. Never the body, its text or the URL: the
# endpoint could be an internal service (see tfc.utils.ssrf_guard). Each is
# under 200 characters, so the run-prompt error path shows it whole.
ENDPOINT_ANSWERED_ERROR_MESSAGE = (
    "The model's endpoint answered with an error instead of an answer. Check "
    "the model name and key for this model in Settings > AI Providers, then "
    "run again."
)
NOT_CHAT_COMPLETIONS_MESSAGE = (
    "The model's endpoint answered, but not in the chat-completions format. "
    "Its API Base URL must be the full chat-completions URL, such as "
    "https://your-host/v1/chat/completions."
)


class UnreadableCompletionError(RuntimeError):
    """A custom endpoint's 200 answer that holds no chat completion."""


@dataclass
class ChatCompletionBody:
    """The parts of an OpenAI-compatible chat-completions body a run uses."""

    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning: str | None = None
    usage: dict[str, Any] | None = None


@dataclass
class UsageInfo:
    """
    Token usage information for LLM responses.

    Tracks prompt tokens, completion tokens, and total tokens
    for cost calculation and usage reporting.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def to_cost_dict(self) -> dict[str, Any]:
        """Convert to dictionary format expected by calculate_total_cost."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class ResponseFormatter:
    """
    Formats LLM model responses into standardized structures.

    Used by LLMHandler for:
    - Standard completion responses
    - Streaming response metadata
    - Tool call accumulation during streaming
    """

    @staticmethod
    def _build_value_info(
        response_content: Any,
        runtime: float,
        model: str,
        metadata: dict[str, Any] | None = None,
        failure: str | None = None,
    ) -> dict[str, Any]:
        """
        Build a standardized value_info dictionary.

        This is the single source of truth for value_info structure,
        ensuring consistency across all response formatters.

        Args:
            response_content: The response data to include
            runtime: Execution time in milliseconds
            model: Model name used
            metadata: Optional metadata dict (usage, cost, etc.)
            failure: Optional error message if request failed

        Returns:
            Standardized value_info dictionary
        """
        return {
            "name": None,
            "data": {"response": response_content},
            "failure": failure,
            "runtime": runtime,
            "model": model,
            "metrics": [],
            "metadata": metadata or {},
            "output": None,
        }

    @staticmethod
    def extract_reasoning_content(response, show_reasoning_process: bool) -> str | None:
        """Extract reasoning/thinking content and prepend to response if enabled."""
        if not show_reasoning_process:
            return None

        message = response.choices[0].message
        reasoning_text = None

        # Try reasoning_content (o1/o3 models)
        if hasattr(message, "reasoning_content") and message.reasoning_content:
            reasoning_text = message.reasoning_content
        # Try thinking_blocks (Anthropic)
        elif hasattr(message, "thinking_blocks") and message.thinking_blocks:
            reasoning_text = "\n\n".join(
                block.get("thinking", "") for block in message.thinking_blocks
            )

        return reasoning_text

    @staticmethod
    def format_llm_response(
        response: Any,
        model: str,
        start_time: float,
        tools: list[dict] | None = None,
        output_format: str | None = None,
        show_reasoning_process: bool = False,
        organization_id: str | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """
        Format a standard LLM completion response.

        Args:
            response: LiteLLM ModelResponse object
            model: Model name used for the completion
            start_time: Request start time (time.time())
            tools: Optional list of tools if tool calling was used
            output_format: Optional output format specification
            organization_id: The caller's organization, so a custom model of
                that organization is priced at its saved price

        Returns:
            Tuple of (formatted_response, value_info dict)
        """
        end_time = time.time()
        completion_time = (end_time - start_time) * 1000

        # Extract response content
        response_content = response.choices[0].message.content
        response_model = response.model

        # Extract usage information
        usage = UsageInfo(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

        # Build metadata
        metadata = {
            "usage": usage.to_dict(),
            "cost": calculate_total_cost(
                response_model,
                usage.to_cost_dict(),
                organization_id=organization_id,
                custom_model_name=model,
            ),
            "response_time": completion_time,
            "response_time_unit": "ms",
        }

        # Handle tool calls
        formatted_response = response_content
        if tools and response.choices[0].message.tool_calls:
            tool_calls_list = ResponseFormatter._format_tool_calls(
                response.choices[0].message.tool_calls
            )
            formatted_response = json.dumps(tool_calls_list)
        elif output_format:
            # Apply output format transformation if specified
            formatted_response = ResponseFormatter._apply_output_format(
                response, output_format
            )
        else:
            formatted_response = response_content

        # Extract reasoning content for value_info (old behavior: thinking goes to value_info, not main response)
        reasoning_text = ResponseFormatter.extract_reasoning_content(
            response, show_reasoning_process
        )
        response_content_with_thinking = response_content
        if reasoning_text:
            response_content_with_thinking = (
                f"<thinking>\n{reasoning_text}\n</thinking>\n\n{response_content}"
            )

        value_info = ResponseFormatter._build_value_info(
            response_content=response_content_with_thinking,
            runtime=completion_time,
            model=response_model,
            metadata=metadata,
        )

        return formatted_response, value_info

    @staticmethod
    def _format_tool_calls(tool_calls: list[Any]) -> list[dict[str, Any]]:
        """
        Format tool calls from LiteLLM response to list of dicts.

        Args:
            tool_calls: List of tool call objects from response

        Returns:
            List of tool call dictionaries
        """
        return [
            {
                "id": tool_call.id,
                "type": tool_call.type,
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
            for tool_call in tool_calls
        ]

    @staticmethod
    def parse_chat_completion_body(body_text: str) -> ChatCompletionBody | None:
        """
        Read the answer out of a custom endpoint's chat-completions body.

        Custom models are called over plain HTTP, so their answer arrives as
        the body text, not a LiteLLM response object. This reads it the way
        format_llm_response reads ``response.choices[0].message``.

        Args:
            body_text: The endpoint's response body, decoded

        Returns:
            None when the body is not JSON, or is a JSON number, true/false or
            null: the caller uses the body text as the answer, so plain-text
            endpoints keep working. A JSON string is the answer text. A body
            with ``choices`` gives choices[0]'s message text (text parts
            joined; the completions-style ``text`` when the message has no
            content), its tool calls, its reasoning and the top-level usage.

        Any other JSON (an ``error`` object, an endpoint's own shape) returns
        None as well, so the call succeeds with the body as the answer, as it
        did before this parser existed: a custom model that works today keeps
        working. A text-generation body (``{"generated_text": "..."}`` or a
        one-item list of it) is read as its text.
        """
        not_json = object()
        try:
            data = json.loads(body_text)
        except (TypeError, ValueError):
            data = not_json
        # Raised outside the except block: a JSONDecodeError carries the body.
        if data is not_json or data is None or isinstance(data, (bool, int, float)):
            return None
        if isinstance(data, str):
            return ChatCompletionBody(content=data)
        if isinstance(data, dict) and isinstance(data.get("choices"), list):
            choices = data["choices"]
            if choices and isinstance(choices[0], dict):
                return ResponseFormatter._read_first_choice(data)
            return None
        generated = data[0] if isinstance(data, list) and len(data) == 1 else data
        if isinstance(generated, dict) and isinstance(
            generated.get("generated_text"), str
        ):
            return ChatCompletionBody(content=generated["generated_text"])
        return None

    @staticmethod
    def _read_first_choice(data: dict[str, Any]) -> ChatCompletionBody:
        """The answer parts of ``data["choices"][0]`` and the body's usage."""
        choice = data["choices"][0]
        message = choice.get("message")
        if not isinstance(message, dict):
            message = {}

        content = ResponseFormatter._message_text(message.get("content"))
        if content is None and isinstance(choice.get("text"), str):
            content = choice["text"]

        tool_calls = message.get("tool_calls")
        tool_calls = (
            ResponseFormatter._format_tool_call_dicts(tool_calls)
            if isinstance(tool_calls, list)
            else []
        )
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        usage = data.get("usage")
        return ChatCompletionBody(
            content=content,
            tool_calls=tool_calls or None,
            reasoning=reasoning if isinstance(reasoning, str) and reasoning else None,
            usage=usage if isinstance(usage, dict) else None,
        )

    @staticmethod
    def _message_text(content: Any) -> str | None:
        """A message's content as text: a list of parts is the text of its
        text parts, joined; None (no content) stays None."""
        if content is None or isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for part in content:
                if isinstance(part, str):
                    texts.append(part)
                elif (
                    isinstance(part, dict)
                    and part.get("type") in ("text", "output_text")
                    and isinstance(part.get("text"), str)
                ):
                    texts.append(part["text"])
            return "".join(texts)
        if isinstance(content, (int, float)) and not isinstance(content, bool):
            return str(content)
        return None

    @staticmethod
    def _format_tool_call_dicts(tool_calls: list[Any]) -> list[dict[str, Any]]:
        """Tool calls from a JSON body, in the _format_tool_calls shape."""
        formatted = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                function = {}
            formatted.append(
                {
                    "id": tool_call.get("id"),
                    "type": tool_call.get("type") or "function",
                    "function": {
                        "name": function.get("name"),
                        "arguments": function.get("arguments"),
                    },
                }
            )
        return formatted

    @staticmethod
    def _apply_output_format(response: Any, output_format: str) -> Any:
        """Parse `output_format` to a list/dict/float, else return raw content."""
        return ResponseFormatter.format_output_text(
            response.choices[0].message.content, output_format
        )

    @staticmethod
    def format_output_text(content: Any, output_format: str | None) -> Any:
        """``content`` (the model's text) as `output_format` asks: a list,
        dict or float when it parses as one, else the text unchanged."""
        if output_format == "array":
            parsed = ResponseFormatter._parse_structured(content)
            return parsed if isinstance(parsed, list) else content
        if output_format == "object":
            parsed = ResponseFormatter._parse_structured(content)
            return parsed if isinstance(parsed, dict) else content
        if output_format == "number":
            try:
                return float(strip_code_fence(content))
            except (TypeError, ValueError):
                return content
        return content

    @staticmethod
    def _parse_structured(content: Any) -> Any:
        """
        Best-effort parse of model text into a Python object.

        Unwraps a Markdown code fence some models emit, then tries JSON, then a
        Python literal. Returns the raw content unchanged when neither parse
        succeeds — the caller decides whether the resulting type is acceptable.
        """
        if not isinstance(content, str):
            return content
        unfenced = strip_code_fence(content)
        try:
            return json.loads(unfenced)
        except (json.JSONDecodeError, ValueError):
            try:
                return ast.literal_eval(unfenced)
            except (ValueError, SyntaxError):
                logger.warning("response_format_parse_failed", preview=unfenced[:200])
                return content

    @staticmethod
    def build_streaming_metadata(
        chunk: Any,
        start_time: float,
        model: str | None = None,
        organization_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Build metadata from the final streaming chunk.

        Args:
            chunk: Final chunk from streaming response with usage info
            start_time: Request start time
            model: The model name the call was made with
            organization_id: The caller's organization, so a custom model of
                that organization is priced at its saved price

        Returns:
            Metadata dictionary; response_time is in milliseconds and
            response_time_unit says so
        """
        end_time = time.time()
        # Milliseconds, like format_llm_response and every other producer;
        # dataset cells and experiments read ms. Prompts runs saved before
        # this stored seconds with no unit, and the time chip shows those
        # as seconds.
        completion_time = (end_time - start_time) * 1000

        if not hasattr(chunk, "usage") or chunk.usage is None:
            return {
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "cost": {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0},
                "response_time": completion_time,
                "response_time_unit": "ms",
            }

        usage_dict = {
            "prompt_tokens": chunk.usage.prompt_tokens,
            "completion_tokens": chunk.usage.completion_tokens,
            "total_tokens": chunk.usage.total_tokens,
        }

        return {
            "usage": usage_dict,
            "cost": calculate_total_cost(
                chunk.model,
                dict(chunk.usage),
                organization_id=organization_id,
                custom_model_name=model,
            ),
            "response_time": completion_time,
            "response_time_unit": "ms",
        }

    @staticmethod
    def accumulate_tool_calls(
        existing_tool_calls: list[dict[str, Any]],
        chunk_tool_calls: list[Any],
    ) -> list[dict[str, Any]]:
        """
        Accumulate tool calls from streaming chunks.

        Tool calls come in incrementally during streaming - this method
        accumulates them into complete tool call objects.

        Args:
            existing_tool_calls: Previously accumulated tool calls
            chunk_tool_calls: Tool calls from current chunk

        Returns:
            Updated list of tool calls
        """
        tool_calls = existing_tool_calls.copy()

        for tool_call in chunk_tool_calls:
            # Extend list if needed
            while len(tool_calls) <= tool_call.index:
                tool_calls.append(
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                )

            tc = tool_calls[tool_call.index]

            if tool_call.id:
                tc["id"] += tool_call.id
            if tool_call.function.name:
                tc["function"]["name"] += tool_call.function.name
            if tool_call.function.arguments:
                tc["function"]["arguments"] += tool_call.function.arguments

        return tool_calls
