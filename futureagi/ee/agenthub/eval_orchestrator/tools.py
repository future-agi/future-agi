"""
Tool definitions for the EvalOrchestrator agent.

Organized into three categories:
  - INVESTIGATION TOOLS: inspect_data, search_data, search_kb, get_feedback_examples, call_mcp_tool
  - CRITERIA TOOL:       formalize_criteria
  - EXECUTION TOOLS:     run_eval, submit_result

Individual tool dicts (TOOL_*) are exported so _build_tool_list() can assemble
a conditional subset based on which resources are actually connected.
"""

TOOL_INSPECT_DATA = {
    "type": "function",
    "function": {
        "name": "inspect_data",
        "description": (
            "Inspect evaluation input data at a specific depth. "
            "Use 'overview' for surface-level scan, 'detail' for full content. "
            "For session scope, you can drill into specific traces. "
            "For trace scope, you can drill into specific spans."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_type": {
                    "type": "string",
                    "enum": ["session", "trace", "span", "dataset_row", "cell"],
                    "description": "What level of data to inspect.",
                },
                "target_id": {
                    "type": "string",
                    "description": "ID of the session/trace/span/row/cell to inspect.",
                },
                "depth": {
                    "type": "string",
                    "enum": ["overview", "detail"],
                    "description": (
                        "overview = names/types/structure only. "
                        "detail = full input/output content."
                    ),
                },
            },
            "required": ["target_type", "target_id", "depth"],
        },
    },
}

TOOL_SEARCH_DATA = {
    "type": "function",
    "function": {
        "name": "search_data",
        "description": (
            "Search across the input data for a keyword or pattern. "
            "Returns matching spans/rows with context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Keyword to search for in input/output/metadata.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["all", "inputs", "outputs", "metadata"],
                    "description": "Where to search. Default 'all'.",
                },
            },
            "required": ["keyword"],
        },
    },
}

TOOL_SEARCH_KB = {
    "type": "function",
    "function": {
        "name": "search_kb",
        "description": (
            "Search the connected knowledge base for relevant context. "
            "Returns top matching chunks. Only call if a KB is connected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for the knowledge base.",
                },
                "kb_id": {
                    "type": "string",
                    "description": "ID of the knowledge base to search.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of chunks to return. Default 5.",
                },
            },
            "required": ["query", "kb_id"],
        },
    },
}

TOOL_GET_FEEDBACK_EXAMPLES = {
    "type": "function",
    "function": {
        "name": "get_feedback_examples",
        "description": (
            "Retrieve past human feedback for this eval as few-shot examples. "
            "Returns feedback comments + corrected labels, retrieved via RAG."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "eval_template_id": {
                    "type": "string",
                    "description": "The eval template ID to fetch feedback for.",
                },
                "context_hint": {
                    "type": "string",
                    "description": (
                        "Brief description of the current data to improve RAG retrieval."
                    ),
                },
            },
            "required": ["eval_template_id"],
        },
    },
}

TOOL_CALL_MCP = {
    "type": "function",
    "function": {
        "name": "call_mcp_tool",
        "description": (
            "Invoke an external tool via MCP. Only call for tools listed "
            "in the available resources."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the MCP tool to invoke.",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Parameters to pass to the tool. "
                        "Use {{var}} values from the input data."
                    ),
                },
            },
            "required": ["tool_name", "params"],
        },
    },
}

TOOL_FORMALIZE_CRITERIA = {
    "type": "function",
    "function": {
        "name": "formalize_criteria",
        "description": (
            "Turn a vague or informal user criteria into a well-structured "
            "evaluation prompt with clear instructions and response format. "
            "Call this FIRST if the scout brief says criteria_needs_formalization=true. "
            "Examples of vague input: 'is this polite?', 'check for loops', 'was it good?' "
            "Returns a formalized criteria string ready for use in run_eval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "raw_criteria": {
                    "type": "string",
                    "description": "The user's original criteria (possibly vague).",
                },
                "data_summary": {
                    "type": "string",
                    "description": (
                        "Brief description of what data is being evaluated "
                        "(helps contextualize the criteria)."
                    ),
                },
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "The evaluation choices — if empty, the agent should "
                        "also define appropriate choices."
                    ),
                },
                "response_format": {
                    "type": "string",
                    "enum": ["boolean", "score_1_5", "category", "custom"],
                    "description": (
                        "Desired response format. 'boolean' for pass/fail, "
                        "'score_1_5' for 1-5 scale, 'category' for multi-label, "
                        "'custom' to let the agent decide."
                    ),
                },
            },
            "required": ["raw_criteria", "data_summary"],
        },
    },
}

