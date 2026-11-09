"""Single default-deny capability policy for background trace error analysis.

This is an execution policy, not merely a tool-discovery filter. Every native,
generated and async Falcon dispatch entry calls it, including direct tool.run.
Interactive callers (no background_analysis guard) retain their full toolset.
"""

from ai_tools.base import ToolResult

# Bind names to implementations: a connector or generated adapter cannot borrow
# an approved name. Any addition needs the independent AST/security regression.
BACKGROUND_TOOL_ALLOWLIST = {
    "explore_trace_legacy": "ai_tools.tools.tracing.explore_trace.ExploreTraceTool",
    "read_trace_span": "ai_tools.tools.tracing.read_trace_span.ReadTraceSpanTool",
    "submit_trace_finding": "ai_tools.tools.tracing.submit_trace_finding.SubmitTraceFindingTool",
    "submit_trace_scores": "ai_tools.tools.tracing.submit_trace_scores.SubmitTraceScoresTool",
}

# explore_trace_legacy is the skill's one-shot Chauffeur/Haiku summary, NOT a
# nested agent. It stays with checkpoints around reads and its one model call.
# The distinct canonical explore_trace navigator is unnecessary and is denied.
# Also denied: evaluate_with_agent (orchestrator), analyze_project_traces and
# analyze_error_cluster (child jobs), search_traces (unneeded broad discovery),
# tool_search, all MCP/connector-OAuth/generated adapters, and every future tool.


def background_tool_denial(tool, context):
    """Return a typed refusal before validation, credential access or dispatch.

    Pure/safe on the async loop. Live guard checks belong in the execution
    thread (or await guard.checkpoint), not in this capability predicate.
    """
    from tracer.services.background_transport import current_guard

    if (
        getattr(context, "background_analysis", None) is None
        and current_guard() is None
    ):
        return None
    cls = type(tool)
    identity = f"{cls.__module__}.{cls.__name__}"
    if BACKGROUND_TOOL_ALLOWLIST.get(getattr(tool, "name", None)) == identity:
        return None
    return ToolResult.error(
        "This tool is not allowed for background trace analysis.",
        error_code="background_tool_not_allowed",
    )


def background_tools(registry, context):
    """Register only the reviewed capabilities, without connector discovery."""
    tools = []
    for name in BACKGROUND_TOOL_ALLOWLIST:
        tool = registry.get(name)
        if tool is None or background_tool_denial(tool, context) is not None:
            raise RuntimeError(f"Background analysis capability unavailable: {name}")
        tools.append(tool)
    return tuple(tools)
