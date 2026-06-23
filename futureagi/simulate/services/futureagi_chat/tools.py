"""Future AGI Chat tool definitions for chat simulation.

Defines tools available to the simulator LLM, including the endCall tool
that signals conversation termination.
"""

from typing import Any

# Tool definition for endCall (matches Vapi behavior)
# Keep description simple - detailed instructions are in the system prompt
END_CALL_TOOL = {
    "type": "function",
    "function": {
        "name": "endCall",
        "description": (
            "End the conversation when it is mutually finished. "
            "Call this to gracefully terminate the conversation after both sides have concluded."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason for ending the call",
                }
            },
            "required": [],
        },
    },
}


def check_for_end_call(tool_calls: list[Any] | None) -> tuple[bool, str | None]:
    """Check if response contains endCall tool invocation.

    Args:
        tool_calls: List of tool call objects from LLM response.

    Returns:
        Tuple of (has_ended, reason). reason is extracted from endCall arguments if available.
    """
    if not tool_calls:
        return False, None

    for tc in tool_calls:
        # Handle both dict and object forms
        if isinstance(tc, dict):
            func_name = tc.get("function", {}).get("name")
            func_args = tc.get("function", {}).get("arguments", "{}")
        else:
            # Object with attributes (e.g., from litellm)
            func_name = getattr(getattr(tc, "function", None), "name", None)
            func_args = getattr(getattr(tc, "function", None), "arguments", "{}")

        if func_name == "endCall":
            # Extract reason from arguments if available
            reason = None
            try:
                import json

                args = (
                    json.loads(func_args) if isinstance(func_args, str) else func_args
                )
                reason = args.get("reason") if isinstance(args, dict) else None
            except (json.JSONDecodeError, TypeError):
                pass
            return True, reason or "Chat ended by simulator"

    return False, None


def get_simulator_tools() -> list[dict]:
    """Get the list of tools available to the simulator.

    Returns:
        List of tool definitions in OpenAI function calling format.
    """
    return [END_CALL_TOOL]
