"""
Judge Agent — Actively investigates traces using tool calls with Claude Sonnet.

The Judge gets a trace outline + Chauffeur's sub-flow report, then uses tools to
read specific spans, search for evidence, and submit findings. It follows the
ERROR_TAXONOMY as its reference guide and dynamically decides which categories
to investigate.

For large spans, read_span routes through the Chauffeur (Haiku) for a summary.
read_span_exact always returns raw content (for verbatim evidence quotes).
"""

import json
import threading
from typing import Any

import structlog

from ee.agenthub.traceerroragent.prompts_v2 import (
    build_judge_user_prompt,
    get_judge_system_prompt,
)
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs

# Keep last N tool results in full, compact older ones
COMPACT_KEEP_RECENT = 6
# Agent Compass v2 — Judge & Chauffeur
AGENT_COMPASS_JUDGE_MAX_TURNS = 30
AGENT_COMPASS_SPAN_CONTENT_THRESHOLD = (
    3000  # Chars: above this, read_span routes through Chauffeur
)

logger = structlog.get_logger(__name__)


# ============================================================================
# TOOL DEFINITIONS (OpenAI function calling format)
# ============================================================================

JUDGE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_span",
            "description": "Read details of a specific span. Small spans return raw content. Large spans return a Chauffeur-generated summary. Use read_span_exact if you need verbatim quotes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "span_id": {
                        "type": "string",
                        "description": "The ID of the span to read",
                    }
                },
                "required": ["span_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_span_exact",
            "description": "Read the EXACT raw content of a span, regardless of size. Use this ONLY when you need verbatim quotes for evidence in a finding. Warning: large spans return full content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "span_id": {
                        "type": "string",
                        "description": "The ID of the span to read in full",
                    }
                },
                "required": ["span_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_children",
            "description": "Get the child spans of a parent span. Returns a summary of each child (ID, name, type, status).",
            "parameters": {
                "type": "object",
                "properties": {
                    "span_id": {
                        "type": "string",
                        "description": "The ID of the parent span",
                    }
                },
                "required": ["span_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spans_by_type",
            "description": "Get all spans of a specific observation type. Types: llm, tool, guardrail, retriever, agent, chain, embedding, reranker, evaluator, conversation, unknown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "observation_type": {
                        "type": "string",
                        "description": "The span type to filter by (e.g., 'llm', 'tool', 'guardrail')",
                    }
                },
                "required": ["observation_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_spans",
            "description": "Search across all span content (input, output, metadata) for a keyword. Returns matching span summaries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "The keyword to search for in span content",
                    }
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_finding",
            "description": "Submit an error finding. Call this for each error you discover during investigation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Error category path from taxonomy (e.g., 'Thinking & Response Issues > Hallucination Errors > Hallucinated Content')",
                    },
                    "location_spans": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Span IDs where the error was found",
                    },
                    "evidence_snippets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exact quotes from span data proving the error",
                    },
                    "description": {
                        "type": "string",
                        "description": "What went wrong",
                    },
                    "impact": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                        "description": "Impact level of the error",
                    },
                    "urgency_to_fix": {
                        "type": "string",
                        "enum": ["IMMEDIATE", "HIGH", "MEDIUM", "LOW"],
                        "description": "How urgently this should be fixed",
                    },
                    "root_causes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Root cause(s) of the error",
                    },
                    "recommendation": {
                        "type": "string",
                        "description": "How to fix the error",
                    },
                    "immediate_fix": {
                        "type": "string",
                        "description": "Quick fix suggestion",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score (0.0-1.0, must be >= 0.7)",
                    },
                    "trace_impact": {
                        "type": "string",
                        "description": "How this error affects the overall trace execution",
                    },
                },
                "required": [
                    "category",
                    "location_spans",
                    "evidence_snippets",
                    "description",
                    "impact",
                    "root_causes",
                    "recommendation",
                    "confidence",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_scores",
            "description": "Submit quality scores for the trace. Score each dimension from 1 (worst) to 5 (best).",
            "parameters": {
                "type": "object",
                "properties": {
                    "factual_grounding": {
                        "type": "object",
                        "properties": {
                            "score": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["score", "reason"],
                    },
                    "privacy_and_safety": {
                        "type": "object",
                        "properties": {
                            "score": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["score", "reason"],
                    },
                    "instruction_adherence": {
                        "type": "object",
                        "properties": {
                            "score": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["score", "reason"],
                    },
                    "optimal_plan_execution": {
                        "type": "object",
                        "properties": {
                            "score": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["score", "reason"],
                    },
                },
                "required": [
                    "factual_grounding",
                    "privacy_and_safety",
                    "instruction_adherence",
                    "optimal_plan_execution",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_summary",
            "description": "Submit the final summary of the investigation. Call this LAST to complete the investigation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "overall_score": {
                        "type": "number",
                        "description": "Overall quality score (1-5)",
                    },
                    "error_count": {
                        "type": "integer",
                        "description": "Total number of errors found",
                    },
                    "insights": {
                        "type": "string",
                        "description": "Key insights about the trace quality and issues found",
                    },
                    "recommended_priority": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                        "description": "Overall priority for fixing issues in this trace",
                    },
                },
                "required": [
                    "overall_score",
                    "error_count",
                    "insights",
                    "recommended_priority",
                ],
            },
        },
    },
]


