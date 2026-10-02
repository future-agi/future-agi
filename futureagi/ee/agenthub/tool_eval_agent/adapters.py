"""
Tool Call Data Adapters — Factory + Adapter pattern for provider-agnostic tool evaluation.

Each adapter extracts the customer's tool call data from their voice provider
and normalizes it into a list of TranscriptMessage objects.
"""

import json
from abc import ABC, abstractmethod
from typing import Any

import structlog

from simulate.models.test_execution import CallExecution
try:
    from ee.voice.services.types.voice import TranscriptMessage
except ImportError:
    TranscriptMessage = None
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)


class ToolCallDataAdapter(ABC):
    """Extracts customer's tool call data from their voice provider."""

    @abstractmethod
    def get_tool_call_transcript(
        self,
        call_execution: CallExecution,
        customer_call_id: str | None = None,
        api_key: str | None = None,
    ) -> list[TranscriptMessage]:
        """Return the customer's call transcript with tool calls normalized.

        Returns ALL messages (user/assistant/tool_call/tool_call_result)
        so eval agent has full conversation context.
        """
        ...


class VapiToolCallAdapter(ToolCallDataAdapter):
    """Extracts tool call data from customer's VAPI account."""

    def get_tool_call_transcript(
        self,
        call_execution: CallExecution,
        customer_call_id: str | None = None,
        api_key: str | None = None,
    ) -> list[TranscriptMessage]:
        """Fetch and normalize VAPI messages into TranscriptMessage list.

        1. If customer_call_id + api_key: fetch from VAPI API
        2. Else: read from call_execution.provider_call_data["vapi"]["artifact"]["messages"]
        3. Parse VAPI message format → list[TranscriptMessage]
        """
        messages = self._get_raw_messages(call_execution, customer_call_id, api_key)
        return self._normalize_messages(messages)

    def _get_raw_messages(
        self,
        call_execution: CallExecution,
        customer_call_id: str | None,
        api_key: str | None,
    ) -> list[dict[str, Any]]:
        """Get raw VAPI messages from API or stored provider_call_data."""
        # If we have customer credentials, fetch from VAPI API
        if customer_call_id and api_key:
            from ee.voice.services.vapi_service import VapiService

            vapi_service = VapiService(api_key=api_key)
            vapi_data = vapi_service.fetch_call(customer_call_id)
            artifact = vapi_data.get("artifact", {})
            return artifact.get("messages", [])

        # Fall back to stored provider_call_data
        provider_data = call_execution.provider_call_data or {}
        vapi_data = provider_data.get(ProviderChoices.VAPI, {})

        if not vapi_data:
            # Try fetching from VAPI API using our call ID
            if call_execution.service_provider_call_id:
                try:
                    from ee.voice.services.vapi_service import VapiService

                    vapi_service = VapiService()
                    vapi_data = vapi_service.fetch_call(
                        call_execution.service_provider_call_id
                    )
                except Exception as e:
                    logger.error(
                        "failed_to_fetch_vapi_data",
                        error=str(e),
                        call_id=str(call_execution.id),
                    )
                    return []

        artifact = vapi_data.get("artifact", {})
        return artifact.get("messages", [])

    def _normalize_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[TranscriptMessage]:
        """Convert VAPI message format to TranscriptMessage list.

        VAPI message mapping:
        - {"role": "tool_calls", "toolCalls": [{id, function: {name, args}}], "time": T}
          → One TranscriptMessage per tool call: role="tool_call", content=name
        - {"role": "tool_call_result", "toolCallId": id, "name": N, "result": R, "time": T}
          → role="tool_call_result", content=R, tool_call_id=id
        - {"role": "user"/"bot"/"assistant", "message": text, "time": T}
          → role=role, content=text
        """
        result: list[TranscriptMessage] = []

        for message in messages:
            role = message.get("role", "")
            time_ms = message.get("time", 0)
            time_sec = time_ms / 1000.0

            if role == "tool_calls" and message.get("toolCalls"):
                # One TranscriptMessage per tool call in the array
                for tool_call in message.get("toolCalls", []):
                    function_info = tool_call.get("function", {})
                    arguments_raw = function_info.get("arguments", "{}")

                    # Parse arguments
                    if isinstance(arguments_raw, str):
                        try:
                            arguments = json.loads(arguments_raw)
                        except json.JSONDecodeError:
                            arguments = {"raw": arguments_raw}
                    elif isinstance(arguments_raw, dict):
                        arguments = arguments_raw
                    else:
                        arguments = {}

                    result.append(
                        TranscriptMessage(
                            role="tool_call",
                            content=function_info.get("name", "unknown"),
                            time=time_sec,
                            tool_call_id=tool_call.get("id"),
                            arguments=arguments,
                        )
                    )

            elif role == "tool_call_result":
                result_content = message.get("result", "")
                if not isinstance(result_content, str):
                    result_content = json.dumps(result_content)

                result.append(
                    TranscriptMessage(
                        role="tool_call_result",
                        content=result_content,
                        time=time_sec,
                        tool_call_id=message.get("toolCallId"),
                    )
                )

            elif role in ("user", "bot", "assistant", "system"):
                content = message.get("message", "")
                result.append(
                    TranscriptMessage(
                        role=role,
                        content=content,
                        time=time_sec,
                    )
                )

        # Sort by time to maintain chronological order
        result.sort(key=lambda m: m.time)
        return result


