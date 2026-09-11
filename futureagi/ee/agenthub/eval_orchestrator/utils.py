"""
Utility functions for the EvalOrchestrator.

- build_input_summary: produce a lightweight surface-level summary for the Scout
- build_trace_outline: hierarchical span tree (structure only, no content)
- serialize_span_full: full span content dict for the Orchestrator's inspect_data tool
- gather_available_resources: collect what KB/feedback/MCP resources are connected
"""

import json
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Input summary builders (for Scout)
# ---------------------------------------------------------------------------

def build_input_summary(scope: str, source_id: str) -> str:
    """
    Build a lightweight input summary for the Scout.
    Produces a surface-level view without loading full content.

    Args:
        scope: "span" | "trace" | "session" | "dataset_row" | "cell"
        source_id: ID of the object to summarise

    Returns:
        Formatted string summary
    """
    try:
        if scope == "span":
            return _summarise_span(source_id)
        elif scope == "trace":
            return _summarise_trace(source_id)
        elif scope == "session":
            return _summarise_session(source_id)
        elif scope == "dataset_row":
            return _summarise_dataset_row(source_id)
        elif scope == "cell":
            return _summarise_cell(source_id)
        else:
            return f"Unknown scope: {scope} (id={source_id})"
    except Exception as e:
        logger.error("build_input_summary failed", scope=scope, source_id=source_id, error=str(e))
        return f"[Summary unavailable for {scope} id={source_id}: {e}]"


def _summarise_span(span_id: str) -> str:
    from tracer.models.observation_span import ObservationSpan

    span = ObservationSpan.objects.get(id=span_id)
    input_preview = _truncate(span.input, 500)
    output_preview = _truncate(span.output, 500)
    return (
        f"SPAN: {span.name} (type: {span.observation_type})\n"
        f"Status: {span.status}\n"
        f"Model: {getattr(span, 'model', None)}\n"
        f"Input preview: {input_preview}\n"
        f"Output preview: {output_preview}\n"
        f"Has metadata: {bool(span.metadata)}"
    )


def _summarise_trace(trace_id: str) -> str:
    from tracer.models.trace import Trace
    from tracer.models.observation_span import ObservationSpan

    trace = Trace.objects.get(id=trace_id)
    spans = ObservationSpan.objects.filter(trace_id=trace_id).order_by("start_time", "created_at")
    outline = build_trace_outline(trace, spans)
    return f"TRACE: {trace.name or trace_id}\n{outline}"


def _summarise_session(session_id: str) -> str:
    from tracer.models.trace_session import TraceSession
    from tracer.models.trace import Trace
    from tracer.models.observation_span import ObservationSpan

    session = TraceSession.objects.get(id=session_id)
    traces = Trace.objects.filter(session_id=session_id).prefetch_related("observation_spans")
    lines = [
        f"SESSION: {getattr(session, 'name', None) or session_id}",
        f"Trace count: {traces.count()}",
        "",
    ]
    for t in traces:
        span_count = t.observation_spans.count()
        error_spans = ObservationSpan.objects.filter(
            trace_id=t.id, status="error"
        ).count()
        input_preview = _truncate(t.input, 200)
        lines.append(
            f"  - Trace: {t.name or t.id} | Spans: {span_count} | "
            f"Errors: {error_spans} | Input preview: {input_preview}"
        )
    return "\n".join(lines)


def _summarise_dataset_row(row_id: str) -> str:
    from model_hub.models.develop_dataset import Row, Cell

    row = Row.objects.get(id=row_id)
    cells = Cell.objects.filter(row=row).select_related("column")
    lines = [f"DATASET ROW: {row_id}", "Columns:"]
    for cell in cells:
        data_type = getattr(cell.column, "data_type", "text")
        preview = _truncate(cell.value, 200)
        lines.append(f"  - {cell.column.name} [{data_type}]: {preview}")
    return "\n".join(lines)


def _summarise_cell(cell_id: str) -> str:
    from model_hub.models.develop_dataset import Cell

    cell = Cell.objects.select_related("column", "row").get(id=cell_id)
    data_type = getattr(cell.column, "data_type", "text")
    preview = _truncate(cell.value, 500)
    return (
        f"DATASET CELL: {cell_id}\n"
        f"Column: {cell.column.name}\n"
        f"Data type: {data_type}\n"
        f"Row: {cell.row_id}\n"
        f"Value: {preview}"
    )


# ---------------------------------------------------------------------------
# Trace outline builder (structure only — no input/output content)
# ---------------------------------------------------------------------------

