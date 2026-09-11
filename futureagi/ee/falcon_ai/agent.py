import asyncio
import json
import re
import traceback
import uuid

import structlog
from channels.db import database_sync_to_async

from ai_tools.base import ToolContext, ToolResult
from ai_tools.registry import registry as tool_registry
from ee.falcon_ai.context_manager import ContextManager
from ee.falcon_ai.llm_client import FalconLLMClient
from ee.falcon_ai.modes import detect_mode, filter_tools_for_message, load_tools_for_mode
from ee.licensing.activation_client import ManagedServiceError
from ee.falcon_ai.prompt_builder import PromptBuilder
from tfc.middleware.workspace_context import workspace_context

logger = structlog.get_logger(__name__)

SELF_CORRECTION_THRESHOLD = 3  # inject hint after this many consecutive errors
REPETITION_THRESHOLD = 3  # warn if same tool called this many times

_UUID_IN_BACKTICKS = re.compile(
    r"`([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})`"
)


def _extract_primary_entity_id(result_text: str) -> str | None:
    if not result_text:
        return None
    match = _UUID_IN_BACKTICKS.search(result_text)
    return match.group(1) if match else None


class AgentLoop:
    # Pure safety circuit breaker — should never be hit.
    # The token budget warning (at 80% of MAX_TURN_TOKENS) is the real stopping mechanism.
    MAX_ITERATIONS = 200
    # Token budget per turn — warn model when approaching this
    MAX_TURN_TOKENS = 500000

    def __init__(self, tool_context: ToolContext, conversation):
        self.tool_context = tool_context
        self.conversation = conversation
        self.llm_client = FalconLLMClient()
        self.prompt_builder = PromptBuilder()
        self.context_manager = ContextManager()
        self.tools = []
        self.mode = "general"
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._cached_openai_tools = None
        self._budget_warned = False
        # Partial state — accessible after cancellation for saving
        self.partial_content = ""
        self.partial_tool_calls = []
        self.assistant_message_id = None

    @staticmethod
    def _apply_precontent_to_last_user_message(messages, precontent_blocks, file_images):
        """Attach precontent and case media to the last user message; text-only stays a string so per-message truncation still applies."""
        if not (file_images or precontent_blocks):
            return
        text_only = bool(precontent_blocks) and all(
            isinstance(b, dict)
            and b.get("type") == "text"
            and isinstance(b.get("text"), str)
            for b in (precontent_blocks or [])
        )
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.get("role") != "user" or not isinstance(msg.get("content"), str):
                continue
            if not file_images and text_only:
                gt_text = "\n\n".join(b["text"] for b in precontent_blocks)
                msg["content"] = gt_text + "\n\n" + msg["content"]
            else:
                parts: list = []
                if precontent_blocks:
                    parts.extend(precontent_blocks)
                parts.append({"type": "text", "text": msg["content"]})
                if file_images:
                    parts.extend(file_images)
                msg["content"] = parts
            return

    def _tools_as_openai_format(self):
        """Convert tools to OpenAI function calling format (cached after first call)."""
        if self._cached_openai_tools is not None:
            return self._cached_openai_tools
        result = []
        for tool in self.tools:
            schema = tool.input_schema
            params = (
                schema
                if schema.get("properties")
                else {"type": "object", "properties": {}}
            )
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": params,
                    },
                }
            )
        self._cached_openai_tools = result
        return result

    @database_sync_to_async
    def _load_memories(self):
        """Load workspace memories for the prompt builder."""
        from ee.falcon_ai.models import FalconMemory

        try:
            memories = FalconMemory.objects.filter(
                organization=self.tool_context.organization,
                workspace=self.tool_context.workspace,
            ).values("key", "value")[:20]
            return list(memories)
        except Exception:
            return []

    @database_sync_to_async
    def _load_mcp_tools_sync(self):
        """Load MCP connector tools (sync, called via database_sync_to_async)."""
        from ee.falcon_ai.mcp_tools import load_mcp_tools

        return load_mcp_tools(
            organization=self.tool_context.organization,
            workspace=self.tool_context.workspace,
        )

    async def _load_mcp_tools(self):
        """Load external MCP connector tools for this org/workspace."""
        try:
            return await self._load_mcp_tools_sync()
        except Exception as e:
            logger.warning("Failed to load MCP tools: %s", e)
            return []

    async def run(
        self,
        user_message,
        history_messages,
        send_callback,
        context_page="",
        context_info=None,
        file_images=None,
        precontent_blocks=None,
        skill=None,
        system_prompt_override=None,
        tools_override=None,
    ):
        """Execute the agent loop with mode detection, parallel tool execution, and retry.

        Args:
            system_prompt_override: If provided, replaces the default PromptBuilder
                system prompt. Used by AgentEvaluator to inject eval-specific instructions.
            tools_override: If provided, replaces the mode-detected tools list.
                Used by AgentEvaluator to restrict available tools.
        """

        # 0. Cluster-RCA delegation. When the cluster-rca skill is active on the
        # first turn, hand off to the dedicated cluster-analysis agent: it streams
        # its own investigation over this socket and caches the synthesis. Falcon
        # answers the follow-up turns (which arrive without the skill) against that
        # synthesis. Falls through to the normal loop if no cluster is in context.
        # First exchange = no assistant turn yet. (The user's message is already
        # saved to history by the consumer before we get here, so we can't test
        # `not history_messages` — check for a prior assistant reply instead.)
        _is_first_turn = not any(
            m.get("role") == "assistant" for m in (history_messages or [])
        )
        if (
            skill is not None
            and getattr(skill, "slug", None) == "cluster-rca"
            and _is_first_turn
        ):
            from ee.agenthub.cluster_rca.falcon_bridge import run_cluster_rca

            rca_result = await run_cluster_rca(
                tool_context=self.tool_context,
                context_info=context_info,
                user_message=user_message,
                send_callback=send_callback,
            )
            if rca_result is not None:
                return rca_result

        # 1. Detect mode and load tools
        self.mode = detect_mode(context_page, user_message)
        if tools_override is not None:
            self.tools = tools_override
        else:
            self.tools = load_tools_for_mode(self.mode)

        # 1b. Load external MCP connector tools (skip when tools are overridden)
        if tools_override is None:
            mcp_tools = await self._load_mcp_tools()
            if mcp_tools:
                seen = {t.name for t in self.tools}
                for mt in mcp_tools:
                    if mt.name not in seen:
                        self.tools.append(mt)
                        seen.add(mt.name)

        # Two-tier tool system (like Claude Code):
        # Tier 1: Always-loaded tools — sent to API in every request
        # Tier 2: Deferred tools — only names in system prompt, loaded via tool_search
        total_tools_available = len(self.tools)
        self._all_tools = list(self.tools)  # Keep full list for tool_search

        # Extract recent tool names for continuity
        recent_tool_names = set()
        for msg in history_messages[-6:]:
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("tool_name"):
                    recent_tool_names.add(tc["tool_name"])

        # Filter to ~40 active tools (sent to API with full schemas)
        self.tools = filter_tools_for_message(
            self.tools, user_message, recent_tool_names, max_tools=40
        )
        self._cached_openai_tools = None

        # Send mode_detected event
        await send_callback(
            {
                "type": "mode_detected",
                "data": {
                    "mode": self.mode,
                    "tool_count": len(self.tools),
                    "total_tools_available": total_tools_available,
                },
            }
        )

        # 2. Build system prompt
        if system_prompt_override:
            system_prompt = system_prompt_override
        else:
            # Default: use PromptBuilder
            memories = await self._load_memories()
            workspace_name = (
                self.tool_context.workspace.name
                if self.tool_context.workspace
                else "Default"
            )
            user_email = self.tool_context.user.email

            ctx_info = context_info or {}
            context_str = context_page
            if ctx_info.get("entity_type") and ctx_info.get("entity_id"):
                context_str += (
                    f" (viewing {ctx_info['entity_type']} ID: {ctx_info['entity_id']})"
                )
            if ctx_info.get("path"):
                context_str += f" at {ctx_info['path']}"

            # Imagine mode: append instruction from frontend context
            extra = ctx_info.get("extra") or {}
            if extra.get("instruction"):
                context_str += f"\n\n## Mode Instructions\n{extra['instruction']}"

            system_prompt = self.prompt_builder.build(
                mode=self.mode,
                skill=skill,
                memories=memories,
                tools=self.tools,
                context=context_str,
                workspace_name=workspace_name,
                user_email=user_email,
            )

        # 3. Context compaction — multi-tier: light strip or full LLM summarization
        self.conversation, history_messages = (
            await self.context_manager.compact_if_needed(
                self.conversation, history_messages, self.llm_client
            )
        )

        # 4. Build messages with context management
        messages = self.context_manager.prepare_messages(
            system_prompt,
            history_messages,
            user_message,
            context_summary=self.conversation.context_summary,
        )

        self._apply_precontent_to_last_user_message(
            messages, precontent_blocks, file_images,
        )

        # No mid-conversation system messages — causes 400 errors with Anthropic API.
        # Tool-use reasoning guidance is in the main system prompt instead.

        openai_tools = self._tools_as_openai_format()
        assistant_message_id = str(uuid.uuid4())
        self.assistant_message_id = assistant_message_id
        full_content = ""
        all_tool_calls_log = []
        completion_card = None
        model_used = ""
        tool_call_counter = 0
        consecutive_errors = 0

        for iteration in range(self.MAX_ITERATIONS):
            # Force wrap-up when approaching the limit
            # Token-budget-based warning — more natural than iteration count
            turn_tokens = self.total_input_tokens + self.total_output_tokens
            budget_used = (
                turn_tokens / self.MAX_TURN_TOKENS if self.MAX_TURN_TOKENS else 0
            )
            if budget_used > 0.8 and not getattr(self, "_budget_warned", False):
                self._budget_warned = True
                logger.warning(
                    "falcon_ai_approaching_token_budget",
                    conversation_id=str(self.conversation.id),
                    turn_tokens=turn_tokens,
                    budget_pct=round(budget_used * 100),
                )
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Note: You are approaching the token budget for this turn. "
                            "Finish any in-progress work, then summarize results for the user."
                        ),
                    }
                )

            # Send iteration_start event
            await send_callback(
                {
                    "type": "iteration_start",
                    "data": {
                        "message_id": assistant_message_id,
                        "iteration": iteration + 1,
                        "max_iterations": self.MAX_ITERATIONS,
                    },
                }
            )

            # Truncate tool results in messages to manage context window
            messages = self.context_manager.truncate_messages(messages)

            # Stream LLM response with retry
            tool_calls_accumulator = {}
            current_content = ""
            finish_reason = None

            # Debug: log message structure for eval debugging
            if system_prompt_override:
                msg_summary = []
                for m in messages:
                    role = m.get("role", "?")
                    content = m.get("content", "")
                    if isinstance(content, list):
                        types = [
                            b.get("type", "?") for b in content if isinstance(b, dict)
                        ]
                        msg_summary.append(f"{role}:[{','.join(types)}]")
                    else:
                        msg_summary.append(f"{role}:text({len(str(content))})")
                logger.info(
                    "agent_eval_llm_call",
                    messages=msg_summary,
                    tools_count=len(openai_tools) if openai_tools else 0,
                )

            try:
                async for chunk in self.llm_client.stream_with_retry(
                    messages, openai_tools if openai_tools else None
                ):
                    choices = chunk.get("choices", [])
                    if not choices:
                        # Track token usage from chunk
                        usage = chunk.get("usage")
                        if usage:
                            self.total_input_tokens += usage.get("prompt_tokens", 0)
                            self.total_output_tokens += usage.get(
                                "completion_tokens", 0
                            )
                        continue
                    delta = choices[0].get("delta", {})
                    finish_reason = choices[0].get("finish_reason")
                    model_used = chunk.get("model", model_used)

                    # Track usage if present in chunk
                    usage = chunk.get("usage")
                    if usage:
                        self.total_input_tokens += usage.get("prompt_tokens", 0)
                        self.total_output_tokens += usage.get("completion_tokens", 0)

                    # Handle text content
                    if delta.get("content"):
                        text = delta["content"]
                        current_content += text
                        await send_callback(
                            {
                                "type": "text_delta",
                                "data": {
                                    "delta": text,
                                    "message_id": assistant_message_id,
                                },
                            }
                        )

                    # Handle tool calls
                    if delta.get("tool_calls"):
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index", 0)
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": tc.get("id", ""),
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.get("function", {}).get("name"):
                                tool_calls_accumulator[idx]["name"] = tc["function"][
                                    "name"
                                ]
                            if tc.get("function", {}).get("arguments"):
                                tool_calls_accumulator[idx]["arguments"] += tc[
                                    "function"
                                ]["arguments"]

                    # Drain trailing usage chunks before starting tool execution.
                    if finish_reason == "tool_calls":
                        continue
                    # stop / end_turn / max_tokens — continue draining
                    # the stream so we capture the trailing usage chunk
                    # (sent by stream_options.include_usage).
                    if finish_reason in ("stop", "end_turn", "max_tokens"):
                        continue
            except ManagedServiceError:
                raise
            except Exception as e:
                error_detail = str(e)
                # Extract response body from httpx errors for debugging
                if hasattr(e, "response") and e.response is not None:
                    try:
                        error_detail += f" | body: {e.response.text[:500]}"
                    except Exception:
                        pass
                # The ``str(e)`` of several exception types (e.g.
                # ``httpx.RemoteProtocolError``) is empty, which makes
                # "llm_stream_error error=" useless. Surface the type
                # and repr too so the failure mode is debuggable from
                # logs alone.
                logger.error(
                    "llm_stream_error",
                    error=error_detail,
                    exc_type=type(e).__name__,
                    exc_repr=repr(e)[:500],
                )
                await send_callback(
                    {"type": "error", "data": {"message": f"LLM error: {str(e)}"}}
                )
                break

            full_content += current_content
            self.partial_content = full_content
            self.partial_tool_calls = all_tool_calls_log

            # If LLM returned nothing (no text and no tool calls), break to prevent empty iterations
            if not current_content and not tool_calls_accumulator:
                break

            # If no tool calls, we're done
            if not tool_calls_accumulator:
                break

            # Build assistant message with tool calls for conversation.
            # rstrip: some upstreams reject trailing whitespace in history.
            _content = current_content.rstrip() if current_content else ""
            messages.append(
                {
                    "role": "assistant",
                    "content": _content or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in tool_calls_accumulator.values()
                    ],
                }
            )

            # Execute tool calls in parallel
            tool_call_items = list(tool_calls_accumulator.items())
            tool_executions = []

            for idx, tc in tool_call_items:
                tool_name = tc["name"]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}

                tool = tool_registry.get(tool_name)
                # Fall back to agent's own tool list (for MCP tools not in registry)
                if not tool:
                    tool = next((t for t in self.tools if t.name == tool_name), None)
                # Deferred tool loading: if not in active tools, check full list
                if not tool and hasattr(self, "_all_tools"):
                    tool = next(
                        (t for t in self._all_tools if t.name == tool_name), None
                    )
                    if tool:
                        # Auto-load the deferred tool into active list
                        self.tools.append(tool)
                        self._cached_openai_tools = None
                tool_executions.append(
                    {
                        "idx": idx,
                        "tc": tc,
                        "tool_name": tool_name,
                        "tool": tool,
                        "args": args,
                    }
                )

            # Send tool_call_start events for all tools
            for exec_info in tool_executions:
                tool_call_counter += 1
                exec_info["call_id"] = f"tc_{tool_call_counter}"
                exec_info["step"] = tool_call_counter
                tool_description = (
                    exec_info["tool"].description
                    if exec_info["tool"]
                    else f"Execute {exec_info['tool_name']}"
                )
                exec_info["tool_description"] = tool_description

                await send_callback(
                    {
                        "type": "tool_call_start",
                        "data": {
                            "message_id": assistant_message_id,
                            "call_id": exec_info["call_id"],
                            "tool_name": exec_info["tool_name"],
                            "tool_description": tool_description,
                            "params": exec_info["args"],
                            "step": exec_info["step"],
                        },
                    }
                )

            # Execute all tool calls in parallel
            async def _run_tool(exec_info):
                if exec_info["tool"]:
                    try:
                        tool = exec_info["tool"]
                        # MCP tools: use async path directly (no thread pool blocking)
                        if hasattr(tool, "async_execute"):
                            from ai_tools.base import ToolResult as _TR

                            try:
                                params = tool.input_model.model_validate(
                                    exec_info["args"] or {}
                                )
                            except Exception as e:
                                return f"Invalid parameters: {e}", True
                            result = await asyncio.wait_for(
                                tool.async_execute(params, self.tool_context),
                                timeout=45,  # MCP tools get more time (network calls)
                            )
                        else:
                            # Built-in tools: run in thread pool
                            result = await asyncio.wait_for(
                                self._execute_tool(tool, exec_info["args"]),
                                timeout=30,
                            )
                    except asyncio.TimeoutError:
                        return (
                            f"Tool '{exec_info['tool_name']}' timed out after 30s",
                            True,
                        )
                    return (
                        result.content if result else "No result",
                        result.is_error if result else True,
                    )
                else:
                    # Suggest similar tools to help the model self-correct
                    suggestions = self._find_similar_tools(exec_info["tool_name"])
                    hint = f"Tool '{exec_info['tool_name']}' not found."
                    if suggestions:
                        hint += f" Did you mean: {', '.join(suggestions)}?"
                    return hint, True

            results = await asyncio.gather(
                *[_run_tool(ei) for ei in tool_executions],
                return_exceptions=True,
            )

            # Process results and send tool_call_result events
            iteration_errors = 0
            for exec_info, result in zip(tool_executions, results):
                if isinstance(result, Exception):
                    logger.error(
                        "tool_execution_exception",
                        tool=exec_info["tool_name"],
                        error=str(result),
                        traceback=traceback.format_exception(
                            type(result), result, result.__traceback__
                        ),
                    )
                    result_text = f"Tool execution error: {str(result)}"
                    is_error = True
                else:
                    result_text, is_error = result

                if is_error:
                    iteration_errors += 1

                result_summary = self._build_result_summary(
                    exec_info["tool_name"], result_text, is_error
                )

                await send_callback(
                    {
                        "type": "tool_call_result",
                        "data": {
                            "message_id": assistant_message_id,
                            "call_id": exec_info["call_id"],
                            "tool_name": exec_info["tool_name"],
                            "status": "error" if is_error else "completed",
                            "result_summary": result_summary,
                            "result_full": result_text[:2000],
                            "step": exec_info["step"],
                        },
                    }
                )

                all_tool_calls_log.append(
                    {
                        "call_id": exec_info["call_id"],
                        "tool_name": exec_info["tool_name"],
                        "tool_description": exec_info["tool_description"],
                        "params": exec_info["args"],
                        "status": "error" if is_error else "completed",
                        "result_summary": result_summary,
                        "result_full": result_text[:2000],
                        "step": exec_info["step"],
                    }
                )

                # Emit widget_render event for render_widget tool calls
                if exec_info["tool_name"] == "render_widget" and not is_error:
                    try:
                        widget_data = json.loads(result_text)
                        widget_event = {
                            "type": "widget_render",
                            "data": {
                                "message_id": assistant_message_id,
                                "action": widget_data.get("action", "add"),
                            },
                        }
                        if "widget" in widget_data:
                            widget_event["data"]["widget"] = widget_data["widget"]
                        if "widgets" in widget_data:
                            widget_event["data"]["widgets"] = widget_data["widgets"]
                        await send_callback(widget_event)
                    except (json.JSONDecodeError, KeyError, TypeError):
                        pass

                # Clean error messages before sending to LLM, then truncate
                content_for_llm = result_text
                if is_error:
                    content_for_llm = self._clean_error_message(result_text)
                truncated_result = self.context_manager.truncate_result(content_for_llm)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": exec_info["tc"]["id"],
                        "content": truncated_result,
                    }
                )

                # Check for completion card
                if not is_error and exec_info["tool_name"].startswith("create_"):
                    completion_card = self._build_completion_card(
                        exec_info["tool_name"], result_text
                    )

            # Self-correction: track consecutive errors
            if iteration_errors == len(tool_executions):
                consecutive_errors += 1
            else:
                consecutive_errors = 0

            if consecutive_errors >= SELF_CORRECTION_THRESHOLD:
                # Deduplicate: skip if the last system message already has a hint
                _last_sys = next(
                    (m for m in reversed(messages) if m.get("role") == "system"),
                    None,
                )
                if _last_sys and "consecutive tool call failures" in _last_sys.get(
                    "content", ""
                ):
                    consecutive_errors = 0
                else:
                    # Build a helpful hint with available tool names
                    available_tools = ", ".join(t.name for t in self.tools[:30])
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "STOP: You have had multiple consecutive tool call failures. "
                                "You MUST try a DIFFERENT tool or DIFFERENT parameters. "
                                "Do NOT call the same tool with the same approach again.\n\n"
                                f"Available tools you can use: {available_tools}\n\n"
                                "Common tools: list_datasets, list_eval_templates, whoami, "
                                "list_projects, search_traces, get_cost_breakdown, "
                                "list_experiments, list_prompt_templates.\n\n"
                                "If you cannot complete the task, explain what went wrong "
                                "and ask the user for help."
                            ),
                        }
                    )
                    consecutive_errors = 0  # reset after hint

            # Repetition detection: warn if same tool called too many times
            tool_name_counts = {}
            for log_entry in all_tool_calls_log:
                tn = log_entry["tool_name"]
                tool_name_counts[tn] = tool_name_counts.get(tn, 0) + 1
            for tn, count in tool_name_counts.items():
                if count == REPETITION_THRESHOLD:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                f"NOTE: You have called '{tn}' {count} times. "
                                "Avoid calling the same tool repeatedly — summarize "
                                "the data you already have and move on. If you need "
                                "more details, explain what you found so far and ask "
                                "the user if they want you to continue."
                            ),
                        }
                    )

            # max_tokens means the model's output was truncated — don't continue the loop
            if finish_reason == "max_tokens":
                logger.warning(
                    "falcon_ai_max_tokens_hit",
                    conversation_id=str(self.conversation.id),
                    iteration=iteration + 1,
                )
                break

        # Send completion card if we have one
        if completion_card:
            await send_callback(
                {
                    "type": "completion",
                    "data": {
                        "message_id": assistant_message_id,
                        "completion_card": completion_card,
                    },
                }
            )

        # Auto-generate title for new conversations
        title = await self._maybe_generate_title(
            user_message, full_content, send_callback
        )

        # Return data for saving
        return {
            "id": assistant_message_id,
            "content": full_content,
            "tool_calls": all_tool_calls_log,
            "completion_card": completion_card,
            "model_used": model_used,
            "mode": self.mode,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "title": title,
        }

    async def _maybe_generate_title(
        self, user_message, assistant_response, send_callback
    ):
        """Generate a short title for new conversations (title == 'New conversation')."""
        if self.conversation.title != "New conversation":
            return None

        try:
            title_messages = [
                {
                    "role": "system",
                    "content": (
                        "Generate a short title (max 6 words) for this conversation. "
                        "Return ONLY the title text, nothing else."
                    ),
                },
                {"role": "user", "content": user_message},
                {
                    "role": "assistant",
                    "content": assistant_response[:500] if assistant_response else "",
                },
            ]
            title_parts = []
            async for chunk in self.llm_client.stream_with_retry(
                title_messages, tools=None, max_retries=1
            ):
                usage = chunk.get("usage")
                if usage:
                    self.total_input_tokens += usage.get("prompt_tokens", 0)
                    self.total_output_tokens += usage.get("completion_tokens", 0)

                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if delta.get("content"):
                        title_parts.append(delta["content"])

            title = "".join(title_parts).strip().strip('"').strip("'")[:100]
            if title:
                await self._save_conversation_title(title)
                await send_callback(
                    {
                        "type": "title_generated",
                        "data": {
                            "conversation_id": str(self.conversation.id),
                            "title": title,
                        },
                    }
                )
                return title
        except Exception as e:
            logger.warning("falcon_ai_title_generation_failed", error=str(e))
        return None

    @database_sync_to_async
    def _save_conversation_title(self, title):
        """Save the generated title to the conversation."""
        self.conversation.title = title
        self.conversation.save(update_fields=["title", "updated_at"])

    def _find_similar_tools(self, name, max_results=3):
        """Find tools with similar names to help LLM self-correct."""
        all_names = [t.name for t in self.tools]
        # Simple substring matching — find tools that share word parts
        name_parts = set(name.lower().replace("_", " ").split())
        scored = []
        for tool_name in all_names:
            tool_parts = set(tool_name.lower().replace("_", " ").split())
            overlap = len(name_parts & tool_parts)
            if overlap > 0:
                scored.append((overlap, tool_name))
        scored.sort(key=lambda x: -x[0])
        return [s[1] for s in scored[:max_results]]

    def _build_result_summary(self, tool_name, result_text, is_error):
        """Build a short summary from the tool result for display in the UI."""
        if is_error:
            first_line = result_text.split("\n")[0]
            return first_line[:200] if len(first_line) > 200 else first_line

        # Try to parse JSON and build a meaningful summary
        text = result_text.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                import json as _json

                data = _json.loads(text)
                # Handle docs search results
                if isinstance(data, dict) and "results" in data:
                    results = data["results"]
                    if isinstance(results, list) and results:
                        titles = [
                            r.get("title", "") for r in results[:3] if r.get("title")
                        ]
                        return f"Found {len(results)} docs: {', '.join(titles)}"
                # Handle other JSON
                if isinstance(data, dict):
                    keys = list(data.keys())[:3]
                    return f"Result with {len(data)} fields: {', '.join(keys)}"
                if isinstance(data, list):
                    return f"List with {len(data)} items"
            except Exception:
                pass

        # Find first non-empty, non-bracket line
        for line in text.split("\n"):
            line = line.strip()
            if line and line not in ("{", "}", "[", "]", ","):
                if len(line) <= 150:
                    return line
                return line[:147] + "..."

        return text[:150] if text else "Done"

    @database_sync_to_async
    def _execute_tool(self, tool, params):
        """Execute a tool synchronously (tools use Django ORM)."""
        try:
            with workspace_context(
                self.tool_context.workspace,
                organization=self.tool_context.organization,
                user=self.tool_context.user,
            ):
                return tool.run(params, self.tool_context)
        except Exception as e:
            logger.error("tool_execution_error", tool=tool.name, error=str(e))
            return ToolResult.error(str(e))

    def _clean_error_message(self, error_text):
        """Simplify verbose error messages while keeping actionable info."""
        import re

        # If the error contains our schema hint ("Expected schema:"), keep it intact
        # — the LLM needs this to self-correct
        if "Expected schema:" in error_text:
            # Just truncate the raw Pydantic error but keep the schema + "You sent"
            parts = error_text.split("\n\nExpected schema:")
            if len(parts) == 2:
                # Shorten the Pydantic error itself
                pydantic_part = parts[0]
                fields = re.findall(r"(\w+)\s+Field required", pydantic_part)
                if fields:
                    pydantic_short = f"Missing required fields: {', '.join(fields)}"
                else:
                    pydantic_short = pydantic_part[:200]
                return f"{pydantic_short}\n\nExpected schema:{parts[1]}"

        # Remove stack traces — keep only the final error line
        if "Traceback" in error_text:
            lines = error_text.split("\n")
            for line in reversed(lines):
                if line.strip() and not line.startswith(" "):
                    return line.strip()

        # Remove URLs from error messages
        error_text = re.sub(r"https?://\S+", "", error_text).strip()

        # Truncate overly long errors
        if len(error_text) > 500:
            return error_text[:497] + "..."

        return error_text

    def _build_completion_card(self, tool_name, result_text):
        """Build a completion card for create actions.

        If `result_text` contains a UUID in backticks (the convention used by
        all create_* tools — see key_value_block output in ai_tools/tools/**),
        the first one is treated as the primary entity id and used to deep-link
        to the detail page. Otherwise we fall back to `path_prefix` (list page).
        """
        action_map = {
            "create_dataset": {
                "title": "Dataset created",
                "action_label": "Go to dataset",
                "path_prefix": "/dashboard/develop/",
                "detail_path": "/dashboard/develop/{id}",
            },
            "create_dataset_from_file": {
                "title": "Dataset created",
                "action_label": "Go to dataset",
                "path_prefix": "/dashboard/develop/",
                "detail_path": "/dashboard/develop/{id}",
            },
            "create_dataset_from_huggingface": {
                "title": "Dataset created",
                "action_label": "Go to dataset",
                "path_prefix": "/dashboard/develop/",
                "detail_path": "/dashboard/develop/{id}",
            },
            "create_eval_template": {
                "title": "Evaluation template created",
                "action_label": "Go to evaluation",
                "path_prefix": "/dashboard/evaluations/",
                "detail_path": "/dashboard/evaluations/{id}",
            },
            "create_composite_eval": {
                "title": "Composite evaluation created",
                "action_label": "Go to evaluation",
                "path_prefix": "/dashboard/evaluations/",
                "detail_path": "/dashboard/evaluations/{id}",
            },
            "create_eval_task": {
                "title": "Eval task created",
                "action_label": "Go to task",
                "path_prefix": "/dashboard/tasks/",
                "detail_path": "/dashboard/tasks/{id}",
            },
            "create_custom_eval_config": {
                "title": "Custom eval created",
                "action_label": "Go to observe",
                "path_prefix": "/dashboard/observe/",
            },
            "create_project": {
                "title": "Project created",
                "action_label": "Go to project",
                "path_prefix": "/dashboard/observe/",
                "detail_path": "/dashboard/observe/{id}",
            },
            "create_experiment": {
                "title": "Experiment created",
                "action_label": "Go to experiment",
                "path_prefix": "/dashboard/develop/",
                "detail_path": "/dashboard/develop/experiment/{id}/data",
            },
            "create_prompt_template": {
                "title": "Prompt template created",
                "action_label": "Go to prompt",
                "path_prefix": "/dashboard/workbench/",
                "detail_path": "/dashboard/workbench/create/{id}",
            },
            "create_prompt_version": {
                "title": "Prompt version created",
                "action_label": "Go to prompts",
                "path_prefix": "/dashboard/workbench/",
            },
            "create_prompt_simulation": {
                "title": "Prompt simulation created",
                "action_label": "Go to prompts",
                "path_prefix": "/dashboard/workbench/",
            },
            "create_optimization_run": {
                "title": "Optimization run created",
                "action_label": "Go to prompts",
                "path_prefix": "/dashboard/workbench/",
            },
            "create_scenario": {
                "title": "Scenario created",
                "action_label": "Go to scenario",
                "path_prefix": "/dashboard/simulate/scenarios/",
                "detail_path": "/dashboard/simulate/scenarios/{id}",
            },
            "create_persona": {
                "title": "Persona created",
                "action_label": "Go to personas",
                "path_prefix": "/dashboard/simulate/personas/",
            },
            "create_agent_definition": {
                "title": "Agent definition created",
                "action_label": "Go to agent definition",
                "path_prefix": "/dashboard/simulate/agent-definitions/",
                "detail_path": "/dashboard/simulate/agent-definitions/{id}",
            },
            "create_agent_version": {
                "title": "Agent version created",
                "action_label": "Go to agent",
                "path_prefix": "/dashboard/agents/",
                "detail_path": "/dashboard/agents/playground/{id}",
            },
            "create_simulator_agent": {
                "title": "Simulator agent created",
                "action_label": "Go to agent definitions",
                "path_prefix": "/dashboard/simulate/agent-definitions/",
            },
            "create_run_test": {
                "title": "Run test created",
                "action_label": "Go to test",
                "path_prefix": "/dashboard/simulate/test/",
                "detail_path": "/dashboard/simulate/test/{id}",
            },
            "create_simulate_eval_config": {
                "title": "Simulate eval config created",
                "action_label": "Go to tests",
                "path_prefix": "/dashboard/simulate/test/",
            },
            "create_annotation_label": {
                "title": "Annotation label created",
                "action_label": "Go to labels",
                "path_prefix": "/dashboard/annotations/labels/",
            },
            "create_annotation_queue": {
                "title": "Annotation queue created",
                "action_label": "Go to queue",
                "path_prefix": "/dashboard/annotations/queues/",
                "detail_path": "/dashboard/annotations/queues/{id}",
            },
            "create_annotation": {
                "title": "Annotation created",
                "action_label": "Go to annotations",
                "path_prefix": "/dashboard/annotations/",
            },
            "create_trace_annotation": {
                "title": "Trace annotation created",
                "action_label": "Go to observe",
                "path_prefix": "/dashboard/observe/",
            },
            "create_score": {
                "title": "Score created",
                "action_label": "Go to observe",
                "path_prefix": "/dashboard/observe/",
            },
            "create_alert_monitor": {
                "title": "Alert created",
                "action_label": "Go to alerts",
                "path_prefix": "/dashboard/alerts/",
            },
            "create_api_key": {
                "title": "API key created",
                "action_label": "Go to keys",
                "path_prefix": "/dashboard/keys/",
            },
            "create_knowledge_base": {
                "title": "Knowledge base created",
                "action_label": "Go to knowledge base",
                "path_prefix": "/dashboard/knowledge/",
                "detail_path": "/dashboard/knowledge/{id}",
            },
        }
        card_info = action_map.get(
            tool_name,
            {
                "title": f"{tool_name.replace('create_', '').replace('_', ' ').title()} created",
                "action_label": "View",
                "path_prefix": "/dashboard/get-started/",
            },
        )

        action_path = card_info["path_prefix"]
        detail_template = card_info.get("detail_path")
        if detail_template:
            entity_id = _extract_primary_entity_id(result_text)
            if entity_id:
                action_path = detail_template.format(id=entity_id)

        return {
            "title": card_info["title"],
            "status": "completed",
            "action_label": card_info["action_label"],
            "action_path": action_path,
        }
