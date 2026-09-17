"""Tool schemas + error helper for the cluster RCA agent.

8 tools, codebase-exploration mental model:
  list      — find by dimension (ls/find equivalent)
  search    — grep across entities
  read      — selective with depth ladder
  aggregate — group-by counts (the killer primitive)
  compare   — diff two populations
  timeline  — temporal pattern + deploy markers
  submit_finding    — mid-investigation observation
  submit_synthesis  — TERMINAL: 2-sent root cause + 1-sent fix

# Filter DSL (shared across all read tools)

  {key: value}                # eq (default for scalars)
  {key: {"gt": v}}            # >
  {key: {"gte": v}}           # >=
  {key: {"lt": v}}            # <
  {key: {"lte": v}}           # <=
  {key: {"in": [v1, v2]}}     # IN list
  {key: {"not_in": [v1, v2]}} # NOT IN list
  {key: null}                 # IS NULL

Keys are AND'd. No OR, no nested boolean expressions.

# Tool returns

Each handler returns a plain dict — matches the in-repo pattern used by the
Judge agent (`ee/agenthub/traceerroragent/judge.py`). The LLM consumes
tool-response JSON as unstructured data, so dataclass envelopes add maintenance
debt without adding contract value at this boundary.

The one invariant the LLM relies on:

  error    -> {"is_error": true, "code": "<code>", "message": "<text>"}
  success  -> any other dict (no top-level "is_error" key)

Internal Python state that crosses the LLM→ORM/API boundary (findings,
synthesis, persisted run) IS typed — see types.py.
"""

from typing import Any


FILTER_DSL_NOTE = (
    "Filter is a flat dict. Bare values mean equality; operator dicts unlock "
    "comparisons: {key: value}, {key: {gt|gte|lt|lte|in|not_in|contains|"
    "starts_with|ends_with|between: v}}, {key: null} for IS NULL. "
    "Column family by prefix: bare key=built-in column, attr.<k>=user span "
    "attribute, eval.<k>=eval metric, ann.<k>=annotation. Keys AND together. "
    "Errors return {is_error: true, code, message}."
)