class ChatToolCallAdapter:
    """Extracts tool call data from ChatMessageModel (TEXT agents).

    Not provider-specific — reads from our DB.
    Replaces ToolEvalAgent._get_chat_data_from_database() + _extract_tool_calls().
    """

    def get_tool_call_transcript(
        self,
        call_execution: CallExecution,
    ) -> list[TranscriptMessage]:
        """Read ChatMessageModel content arrays and normalize to TranscriptMessage list."""
        from simulate.models.chat_message import ChatMessageModel

        # Get assistant messages for this call execution
        chat_messages = ChatMessageModel.objects.filter(
            call_execution=call_execution,
            role=ChatMessageModel.RoleChoices.ASSISTANT,
            deleted=False,
        ).order_by("created_at")

        if not chat_messages.exists():
            logger.warning(
                "no_chat_messages_found",
                call_execution_id=str(call_execution.id),
            )
            return []

        first_message = chat_messages.first()
        start_time = first_message.created_at if first_message else None

        result: list[TranscriptMessage] = []
        message_idx = 0

        for chat_msg in chat_messages:
            content_array = chat_msg.content
            if not content_array or not isinstance(content_array, list):
                continue

            for msg_dict in content_array:
                if not isinstance(msg_dict, dict):
                    continue

                # Calculate time in seconds from start
                if start_time:
                    time_delta = chat_msg.created_at - start_time
                    time_sec = time_delta.total_seconds() + (message_idx * 0.001)
                else:
                    time_sec = float(message_idx)

                role = msg_dict.get("role", "unknown")

                # Handle tool calls in assistant messages
                tool_calls = msg_dict.get("tool_calls", [])
                if tool_calls:
                    for tool_call in tool_calls:
                        if isinstance(tool_call, dict):
                            tool_call_dict = tool_call
                        else:
                            tool_call_dict = (
                                dict(tool_call)
                                if hasattr(tool_call, "__dict__")
                                else {}
                            )

                        function_info = tool_call_dict.get("function", {})
                        if isinstance(function_info, dict):
                            function_dict = function_info
                        else:
                            function_dict = (
                                dict(function_info)
                                if hasattr(function_info, "__dict__")
                                else {}
                            )

                        arguments_raw = function_dict.get("arguments", "{}")
                        if isinstance(arguments_raw, str):
                            try:
                                arguments = json.loads(arguments_raw)
                            except json.JSONDecodeError:
                                arguments = {"raw": arguments_raw}
                        elif isinstance(arguments_raw, dict):
                            arguments = arguments_raw
                        else:
                            arguments = {}

                        result.append(
                            TranscriptMessage(
                                role="tool_call",
                                content=function_dict.get("name", "unknown"),
                                time=time_sec,
                                tool_call_id=tool_call_dict.get(
                                    "id", f"tool_call_{message_idx}"
                                ),
                                arguments=arguments,
                            )
                        )

                # Handle tool results
                if role == "tool" and msg_dict.get("tool_call_id"):
                    content = msg_dict.get("content", "")
                    result.append(
                        TranscriptMessage(
                            role="tool_call_result",
                            content=content,
                            time=time_sec,
                            tool_call_id=msg_dict.get("tool_call_id"),
                        )
                    )

                # Handle regular messages
                content = msg_dict.get("content", "")
                if content and role in ("user", "assistant"):
                    result.append(
                        TranscriptMessage(
                            role="bot" if role == "assistant" else role,
                            content=content,
                            time=time_sec,
                        )
                    )

                message_idx += 1

        result.sort(key=lambda m: m.time)
        return result


def get_tool_call_adapter(provider: str) -> ToolCallDataAdapter:
    """Factory — returns the right adapter for the customer's voice provider."""
    adapters = {
        ProviderChoices.VAPI: VapiToolCallAdapter,
        # Future: ProviderChoices.SOME_OTHER → SomeOtherToolCallAdapter
    }
    adapter_cls = adapters.get(provider)
    if not adapter_cls:
        raise ValueError(f"No tool call adapter for provider: {provider}")
    return adapter_cls()
