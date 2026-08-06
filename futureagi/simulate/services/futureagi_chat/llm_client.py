"""LLM client for Future AGI chat service.

Uses the existing LLM class for completion with tools (retry, provider routing, etc.).
"""

from typing import Any, Dict, List, Optional

import litellm
import structlog

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from simulate.services.futureagi_chat.tools import (
    check_for_end_call,
    get_simulator_tools,
)
from simulate.services.types.chat import LLMUsage

# Enable litellm to modify parameters for Bedrock compatibility
litellm.modify_params = True

logger = structlog.get_logger(__name__)


def generate_simulator_response(
    messages: List[Dict[str, Any]],
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    organization_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a response from the simulator LLM using the LLM class.

    Args:
        messages: Conversation history in OpenAI format.
        system_prompt: System prompt for the simulator.
        model: LLM model to use.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
        organization_id: Organization ID (for logging only).
        workspace_id: Workspace ID (for logging only).

    Returns:
        Dict containing content, tool_calls, has_chat_ended, usage (LLMUsage), model.
    """
    # The session transcript is stored from the agent-under-test's perspective:
    # the persona's lines are "user" and the agent's are "assistant". The
    # persona LLM must speak as the customer, so flip the seating before the
    # call — its own past lines become "assistant" (the side it generates) and
    # the agent's become "user" (the side it replies to). Without the flip the
    # context ends on an assistant turn and the model continues the agent's
    # message instead of answering it; on Vertex/Gemini a trailing "model"
    # turn is literally treated as a continuation, yielding empty or echoed
    # customer messages. Any role other than user/assistant passes through.
    flipped_messages = [
        {
            **m,
            "role": {"user": "assistant", "assistant": "user"}.get(
                m.get("role"), m.get("role")
            ),
        }
        for m in messages
    ]

    # Use content block format with cache_control so Anthropic/Bedrock can
    # cache the system prompt across turns (avoids re-processing each call).
    full_messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ] + flipped_messages

    provider = ModelConfigs.get_provider(model)
    llm = LLM(
        provider=provider,
        model_name=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    logger.debug(
        "simulator_llm_call",
        model=model,
        provider=provider,
        message_count=len(messages),
        organization_id=organization_id,
    )

    response = llm._get_completion_with_tools(
        full_messages,
        get_simulator_tools(),
    )

    choice = response.choices[0]
    message = choice.message

    content = message.content or ""
    tool_calls = []
    has_chat_ended = False
    ended_reason = None

    if message.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]
        has_chat_ended, ended_reason = check_for_end_call(tool_calls)

    usage = LLMUsage()
    if response.usage:
        usage = LLMUsage(
            input_tokens=response.usage.prompt_tokens or 0,
            output_tokens=response.usage.completion_tokens or 0,
            total_tokens=response.usage.total_tokens or 0,
        )

    logger.debug(
        "simulator_llm_response",
        model=model,
        content_length=len(content),
        tool_calls_count=len(tool_calls),
        has_chat_ended=has_chat_ended,
    )

    return {
        "content": content,
        "tool_calls": tool_calls,
        "has_chat_ended": has_chat_ended,
        "ended_reason": ended_reason,
        "usage": usage,
        "model": response.model,
    }
