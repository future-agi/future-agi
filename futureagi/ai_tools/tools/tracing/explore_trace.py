"""
explore_trace — the Chauffeur step as a Falcon tool.

Reads all spans from the DB, builds a trace outline, calls Haiku to summarize
sub-flows, and returns a compact report. This is what the old TraceErrorAnalysisAgent's
Chauffeur + outline builder did — one cheap tool call that replaces dozens of
get_trace + read_trace_span calls.
"""

import json
import os
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import section
from ai_tools.registry import register_tool


class ExploreTraceInput(PydanticBaseModel):
    trace_id: UUID = Field(description="The UUID of the trace to explore")


@register_tool
class ExploreTraceTool(BaseTool):
    # Renamed from ``explore_trace`` → ``explore_trace_legacy``. The name
    # ``explore_trace`` now belongs to ``TraceExplorerTool`` in
    # ``ai_tools.tools.web.trace_explorer``, which is the navigator the
    # eval agent uses (load/get/search/tree actions). Both modules
    # import at app-ready, so they used to fight for the same name and
    # crashed backend startup on every cold boot. Keeping this tool
    # under a distinct name preserves the older "read-all-spans +
    # Chauffeur summary" workflow for anything that explicitly looks
    # it up, while letting the canonical navigator keep the short name.
    name = "explore_trace_legacy"
    description = (
        "Explores a trace by reading ALL spans and producing a compact summary. "
        "Returns a span tree outline and a Chauffeur analysis that identifies "
        "logical sub-flows, flags anomalies, and summarizes what each part of "
        "the trace did. Prefer ``explore_trace`` (the on-demand navigator) "
        "for most eval workflows — use this legacy tool only when you want "
        "the one-shot summary."
    )
    category = "tracing"
    input_model = ExploreTraceInput

    def execute(self, params: ExploreTraceInput, context: ToolContext) -> ToolResult:
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace import Trace

        # Verify trace access
        try:
            trace = Trace.objects.select_related("project").get(
                id=params.trace_id,
                project__organization=context.organization,
            )
        except Trace.DoesNotExist:
            return ToolResult.not_found("Trace", str(params.trace_id))

        # Load all spans
        spans = list(
            ObservationSpan.objects.filter(trace=trace, deleted=False).order_by(
                "start_time", "created_at"
            )
        )

        if not spans:
            return ToolResult(
                content=section(
                    "Trace Exploration",
                    f"Trace `{params.trace_id}` has no spans.",
                ),
                data={"trace_id": str(params.trace_id), "total_spans": 0},
            )

        # Build span map and tree (same as TraceErrorAnalysisAgent)
        span_map = {str(s.id): s for s in spans}

        # Build tree structure
        tree = {}
        child_ids = set()
        for s in spans:
            pid = getattr(s, "parent_span_id", None)
            if pid and str(pid) in span_map:
                child_ids.add(str(s.id))

        # Root nodes = spans not in any child set
        for s in spans:
            sid = str(s.id)
            if sid not in child_ids:
                tree[sid] = {"span": s, "children": []}

        # Build children
        for s in spans:
            pid = getattr(s, "parent_span_id", None)
            if pid:
                pid_str = str(pid)
                # Find parent in tree or create
                parent_node = self._find_node(tree, pid_str)
                if parent_node:
                    parent_node["children"].append({"span": s, "children": []})
                elif pid_str not in tree:
                    # Orphan — add as root
                    tree[str(s.id)] = {"span": s, "children": []}

        # Sort children by time
        for node in tree.values():
            self._sort_children(node)

        # Build outline (lightweight, no content)
        outline = self._build_outline(tree, str(params.trace_id), len(spans))

        # Build full execution summary (with content for Chauffeur)
        execution_summary = self._build_execution_summary(
            tree, str(params.trace_id), len(spans), spans
        )

        # Run Chauffeur (Haiku) for sub-flow identification
        chauffeur_report = self._run_chauffeur(execution_summary)

        # Build the output
        content = section("Trace Exploration", outline)

        if chauffeur_report.get("sub_flows"):
            content += "\n\n### Chauffeur Analysis\n\n"
            content += f"**Overview:** {chauffeur_report.get('trace_overview', '')}\n\n"
            for sf in chauffeur_report["sub_flows"]:
                content += f"**{sf.get('name', 'Unnamed')}** "
                content += f"(spans: {', '.join(sf.get('spans', []))})\n"
                content += f"> {sf.get('summary', '')}\n"
                if sf.get("flags"):
                    content += f"> Flags: {', '.join(sf['flags'])}\n"
                content += "\n"
        else:
            content += (
                "\n\n_Chauffeur analysis unavailable. "
                "Use read_trace_span to investigate spans directly._\n"
            )

        # Add span ID reference table — explicit mapping for read_trace_span
        content += "\n\n### Span ID Reference\n"
        content += "Use these EXACT IDs with read_trace_span. Do NOT invent IDs.\n\n"
        content += "| Name | ID | Type | Status |\n"
        content += "|------|------|------|--------|\n"
        for s in spans:
            name = getattr(s, "name", "") or ""
            sid = str(s.id)
            stype = getattr(s, "observation_type", "") or ""
            status = getattr(s, "status", "") or ""
            content += f"| {name} | `{sid}` | {stype} | {status} |\n"

        return ToolResult(
            content=content,
            data={
                "trace_id": str(params.trace_id),
                "total_spans": len(spans),
                "sub_flows": chauffeur_report.get("sub_flows", []),
                "trace_overview": chauffeur_report.get("trace_overview", ""),
            },
        )

    def _find_node(self, tree, target_id):
        """Find a node in the tree by span ID."""
        for sid, node in tree.items():
            if sid == target_id:
                return node
            found = self._find_in_children(node, target_id)
            if found:
                return found
        return None

    def _find_in_children(self, node, target_id):
        for child in node.get("children", []):
            if str(child["span"].id) == target_id:
                return child
            found = self._find_in_children(child, target_id)
            if found:
                return found
        return None

    def _sort_children(self, node):
        node["children"].sort(
            key=lambda n: getattr(n["span"], "start_time", None)
            or getattr(n["span"], "created_at", None)
        )
        for child in node["children"]:
            self._sort_children(child)

    def _build_outline(self, tree, trace_id, total_spans):
        """Build lightweight outline — same as TraceErrorAnalysisAgent._build_trace_outline."""
        lines = [
            f"TRACE OUTLINE (Trace ID: {trace_id}, Total Spans: {total_spans})",
            "",
        ]

        def walk(node, path):
            span = node["span"]
            sid = str(span.id)
            name = getattr(span, "name", "") or ""
            stype = getattr(span, "observation_type", "") or ""
            status = getattr(span, "status", "") or ""
            latency = getattr(span, "latency_ms", None)
            model = getattr(span, "model", "") or ""
            path_str = ".".join(str(p) for p in path)
            lat = f" ({latency}ms)" if latency else ""
            st = f" [{status}]" if status else ""
            mdl = f" model={model}" if model else ""
            lines.append(f"{path_str}  [{stype}] {name}{st}{lat}{mdl}  (ID: {sid})")
            for idx, child in enumerate(node["children"], start=1):
                walk(child, path + [idx])

        roots = sorted(
            tree.values(),
            key=lambda n: getattr(n["span"], "start_time", None)
            or getattr(n["span"], "created_at", None),
        )
        for i, root in enumerate(roots, start=1):
            walk(root, [i])

        return "\n".join(lines)

    def _build_execution_summary(self, tree, trace_id, total_spans, spans):
        """Build full execution summary — same as TraceErrorAnalysisAgent.create_trace_execution_summary."""
        lines = [
            "TRACE EXECUTION TREE:",
            f"Trace ID: {trace_id}",
            f"Total Spans: {total_spans}",
            "",
        ]

        # User query from first span
        if spans:
            first_input = getattr(spans[0], "input", "") or ""
            if not isinstance(first_input, str):
                try:
                    first_input = json.dumps(first_input, ensure_ascii=False)
                except Exception:
                    first_input = str(first_input)
            lines.append(f"USER QUERY: {first_input}")
            lines.append("")

        outline_lines = []
        detail_lines = []
        step_counter = [0]

        def walk(node, path):
            span = node["span"]
            sid = str(span.id)
            name = getattr(span, "name", "") or ""
            stype = getattr(span, "observation_type", "") or ""
            sinput = getattr(span, "input", "") or ""
            soutput = getattr(span, "output", "") or ""
            smetadata = getattr(span, "metadata", {}) or {}
            path_str = ".".join(str(p) for p in path)

            outline_lines.append(f"{path_str}  {name}  [Span ID: {sid}]")

            step_counter[0] += 1
            detail_lines.append(f"STEP {step_counter[0]} [Path {path_str}]:")
            detail_lines.append(f"Span ID: {sid}")
            detail_lines.append(f"Span Name: {name}")
            detail_lines.append(f"Span Type: {stype}")
            if sinput:
                sinput_str = str(sinput)
                if len(sinput_str) > 2000:
                    sinput_str = (
                        sinput_str[:1500]
                        + f"\n... [{len(sinput_str) - 1500} chars truncated]"
                    )
                detail_lines.append(f"Input: {sinput_str}")
            if soutput:
                soutput_str = str(soutput)
                if len(soutput_str) > 2000:
                    soutput_str = (
                        soutput_str[:1500]
                        + f"\n... [{len(soutput_str) - 1500} chars truncated]"
                    )
                detail_lines.append(f"Output: {soutput_str}")
            if smetadata:
                relevant = {
                    k: v
                    for k, v in smetadata.items()
                    if v
                    and k
                    in [
                        "tool_result",
                        "tool_call",
                        "expected_format",
                        "goal",
                        "subtasks",
                    ]
                }
                if relevant:
                    detail_lines.append(f"Metadata: {json.dumps(relevant, indent=2)}")
            detail_lines.append("=" * 50)
            detail_lines.append("")

            for idx, child in enumerate(node["children"], start=1):
                walk(child, path + [idx])

        roots = sorted(
            tree.values(),
            key=lambda n: getattr(n["span"], "start_time", None)
            or getattr(n["span"], "created_at", None),
        )
        for i, root in enumerate(roots, start=1):
            walk(root, [i])

        lines.append("OUTLINE (Path → Span):")
        lines.extend(outline_lines)
        lines.append("")
        lines.append("DETAILS (Follow paths to reference spans):")
        lines.extend(detail_lines)

        return "\n".join(lines)

    def _run_chauffeur(self, trace_execution_summary):
        """Identify sub-flows in the trace using an LLM call.

        Uses litellm directly with the model configured.
        """
        import re

        import litellm
        import structlog

        logger = structlog.get_logger(__name__)

        CHAUFFEUR_PROMPT = (
            "You are a trace exploration assistant. Your job is to read a complete "
            "execution trace of an AI agent and identify the logical sub-flows within it.\n\n"
            "A sub-flow is a sequence of related spans that together accomplish one logical "
            "step in the agent's execution. Examples:\n"
            "- 'Retrieval sub-flow': embedding → vector search → reranker → result\n"
            "- 'Tool execution sub-flow': planning LLM → tool call → tool response → parsing\n"
            "- 'Response generation sub-flow': context assembly → LLM call → guardrail check → final output\n"
            "- 'Error handling sub-flow': failed call → retry → fallback\n\n"
            "For each sub-flow you identify:\n"
            "1. List the span IDs that belong to it (in execution order)\n"
            "2. Summarize what happened factually — what data went in, what each span did, what came out\n"
            "3. Flag any factual observations that seem unusual (but do NOT interpret or judge them)\n\n"
            "IMPORTANT RULES:\n"
            "1. You do NOT interpret errors or assign blame. You only describe what happened.\n"
            "2. Summarize content factually — what was the input, what was the output, what did each span do.\n"
            "3. A span can belong to multiple sub-flows if it bridges them.\n"
            "4. Focus on data flow: what data entered each span and what came out.\n"
            "5. Always include span IDs exactly as they appear in the trace.\n"
            "6. Cover ALL spans in the trace — every span should appear in at least one sub-flow.\n\n"
            "OUTPUT FORMAT (strict JSON):\n"
            "{\n"
            '  "sub_flows": [\n'
            "    {\n"
            '      "name": "short descriptive name",\n'
            '      "spans": ["span_id_1", "span_id_2", "span_id_3"],\n'
            '      "summary": "Factual description: user asked X, span_1 received Y...",\n'
            '      "flags": ["factual observation about something unusual, if any"]\n'
            "    }\n"
            "  ],\n"
            '  "trace_overview": "One paragraph factual summary of what the entire trace does end-to-end"\n'
            "}"
        )

        HAIKU_MODEL = os.environ.get("BEDROCK_HAIKU_ARN", "")

        try:
            response = litellm.completion(
                model=HAIKU_MODEL,
                messages=[
                    {"role": "system", "content": CHAUFFEUR_PROMPT},
                    {"role": "user", "content": trace_execution_summary},
                ],
                max_tokens=16000,
                temperature=0.2,
            )
            raw = response.choices[0].message.content

            # Parse JSON from response
            parsed = None
            if "```json" in raw:
                match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group(1))
                    except (json.JSONDecodeError, ValueError):
                        pass
            elif "```" in raw:
                match = re.search(r"```\s*(.*?)\s*```", raw, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group(1))
                    except (json.JSONDecodeError, ValueError):
                        pass

            if not parsed:
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group(0))
                    except (json.JSONDecodeError, ValueError):
                        pass

            if not parsed:
                return {"sub_flows": [], "trace_overview": ""}

            # Normalize format
            if "sub_flows" in parsed:
                return {
                    "sub_flows": parsed["sub_flows"],
                    "trace_overview": parsed.get("trace_overview", ""),
                }
            elif "span_groups" in parsed:
                sub_flows = []
                for g in parsed["span_groups"]:
                    sub_flows.append(
                        {
                            "name": g.get("signal", "unnamed"),
                            "spans": g.get("spans", []),
                            "summary": g.get("signal", ""),
                            "flags": [],
                        }
                    )
                return {"sub_flows": sub_flows, "trace_overview": ""}

            return {"sub_flows": [], "trace_overview": ""}

        except Exception as e:
            logger.warning(f"Chauffeur analysis failed: {e}")
            return {"sub_flows": [], "trace_overview": ""}
