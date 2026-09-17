"""
EvalOrchestrator — the brain of "Auto" eval mode.

Smart agentic loop (Sonnet) that dynamically investigates the data,
gathers context, and runs a single-LLM-call eval (run_eval tool) at the
right model tier (flash / small / large).

Architecture:
    Scout (Haiku) → produces EvalBrief
    EvalOrchestrator (Sonnet, tool-use loop) → uses tools to investigate,
        calls run_eval (single LLM call), checks confidence, retries if needed,
        then calls submit_result.

Key invariant: run_eval is always a single LLM call.
Depth and thoroughness come from the Orchestrator's own loop turns —
exactly as the JudgeAgent in traceerroragent/judge.py is the analyzer
(not a delegator).

Mirrors JudgeAgent.run() + _execute_tool() + _compact_old_tool_results().
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

import structlog

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from ee.turing.client import TuringClient
from ee.agenthub.eval_orchestrator.prompts import ORCHESTRATOR_SYSTEM_PROMPT
from ee.agenthub.eval_orchestrator.tools import (
    ORCHESTRATOR_TOOLS_BASE,
    TOOL_SEARCH_KB,
    TOOL_GET_FEEDBACK_EXAMPLES,
    TOOL_CALL_MCP,
)
from ee.agenthub.eval_orchestrator.utils import (
    build_trace_outline,
    serialize_span_full,
    _truncate,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class EvalConfig:
    """
    Runtime configuration for a single auto-mode evaluation.

    Passed to EvalOrchestrator.__init__ alongside the Scout brief.
    Created by the caller before invoking the orchestrator.
    """
    criteria: str                    # raw eval criteria (may be vague)
    input_scope: str                 # "span" | "trace" | "session" | "dataset_row" | "cell"
    source_id: str                   # ID of the object being evaluated
    choices: Optional[list] = None   # None → scorer mode (0.0–1.0), or ["Passed","Failed"], or custom labels

    eval_template_id: Optional[str] = None       # for feedback retrieval; None on first run
    kb_id: Optional[str] = None                  # connected KB id, or None
    available_resources: Optional[dict] = None   # from gather_available_resources()


class InputResolver:
    """
    Placeholder for the InputResolver introduced in Workstream 3.
    Reserved for future use by the orchestrator tools when cross-scope
    input resolution is needed.
    """
    pass


# ---------------------------------------------------------------------------
# EvalOrchestrator
# ---------------------------------------------------------------------------

class EvalOrchestrator:
    """
    Smart agentic loop that dynamically decides how to evaluate,
    which model to use, and which resources to consult.

    Mirrors JudgeAgent — same loop structure, same compaction, same tool-dispatch
    pattern — but operates at a higher level of abstraction (evaluation strategy
    rather than trace error finding).
    """

    # Hard cap matching AGENT_COMPASS_JUDGE_MAX_TURNS in judge.py
    MAX_TURNS: int = 15
    # Max run_eval calls per evaluation (initial + up to 2 retries)
    MAX_EVAL_CALLS: int = 3
    # Keep this many recent tool-result messages in full; compact the rest
    COMPACT_KEEP_RECENT: int = 6

    # Turing routing — mirrors DeterministicAgent
    TURING_MODEL_PREFIXES = ("turing_", "turing-")
    TURING_PROVIDER = ModelConfigs.TURING_SMALL.provider

    def __init__(
        self,
        eval_config: EvalConfig,
        scout_brief: dict,
        input_resolver: Optional[InputResolver] = None,
    ) -> None:
        """
        Args:
            eval_config:     Eval configuration (criteria, scope, source_id, resources, etc.)
            scout_brief:     Output of EvalScout.run() enriched with available_resources.
                             Caller must set brief["available_resources"] = available_resources
                             before constructing the orchestrator.
            input_resolver:  Reserved for Workstream 3. Pass None.
        """
        # Default to scorer mode when no choices provided (same as DeterministicEvaluator)
        SCORER_CHOICES = ["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"]
        if not eval_config.choices:
            eval_config.choices = SCORER_CHOICES
            self.scorer = True
        else:
            self.scorer = eval_config.choices == SCORER_CHOICES

        self.eval_config = eval_config
        self.input_resolver = input_resolver
        self.scout_brief = scout_brief

        # Orchestrator routing — mirrors DeterministicAgent.
        # Turing models go through TuringClient directly;
        # other models go through LLM (litellm).
        config = ModelConfigs.TURING_SMALL
        self._orchestrator_model = config.model_name
        self._orchestrator_is_turing = (
            self._is_turing_model(config.model_name)
            or config.provider == self.TURING_PROVIDER
        )

        if self._orchestrator_is_turing:
            self.turing_client: Optional[TuringClient] = TuringClient()
            self.llm: Optional[LLM] = None
        else:
            self.turing_client = None
            self.llm = LLM(
                provider=config.provider,
                model_name=config.model_name,
                temperature=0.2,
                max_tokens=16000,
            )

        # Cumulative orchestrator token usage (TuringClient only stores last call)
        self._orchestrator_token_usage: dict = {"prompt_tokens": 0, "completion_tokens": 0}

        # Dynamic tool list: only tools for actually-connected resources
        self.tools = self._build_tool_list()

        # Tracking state
        self._final_result: Optional[dict] = None
        self._investigation_complete: bool = False
        self._eval_calls: int = 0
        self._model_used: Optional[str] = None
        self._resources_used: list = []
        self._drill_down_performed: bool = False
        self._orchestrator_turns: int = 0
        # Token tracking per component
        self._eval_token_usage: dict = {"prompt_tokens": 0, "completion_tokens": 0}
        self._formalization_token_usage: dict = {"prompt_tokens": 0, "completion_tokens": 0}
        # Auto-detected media — pre-populated at init from the source data,
        # also updated by inspect_data calls. Used as fallback in run_eval
        # when the agent doesn't explicitly pass the `media` parameter.
        self._detected_media: list[dict] = []
        # Cache org_id for scoping inspect queries
        self._org_id: Optional[str] = self._resolve_organization_id()
        self._preload_media()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Main agentic loop. Mirrors JudgeAgent.run().

        Returns:
            {
                "result":              { "result": str, "confidence": float, "explanation": str },
                "model_used":          "flash" | "small" | "large",
                "eval_calls":          int,
                "resources_used":      [str, ...],
                "drill_down_performed": bool,
                "orchestrator_turns":  int,
                "token_usage":         { scout, formalization, orchestrator, eval_calls, total },
            }
        """
        messages = [
            {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_prompt()},
        ]

        for turn in range(self.MAX_TURNS):
            self._orchestrator_turns = turn + 1

            # Warn the agent when running low on turns so it wraps up
            remaining = self.MAX_TURNS - turn
            if remaining == 2 and not self._investigation_complete:
                messages.append({
                    "role": "user",
                    "content": (
                        "WARNING: You have only 2 turns remaining. "
                        "You MUST call run_eval now (if you haven't already) "
                        "and then submit_result on the next turn. "
                        "Do not start new investigations."
                    ),
                })

            try:
                content = self._call_orchestrator_llm(messages)
                tool_call = self._parse_tool_call_from_response(content)

                if tool_call:
                    messages.append({"role": "assistant", "content": content})

                    tool_name = tool_call.get("tool", "")
                    tool_args = tool_call.get("params", {})

                    logger.info(
                        "EvalOrchestrator tool call",
                        turn=turn + 1,
                        tool=tool_name,
                    )
                    result = self._execute_tool(tool_name, tool_args)

                    messages.append({
                        "role": "user",
                        "content": f"Tool result for {tool_name}:\n{json.dumps(result, default=str)}",
                    })

                    self._compact_old_tool_results(messages)

                    if self._investigation_complete:
                        logger.info(
                            "EvalOrchestrator finished",
                            turns=turn + 1,
                            eval_calls=self._eval_calls,
                        )
                        break

                else:
                    logger.info(
                        "EvalOrchestrator finished (no tool call)",
                        turns=turn + 1,
                        eval_calls=self._eval_calls,
                    )
                    break

            except Exception as e:
                logger.error(
                    "EvalOrchestrator turn failed",
                    turn=turn + 1,
                    error=str(e),
                )
                break

        # If the agent never submitted, force a final eval + submit
        if not self._investigation_complete:
            logger.warning(
                "EvalOrchestrator max turns reached — forcing final submission",
                turns=self._orchestrator_turns,
            )
            self._force_final_submission(messages)

        return self._build_final_result()

    # ------------------------------------------------------------------
    # Turing routing helpers (mirrors DeterministicAgent pattern)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_turing_model(model_name: Optional[str]) -> bool:
        lower = (model_name or "").lower()
        return any(
            lower.startswith(p) for p in EvalOrchestrator.TURING_MODEL_PREFIXES
        )

    @staticmethod
    def _parse_tool_call_from_response(content: str) -> Optional[dict]:
        """
        Extract a prompt-based tool call from the model's response.

        The model is instructed to output:
            <tool_call>
            {"tool": "TOOL_NAME", "params": {...}}
            </tool_call>

        Returns the parsed dict or None if no tool call is present.
        """
        match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _call_orchestrator_llm(self, messages: list) -> str:
        """
        Call the orchestrator LLM, routing to TuringClient or LLM.

        Mirrors DeterministicAgent._call_llm() — turing models go to
        TuringClient directly; other models go through litellm.
        Returns the response content string (prompt-based tool calling).
        """
        if self._orchestrator_is_turing:
            content = self.turing_client.chat_completion(
                model=self._orchestrator_model,
                messages=messages,
                temperature=0.2,
                max_tokens=16000,
            )
            usage = self.turing_client.token_usage
            self._orchestrator_token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            self._orchestrator_token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            return content
        else:
            return self.llm._get_completion_content(
                messages=messages,
                model=self.llm.model_name,
            )

    # ------------------------------------------------------------------
    # Tool list builder (conditional — based on connected resources)
    # ------------------------------------------------------------------

    def _build_tool_list(self) -> list:
        """
        Build the dynamic tool list.

        Tools for disconnected resources are NOT included — this prevents
        the Orchestrator from attempting calls that cannot succeed.
        """
        tools = list(ORCHESTRATOR_TOOLS_BASE)  # always available

        available = self.scout_brief.get("available_resources", {}) or {}

        # KB tool — only if a KB is connected on eval_config
        if self.eval_config.kb_id:
            tools.append(TOOL_SEARCH_KB)

        # Feedback examples tool — only if feedback exists for this eval template
        feedback_count = available.get("feedback_count", 0)
        if feedback_count and feedback_count > 0:
            tools.append(TOOL_GET_FEEDBACK_EXAMPLES)

        # MCP tool — only if at least one MCP tool is connected
        mcp_tools = available.get("mcp_tools", [])
        if mcp_tools:
            tools.append(TOOL_CALL_MCP)

        return tools

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_tools_description(tools: list) -> str:
        """Serialize tool definitions to a human-readable text block."""
        lines = []
        for tool in tools:
            fn = tool["function"]
            lines.append(f"### {fn['name']}")
            lines.append(fn["description"])
            props = fn.get("parameters", {}).get("properties", {})
            required = fn.get("parameters", {}).get("required", [])
            if props:
                lines.append("Parameters:")
                for pname, pdef in props.items():
                    req_str = " (required)" if pname in required else " (optional)"
                    ptype = pdef.get("type", "any")
                    pdesc = pdef.get("description", "")
                    enum_vals = pdef.get("enum", [])
                    enum_str = f" Options: {enum_vals}" if enum_vals else ""
                    lines.append(f"  - {pname} ({ptype}{req_str}): {pdesc}{enum_str}")
            lines.append("")
        return "\n".join(lines)

    def _build_user_prompt(self) -> str:
        available = self.scout_brief.get("available_resources", {}) or {}
        parts = [
            "## EVAL CRITERIA",
            self.eval_config.criteria.strip(),
            "",
            "## SCOUT BRIEF",
            json.dumps(
                {k: v for k, v in self.scout_brief.items() if k != "available_resources"},
                indent=2,
            ),
            "",
            "## INPUT SCOPE",
            f"Scope: {self.eval_config.input_scope}",
            f"Source ID: {self.eval_config.source_id}",
            "",
            "## EVALUATION CHOICES",
            json.dumps(self.eval_config.choices),
            *([
                "NOTE: This is scorer mode — 0.0 means not following the rule at all, "
                "1.0 means following the rule completely."
            ] if self.scorer else []),
            "",
            "## AVAILABLE RESOURCES",
            json.dumps(available, indent=2),
            "",
            "## AVAILABLE TOOLS",
            self._build_tools_description(self.tools),
        ]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Tool dispatcher
    # ------------------------------------------------------------------

    def _execute_tool(self, tool_name: str, args: dict) -> dict:
        """Dispatch a tool call. Mirrors JudgeAgent._execute_tool()."""
        handlers = {
            "inspect_data": self._tool_inspect_data,
            "search_data": self._tool_search_data,
            "search_kb": self._tool_search_kb,
            "get_feedback_examples": self._tool_get_feedback,
            "call_mcp_tool": self._tool_call_mcp,
            "formalize_criteria": self._tool_formalize_criteria,
            "run_eval": self._tool_run_eval,
            "submit_result": self._tool_submit_result,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return handler(**args)
        except Exception as e:
            logger.error("Orchestrator tool failed", tool=tool_name, error=str(e))
            return {"error": f"Tool '{tool_name}' failed: {str(e)}"}

    # ------------------------------------------------------------------
    # Investigation tools
    # ------------------------------------------------------------------

    def _tool_inspect_data(
        self,
        target_type: str,
        target_id: str,
        depth: str,
    ) -> dict:
        """
        Drill into data at the requested depth.
        overview = structure / metadata only (no content).
        detail   = full input/output content.
        """
        if depth == "detail":
            self._drill_down_performed = True

        if target_type == "span":
            return self._inspect_span(target_id, depth)
        elif target_type == "trace":
            return self._inspect_trace(target_id, depth)
        elif target_type == "session":
            return self._inspect_session(target_id, depth)
        elif target_type == "dataset_row":
            return self._inspect_dataset_row(target_id, depth)
        elif target_type == "cell":
            return self._inspect_cell(target_id)
        else:
            return {"error": f"Unknown target_type: {target_type}"}

    def _inspect_span(self, span_id: str, depth: str) -> dict:
        from tracer.models.observation_span import ObservationSpan

        qs = ObservationSpan.objects.all()
        if self._org_id:
            qs = qs.filter(project__organization_id=self._org_id)
        span = qs.get(id=span_id)
        if depth == "overview":
            return {
                "span_id": str(span.id),
                "name": span.name,
                "type": span.observation_type,
                "status": span.status,
                "model": getattr(span, "model", None),
                "has_input": bool(span.input),
                "has_output": bool(span.output),
                "has_metadata": bool(span.metadata),
            }
        else:  # detail
            return serialize_span_full(span)

    def _inspect_trace(self, trace_id: str, depth: str) -> dict:
        from tracer.models.trace import Trace
        from tracer.models.observation_span import ObservationSpan

        qs = Trace.objects.all()
        if self._org_id:
            qs = qs.filter(project__organization_id=self._org_id)
        trace = qs.get(id=trace_id)
        spans = ObservationSpan.objects.filter(trace_id=trace_id).order_by(
            "start_time", "created_at"
        )
        if depth == "overview":
            return {
                "trace_id": str(trace.id),
                "name": trace.name,
                "span_count": spans.count(),
                "outline": build_trace_outline(trace, spans),
            }
        else:  # detail
            return {
                "trace_id": str(trace.id),
                "name": trace.name,
                "input": trace.input,
                "output": trace.output,
                "spans": [serialize_span_full(s) for s in spans],
            }

    def _inspect_session(self, session_id: str, depth: str) -> dict:
        from tracer.models.trace_session import TraceSession
        from tracer.models.trace import Trace
        from tracer.models.observation_span import ObservationSpan

        qs = TraceSession.objects.all()
        if self._org_id:
            qs = qs.filter(project__organization_id=self._org_id)
        session = qs.get(id=session_id)
        traces = Trace.objects.filter(session_id=session_id)

        if depth == "overview":
            return {
                "session_id": str(session.id),
                "traces": [
                    {
                        "trace_id": str(t.id),
                        "name": t.name,
                        "span_count": ObservationSpan.objects.filter(trace_id=t.id).count(),
                    }
                    for t in traces
                ],
            }
        else:  # detail
            result = {"session_id": str(session.id), "traces": []}
            for t in traces:
                spans = ObservationSpan.objects.filter(trace_id=t.id).order_by(
                    "start_time", "created_at"
                )
                result["traces"].append({
                    "trace_id": str(t.id),
                    "name": t.name,
                    "input": t.input,
                    "output": t.output,
                    "spans": [serialize_span_full(s) for s in spans],
                })
            return result

    _DATA_TYPE_TO_MEDIA = {"image": "image", "audio": "audio", "document": "pdf"}

    def _inspect_dataset_row(self, row_id: str, depth: str) -> dict:
        from model_hub.models.develop_dataset import Row, Cell

        qs = Row.objects.all()
        if self._org_id:
            qs = qs.filter(dataset__organization_id=self._org_id)
        row = qs.get(id=row_id)
        cells = Cell.objects.filter(row=row).select_related("column")
        result_cells = {}
        for cell in cells:
            data_type = getattr(cell.column, "data_type", "text")
            result_cells[cell.column.name] = {
                "value": cell.value,
                "data_type": data_type,
            }
            # Auto-detect media for run_eval fallback
            self._collect_media(data_type, cell.value)
        return {"row_id": str(row.id), "cells": result_cells}

    def _inspect_cell(self, cell_id: str) -> dict:
        from model_hub.models.develop_dataset import Cell

        qs = Cell.objects.select_related("column", "row")
        if self._org_id:
            qs = qs.filter(dataset__organization_id=self._org_id)
        cell = qs.get(id=cell_id)
        data_type = getattr(cell.column, "data_type", "text")
        # Auto-detect media for run_eval fallback
        self._collect_media(data_type, cell.value)
        return {
            "cell_id": str(cell.id),
            "column_name": cell.column.name,
            "row_id": str(cell.row_id),
            "value": cell.value,
            "data_type": data_type,
        }

    def _collect_media(self, data_type: str, value: Any) -> None:
        """Store media URL from inspected cell for auto-injection into run_eval."""
        media_type = self._DATA_TYPE_TO_MEDIA.get(data_type)
        if media_type and value and isinstance(value, str) and value.startswith("http"):
            # Avoid duplicates
            if not any(m["url"] == value for m in self._detected_media):
                self._detected_media.append({"type": media_type, "url": value})

    def _preload_media(self) -> None:
        """Pre-populate _detected_media at init from the source data.

        Ensures media is available for run_eval even if the agent skips
        inspect_data and goes straight to evaluation.
        """
        scope = self.eval_config.input_scope
        source_id = self.eval_config.source_id
        try:
            if scope == "cell":
                from model_hub.models.develop_dataset import Cell
                cell = Cell.objects.select_related("column").get(id=source_id)
                self._collect_media(
                    getattr(cell.column, "data_type", "text"), cell.value
                )
            elif scope == "dataset_row":
                from model_hub.models.develop_dataset import Row, Cell
                cells = Cell.objects.filter(row_id=source_id).select_related("column")
                for cell in cells:
                    self._collect_media(
                        getattr(cell.column, "data_type", "text"), cell.value
                    )
            elif scope == "span":
                from tracer.models.observation_span import ObservationSpan
                span = ObservationSpan.objects.get(id=source_id)
                self._detect_media_in_text(span.input)
                self._detect_media_in_text(span.output)
            elif scope == "trace":
                from tracer.models.observation_span import ObservationSpan
                spans = ObservationSpan.objects.filter(trace_id=source_id)
                for span in spans:
                    self._detect_media_in_text(span.input)
                    self._detect_media_in_text(span.output)
            elif scope == "session":
                from tracer.models.trace import Trace
                from tracer.models.observation_span import ObservationSpan
                trace_ids = Trace.objects.filter(
                    session_id=source_id
                ).values_list("id", flat=True)
                spans = ObservationSpan.objects.filter(trace_id__in=trace_ids)
                for span in spans:
                    self._detect_media_in_text(span.input)
                    self._detect_media_in_text(span.output)

            if self._detected_media:
                logger.info(
                    "Preloaded media from source data",
                    scope=scope,
                    media_count=len(self._detected_media),
                    types=[m["type"] for m in self._detected_media],
                )
        except Exception as e:
            logger.debug("Media preload failed (non-fatal)", error=str(e))

    @staticmethod
    def _is_media_url(url: str) -> bool:
        """Check if a URL likely points to media content (image/audio/pdf)."""
        lower = url.lower()
        # S3 paths with known media prefixes
        if any(seg in lower for seg in ("/images/", "/audio/", "/documents/")):
            return True
        # Common media file extensions
        if any(lower.endswith(ext) for ext in (
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
            ".mp3", ".wav", ".ogg", ".m4a", ".flac",
            ".pdf",
        )):
            return True
        return False

    def _detect_media_in_text(self, text: Any) -> None:
        """Scan text (span input/output) for media URLs."""
        if not text or not isinstance(text, str):
            return
        urls = re.findall(r'https?://[^\s\'"<>]+', text)
        for url in urls:
            if not self._is_media_url(url):
                continue
            lower = url.lower()
            if any(seg in lower for seg in ("/images/", ".png", ".jpg", ".jpeg", ".gif", ".webp")):
                self._collect_media("image", url)
            elif any(seg in lower for seg in ("/audio/", ".mp3", ".wav", ".ogg", ".m4a")):
                self._collect_media("audio", url)
            elif any(seg in lower for seg in ("/documents/", ".pdf")):
                self._collect_media("document", url)

    def _tool_search_data(self, keyword: str, scope: str = "all") -> dict:
        """
        Search across input data for a keyword.
        Mirrors JudgeAgent._tool_search_spans() but scope-aware.
        """
        from tracer.models.observation_span import ObservationSpan

        results = []
        try:
            input_scope = self.eval_config.input_scope
            source_id = self.eval_config.source_id

            if input_scope == "span":
                span_ids = [source_id]
            elif input_scope == "trace":
                span_ids = list(
                    ObservationSpan.objects.filter(trace_id=source_id)
                    .values_list("id", flat=True)
                )
            elif input_scope == "session":
                from tracer.models.trace import Trace
                trace_ids = Trace.objects.filter(session_id=source_id).values_list("id", flat=True)
                span_ids = list(
                    ObservationSpan.objects.filter(trace_id__in=trace_ids)
                    .values_list("id", flat=True)
                )
            else:
                return {"matches": [], "count": 0, "note": f"search_data not supported for {input_scope} scope"}

            kw_lower = keyword.lower()
            for span in ObservationSpan.objects.filter(id__in=span_ids):
                matched_fields = []

                input_str = _truncate(span.input, 5000)
                output_str = _truncate(span.output, 5000)
                meta_str = _truncate(span.metadata, 5000)

                if scope in ("all", "inputs") and kw_lower in input_str.lower():
                    matched_fields.append("input")
                if scope in ("all", "outputs") and kw_lower in output_str.lower():
                    matched_fields.append("output")
                if scope in ("all", "metadata") and kw_lower in meta_str.lower():
                    matched_fields.append("metadata")

                if matched_fields:
                    results.append({
                        "span_id": str(span.id),
                        "name": span.name,
                        "type": span.observation_type,
                        "matched_in": matched_fields,
                        "input_snippet": input_str[:300] if "input" in matched_fields else None,
                        "output_snippet": output_str[:300] if "output" in matched_fields else None,
                    })

        except Exception as e:
            logger.error("_tool_search_data failed", error=str(e))
            return {"error": str(e), "matches": [], "count": 0}

        return {"matches": results, "count": len(results)}

    # ------------------------------------------------------------------
    # Resource tools (conditionally added to tool list)
    # ------------------------------------------------------------------

    def _tool_search_kb(self, query: str, kb_id: str, top_k: int = 5) -> dict:
        """Search the connected knowledge base via KBIndexer."""
        from model_hub.utils.kb_indexer import KBIndexer

        try:
            chunks = KBIndexer().search(kb_id=kb_id, query=query, top_k=top_k)
            if "kb" not in self._resources_used:
                self._resources_used.append("kb")
            return {"kb_id": kb_id, "query": query, "chunks": chunks}
        except Exception as e:
            logger.error("_tool_search_kb failed", kb_id=kb_id, error=str(e))
            return {"error": str(e), "chunks": []}

    def _resolve_organization_id(self) -> Optional[str]:
        """
        Derive organization_id from the source object rather than requiring
        the caller to pass it in — mirrors how eval_runner derives it from
        the dataset/row rather than expecting it as an explicit argument.
        """
        scope = self.eval_config.input_scope
        source_id = self.eval_config.source_id
        try:
            if scope == "dataset_row":
                from model_hub.models.develop_dataset import Row
                row = Row.objects.select_related("dataset__organization").get(id=source_id)
                return str(row.dataset.organization_id)
            elif scope == "span":
                from tracer.models.observation_span import ObservationSpan
                span = ObservationSpan.objects.select_related("project__organization").get(id=source_id)
                return str(span.project.organization_id)
            elif scope == "trace":
                from tracer.models.trace import Trace
                trace = Trace.objects.select_related("project__organization").get(id=source_id)
                return str(trace.project.organization_id)
            elif scope == "session":
                from tracer.models.trace_session import TraceSession
                session = TraceSession.objects.select_related("project__organization").get(id=source_id)
                return str(session.project.organization_id)
            elif scope == "cell":
                from model_hub.models.develop_dataset import Cell
                cell = Cell.objects.select_related("dataset__organization").get(id=source_id)
                return str(cell.dataset.organization_id)
        except Exception as e:
            logger.warning("_resolve_organization_id failed", scope=scope, source_id=source_id, error=str(e))
        return None

    def _resolve_input_mapping(self) -> tuple[list, list]:
        """
        Resolve actual input values and column names for the current eval target,
        keyed by the eval template's required_keys so they match what is stored
        in ClickHouse by data_formatter.

        Returns (inputs, input_cols) suitable for EmbeddingManager.retrieve_avg_rag_based_examples().
        """
        scope = self.eval_config.input_scope
        source_id = self.eval_config.source_id

        if scope == "cell":
            from model_hub.models.develop_dataset import Cell
            cell = Cell.objects.select_related("column").get(id=source_id)
            return [cell.value or ""], [cell.column.name]

        if scope == "dataset_row":
            from model_hub.models.develop_dataset import Row, Cell
            from model_hub.models.evals_metric import UserEvalMetric
            row = Row.objects.get(id=source_id)

            # Use UserEvalMetric mapping to get required_key → cell value pairs.
            # Feedback is stored with required_keys as column names, so input_cols
            # must use those same keys or retrieval will find nothing.
            uem = UserEvalMetric.objects.filter(
                template_id=self.eval_config.eval_template_id,
                dataset=row.dataset,
                deleted=False,
            ).first()
            if uem:
                mapping = uem.config.get("mapping", {})
                cells_by_col = {
                    str(cell.column_id): (cell.value or "")
                    for cell in Cell.objects.filter(row=row)
                }
                required_keys = []
                values = []
                for rk, col_id in mapping.items():
                    if rk == "call_type":
                        continue
                    col_id_str = str(col_id) if col_id else ""
                    val = cells_by_col.get(col_id_str, "")
                    required_keys.append(rk)
                    values.append(val)
                if values:
                    return values, required_keys

            # Fallback: raw cell data (column names won't match stored required_keys)
            cells = Cell.objects.filter(row=row).select_related("column")
            return [cell.value or "" for cell in cells], [cell.column.name for cell in cells]

        # For span/trace/session: use the eval template's required_keys so that
        # input_cols match what was stored by data_formatter (e.g. ["output"] not
        # ["input", "output"]). Fall back to ["input", "output"] if unavailable.
        try:
            from model_hub.models.evals_metric import EvalTemplate
            tmpl = EvalTemplate.objects.get(id=self.eval_config.eval_template_id)
            required_keys = tmpl.config.get("required_keys") or []
        except Exception:
            required_keys = []

        span_data = {}
        if scope == "span":
            from tracer.models.observation_span import ObservationSpan
            span = ObservationSpan.objects.get(id=source_id)
            span_data = {"input": str(span.input or ""), "output": str(span.output or "")}
        elif scope == "trace":
            from tracer.models.trace import Trace
            trace = Trace.objects.get(id=source_id)
            span_data = {"input": str(trace.input or ""), "output": str(trace.output or "")}
        elif scope == "session":
            from tracer.models.trace_session import TraceSession
            session = TraceSession.objects.get(id=source_id)
            span_data = {"input": str(session.input or ""), "output": str(session.output or "")}
        else:
            return [], []

        if required_keys:
            values = [span_data.get(rk, "") for rk in required_keys]
            return values, list(required_keys)

        # Final fallback: both input and output
        return [span_data["input"], span_data["output"]], ["input", "output"]

    def _tool_get_feedback(
        self,
        eval_template_id: str,
        context_hint: str = "",
    ) -> dict:
        """
        Retrieve past human feedback as few-shot examples via EmbeddingManager RAG.
        Uses the actual resolved input values (mirroring eval_runner.get_few_shot_examples),
        not the LLM-provided context_hint.
        Always uses self.eval_config.eval_template_id so the correct template is queried.
        """
        if not self.eval_config.eval_template_id:
            return {"fewshots": "", "count": 0}

        from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager

        inputs, input_cols = self._resolve_input_mapping()
        logger.info("_tool_get_feedback resolved", scope=self.eval_config.input_scope, source_id=self.eval_config.source_id, input_cols=input_cols, eval_template_id=self.eval_config.eval_template_id, inputs_preview=[str(v)[:60] for v in inputs])
        if not inputs:
            return {"fewshots": "", "count": 0}

        em = EmbeddingManager()
        try:
            examples = em.retrieve_avg_rag_based_examples(
                eval_id=self.eval_config.eval_template_id,
                inputs=inputs,
                input_cols=input_cols,
                organization_id=self._resolve_organization_id(),
                workspace_id=None,  # feedback is stored without workspace_id in all write paths
            )
            fewshots = em.process_examples(
                examples,
                inputs=input_cols,
                feedback_col_name="feedback_comment",
                corrected_label_col_name="feedback_value",
            )
        except Exception as e:
            logger.warning("_tool_get_feedback RAG failed", error=str(e))
            fewshots = ""
        finally:
            em.close()

        if "feedback" not in self._resources_used:
            self._resources_used.append("feedback")

        count = len(fewshots) if isinstance(fewshots, list) else (1 if fewshots else 0)
        print(f"[FEEDBACK ORCHESTRATOR] _tool_get_feedback returned {count} fewshots for eval_template={self.eval_config.eval_template_id} scope={self.eval_config.input_scope}", flush=True)
        return {"fewshots": fewshots, "count": count}

    def _tool_call_mcp(self, tool_name: str, params: dict) -> dict:
        """Invoke an external MCP tool via Falcon AI connectors."""
        # Verify tool is in the available resources
        available = self.eval_config.available_resources or {}
        mcp_tools = available.get("mcp_tools", [])
        known_names = {t["name"] for t in mcp_tools if isinstance(t, dict)}
        if tool_name not in known_names:
            return {"error": f"MCP tool '{tool_name}' is not available. Available: {sorted(known_names)}"}

        try:
            from ee.falcon_ai.models import MCPConnector
            from ee.falcon_ai.mcp_proxy import MCPConnectorProxy

            org_id = self._resolve_organization_id()
            # Find the connector that has this tool
            connectors = MCPConnector.objects.filter(
                organization_id=org_id,
                is_active=True,
                is_verified=True,
            )
            connector = None
            for c in connectors:
                discovered = c.discovered_tools or []
                enabled = c.enabled_tool_names or []
                for tool_schema in discovered:
                    name = tool_schema.get("name", "")
                    if name == tool_name:
                        if not enabled or name in enabled:
                            connector = c
                            break
                if connector:
                    break

            if not connector:
                return {"error": f"No connector found with tool '{tool_name}'"}

            proxy = MCPConnectorProxy()
            result = proxy.execute_tool_sync(connector, tool_name, params)

            if "mcp" not in self._resources_used:
                self._resources_used.append("mcp")
            return {"tool_name": tool_name, "result": result}
        except Exception as e:
            logger.error("_tool_call_mcp failed", tool_name=tool_name, error=str(e))
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Criteria formalization tool
    # ------------------------------------------------------------------

    def _tool_formalize_criteria(
        self,
        raw_criteria: str,
        data_summary: str,
        choices: Optional[list] = None,
        response_format: str = "custom",
    ) -> dict:
        """
        Turn a vague user criteria into a well-structured eval prompt.
        Uses a cheap Flash model — single LLM call, not an agentic loop.
        """
        flash_config = ModelConfigs.TURING_FLASH
        flash_is_turing = (
            self._is_turing_model(flash_config.model_name)
            or flash_config.provider == self.TURING_PROVIDER
        )
        if flash_is_turing:
            formalization_client: Optional[TuringClient] = TuringClient()
            formalization_llm = None
        else:
            formalization_client = None
            formalization_llm = LLM(
                model_name=flash_config.model_name,
                provider=flash_config.provider,
                temperature=0.1,
                max_tokens=2000,
            )

        prompt = f"""You are an evaluation prompt engineer. The user wants to evaluate
data but gave a vague or informal criteria. Your job is to formalize it into a
well-structured evaluation prompt.

USER'S CRITERIA: {raw_criteria}
DATA BEING EVALUATED: {data_summary}
CURRENT CHOICES: {json.dumps(choices or [])}
DESIRED RESPONSE FORMAT: {response_format}

Output a JSON object:
{{
    "formalized_criteria": "The full evaluation prompt with clear instructions, what to
        look for, and {{output}} or {{context}} placeholders for the data",
    "choices": ["choice1", "choice2"],
    "response_format": "boolean|score_1_5|category",
    "reasoning": "why this formalization captures the user's intent"
}}

Guidelines:
- If response_format is "boolean": choices should be ["Passed", "Failed"]
- If response_format is "score_1_5": choices should be ["1", "2", "3", "4", "5"]
- If response_format is "category": define meaningful category names
- If response_format is "custom" or choices are empty: infer the best format
- The formalized criteria must be unambiguous — another LLM should be able to
  evaluate consistently using it
- Include specific positive/negative signals to look for
- Use {{output}} as the placeholder for the data being evaluated

Respond with ONLY the JSON object, no markdown fences."""

        messages = [{"role": "user", "content": prompt}]
        try:
            if flash_is_turing:
                response = formalization_client.chat_completion(
                    model=flash_config.model_name,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=2000,
                )
                usage = formalization_client.token_usage
            else:
                response = formalization_llm._get_completion_content(
                    messages=messages,
                    model=formalization_llm.model_name,
                )
                usage = formalization_llm.token_usage
            # Track formalization token usage
            self._formalization_token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            self._formalization_token_usage["completion_tokens"] += usage.get("completion_tokens", 0)

            return self._parse_json_response(response)
        except Exception as e:
            logger.error("_tool_formalize_criteria failed", error=str(e))
            return {
                "formalized_criteria": raw_criteria,
                "choices": choices or ["Passed", "Failed"],
                "response_format": "boolean",
                "reasoning": f"Formalization failed ({e}); using raw criteria.",
            }

    # ------------------------------------------------------------------
    # Model resolution
    # ------------------------------------------------------------------

    # Model capability tiers: flash=text, small=text+image, large=all
    _MODEL_TIER = {"flash": 0, "small": 1, "large": 2}

    def _enforce_model_for_media(self, model: str) -> str:
        """Upgrade model if _detected_media requires a higher tier."""
        if not self._detected_media:
            return model
        media_types = {m["type"] for m in self._detected_media}
        required = "flash"
        if "image" in media_types:
            required = "small"
        if "audio" in media_types or "pdf" in media_types:
            required = "large"
        if self._MODEL_TIER.get(required, 0) > self._MODEL_TIER.get(model, 1):
            logger.info(
                "run_eval model upgraded for media support",
                requested=model,
                upgraded_to=required,
                media_types=list(media_types),
            )
            return required
        return model

    def _resolve_model_config(self, model: str):
        """Map the orchestrator's tier choice ('flash'/'small'/'large') to ModelConfig."""
        model_map = {
            "flash": ModelConfigs.TURING_FLASH,
            "small": ModelConfigs.TURING_SMALL,
            "large": ModelConfigs.TURING_LARGE,
        }
        return model_map.get(model, ModelConfigs.TURING_SMALL)

    # ------------------------------------------------------------------
    # Execution tools
    # ------------------------------------------------------------------

    def _tool_run_eval(
        self,
        criteria: str,
        data: str,
        choices: list,
        model: str = "small",
        supplementary_context: str = "",
        fewshots: Optional[list] = None,
        media: Optional[list] = None,
    ) -> dict:
        """
        Execute a single LLM judge call.

        NOT a multi-stage pipeline — just criteria + data + model → judgment.
        Depth comes from the Orchestrator's own loop, not from this tool.

        Enforces MAX_EVAL_CALLS to prevent runaway retries.
        """
        if self._eval_calls >= self.MAX_EVAL_CALLS:
            return {
                "error": (
                    f"Maximum eval calls ({self.MAX_EVAL_CALLS}) reached. "
                    "Call submit_result with the best result you have."
                )
            }

        # Enforce minimum model tier based on detected media
        model = self._enforce_model_for_media(model)
        model_config = self._resolve_model_config(model)
        eval_is_turing = (
            self._is_turing_model(model_config.model_name)
            or model_config.provider == self.TURING_PROVIDER
        )
        if eval_is_turing:
            eval_turing_client: Optional[TuringClient] = TuringClient()
            eval_llm = None
        else:
            eval_turing_client = None
            eval_llm = LLM(
                provider=model_config.provider,
                model_name=model_config.model_name,
                temperature=0.1,
                max_tokens=4000,
            )

        eval_prompt = self._build_eval_prompt(
            criteria=criteria,
            data=data,
            choices=choices,
            supplementary_context=supplementary_context,
            fewshots=fewshots,
        )

        # Build multimodal messages if media attachments are provided.
        # Fallback: if agent didn't pass media but we detected media during inspect,
        # auto-inject it so the eval LLM can actually see/hear the content.
        effective_media = media or self._detected_media or None
        if effective_media:
            logger.info(
                "run_eval: using multimodal content",
                media_count=len(effective_media),
                auto_detected=not bool(media),
            )
            content = self._build_multimodal_content(eval_prompt, effective_media)
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "user", "content": eval_prompt}]
        try:
            if eval_is_turing:
                response = eval_turing_client.chat_completion(
                    model=model_config.model_name,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=4000,
                )
                usage = eval_turing_client.token_usage
            else:
                response = eval_llm._get_completion_content(
                    messages=messages,
                    model=eval_llm.model_name,
                )
                usage = eval_llm.token_usage
        except Exception as e:
            logger.error("_tool_run_eval LLM call failed", model=model, error=str(e))
            return {"error": f"Eval LLM call failed: {e}"}

        # Track eval call stats
        self._eval_calls += 1
        self._model_used = model  # last model used wins
        self._eval_token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        self._eval_token_usage["completion_tokens"] += usage.get("completion_tokens", 0)

        result = self._parse_eval_response(response, choices)
        result["model_used"] = model_config.model_name
        return result

    def _build_eval_prompt(
        self,
        criteria: str,
        data: str,
        choices: list,
        supplementary_context: str = "",
        fewshots: Optional[list] = None,
    ) -> str:
        """Build the prompt for the single LLM judge call."""
        parts = [f"## Evaluation Criteria\n{criteria}"]

        if supplementary_context:
            parts.append(f"## Additional Context\n{supplementary_context}")

        if fewshots:
            parts.append("## Reference Examples")
            for i, ex in enumerate(fewshots, 1):
                parts.append(f"Example {i}: {json.dumps(ex, default=str)}")

        parts.append(f"## Data to Evaluate\n{data}")
        parts.append(
            f"## Instructions\n"
            f"Evaluate the data against the criteria above. "
            f"Choose one of: {json.dumps(choices)}.\n"
            f"Respond with a JSON object:\n"
            f'{{"result": "<chosen label>", "confidence": <0.0-1.0>, '
            f'"explanation": "<detailed reasoning>"}}'
        )

        return "\n\n".join(parts)

    @staticmethod
    def _build_multimodal_content(text_prompt: str, media: list) -> list:
        """
        Build multimodal content blocks for the eval LLM call.

        Follows the same pattern as DeterministicAgent._build_content_block():
        images → {"type": "image_url", "image_url": {"url": ...}}
        audio  → {"type": "image_url", "image_url": {"url": ...}} (same block type)
        pdf    → {"type": "file", "file": {"file_id": ..., "format": "application/pdf"}}
        """
        content: list = []

        for i, item in enumerate(media, 1):
            media_type = item.get("type", "")
            url = item.get("url", "")
            if not url:
                continue

            if media_type == "image":
                content.extend([
                    {"type": "text", "text": f"<image_{i}>"},
                    {"type": "image_url", "image_url": {"url": url}},
                    {"type": "text", "text": f"</image_{i}>"},
                ])
            elif media_type == "audio":
                content.extend([
                    {"type": "text", "text": f"<audio_{i}>"},
                    {"type": "image_url", "image_url": {"url": url}},
                    {"type": "text", "text": f"</audio_{i}>"},
                ])
            elif media_type == "pdf":
                content.extend([
                    {"type": "text", "text": f"<pdf_{i}>"},
                    {"type": "file", "file": {"file_id": url, "format": "application/pdf"}},
                    {"type": "text", "text": f"</pdf_{i}>"},
                ])

        # Append the text prompt after all media blocks
        content.append({"type": "text", "text": text_prompt})

        return content

    def _parse_eval_response(self, response: str, choices: list) -> dict:
        """
        Parse the LLM judge response into a structured result dict.
        Falls back gracefully if JSON cannot be extracted.
        """
        parsed = self._parse_json_response(response)

        # Validate result is a known choice
        result_val = parsed.get("result", "")
        if result_val not in choices and choices:
            # Try case-insensitive match
            for c in choices:
                if c.lower() == result_val.lower():
                    parsed["result"] = c
                    break
            else:
                # Last resort: pick first choice and lower confidence
                parsed["result"] = choices[0]
                parsed["confidence"] = min(parsed.get("confidence", 0.5), 0.5)

        # Clamp confidence to [0, 1]
        confidence = parsed.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        parsed["confidence"] = max(0.0, min(1.0, confidence))

        if "explanation" not in parsed:
            parsed["explanation"] = response[:2000]

        return parsed

    def _tool_submit_result(
        self,
        result: str,
        confidence: float,
        explanation: str,
    ) -> dict:
        """Submit the final evaluation result. Marks investigation complete."""
        self._final_result = {
            "result": result,
            "confidence": float(confidence),
            "explanation": explanation,
        }
        self._investigation_complete = True
        logger.info(
            "EvalOrchestrator: result submitted",
            result=result,
            confidence=confidence,
        )
        return {"status": "submitted"}

    # ------------------------------------------------------------------
    # Context compaction (mirrors JudgeAgent._compact_old_tool_results)
    # ------------------------------------------------------------------

    def _compact_old_tool_results(
        self,
        messages: list,
        keep_recent: int = COMPACT_KEEP_RECENT,
    ) -> None:
        """
        Compact old tool-result messages to prevent context window growth.
        Keeps the most recent `keep_recent` tool results in full;
        older ones are replaced with a brief summary.
        """
        tool_message_indices = [
            i for i, m in enumerate(messages)
            if m.get("role") == "user" and m.get("content", "").startswith("Tool result for")
        ]

        if len(tool_message_indices) <= keep_recent:
            return

        to_compact_indices = tool_message_indices[:-keep_recent]
        for idx in to_compact_indices:
            msg = messages[idx]
            content = msg.get("content", "")
            if not content or len(content) <= 200:
                continue

            # Don't compact already-compacted messages
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and parsed.get("_compacted"):
                    continue
            except (json.JSONDecodeError, TypeError):
                pass

            messages[idx]["content"] = json.dumps({
                "_compacted": True,
                "summary": content[:100] + f" [...{len(content) - 100} chars compacted]",
            })

    # ------------------------------------------------------------------
    # Forced submission on max-turns exhaustion
    # ------------------------------------------------------------------

    def _force_final_submission(self, messages: list) -> None:
        """
        When the agent loop exhausts MAX_TURNS without calling submit_result,
        make one extra LLM call instructing it to evaluate and submit immediately.
        """
        messages.append({
            "role": "user",
            "content": (
                "You have run out of turns. You MUST call submit_result RIGHT NOW "
                "with your best evaluation based on everything you have gathered so far. "
                "If you have not yet called run_eval, make your best judgment from "
                "the data you inspected and submit directly. Pick the most appropriate "
                "choice, assign a confidence score, and provide your explanation. "
                "Do NOT call any other tool — only submit_result."
            ),
        })
        try:
            content = self._call_orchestrator_llm(messages)
            tool_call = self._parse_tool_call_from_response(content)
            if tool_call and tool_call.get("tool") == "submit_result":
                self._execute_tool("submit_result", tool_call.get("params", {}))
            elif tool_call:
                # Agent called something else — force submit with whatever we have
                result = self._execute_tool(tool_call["tool"], tool_call.get("params", {}))
                # If it was run_eval, try one more time for submit
                if tool_call["tool"] == "run_eval" and not self._investigation_complete:
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": f"Tool result for {tool_call['tool']}:\n{json.dumps(result, default=str)}\n\nNow call submit_result immediately.",
                    })
                    content2 = self._call_orchestrator_llm(messages)
                    tc2 = self._parse_tool_call_from_response(content2)
                    if tc2 and tc2.get("tool") == "submit_result":
                        self._execute_tool("submit_result", tc2.get("params", {}))
        except Exception as e:
            logger.error("_force_final_submission failed", error=str(e))

    # ------------------------------------------------------------------
    # Final result builder
    # ------------------------------------------------------------------

    def _build_final_result(self) -> dict:
        """
        Assemble the final result dict with orchestrator metadata.
        If submit_result was never called (e.g., max turns reached),
        returns a graceful failure result.
        """
        # Compute token totals
        if self._orchestrator_is_turing:
            orchestrator_usage = self._orchestrator_token_usage
        else:
            orchestrator_usage = self.llm.token_usage if self.llm else {}
        scout_usage = self.scout_brief.get("_scout_token_usage", {})

        total_prompt = (
            scout_usage.get("prompt_tokens", 0)
            + self._formalization_token_usage["prompt_tokens"]
            + orchestrator_usage.get("prompt_tokens", 0)
            + self._eval_token_usage["prompt_tokens"]
        )
        total_completion = (
            scout_usage.get("completion_tokens", 0)
            + self._formalization_token_usage["completion_tokens"]
            + orchestrator_usage.get("completion_tokens", 0)
            + self._eval_token_usage["completion_tokens"]
        )

        eval_result = self._final_result or {
            "result": None,
            "confidence": 0.0,
            "explanation": (
                "Evaluation did not complete — max turns reached without submit_result."
            ),
        }

        return {
            "result": eval_result,
            "model_used": self._model_used,
            "eval_calls": self._eval_calls,
            "resources_used": self._resources_used,
            "drill_down_performed": self._drill_down_performed,
            "orchestrator_turns": self._orchestrator_turns,
            "token_usage": {
                "scout": scout_usage,
                "formalization": self._formalization_token_usage,
                "orchestrator": {
                    "prompt_tokens": orchestrator_usage.get("prompt_tokens", 0),
                    "completion_tokens": orchestrator_usage.get("completion_tokens", 0),
                },
                "eval_calls": self._eval_token_usage,
                "total": {
                    "prompt_tokens": total_prompt,
                    "completion_tokens": total_completion,
                },
            },
        }

    # ------------------------------------------------------------------
    # JSON parsing helper
    # ------------------------------------------------------------------

    def _parse_json_response(self, response: str) -> dict:
        """
        Try to extract a JSON object from an LLM response.
        Handles markdown fences, bare JSON, and embedded objects.
        Returns an empty dict on complete failure.
        """
        # 1. Markdown-fenced JSON
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                pass

        # 2. Entire response as JSON
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            pass

        # 3. Find any JSON object in the response
        obj_match = re.search(r"\{.*\}", response, re.DOTALL)
        if obj_match:
            try:
                return json.loads(obj_match.group())
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse JSON from LLM response", preview=response[:200])
        return {}
