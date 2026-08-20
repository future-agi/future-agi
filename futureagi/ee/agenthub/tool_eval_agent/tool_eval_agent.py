"""
Tool Evaluation Agent
Evaluates tool calls in conversation transcripts for correctness, timing, and result accuracy.

Provider-agnostic: consumes normalized TranscriptMessage objects via adapters.
Legacy VAPI-specific methods are preserved for backward compatibility.
"""

import json
import traceback
from typing import Dict, List, Any, Optional
from datetime import datetime

from ee.agenthub.tool_eval_agent.prompts import (
    TOOL_EVAL_PLANNING_PROMPT,
    TOOL_EVAL_ANALYSIS_PROMPT,
    TOOL_EVAL_VALIDATION_PROMPT,
)
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
import structlog

logger = structlog.get_logger(__name__)
from simulate.models.test_execution import CallExecution, CallTranscript
from simulate.models.chat_message import ChatMessageModel
try:
    from ee.voice.services.types.voice import TranscriptMessage
except ImportError:
    TranscriptMessage = None
from tracer.models.observability_provider import ProviderChoices


class ToolEvalAgent:
    """
    Agent to evaluate tool calls in conversation transcripts.
    Analyzes tool selection, timing, parameters, and results.

    Primary entry point: evaluate_tool_calls(messages) — consumes
    provider-agnostic TranscriptMessage objects from adapters.
    """

    def __init__(
        self,
        model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name,
        temperature=ModelConfigs.VERTEX_GEMINI_2_5_PRO.temperature,
        max_tokens=ModelConfigs.VERTEX_GEMINI_2_5_PRO.max_tokens,
        provider=ModelConfigs.VERTEX_GEMINI_2_5_PRO.provider,
        llm=None,
    ):
        """
        Initialize the ToolEvalAgent.

        Args:
            model_name: LLM model name to use
            temperature: LLM temperature
            max_tokens: Maximum tokens for LLM response
            llm: Optional pre-configured LLM instance
        """
        if llm:
            self.llm = llm
        else:
            self.llm = LLM(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
            )

    def evaluate_tool_calls(
        self, messages: list[TranscriptMessage]
    ) -> Dict[str, Any]:
        """Evaluate tool calls from normalized transcript messages.

        This is the primary, provider-agnostic entry point. Adapters produce
        the TranscriptMessage list; this method builds the data structures
        that _evaluate_single_tool_call expects.

        Returns:
            Dict with "tool_calls_data" (list of dicts) and
            "conversation_context" (list of dicts).
        """
        tool_call_msgs = [m for m in messages if m.role == "tool_call"]
        result_msgs = {
            m.tool_call_id: m
            for m in messages
            if m.role == "tool_call_result"
        }

        if not tool_call_msgs:
            return {"tool_calls_data": [], "conversation_context": []}

        # Build conversation context (all messages as dicts)
        conversation_context = [
            {
                "timestamp_ms": int(m.time * 1000),
                "speaker_role": m.role,
                "content": m.content,
            }
            for m in messages
        ]

        # Build tool_calls_data dicts
        tool_calls_data = []
        for tc in tool_call_msgs:
            result_msg = result_msgs.get(tc.tool_call_id)
            timestamp_ms = int(tc.time * 1000)
            result_timestamp_ms = (
                int(result_msg.time * 1000) if result_msg else None
            )

            tool_calls_data.append(
                {
                    "tool_call_id": tc.tool_call_id,
                    "tool_name": tc.content,
                    "arguments": tc.arguments or {},
                    "timestamp_ms": timestamp_ms,
                    "seconds_from_start": tc.time,
                    "result": (
                        self._parse_result(result_msg.content)
                        if result_msg
                        else None
                    ),
                    "result_timestamp_ms": result_timestamp_ms,
                    "context_before": self._get_context_before(
                        conversation_context, timestamp_ms
                    ),
                    "context_after": self._get_context_after(
                        conversation_context,
                        result_timestamp_ms or timestamp_ms,
                        limit=3,
                    ),
                }
            )

        return {
            "tool_calls_data": tool_calls_data,
            "conversation_context": conversation_context,
        }

    def evaluate_tools_by_call_id(self, vapi_call_id: str) -> Dict[str, Any]:
        """
        Evaluate all tool calls for a given VAPI call ID.
        Fetches data directly from VAPI API if CallExecution doesn't exist.

        Args:
            vapi_call_id: The VAPI call ID to evaluate

        Returns:
            Dict containing evaluation results for all tool calls
        """
        try:
            # Try to get call execution from database first
            call_execution = self._get_call_execution(vapi_call_id)

            if call_execution:
                # Use existing flow with database
                logger.info(
                    f"Found CallExecution for {vapi_call_id}, using database data"
                )
                call_data = self._get_call_data(call_execution)
                call_execution_id = str(call_execution.id)
            else:
                # Fetch directly from VAPI API
                logger.info(
                    f"No CallExecution found for {vapi_call_id}, fetching directly from VAPI API"
                )
                call_data = self._get_call_data_from_provider(vapi_call_id)
                call_execution_id = None

            # Extract tool calls from messages
            tool_calls_data = self._extract_tool_calls(call_data)

            if not tool_calls_data:
                return {"total_tool_calls": 0, "tool_evaluations": []}

            # Evaluate each tool call
            evaluations = []
            for tool_call in tool_calls_data:
                try:
                    evaluation = self._evaluate_single_tool_call(
                        tool_call=tool_call,
                        conversation_context=call_data["conversation_context"],
                        all_tool_calls=tool_calls_data,
                    )
                    evaluations.append(evaluation)
                except Exception as e:
                    logger.error(
                        f"Error evaluating tool call {tool_call.get('tool_call_id')}: {str(e)}"
                    )
                    traceback.print_exc()
                    evaluations.append(
                        {
                            "tool_call_id": tool_call.get("tool_call_id"),
                            "tool_name": tool_call.get("tool_name"),
                            "result": False,
                            "summary": f"Evaluation error: {str(e)}",
                        }
                    )

            # Generate overall summary
            summary = self._generate_summary(evaluations, tool_calls_data)

            # Simplify output structure
            simplified_evaluations = []
            for eval_result in evaluations:
                simplified_evaluations.append(
                    {
                        "tool_call_id": eval_result.get("tool_call_id"),
                        "tool_name": eval_result.get("tool_name"),
                        "result": eval_result.get("result", False),  # Boolean pass/fail
                        "summary": eval_result.get("summary", ""),
                    }
                )

            return {
                "total_tool_calls": len(tool_calls_data),
                "tool_evaluations": simplified_evaluations,
            }

        except Exception as e:
            logger.error(f"Error evaluating tools for call {vapi_call_id}: {str(e)}")
            traceback.print_exc()
            return {
                "vapi_call_id": vapi_call_id,
                "error": str(e),
                "traceback": traceback.format_exc(),
            }

    def _get_call_execution(self, vapi_call_id: str) -> Optional[CallExecution]:
        """Get CallExecution from database by service_provider_call_id."""
        try:
            return CallExecution.objects.get(service_provider_call_id=vapi_call_id)
        except CallExecution.DoesNotExist:
            return None
        except Exception as e:
            logger.error(f"Error fetching call execution: {str(e)}")
            return None

    def _get_call_data(self, call_execution: CallExecution) -> Dict[str, Any]:
        """
        Get complete call data including transcript and messages from database.

        Args:
            call_execution: CallExecution instance

        Returns:
            Dict containing conversation context and messages
        """
        # Get transcripts from database
        transcripts = CallTranscript.objects.filter(
            call_execution=call_execution
        ).order_by("start_time_ms")

        # Format conversation context
        conversation_context = []
        for transcript in transcripts:
            conversation_context.append(
                {
                    "timestamp_ms": transcript.start_time_ms,
                    "speaker_role": transcript.speaker_role,
                    "content": transcript.content,
                }
            )

        # Get VAPI call data for detailed message information
        # provider_call_data is a dict keyed by provider (e.g., "vapi")
        vapi_data = {}
        if call_execution.provider_call_data:
            vapi_data = call_execution.provider_call_data.get(ProviderChoices.VAPI, {})

        if not vapi_data:
            # Fetch from VAPI API if not stored
            try:
                vapi_data = self.vapi_service.get_call(
                    call_execution.service_provider_call_id
                )
            except Exception as e:
                logger.error(f"Error fetching VAPI data: {str(e)}")

        return {
            "conversation_context": conversation_context,
            "vapi_data": vapi_data,
            "call_metadata": call_execution.call_metadata
            if hasattr(call_execution, "call_metadata")
            else {},
        }

    def _get_call_data_from_provider(
        self, vapi_call_id: str, api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get complete call data directly from VAPI API (no database dependency).

        Args:
            vapi_call_id: VAPI call ID
            api_key: Optional API key for VAPI access (if different from default)

        Returns:
            Dict containing conversation context and messages
        """
        try:
            # Fetch call data from VAPI API
            logger.info(f"Fetching call data from VAPI API for {vapi_call_id}")

            # Use provided API key or fall back to default vapi_service
            if api_key:
                vapi_service = VapiService(api_key=api_key)
                vapi_data = vapi_service.get_call(vapi_call_id)
            else:
                vapi_data = self.vapi_service.get_call(vapi_call_id)

            # Extract messages from artifact
            artifact = vapi_data.get("artifact", {})
            messages = artifact.get("messages", [])

            # Build conversation context from messages
            conversation_context = []
            for message in messages:
                role = message.get("role", "unknown")
                time_ms = message.get("time", 0)

                # Map VAPI roles to our format
                speaker_role = self._map_vapi_role(role)

                # Get content based on role
                if role == "tool_calls":
                    # For tool calls, store the raw tool call data
                    content = json.dumps(message.get("toolCalls", []))
                elif role == "tool_call_result":
                    # For tool results, store the result
                    content = message.get("result", "")
                else:
                    # For user/bot messages
                    content = message.get("message", "")

                conversation_context.append(
                    {
                        "timestamp_ms": time_ms,
                        "speaker_role": speaker_role,
                        "content": content,
                    }
                )

            # IMPORTANT: Sort by timestamp to maintain correct chronological order
            conversation_context.sort(key=lambda x: x["timestamp_ms"])

            logger.info(
                f"Built conversation context with {len(conversation_context)} messages (sorted by timestamp)"
            )

            return {
                "conversation_context": conversation_context,
                "vapi_data": vapi_data,
                "call_metadata": vapi_data.get("metadata", {}),
            }

        except Exception as e:
            logger.error(f"Error fetching call data from VAPI: {str(e)}")
            raise ValueError(f"Failed to fetch call data from VAPI API: {str(e)}")

    def _map_vapi_role(self, role: str) -> str:
        """Map VAPI role to CallTranscript speaker role format."""
        role_mapping = {
            "user": "user",
            "bot": "bot",
            "assistant": "assistant",
            "system": "system",
            "tool_calls": "tool_calls",
            "tool_call_result": "tool_call_result",
            "tool": "tool_call_result",
        }
        return role_mapping.get(role, "unknown")

    def _extract_tool_calls(self, call_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract all tool calls from the conversation.
        Parses VAPI transcript format with toolCalls and tool_call_result roles.

        Args:
            call_data: Complete call data with conversation context

        Returns:
            List of tool call dictionaries with context
        """
        tool_calls = []

        # Get messages from VAPI artifact
        vapi_data = call_data.get("vapi_data", {})
        artifact = vapi_data.get("artifact", {})
        messages = artifact.get("messages", [])

        # Build conversation context for each tool call
        conversation_context = call_data.get("conversation_context", [])

        # Process messages to find tool calls and their results
        tool_call_map = {}

        for idx, message in enumerate(messages):
            role = message.get("role", "")
            time_ms = message.get("time", 0)

            # Handle messages with role "tool_calls" containing toolCalls array
            # Format: {"role": "tool_calls", "toolCalls": [...], "time": ..., "secondsFromStart": ...}
            if role == "tool_calls" and message.get("toolCalls"):
                tool_call_list = message.get("toolCalls", [])

                for tool_call in tool_call_list:
                    tool_call_id = tool_call.get("id", f"tool_call_{idx}")
                    function_info = tool_call.get("function", {})

                    # Get conversation context up to this point (pass milliseconds directly)
                    context_before = self._get_context_before(
                        conversation_context, time_ms
                    )

                    tool_call_data = {
                        "tool_call_id": tool_call_id,
                        "tool_name": function_info.get("name", "unknown"),
                        "arguments": self._parse_arguments(
                            function_info.get("arguments", "{}")
                        ),
                        "timestamp_ms": time_ms,
                        "seconds_from_start": message.get(
                            "secondsFromStart", time_ms / 1000
                        ),
                        "context_before": context_before,
                        "message_index": idx,
                        "result": None,
                        "result_timestamp_ms": None,
                        "context_after": [],
                    }

                    tool_call_map[tool_call_id] = tool_call_data

            # Handle tool result messages with role "tool_call_result"
            # Format: {"role": "tool_call_result", "name": "...", "result": "...", "toolCallId": "...", "time": ...}
            elif role == "tool_call_result":
                tool_call_id = message.get("toolCallId")

                if tool_call_id and tool_call_id in tool_call_map:
                    result_content = message.get("result", "")
                    # Try to parse result as JSON if it's a string
                    parsed_result = self._parse_result(result_content)

                    tool_call_map[tool_call_id]["result"] = parsed_result
                    tool_call_map[tool_call_id]["result_timestamp_ms"] = time_ms
                    tool_call_map[tool_call_id]["result_seconds_from_start"] = (
                        message.get("secondsFromStart", time_ms / 1000)
                    )
                    tool_call_map[tool_call_id]["tool_name_from_result"] = message.get(
                        "name", tool_call_map[tool_call_id]["tool_name"]
                    )
                    tool_call_map[tool_call_id]["context_after"] = (
                        self._get_context_after(
                            conversation_context,
                            time_ms,  # Pass milliseconds directly
                            limit=3,
                        )
                    )

        # Convert map to list
        tool_calls = list(tool_call_map.values())

        return tool_calls

    def _get_chat_data_from_database(
        self, call_execution: CallExecution
    ) -> Dict[str, Any]:
        """
        Get complete chat data from ChatMessageModel in database.
        Fetches user role messages and extracts tool data from their content field (MessageArray with VAPI schema).

        Args:
            call_execution: CallExecution instance to fetch chat messages for

        Returns:
            Dict containing conversation context and messages in VAPI-like format
        """
        try:
            # Fetch user role messages from database
            logger.info(
                f"Fetching chat data from database for call execution {call_execution.id}"
            )

            # Get all chat messages with role=USER for this call execution, ordered by creation time
            user_chat_messages = ChatMessageModel.objects.filter(
                call_execution=call_execution,
                role=ChatMessageModel.RoleChoices.ASSISTANT,
                deleted=False,
            ).order_by("created_at")

            if not user_chat_messages.exists():
                logger.warning(
                    f"No user chat messages found for call execution {call_execution.id}"
                )

            # Get the start time from the first message for calculating relative timestamps
            first_message = user_chat_messages.first()
            start_time = first_message.created_at if first_message else None

            # Build conversation context from content field (MessageArray)
            conversation_context = []
            messages_list = []
            message_idx = 0

            # Process all user messages and extract MessageArray from content field
            for chat_msg in user_chat_messages:
                logger.info(f"Chat message: {chat_msg} FOR MESSAGE {chat_msg.id}")
                # Get the content field which contains the MessageArray (list of ChatMessage objects)
                content_array = chat_msg.content
                logger.info(f"Content array: {content_array} FOR MESSAGE {chat_msg.id}")

                if not content_array or not isinstance(content_array, list):
                    logger.warning(
                        f"Chat message {chat_msg.id} has no content array or invalid format"
                    )
                    continue

                # Process each message in the content array (MessageArray)
                for msg_dict in content_array:
                    if not isinstance(msg_dict, dict):
                        continue

                    # Convert created_at to milliseconds timestamp (use chat_msg timestamp as base)
                    if start_time:
                        time_delta = chat_msg.created_at - start_time
                        time_ms = (
                            int(time_delta.total_seconds() * 1000) + message_idx
                        )  # Add small offset for ordering
                    else:
                        time_ms = message_idx * 1000

                    role = msg_dict.get("role", "unknown")

                    # Map chat roles to our format
                    speaker_role = self._map_vapi_role(role)

                    # Get content from message
                    content = msg_dict.get("content", "")

                    # Handle tool calls in assistant messages
                    tool_calls = msg_dict.get("tool_calls", [])

                    logger.info(
                        f"Tool calls: {tool_calls} FOR MESSAGE {chat_msg.id}, content: {content}, speaker_role: {speaker_role}, role: {role}"
                    )
                    if tool_calls:
                        # Create tool_calls message entry (similar to voice format)

                        tool_calls_list = []
                        for tool_call in tool_calls:
                            # tool_calls is a list of ToolCall objects (dicts)
                            if isinstance(tool_call, dict):
                                tool_call_dict = tool_call
                            else:
                                tool_call_dict = (
                                    dict(tool_call)
                                    if hasattr(tool_call, "__dict__")
                                    else {}
                                )

                            # Extract function info
                            function_info = tool_call_dict.get("function", {})
                            if isinstance(function_info, dict):
                                function_dict = function_info
                            else:
                                function_dict = (
                                    dict(function_info)
                                    if hasattr(function_info, "__dict__")
                                    else {}
                                )

                            tool_calls_list.append(
                                {
                                    "id": tool_call_dict.get(
                                        "id", f"tool_call_{message_idx}"
                                    ),
                                    "function": {
                                        "name": function_dict.get("name", "unknown"),
                                        "arguments": function_dict.get(
                                            "arguments", "{}"
                                        ),
                                    },
                                }
                            )
                        logger.info(
                            f"Tool calls list: {tool_calls_list} FOR MESSAGE {chat_msg.id}"
                        )

                        # Add tool_calls message to conversation context
                        conversation_context.append(
                            {
                                "timestamp_ms": time_ms,
                                "speaker_role": "tool_calls",
                                "content": json.dumps(tool_calls_list),
                            }
                        )

                        # Add to messages list for extraction
                        messages_list.append(
                            {
                                "role": "tool_calls",
                                "toolCalls": tool_calls_list,
                                "time": time_ms,
                                "secondsFromStart": time_ms / 1000,
                            }
                        )

                    # Handle tool results (messages with role "tool" and tool_call_id)
                    if role == "tool" and msg_dict.get("tool_call_id"):
                        logger.info(
                            f"Tool result: {msg_dict} FOR MESSAGE {chat_msg.id}"
                        )
                        tool_call_id = msg_dict.get("tool_call_id")
                        result_content = content
                        tool_name = msg_dict.get("name", "")

                        # Add tool result to conversation context
                        conversation_context.append(
                            {
                                "timestamp_ms": time_ms,
                                "speaker_role": "tool_call_result",
                                "content": result_content,
                            }
                        )

                        # Add to messages list for extraction
                        messages_list.append(
                            {
                                "role": "tool_call_result",
                                "toolCallId": tool_call_id,
                                "name": tool_name,
                                "result": result_content,
                                "time": time_ms,
                                "secondsFromStart": time_ms / 1000,
                            }
                        )

                    # Handle regular messages (user/assistant) - add to conversation context
                    if content and role in ["user", "assistant"]:
                        logger.info(
                            f"Regular message: {content} FOR MESSAGE {chat_msg.id}"
                        )
                        conversation_context.append(
                            {
                                "timestamp_ms": time_ms,
                                "speaker_role": speaker_role,
                                "content": content,
                            }
                        )

                    message_idx += 1

            # IMPORTANT: Sort by timestamp to maintain correct chronological order
            conversation_context.sort(key=lambda x: x["timestamp_ms"])
            messages_list.sort(key=lambda x: x.get("time", 0))

            logger.info(
                f"Built conversation context with {len(conversation_context)} messages from database content field (sorted by timestamp)"
            )

            return {
                "conversation_context": conversation_context,
                "vapi_data": {"artifact": {"messages": messages_list}},
                "call_metadata": {},
            }

        except Exception as e:
            logger.error(f"Error fetching chat data from database: {str(e)}")
            raise ValueError(f"Failed to fetch chat data from database: {str(e)}")

    def _format_tool_call_from_transcript(
        self,
        tool_data: Dict[str, Any],
        transcript_item: Dict[str, Any],
        idx: int,
        conversation_context: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Format tool call data from transcript format."""
        return {
            "tool_call_id": tool_data.get("id", f"tool_call_{idx}"),
            "tool_name": tool_data.get("name", "unknown"),
            "arguments": tool_data.get("arguments", {}),
            "timestamp_ms": transcript_item.get("timestamp_ms", 0),
            "context_before": self._get_context_before(
                conversation_context, transcript_item.get("timestamp_ms", 0)
            ),
            "message_index": idx,
            "result": None,
            "result_timestamp_ms": None,
        }

    def _get_context_before(
        self,
        conversation_context: List[Dict[str, Any]],
        timestamp_ms: int,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get conversation context before a specific timestamp (in milliseconds)."""
        context = []
        for item in conversation_context:
            item_ts = item.get("timestamp_ms", 0)
            if item_ts < timestamp_ms:
                context.append(item)

        # Return last 'limit' items (most recent before tool call)
        return context[-limit:] if len(context) > limit else context

    def _get_context_after(
        self,
        conversation_context: List[Dict[str, Any]],
        timestamp_ms: int,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Get conversation context after a specific timestamp (in milliseconds)."""
        context = []
        for item in conversation_context:
            item_ts = item.get("timestamp_ms", 0)
            if item_ts > timestamp_ms:
                context.append(item)
                if len(context) >= limit:
                    break

        return context

    def _parse_arguments(self, arguments: Any) -> Dict[str, Any]:
        """Parse tool call arguments from string or dict."""
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except json.JSONDecodeError:
                return {"raw": arguments}
        return {}

    def _parse_result(self, result: Any) -> Any:
        """Parse tool call result from string or dict."""
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                # Try to parse as JSON
                return json.loads(result)
            except json.JSONDecodeError:
                # Return as is if not valid JSON
                return result
        return result

    def _evaluate_single_tool_call(
        self,
        tool_call: Dict[str, Any],
        conversation_context: List[Dict[str, Any]],
        all_tool_calls: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Evaluate a single tool call using the LLM.

        Args:
            tool_call: Tool call data
            conversation_context: Full conversation context
            all_tool_calls: All tool calls in the conversation

        Returns:
            Evaluation results dictionary
        """
        # Format tool call info
        tool_call_info = self._format_tool_call_for_eval(tool_call)

        # Format conversation context
        formatted_context = self._format_conversation_context(
            tool_call.get("context_before", []), tool_call.get("context_after", [])
        )

        # Step 1: Planning
        planning_prompt = TOOL_EVAL_PLANNING_PROMPT.format(
            conversation_context=formatted_context, tool_call_info=tool_call_info
        )

        messages = [{"role": "user", "content": planning_prompt}]
        planning_response = self.llm._get_completion_content(
            messages=messages, model=self.llm.model_name
        )

        # Step 2: Analysis
        analysis_prompt = TOOL_EVAL_ANALYSIS_PROMPT.format(
            conversation_context=formatted_context,
            tool_call_info=tool_call_info,
            plan=planning_response,
        )

        messages = [{"role": "user", "content": analysis_prompt}]
        analysis_response = self.llm._get_completion_content(
            messages=messages, model=self.llm.model_name
        )

        # Step 3: Validation
        validation_prompt = TOOL_EVAL_VALIDATION_PROMPT.format(
            conversation_context=formatted_context,
            tool_call_info=tool_call_info,
            analysis=analysis_response,
        )

        messages = [{"role": "user", "content": validation_prompt}]
        validation_response = self.llm._get_completion_content(
            messages=messages, model=self.llm.model_name
        )

        # Parse the validation response
        try:
            evaluation = self._parse_evaluation_response(validation_response)

            # Simplify to just what's needed: passed/failed + summary
            result_passed = evaluation.get("result", False)
            summary = evaluation.get("summary", "")

            return {
                "tool_call_id": tool_call.get("tool_call_id"),
                "tool_name": tool_call.get("tool_name"),
                "result": result_passed,
                "summary": summary,
            }
        except Exception as e:
            logger.error(f"Error parsing evaluation response: {str(e)}")
            return {
                "tool_call_id": tool_call.get("tool_call_id"),
                "tool_name": tool_call.get("tool_name"),
                "result": False,
                "summary": f"Failed to evaluate: {str(e)}",
            }

    def _format_tool_call_for_eval(self, tool_call: Dict[str, Any]) -> str:
        """Format tool call data for LLM evaluation."""
        # Format timestamp information
        timestamp_ms = tool_call.get("timestamp_ms", 0)
        seconds_from_start = tool_call.get("seconds_from_start", timestamp_ms / 1000)

        result_timestamp_ms = tool_call.get("result_timestamp_ms", "N/A")
        result_seconds = tool_call.get(
            "result_seconds_from_start",
            result_timestamp_ms / 1000
            if isinstance(result_timestamp_ms, (int, float))
            else "N/A",
        )

        # Calculate execution time if both timestamps available
        execution_time = ""
        if isinstance(result_timestamp_ms, (int, float)) and isinstance(
            timestamp_ms, (int, float)
        ):
            exec_time_ms = result_timestamp_ms - timestamp_ms
            execution_time = f"\nExecution Time: {exec_time_ms} ms ({exec_time_ms / 1000:.3f} seconds)"

        # Format result with proper JSON formatting
        result_content = tool_call.get("result")
        if result_content is not None:
            if isinstance(result_content, (dict, list)):
                result_str = json.dumps(result_content, indent=2)
            else:
                result_str = str(result_content)
        else:
            result_str = "No result available"

        return f"""
Tool Call ID: {tool_call.get("tool_call_id")}
Tool Name: {tool_call.get("tool_name")}
Timestamp: {timestamp_ms} ms (at {seconds_from_start:.3f} seconds from call start)

Arguments:
{json.dumps(tool_call.get("arguments", {}), indent=2)}

Result:
{result_str}

Result Timestamp: {result_timestamp_ms} ms{f" (at {result_seconds:.3f} seconds from call start)" if isinstance(result_seconds, float) else ""}{execution_time}
"""

    def _format_conversation_context(
        self, context_before: List[Dict[str, Any]], context_after: List[Dict[str, Any]]
    ) -> str:
        """Format conversation context for LLM with proper role labels."""
        formatted = "=== CONVERSATION BEFORE TOOL CALL ===\n\n"

        for item in context_before:
            role = item.get("speaker_role", "unknown")
            content = item.get("content", "")
            timestamp = item.get("timestamp_ms", 0)

            # Map role to user-friendly labels
            role_label = {
                "user": "User",
                "assistant": "Assistant/Bot",
                "bot": "Assistant/Bot",
                "system": "System",
                "tool_calls": "Tool Call",
                "tool_call_result": "Tool Result",
            }.get(role, role.title())

            formatted += f"[{timestamp} ms] {role_label}: {content}\n\n"

        formatted += "=== TOOL CALL EXECUTED HERE ===\n\n"

        if context_after:
            formatted += "=== CONVERSATION AFTER TOOL CALL ===\n\n"
            for item in context_after:
                role = item.get("speaker_role", "unknown")
                content = item.get("content", "")
                timestamp = item.get("timestamp_ms", 0)

                # Map role to user-friendly labels
                role_label = {
                    "user": "User",
                    "assistant": "Assistant/Bot",
                    "bot": "Assistant/Bot",
                    "system": "System",
                    "tool_calls": "Tool Call",
                    "tool_call_result": "Tool Result",
                }.get(role, role.title())

                formatted += f"[{timestamp} ms] {role_label}: {content}\n\n"

        return formatted

    def _parse_evaluation_response(self, response: str) -> Dict[str, Any]:
        """Parse the LLM evaluation response into structured data."""
        # Clean the response
        cleaned_response = response.strip()

        # Try to extract JSON
        try:
            # Look for JSON object in the response
            start_idx = cleaned_response.find("{")
            end_idx = cleaned_response.rfind("}")

            if start_idx != -1 and end_idx != -1:
                json_str = cleaned_response[start_idx : end_idx + 1]
                return json.loads(json_str)
            else:
                raise ValueError("No JSON object found in response")
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {str(e)}")
            raise ValueError(f"Failed to parse evaluation JSON: {str(e)}")

    def _generate_summary(
        self, evaluations: List[Dict[str, Any]], tool_calls_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Generate overall summary of tool evaluations."""
        if not evaluations:
            return {"message": "No tool calls evaluated"}

        # Calculate statistics
        total_calls = len(evaluations)

        # Count ratings
        ratings = {
            "EXCELLENT": 0,
            "GOOD": 0,
            "ACCEPTABLE": 0,
            "POOR": 0,
            "FAILED": 0,
            "ERROR": 0,
        }

        total_score = 0.0
        successful_calls = 0
        failed_calls = 0

        for eval_result in evaluations:
            rating = eval_result.get("overall_rating", "UNKNOWN")
            if rating in ratings:
                ratings[rating] += 1

            score = eval_result.get("overall_score", 0.0)
            if isinstance(score, (int, float)):
                total_score += score

            # Count successes and failures
            evaluation_detail = eval_result.get("evaluation", {})
            result_eval = evaluation_detail.get("result", {})

            if result_eval.get("is_correct", False):
                successful_calls += 1
            elif result_eval.get("has_errors", False):
                failed_calls += 1

        avg_score = total_score / total_calls if total_calls > 0 else 0.0

        return {
            "total_tool_calls": total_calls,
            "average_score": round(avg_score, 3),
            "successful_calls": successful_calls,
            "failed_calls": failed_calls,
            "rating_distribution": ratings,
            "success_rate": round(successful_calls / total_calls * 100, 2)
            if total_calls > 0
            else 0.0,
        }