TOOL_RUN_EVAL = {
    "type": "function",
    "function": {
        "name": "run_eval",
        "description": (
            "Execute an evaluation — a SINGLE LLM call that judges the data "
            "against the criteria. This is NOT a multi-stage pipeline. It's one call: "
            "criteria + data + model → {result, confidence, explanation}. "
            "You MUST specify which model to use. "
            "If confidence is low, YOU (the orchestrator) should investigate more "
            "and call run_eval again with better context — the depth comes from "
            "YOUR loop, not from this tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "criteria": {
                    "type": "string",
                    "description": (
                        "The evaluation criteria — must be well-structured. "
                        "If the original criteria was vague, call formalize_criteria first."
                    ),
                },
                "data": {
                    "type": "string",
                    "description": "The data to evaluate (already formatted).",
                },
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The evaluation choices (e.g., ['Passed', 'Failed']).",
                },
                "model": {
                    "type": "string",
                    "enum": ["flash", "small", "large"],
                    "description": (
                        "Which model to run the eval on. "
                        "flash=Haiku (trivial checks), small=Sonnet (most evals), "
                        "large=Opus/Turing Large (nuanced/high-stakes)."
                    ),
                },
                "supplementary_context": {
                    "type": "string",
                    "description": "Additional context from KB/feedback/MCP tools. Optional.",
                },
                "fewshots": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Few-shot examples from feedback. Optional.",
                },
                "media": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["image", "audio", "pdf"],
                                "description": "Media type.",
                            },
                            "url": {
                                "type": "string",
                                "description": "URL of the media file (S3/HTTPS).",
                            },
                        },
                        "required": ["type", "url"],
                    },
                    "description": (
                        "Media attachments (images, audio, PDFs) to include in the eval. "
                        "When inspecting data reveals cells with data_type 'image', 'audio', or 'document', "
                        "pass their URLs here so the eval LLM can see/hear the actual content. "
                        "Optional — omit for text-only evaluations."
                    ),
                },
            },
            "required": ["criteria", "data", "choices", "model"],
        },
    },
}

TOOL_SUBMIT_RESULT = {
    "type": "function",
    "function": {
        "name": "submit_result",
        "description": (
            "Submit the final evaluation result. Call this LAST. "
            "If you ran an eval and are satisfied with the result, submit it. "
            "If you need to refine (e.g., eval on a deeper scope), do that first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "The chosen evaluation label (from choices).",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence score (0.0-1.0).",
                },
                "explanation": {
                    "type": "string",
                    "description": "Full explanation of the evaluation.",
                },
            },
            "required": ["result", "confidence", "explanation"],
        },
    },
}

# Full list of all tools — used for reference only.
# The orchestrator builds a conditional subset via _build_tool_list().
ORCHESTRATOR_TOOLS_ALL = [
    TOOL_INSPECT_DATA,
    TOOL_SEARCH_DATA,
    TOOL_SEARCH_KB,
    TOOL_GET_FEEDBACK_EXAMPLES,
    TOOL_CALL_MCP,
    TOOL_FORMALIZE_CRITERIA,
    TOOL_RUN_EVAL,
    TOOL_SUBMIT_RESULT,
]

# Always-available tools (no connected resource required).
ORCHESTRATOR_TOOLS_BASE = [
    TOOL_INSPECT_DATA,
    TOOL_SEARCH_DATA,
    TOOL_FORMALIZE_CRITERIA,
    TOOL_RUN_EVAL,
    TOOL_SUBMIT_RESULT,
]