class JudgeAgent:
    """Actively investigates traces using tool calls with Claude Sonnet."""

    def __init__(
        self,
        span_map: dict[str, Any],
        parent_map: dict[str, list[str]],
        chauffeur: Any = None,
    ):
        """
        Args:
            span_map: Dict mapping span_id -> span object (loaded in memory).
            parent_map: Dict mapping span_id -> list of child span_ids.
            chauffeur: ChauffeurAgent instance for on-demand content summarization.
        """
        self.span_map = span_map
        self.parent_map = parent_map
        self.chauffeur = chauffeur
        self.content_threshold = AGENT_COMPASS_SPAN_CONTENT_THRESHOLD
        self.errors: list[dict] = []
        self.scores: dict = {}
        self.summary: dict = {}
        self._error_counter = 0
        self._counter_lock = threading.Lock()
        self._investigation_complete = False

        config = ModelConfigs.SONNET_4_5_BEDROCK_ARN
        self.llm = LLM(
            provider=config.provider,
            model_name=config.model_name,
            temperature=0.2,
            max_tokens=16000,
        )
        self.max_turns = AGENT_COMPASS_JUDGE_MAX_TURNS

    def run(
        self,
        trace_outline: str,
        chauffeur_report: dict,
        memory_context: str,
        trace_id: str,
        total_spans: int,
        user_query: str,
        chauffeur_failed: bool = False,
    ) -> dict:
        """
        Run the Judge's agentic investigation loop.

        Returns:
            dict with "errors", "scores", "summary" keys.
        """
        system_prompt = get_judge_system_prompt()
        user_prompt = build_judge_user_prompt(
            trace_outline=trace_outline,
            chauffeur_report=chauffeur_report,
            memory_context=memory_context,
            trace_id=trace_id,
            total_spans=total_spans,
            user_query=user_query,
            chauffeur_failed=chauffeur_failed,
        )

        # Use cache_control breakpoints so Bedrock caches the static prefix
        # (system prompt + initial user prompt) across all turns in the loop.
        # Cached input tokens cost 90% less on Anthropic models.
        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        ]

        for turn in range(self.max_turns):
            try:
                response = self.llm._get_completion_with_tools(
                    messages=messages,
                    tools=JUDGE_TOOLS,
                )

                choice = response.choices[0]

                # If the model stopped without tool calls, we're done
                if choice.finish_reason == "stop" or choice.finish_reason == "end_turn":
                    logger.info(f"Judge finished after {turn + 1} turns")
                    break

                # Process tool calls
                if choice.message.tool_calls:
                    # Append assistant message with tool calls
                    messages.append(choice.message.model_dump())

                    for tool_call in choice.message.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            tool_args = json.loads(tool_call.function.arguments)
                        except json.JSONDecodeError:
                            tool_args = {}

                        result = self._execute_tool(tool_name, tool_args)

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": json.dumps(result, default=str),
                            }
                        )

                    # Compact old tool results to bound context growth
                    self._compact_old_tool_results(messages)

                    if self._investigation_complete:
                        logger.info(f"Judge submitted summary after {turn + 1} turns")
                        break
                else:
                    # No tool calls and didn't stop — shouldn't happen but break to be safe
                    logger.warning(
                        f"Judge turn {turn + 1}: no tool calls and finish_reason={choice.finish_reason}"
                    )
                    break

            except Exception as e:
                logger.error(f"Judge turn {turn + 1} failed: {str(e)}")
                break

        # Build fallback scores/summary if Judge didn't submit them
        if not self.scores:
            self.scores = self._compute_fallback_scores()
        if not self.summary:
            self.summary = self._compute_fallback_summary()

        return {
            "errors": self.errors,
            "scores": self.scores,
            "summary": self.summary,
        }

    def _execute_tool(self, tool_name: str, args: dict) -> dict:
        """Execute a tool call and return the result."""
        handlers = {
            "read_span": self._tool_read_span,
            "read_span_exact": self._tool_read_span_exact,
            "get_children": self._tool_get_children,
            "get_spans_by_type": self._tool_get_spans_by_type,
            "search_spans": self._tool_search_spans,
            "submit_finding": self._tool_submit_finding,
            "submit_scores": self._tool_submit_scores,
            "submit_summary": self._tool_submit_summary,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            return handler(**args)
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {str(e)}")
            return {"error": f"Tool {tool_name} failed: {str(e)}"}

    @property
    def token_usage(self) -> dict:
        """Return token usage from the Judge's LLM calls."""
        return self.llm.token_usage

    # ========================================================================
    # INVESTIGATION TOOLS
    # ========================================================================

    def _get_raw_span_data(self, span_id: str) -> dict | None:
        """Get raw span data as a dict. Returns None if span not found."""
        span = self.span_map.get(span_id)
        if not span:
            return None

        return {
            "span_id": str(span_id),
            "name": getattr(span, "name", "") or "",
            "observation_type": getattr(span, "observation_type", "") or "",
            "status": getattr(span, "status", "") or "",
            "input": getattr(span, "input", "") or "",
            "output": getattr(span, "output", "") or "",
            "metadata": getattr(span, "metadata", {}) or {},
            "latency_ms": getattr(span, "latency_ms", None),
            "model": getattr(span, "model", None),
            "parent_span_id": getattr(span, "parent_span_id", None),
        }

    def _tool_read_span(self, span_id: str, **kwargs: Any) -> dict:
        """Read span details. Large spans are summarized by the Chauffeur."""
        raw = self._get_raw_span_data(span_id)
        if not raw:
            return {"error": f"Span '{span_id}' not found"}

        # Calculate content size
        content_size = len(str(raw.get("input", ""))) + len(str(raw.get("output", "")))

        if content_size <= self.content_threshold or not self.chauffeur:
            # Small span or no chauffeur — return raw
            return raw

        # Large span — route through Chauffeur for summary
        content_text = f"Name: {raw['name']}\nType: {raw['observation_type']}\nStatus: {raw['status']}\n"
        if raw["input"]:
            content_text += f"\nInput:\n{raw['input']}\n"
        if raw["output"]:
            content_text += f"\nOutput:\n{raw['output']}\n"
        if raw["metadata"]:
            content_text += f"\nMetadata:\n{json.dumps(raw['metadata'], default=str)}\n"

        summary = self.chauffeur.summarize_content(span_id, content_text)

        return {
            "span_id": str(span_id),
            "name": raw["name"],
            "observation_type": raw["observation_type"],
            "status": raw["status"],
            "latency_ms": raw["latency_ms"],
            "model": raw["model"],
            "parent_span_id": raw["parent_span_id"],
            "content_summary": summary,
            "_note": "This is a Chauffeur summary. Use read_span_exact() for verbatim quotes.",
        }

    def _tool_read_span_exact(self, span_id: str, **kwargs: Any) -> dict:
        """Read the EXACT raw content of a span, regardless of size."""
        raw = self._get_raw_span_data(span_id)
        if not raw:
            return {"error": f"Span '{span_id}' not found"}
        return raw

    def _tool_get_children(self, span_id: str, **kwargs: Any) -> dict:
        """Get child spans of a parent span."""
        child_ids = self.parent_map.get(span_id, [])
        if not child_ids:
            return {
                "children": [],
                "message": f"No children found for span '{span_id}'",
            }

        children = []
        for child_id in child_ids:
            span = self.span_map.get(child_id)
            if span:
                children.append(
                    {
                        "span_id": str(child_id),
                        "name": getattr(span, "name", "") or "",
                        "observation_type": getattr(span, "observation_type", "") or "",
                        "status": getattr(span, "status", "") or "",
                        "latency_ms": getattr(span, "latency_ms", None),
                    }
                )
        return {"children": children}

    def _tool_get_spans_by_type(self, observation_type: str, **kwargs: Any) -> dict:
        """Get all spans of a specific type."""
        matching = []
        for span_id, span in self.span_map.items():
            span_type = getattr(span, "observation_type", "") or ""
            if span_type.lower() == observation_type.lower():
                matching.append(
                    {
                        "span_id": str(span_id),
                        "name": getattr(span, "name", "") or "",
                        "status": getattr(span, "status", "") or "",
                        "latency_ms": getattr(span, "latency_ms", None),
                        "model": getattr(span, "model", None),
                    }
                )
        return {"spans": matching, "count": len(matching)}

    def _tool_search_spans(self, keyword: str, **kwargs: Any) -> dict:
        """Search span content for a keyword."""
        keyword_lower = keyword.lower()
        matching = []

        for span_id, span in self.span_map.items():
            # Search in input, output, and metadata
            span_input = str(getattr(span, "input", "") or "")
            span_output = str(getattr(span, "output", "") or "")
            span_metadata = str(getattr(span, "metadata", {}) or {})

            if (
                keyword_lower in span_input.lower()
                or keyword_lower in span_output.lower()
                or keyword_lower in span_metadata.lower()
            ):
                matching.append(
                    {
                        "span_id": str(span_id),
                        "name": getattr(span, "name", "") or "",
                        "observation_type": getattr(span, "observation_type", "") or "",
                        "match_in": [
                            field
                            for field, content in [
                                ("input", span_input),
                                ("output", span_output),
                                ("metadata", span_metadata),
                            ]
                            if keyword_lower in content.lower()
                        ],
                    }
                )

        return {"matches": matching, "count": len(matching)}

    # ========================================================================
    # SUBMISSION TOOLS
    # ========================================================================

    def _tool_submit_finding(self, **finding: Any) -> dict:
        """Submit an error finding."""
        confidence = finding.get("confidence", 0)
        if confidence < 0.7:
            return {
                "status": "rejected",
                "reason": f"Confidence {confidence} is below minimum threshold of 0.7",
            }

        with self._counter_lock:
            self._error_counter += 1
            error_id = f"E{str(self._error_counter).zfill(3)}"

        error = {
            "error_id": error_id,
            "cluster_id": f"C{str(self._error_counter).zfill(2)}",
            "category": finding.get("category", ""),
            "location_spans": finding.get("location_spans", []),
            "evidence_snippets": finding.get("evidence_snippets", []),
            "description": finding.get("description", ""),
            "impact": finding.get("impact", "MEDIUM"),
            "urgency_to_fix": finding.get("urgency_to_fix", "HIGH"),
            "root_causes": finding.get("root_causes", []),
            "recommendation": finding.get("recommendation", ""),
            "immediate_fix": finding.get("immediate_fix", ""),
            "confidence": confidence,
            "confidence_basis": finding.get("confidence_basis", ""),
            "trace_impact": finding.get("trace_impact", ""),
            "trace_assessment": finding.get("trace_assessment", ""),
            "memory_enhanced": False,
            "llm_analysis": "",  # Not storing full LLM response in v2
        }

        self.errors.append(error)
        return {"status": "accepted", "error_id": error_id}

    def _tool_submit_scores(self, **scores: Any) -> dict:
        """Submit quality scores."""
        self.scores = {
            "factual_grounding": scores.get(
                "factual_grounding", {"score": 3, "reason": ""}
            ),
            "privacy_and_safety": scores.get(
                "privacy_and_safety", {"score": 3, "reason": ""}
            ),
            "instruction_adherence": scores.get(
                "instruction_adherence", {"score": 3, "reason": ""}
            ),
            "optimal_plan_execution": scores.get(
                "optimal_plan_execution", {"score": 3, "reason": ""}
            ),
        }
        return {"status": "accepted"}

    def _tool_submit_summary(self, **summary: Any) -> dict:
        """Submit final summary and end investigation."""
        self.summary = {
            "overall_score": summary.get("overall_score", 3.0),
            "error_count": summary.get("error_count", len(self.errors)),
            "insights": summary.get("insights", ""),
            "recommended_priority": summary.get("recommended_priority", "MEDIUM"),
        }
        self._investigation_complete = True
        return {"status": "accepted", "investigation_complete": True}

    # ========================================================================
    # FALLBACKS
    # ========================================================================

    def _compact_old_tool_results(self, messages: list[dict]) -> None:
        """Compact old tool results to prevent context growth.

        Keeps the last COMPACT_KEEP_RECENT tool-result messages in full detail.
        Older tool results are replaced with a minimal summary so the model still
        has the span_id / status but not the full content.
        """
        tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]

        if len(tool_indices) <= COMPACT_KEEP_RECENT:
            return

        to_compact = tool_indices[:-COMPACT_KEEP_RECENT]
        for idx in to_compact:
            content = messages[idx].get("content", "")
            if not content or '"_compacted": true' in content:
                continue  # Already compacted or empty

            try:
                data = json.loads(content)
                compact: dict = {"_compacted": True}

                # Preserve key identifiers so the model doesn't lose track
                if "error" in data:
                    compact["error"] = data["error"]
                elif "status" in data:
                    compact["status"] = data["status"]
                else:
                    for key in ("span_id", "observation_type", "count", "name"):
                        if key in data:
                            compact[key] = data[key]
                    if "content_summary" in data:
                        compact["had_summary"] = True
                    if "children" in data:
                        compact["child_count"] = len(data["children"])
                    if "matches" in data:
                        compact["match_count"] = len(data["matches"])

                messages[idx]["content"] = json.dumps(compact)
            except (json.JSONDecodeError, TypeError):
                messages[idx]["content"] = json.dumps({"_compacted": True})

    # ========================================================================
    # FALLBACKS
    # ========================================================================

    def _compute_fallback_scores(self) -> dict:
        """Compute fallback scores when the Judge didn't submit them."""
        factual_errors = [
            e
            for e in self.errors
            if "Thinking" in e["category"] or "Tool" in e["category"]
        ]
        privacy_errors = [
            e
            for e in self.errors
            if "Safety" in e["category"] or "Security" in e["category"]
        ]
        instruction_errors = [
            e
            for e in self.errors
            if "Instruction" in e["category"] or "Format" in e["category"]
        ]
        execution_errors = [
            e
            for e in self.errors
            if "Workflow" in e["category"] or "Task" in e["category"]
        ]

        return {
            "factual_grounding": {
                "score": max(1, 5 - len(factual_errors)),
                "reason": "Fallback score based on error count.",
            },
            "privacy_and_safety": {
                "score": max(1, 5 - len(privacy_errors)),
                "reason": "Fallback score based on error count.",
            },
            "instruction_adherence": {
                "score": max(1, 5 - len(instruction_errors)),
                "reason": "Fallback score based on error count.",
            },
            "optimal_plan_execution": {
                "score": max(1, 5 - len(execution_errors)),
                "reason": "Fallback score based on error count.",
            },
        }

    def _compute_fallback_summary(self) -> dict:
        """Compute fallback summary when the Judge didn't submit one."""
        high_impact = sum(1 for e in self.errors if e.get("impact") == "HIGH")
        if self.scores:
            scores_list = []
            for dim in [
                "factual_grounding",
                "privacy_and_safety",
                "instruction_adherence",
                "optimal_plan_execution",
            ]:
                val = self.scores.get(dim, {})
                if isinstance(val, dict):
                    scores_list.append(val.get("score", 3))
                elif isinstance(val, (int, float)):
                    scores_list.append(val)
                else:
                    scores_list.append(3)
            overall_score = sum(scores_list) / len(scores_list)
        else:
            overall_score = 3.0

        if high_impact > 0:
            priority = "HIGH"
        elif len(self.errors) > 2:
            priority = "MEDIUM"
        else:
            priority = "LOW"

        return {
            "overall_score": round(overall_score, 1),
            "error_count": len(self.errors),
            "insights": "Fallback summary — Judge did not submit a final summary.",
            "recommended_priority": priority,
        }
