"""
Prompt scaffolding strings for AgentEvaluator.

Kept here (rather than inline in evaluator.py) so the eval-runtime logic stays
focused on flow control while the prompt copy stays editable as plain strings.
Nothing in this module is mutated — every value is a constant.

Three groups:
  1. Context Reference block — one short paragraph per loaded root, explaining
     what kind of data the agent is looking at.
  2. Tool action menu + per-root traversal recipes — explain how to use the
     `explore_trace` tool and which actions are recommended for each shape.
  3. Flag → kwarg / root mapping dicts — translate the public
     ``data_injection`` flag names (snake_case + camelCase) to internal kwargs.
"""

from __future__ import annotations


# Context Reference — one short paragraph per loaded root, prepended to the
# agent's system prompt so it knows what shape its data is in.
CONTEXT_REF_BY_ROOT: dict[str, str] = {
    "trace": (
        "- **Trace**: A complete execution flow with multiple spans "
        "(operations) in a parent-child hierarchy. Use to evaluate a "
        "single end-to-end agent execution — what tools fired, what "
        "the LLM produced, where errors occurred, and total latency.\n"
    ),
    "span": (
        "- **Span**: A single operation within a trace — LLM call, "
        "tool execution, retrieval, agent step, guardrail check, etc. "
        "Each span carries input, output, status, status_message, "
        "latency, model, token usage, and span_attributes. **This is "
        "where actual conversational content (user messages, agent "
        "responses, tool I/O) lives.**\n"
    ),
    "session": (
        "- **Session**: A multi-turn interaction containing multiple "
        "traces (each trace = one agent turn or invocation). Use to "
        "evaluate consistency, goal completion, conversation "
        "coherence, repeated tool-call patterns, and agent behavior "
        "across multiple user-agent exchanges.\n"
    ),
    "call": (
        "- **Call**: A voice or chat interaction with transcript, "
        "recording, duration, scenario, ended_reason, and evaluation "
        "data. Use to evaluate voice agent performance with full "
        "call metadata and outcome.\n"
    ),
    "row": (
        "- **Row**: A dataset row with columns containing the data "
        "to evaluate. Use when judging an output against structured "
        "inputs from a dataset.\n"
    ),
}


# Action menu for the `explore_trace` tool. Same regardless of loaded root.
EXPLORE_TRACE_ACTIONS_MENU: str = (
    "**Use the `explore_trace` tool to read content.** "
    "The summaries above are metadata only; for real "
    "conversation content you MUST call `span_detail` on "
    "the relevant span_id.\n"
    "\n"
    "**Actions:**\n"
    "- `keys` — top-level fields\n"
    "- `get` query=\"path.to.field\" — read a specific value\n"
    "- `search` query=\"text\" — substring search\n"
    "- `summary` — counts, errors, latencies\n"
    "- `list_trace_spans` query=\"<trace_id>\" — span metadata (no input/output)\n"
    "- `span_detail` query=\"<span_id>\" — FULL span: input, output, span_attributes\n"
    "- `errors` / `slow_spans` / `span_tree` — trace-level only\n"
    "\n"
    "**Recommended traversal:**\n"
)


# Per-root traversal recipe. Appended after the action menu, one per loaded root.
TRAVERSAL_BY_ROOT: dict[str, str] = {
    "trace": (
        "- Trace: your loaded context already includes a "
        "`spans` list with each span's `id`, `name`, "
        "`observation_type`, and `status`. Call `span_detail` "
        "query=\"<id>\" directly on every span whose "
        "input/output you need to read. Do NOT skip drilling "
        "just because the per-span metadata looks small.\n"
    ),
    "session": (
        "- Session: your loaded context already includes "
        "`traces[N].spans[M]` with concrete span IDs. For each "
        "trace you care about, iterate its `spans` list and "
        "call `span_detail` query=\"<id>\" to read the actual "
        "user messages and agent responses. Drill into "
        "multiple traces if judging multi-turn behavior. "
        "NEVER conclude content is missing without calling "
        "`span_detail` on the span IDs already provided.\n"
    ),
    "span": (
        "- Span: `keys` → `get` query=\"input\" or \"output\" → "
        "`search` for specific text patterns.\n"
    ),
    "call": (
        "- Call: `keys` → `get` query=\"transcript\" / "
        "\"call_summary\" / \"ended_reason\" → `search` for "
        "specific phrases.\n"
    ),
    "row": (
        "- Row: `keys` → `get` query=\"<column_name>\" → `search`\n"
    ),
}


# Per-root quick path used in the explicit-flag branch (concise version,
# distinct from TRAVERSAL_BY_ROOT which is the verbose recipe).
EXPLICIT_PATH_BY_ROOT: dict[str, str] = {
    "span": (
        "  Path: `keys` → `get` query=\"input\" / "
        "\"output\" / \"span_attributes\" → `search` for "
        "specific phrases. Span input/output is right here, "
        "no further drill needed.\n"
    ),
    "call": (
        "  Path: `keys` → `get` query=\"transcript\" / "
        "\"call_summary\" / \"ended_reason\" → `search` for "
        "specific phrases. The voice/chat transcript is in "
        "the loaded context.\n"
    ),
    "row": (
        "  Path: `keys` → `get` query=\"<column_name>\" → "
        "`search` for specific text. Dataset row columns "
        "are at the top level of the loaded context.\n"
    ),
}


# Large-data fallback message (when a full_row blob is loaded into the
# explore_trace store because it's too big to inline in the prompt).
# Caller formats with .format(eval_id=...).
LARGE_DATA_EXPLORATION_TEMPLATE: str = (
    "## Data Available for Exploration\n"
    "The data is available via the `explore_trace` tool.\n"
    "Use `eval_id: \"{eval_id}\"` with these actions:\n"
    "- `summary` — get a high-level overview\n"
    "- `errors` — find error spans\n"
    "- `slow_spans` — find performance bottlenecks\n"
    "- `search` — search for specific patterns\n"
    "- `span_detail` — zoom into a specific span\n"
    "- `span_tree` — see the execution hierarchy\n"
    "Start with `summary` to understand the data, then drill down as needed.\n\n"
)


# Explicit data_injection flag → eval-kwarg mapping. Both snake_case and
# camelCase keys are accepted so payloads from either FE surface resolve.
EXPLICIT_FLAG_TO_KWARG: dict[str, str] = {
    "span_context": "span_context",
    "spanContext": "span_context",
    "trace_context": "trace_context",
    "traceContext": "trace_context",
    "session_context": "session_context",
    "sessionContext": "session_context",
    "call_context": "call_context",
    "callContext": "call_context",
    "dataset_row": "row_context",
    "datasetRow": "row_context",
    "full_row": "row_context",
    "fullRow": "row_context",
}


# Explicit data_injection flag → context root name.
EXPLICIT_FLAG_TO_ROOT: dict[str, str] = {
    "span_context": "span",
    "spanContext": "span",
    "trace_context": "trace",
    "traceContext": "trace",
    "session_context": "session",
    "sessionContext": "session",
    "call_context": "call",
    "callContext": "call",
    "dataset_row": "row",
    "datasetRow": "row",
    "full_row": "row",
    "fullRow": "row",
}