def build_trace_outline(trace: Any, spans: Any) -> str:
    """
    Build a lightweight hierarchical outline of a trace.

    Each span appears as:
        {path}  [{type}] {name} [{status}] ({latency}ms) (ID: {id})

    No input/output content is included — this is the Scout's surface view.
    Mirrors TraceErrorAnalysisAgent._build_trace_outline() / create_trace_execution_summary().
    """
    span_list = list(spans)

    # Build id → node map and parent → children map
    id_to_node: dict[str, dict] = {}
    for span in span_list:
        id_to_node[str(span.id)] = {"span": span, "children": []}

    roots: list[dict] = []
    for span in span_list:
        parent_id = str(span.parent_span_id) if span.parent_span_id else None
        if parent_id and parent_id in id_to_node:
            id_to_node[parent_id]["children"].append(id_to_node[str(span.id)])
        else:
            roots.append(id_to_node[str(span.id)])

    # Sort roots and children by start_time (fall back to created_at)
    def _sort_key(node: dict):
        sp = node["span"]
        return (
            getattr(sp, "start_time", None)
            or getattr(sp, "created_at", None)
            or datetime.max
        )

    roots.sort(key=_sort_key)
    for node in id_to_node.values():
        node["children"].sort(key=_sort_key)

    trace_name = getattr(trace, "name", None) or str(getattr(trace, "id", "unknown"))
    lines = [
        f"TRACE: {trace_name}",
        f"Total spans: {len(span_list)}",
        "",
    ]

    def walk(node: dict, path: str) -> None:
        sp = node["span"]
        latency = getattr(sp, "latency_ms", None)
        latency_str = f"{latency}ms" if latency is not None else "?"
        lines.append(
            f"{path}  [{sp.observation_type}] {sp.name or '(unnamed)'} "
            f"[{sp.status}] ({latency_str}) (ID: {sp.id})"
        )
        for i, child in enumerate(node["children"], 1):
            walk(child, f"{path}.{i}")

    for i, root in enumerate(roots, 1):
        walk(root, str(i))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full span serializer (for Orchestrator's inspect_data detail depth)
# ---------------------------------------------------------------------------

def serialize_span_full(span: Any) -> dict:
    """Return a full content dict for a span — used in inspect_data(depth='detail')."""
    return {
        "span_id": str(span.id),
        "name": span.name,
        "type": span.observation_type,
        "status": span.status,
        "input": span.input,
        "output": span.output,
        "metadata": span.metadata,
        "model": getattr(span, "model", None),
        "latency_ms": getattr(span, "latency_ms", None),
        "parent_span_id": str(span.parent_span_id) if span.parent_span_id else None,
    }


# ---------------------------------------------------------------------------
# Resource gatherer (for Scout + Orchestrator tool list builder)
# ---------------------------------------------------------------------------

def gather_available_resources(
    organization_id: str,
    eval_template_id: str | None = None,
    kb_id: str | None = None,
) -> dict:
    """
    Collect what external resources are connected to this eval.
    Called by the BE before constructing EvalConfig / orchestrator.

    Args:
        eval_template_id: UUID of the EvalTemplate (for feedback count).
        organization_id:  Org UUID from request context.
        kb_id:            KnowledgeBaseFile UUID, or None.

    Returns:
        {
            "knowledge_bases": [{"id", "name", "description"}],
            "feedback_count":  int,
            "mcp_tools":       [],
        }
    """
    resources: dict[str, Any] = {
        "knowledge_bases": [],
        "feedback_count": 0,
        "mcp_tools": [],
    }

    # Knowledge base
    if kb_id:
        try:
            from model_hub.models.develop_dataset import KnowledgeBaseFile  # type: ignore
            kb = KnowledgeBaseFile.objects.get(id=kb_id)
            resources["knowledge_bases"].append({
                "id": str(kb.id),
                "name": getattr(kb, "name", str(kb.id)),
                "description": getattr(kb, "description", ""),
            })
        except Exception:
            resources["knowledge_bases"].append({
                "id": str(kb_id),
                "name": "Knowledge Base",
                "description": "",
            })

    # Feedback examples count
    if eval_template_id:
        try:
            from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager
            em = EmbeddingManager()
            try:
                count = em.get_feedback_count(
                    eval_id=eval_template_id,
                    organization_id=organization_id,
                    workspace_id=None,
                )
                resources["feedback_count"] = count or 0
            except AttributeError:
                resources["feedback_count"] = 0
            finally:
                em.close()
        except Exception as e:
            logger.debug("Could not fetch feedback count", error=str(e))

    # MCP tools — fetch from Falcon AI connectors for this org
    try:
        from ee.falcon_ai.models import MCPConnector

        connectors = MCPConnector.objects.filter(
            organization_id=organization_id,
            is_active=True,
            is_verified=True,
        )
        mcp_tools = []
        for connector in connectors:
            discovered = connector.discovered_tools or []
            enabled = connector.enabled_tool_names or []
            for tool_schema in discovered:
                tool_name = tool_schema.get("name", "")
                # If enabled list is set, only include enabled tools
                if enabled and tool_name not in enabled:
                    continue
                mcp_tools.append({
                    "name": tool_name,
                    "description": tool_schema.get("description", ""),
                })
        resources["mcp_tools"] = mcp_tools
    except Exception as e:
        logger.debug("Could not fetch MCP tools", error=str(e))

    return resources

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(value: Any, max_chars: int) -> str:
    """Convert value to string and truncate to max_chars."""
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)
    if len(value) > max_chars:
        return value[:max_chars] + f"... [{len(value) - max_chars} chars truncated]"
    return value