CLUSTER_RCA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list",
            "description": (
                "Enumerate items by dimension. Returns "
                "{items: [...], total_count, has_more}. " + FILTER_DSL_NOTE
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": [
                            "traces",
                            "spans",
                            "sessions",
                            "tool_names",
                            "error_messages",
                            "versions",
                            "eval_results",
                            "scan_issues",
                            "scan_issue_categories",
                            "fix_layers",
                            "attribute_keys",
                            "prior_analyses",
                        ],
                    },
                    "filter": {
                        "type": "object",
                        "description": "Filter conditions. Valid keys depend on dimension.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max items returned. Default 20, max 100.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Skip this many items before returning. Default 0. Paginate big clusters with offset += limit.",
                    },
                },
                "required": ["dimension"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Full-text search across entities (kevinified text, span attributes, "
                "error messages, scanner briefs). Returns "
                "{items: [{id, snippet, ...}], total_count, has_more}. " + FILTER_DSL_NOTE
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "enum": ["traces", "spans", "sessions", "scan_issues"],
                    },
                    "query": {
                        "type": "string",
                        "description": "Substring or simple pattern to match.",
                    },
                    "filter": {
                        "type": "object",
                        "description": "Filter conditions. Valid keys depend on entity.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max hits. Default 20, max 100.",
                    },
                },
                "required": ["entity", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read one entity at a chosen depth. "
                "Depth ladder (trace only): "
                "'summary' — root I/O + span tree carrying verbatim span I/O, "
                "failing spans served first (default); "
                "'spans' — the same tree with no payloads (cheapest); "
                "'full' — raised I/O budgets (forensic, large). "
                "read(entity='cluster', id=X) returns scope + scanner-summary baseline by default."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "enum": [
                            "cluster",
                            "trace",
                            "span",
                            "session",
                            "eval_result",
                            "eval_config",
                            "version",
                            "scan_issue",
                            "prior_analysis",
                        ],
                    },
                    "id": {
                        "type": "string",
                        "description": "UUID of the entity to read.",
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["summary", "spans", "full"],
                        "description": (
                            "Meaningful for entity='trace' (summary/spans/full) and "
                            "entity='session' (summary/full — 'spans' invalid for session). "
                            "Default 'summary'."
                        ),
                    },
                    "expand": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Dot-paths to fetch FULL (un-truncated) field values. "
                            "Most read() handlers default-truncate big verbatim fields "
                            "(root.input/output, span.input/output, eval_explanation) at 2KB. "
                            "Pass e.g. ['root.input'] or ['input', 'output'] when the truncated "
                            "version is missing detail you need. Use sparingly — context is finite."
                        ),
                    },
                },
                "required": ["entity", "id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate",
            "description": (
                "Count-by-group. THE primitive for finding patterns across many items "
                "in one call. Example: aggregate(metric='trace_count', "
                "filter={cluster_id: X}, group_by='span_tool_name') tells you which tool "
                "dominates failures without reading 20 trace summaries. "
                "Returns {buckets: [{key, count, pct}], total}. " + FILTER_DSL_NOTE
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": [
                            "trace_count",
                            "span_count",
                            "scan_issue_count",
                        ],
                    },
                    "filter": {"type": "object"},
                    "group_by": {
                        "type": "string",
                        "description": (
                            "Column to bucket on. Same prefix rules as filter keys: "
                            "bare key (e.g. 'span_tool_name', 'version', 'session_id', "
                            "'scan_issue_category', 'scan_issue_fix_layer'), "
                            "or 'attr.<key>' for span attributes, "
                            "'eval.<name>' for eval metrics, 'ann.<name>' for annotations. "
                            "For time-bucketed counts use the timeline tool instead."
                        ),
                    },
                },
                "required": ["metric", "filter", "group_by"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare",
            "description": (
                "Diff two populations defined by filters. Returns over-represented "
                "features in set_a vs set_b. Example: "
                "compare(set_a={cluster_id: X, status: 'fail'}, "
                "set_b={cluster_id: X, status: 'pass'}) → behavioral delta. "
                "Returns {features: [{name, a_rate, b_rate, lift}], note}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "set_a": {"type": "object"},
                    "set_b": {"type": "object"},
                },
                "required": ["set_a", "set_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "timeline",
            "description": (
                "Failure count over time with deploy markers overlaid. Use to check "
                "'when did this start.' Returns "
                "{buckets: [{bucket_start, count, deploys: []}], total}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "object"},
                    "bucket": {
                        "type": "string",
                        "enum": ["minute", "hour", "day"],
                        "description": "Time bucket size. Default 'hour'.",
                    },
                },
                "required": ["filter"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_finding",
            "description": (
                "Capture an observation mid-investigation. Submit multiple findings "
                "of different types as you uncover them. They accumulate; they don't "
                "replace each other."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_type": {
                        "type": "string",
                        "enum": [
                            "failure_mode",
                            "behavioral_delta",
                            "deploy_correlation",
                            "outlier_trace",
                            "pattern_evidence",
                        ],
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label, < 80 chars.",
                    },
                    "description": {
                        "type": "string",
                        "description": "1-3 sentences describing the observation.",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["H", "M", "L"],
                    },
                    "evidence_trace_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_span_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "finding_type",
                    "title",
                    "description",
                    "confidence",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_synthesis",
            "description": (
                "TERMINAL TOOL — ends the investigation. Submit the 2-sentence root "
                "cause + 1-sentence fix. Call only when you have enough evidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "synthesis": {
                        "type": "string",
                        "description": "Exactly 2 sentences. First: what's wrong. Second: why.",
                    },
                    "fix": {
                        "type": "string",
                        "description": "Exactly 1 sentence. Concrete, action-shaped.",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["H", "M", "L"],
                    },
                    "evidence_trace_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "suggested_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "2-3 short follow-up questions the cluster's owner is "
                            "most likely to ask next, grounded in THIS investigation "
                            "(e.g. 'Show me a failing trace', 'What changed before "
                            "this started?'). Phrased as the user would type them."
                        ),
                    },
                },
                "required": ["synthesis", "fix", "confidence"],
            },
        },
    },
]


TERMINAL_TOOL_NAME = "submit_synthesis"


# ----------------------------------------------------------------------------
# Error codes
# ----------------------------------------------------------------------------

ERROR_CODE_NOT_FOUND = "not_found"
ERROR_CODE_UNAVAILABLE = "unavailable"
ERROR_CODE_INVALID_FILTER = "invalid_filter"
ERROR_CODE_INVALID_ARGS = "invalid_args"


def tool_error(code: str, message: str) -> dict[str, Any]:
    """Standard error response — the one invariant across all tool handlers.

    The LLM keys off the top-level `is_error: true` to know a call failed.
    Success responses just don't include that key.
    """
    return {"is_error": True, "code": code, "message": message}
