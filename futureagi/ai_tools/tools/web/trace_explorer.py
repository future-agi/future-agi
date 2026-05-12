"""
Eval-context Explorer tool for AI eval agents.

Instead of dumping entire trace / session / span / call / row data into
the prompt, this tool lets the agent navigate and search through large
structured data on demand. Data is keyed by (eval_id, root) where `root`
is one of: row, span, trace, session, call.

The agent can:
- List top-level keys with type + size hints (`keys`)
- Drill into a nested path with `get` (e.g. `transcript[0].content`)
- Substring-search across the entire payload (`search`)
- For trace-shaped data: tree/summary/errors/slow_spans actions
"""

import json
from typing import Optional

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)

# In-memory store for eval context data during an eval run.
# Keyed by (eval_id, root) so each auto-context root (row/span/trace/
# session/call) has its own slot. Cleared per eval run in the evaluator
# finally block.
_context_store: dict[tuple[str, str], object] = {}


def load_context_data(eval_id: str, root: str, data) -> None:
    """Load auto-context data into the store for an eval run.

    `root` is one of: row, span, trace, session, call. Each eval_id can
    hold one blob per root, so an eval that references both `{{span}}`
    and `{{trace}}` gets both loaded under the same eval_id.
    """
    _context_store[(eval_id, root)] = data


def clear_context_data(eval_id: str) -> None:
    """Clear every context entry for a given eval_id."""
    for key in [k for k in _context_store if k[0] == eval_id]:
        _context_store.pop(key, None)


# Backward-compat wrappers — older call sites used (eval_id) only and
# implicitly assumed trace/row data. They now route to the "row" root.
def load_trace_data(eval_id: str, data) -> None:
    """Legacy alias — stores under root="row"."""
    load_context_data(eval_id, "row", data)


def clear_trace_data(eval_id: str) -> None:
    """Legacy alias — clears all roots for this eval_id."""
    clear_context_data(eval_id)


class TraceExploreInput(PydanticBaseModel):
    eval_id: str = Field(
        description="The evaluation run ID (provided in the eval context)"
    )
    root: str = Field(
        default="row",
        description=(
            "Which context blob to explore. One of: row, span, trace, "
            "session, call. Pick the root that matches what the prompt "
            "refers to with {{root}} or {{root.X}}. Default: row."
        ),
    )
    action: str = Field(
        description=(
            "Action to perform. Works on any root: "
            "'keys' (list fields), 'get' (read path, query=field.path), "
            "'search' (find text, query=substring). "
            "Trace-only (root=trace): "
            "'summary' (overview), 'span_tree' (hierarchy), "
            "'filter_spans' (query=field=value, e.g. observation_type=llm), "
            "'span_detail' (query=span_id, fetches full input/output), "
            "'errors', 'slow_spans'. "
            "Session-only (root=session): "
            "'list_trace_spans' (query=trace_id, fetches spans for a trace)."
        )
    )
    query: Optional[str] = Field(
        default=None,
        description=(
            "For 'get': a dot/bracket path like `transcript[0].content` or "
            "`metrics.user_wpm`. For 'search': a substring to find. For "
            "'span_detail': the span ID or name."
        ),
    )
    limit: int = Field(default=5, ge=1, le=20, description="Max results to return")


@register_tool
class TraceExplorerTool(BaseTool):
    name = "explore_trace"
    description = (
        "Explore eval context data. Use the `root` parameter to select which "
        "context to explore (trace/session/span/call/row). "
        "See the 'Additional Context Available' or 'Eval Context' section in "
        "your prompt for which roots are loaded and recommended actions. "
        "Common workflows:\n"
        "- Trace: summary → filter_spans query='observation_type=llm' → span_detail query='<id>'\n"
        "- Session: keys → get query='traces[0]' → list_trace_spans query='<trace_id>' → span_detail\n"
        "- Span/Call: keys → get query='input' or get query='transcript'\n"
        "- Row: keys → get query='<column_name>'"
    )
    category = "web"  # Same category as web_search so it loads together
    input_model = TraceExploreInput

    def execute(self, params: TraceExploreInput, context: ToolContext) -> ToolResult:
        data = _context_store.get((params.eval_id, params.root))
        if data is None:
            # Legacy fallback: older call sites that predate the `root` param
            # stored data keyed only by eval_id. We translate that to
            # root="row" in the store, so this is an explicit user error.
            available = sorted(
                {r for (eid, r) in _context_store if eid == params.eval_id}
            )
            if available:
                return ToolResult.needs_input(
                    f"No data loaded under root='{params.root}' for eval "
                    f"{params.eval_id}. Available roots: {available}.",
                    data={"available_roots": available},
                    missing_fields=["root"],
                )
            return ToolResult.needs_input(
                f"No context data loaded for eval {params.eval_id}. "
                "This tool is only available when the eval prompt references "
                "{{row}} / {{span}} / {{trace}} / {{session}} / {{call}}.",
                data={"available_roots": []},
                missing_fields=["eval_id"],
            )

        spans = _extract_spans(data) if isinstance(data, (dict, list)) else []

        # Generic actions — work on any data shape
        if params.action == "keys":
            return self._keys(data)
        elif params.action == "get":
            return self._get_path(data, params.query)
        elif params.action == "search":
            # If we have spans, use the structured span search; otherwise
            # fall back to generic substring search across the full blob.
            if spans:
                return self._search(spans, params.query, params.limit)
            return self._generic_search(data, params.query, params.limit)
        elif params.action == "summary":
            # Shape-aware: trace → span summary, anything else → keys view.
            if spans:
                return self._summary(data, spans)
            return self._generic_summary(data, params.root)

        # Live DB actions — work without pre-loaded spans
        if params.action == "list_trace_spans":
            return self._list_trace_spans(params.query, params.limit)

        # Filter action — works on loaded spans
        if params.action == "filter_spans":
            if not spans:
                return ToolResult.error(
                    "No spans available to filter. Use action='keys' to see data."
                )
            return self._filter_spans(spans, params.query, params.limit)

        # Trace-specific actions — require span-shaped data
        if not spans:
            return ToolResult.error(
                f"action='{params.action}' only works on trace-shaped data "
                f"(root that has spans/observation_spans). This blob has none. "
                f"Try action='keys' or action='get' query='path' instead.",
                error_code="NO_SPANS",
            )
        if params.action == "span_detail":
            return self._span_detail(spans, params.query)
        elif params.action == "errors":
            return self._errors(spans, params.limit)
        elif params.action == "slow_spans":
            return self._slow_spans(spans, params.limit)
        elif params.action == "span_tree":
            return self._span_tree(spans)
        else:
            return ToolResult.error(
                f"Unknown action: {params.action}. "
                "Generic: keys, get, search, summary. "
                "Trace-specific: span_detail, errors, slow_spans, span_tree."
            )

    # ------------------------------------------------------------------
    # Generic actions (work on any dict/list payload)
    # ------------------------------------------------------------------

    def _keys(self, data):
        """List top-level keys with type + size hints."""
        if isinstance(data, dict):
            lines = ["## Keys", ""]
            for k in sorted(data.keys()):
                v = data[k]
                if isinstance(v, list):
                    lines.append(f"- `{k}`: list[{len(v)} items]")
                elif isinstance(v, dict):
                    lines.append(f"- `{k}`: dict[{len(v)} keys]")
                elif isinstance(v, str):
                    lines.append(f"- `{k}`: str[{len(v)} chars]")
                elif v is None:
                    lines.append(f"- `{k}`: null")
                else:
                    lines.append(f"- `{k}`: {type(v).__name__} = {v}")
            return ToolResult(content="\n".join(lines), data={"key_count": len(data)})
        if isinstance(data, list):
            return ToolResult(
                content=f"Top-level is a list of {len(data)} items. "
                f"Use action='get' query='[0]' to read the first entry.",
                data={"length": len(data)},
            )
        return ToolResult(
            content=f"Top-level value: {type(data).__name__} = {str(data)[:500]}"
        )

    def _get_path(self, data, path: Optional[str]):
        """Walk a dot / bracket path into the payload and return that value.

        Supports: `a.b.c`, `a[0].b`, `[0]`, `a.b[2].c`.
        """
        if not path:
            return ToolResult.error(
                "Provide a path in `query`, e.g. `transcript[0].content` or "
                "`metrics.user_wpm`."
            )
        try:
            current = data
            i = 0
            segment = ""
            path_len = len(path)
            while i < path_len:
                ch = path[i]
                if ch == ".":
                    if segment:
                        current = _dict_get(current, segment)
                        segment = ""
                    i += 1
                elif ch == "[":
                    if segment:
                        current = _dict_get(current, segment)
                        segment = ""
                    end = path.find("]", i)
                    if end == -1:
                        return ToolResult.error(f"Unclosed bracket in path: {path}")
                    idx = path[i + 1 : end]
                    try:
                        idx_int = int(idx)
                    except ValueError:
                        return ToolResult.error(
                            f"Non-integer index '{idx}' in path: {path}"
                        )
                    if not isinstance(current, list):
                        return ToolResult.error(
                            f"Cannot index [{idx_int}] into non-list at '{path[:i]}'"
                        )
                    if idx_int < 0 or idx_int >= len(current):
                        return ToolResult.error(
                            f"Index {idx_int} out of range (len={len(current)}) "
                            f"at '{path[:i]}'"
                        )
                    current = current[idx_int]
                    i = end + 1
                else:
                    segment += ch
                    i += 1
            if segment:
                current = _dict_get(current, segment)
        except _MissingKey as mk:
            return ToolResult.error(
                f"Path not found: {mk}. Use action='keys' to see what's available."
            )

        # Render the value (JSON for dict/list, truncate huge payloads)
        if isinstance(current, (dict, list)):
            rendered = json.dumps(current, default=str, indent=2, ensure_ascii=False)
        else:
            rendered = str(current)
        if len(rendered) > 4000:
            rendered = (
                rendered[:4000] + f"\n... [truncated, {len(rendered)} chars total]"
            )
        return ToolResult(
            content=f"## Path: `{path}`\n\n{rendered}",
            data={"path": path, "type": type(current).__name__},
        )

    def _generic_search(self, data, query, limit):
        """Substring search across the entire blob's serialized form.

        Returns matching top-level keys and any list entries where the
        query appears.
        """
        if not query:
            return ToolResult.error("Provide a search query, e.g. 'error' or 'refund'.")
        q = query.lower()
        hits = []
        if isinstance(data, dict):
            for k, v in data.items():
                if k.lower().find(q) >= 0:
                    hits.append(f"- key match: `{k}`")
                try:
                    blob = json.dumps(v, default=str, ensure_ascii=False).lower()
                except Exception:
                    blob = str(v).lower()
                if q in blob:
                    snippet = _snippet_around(blob, q, width=120)
                    hits.append(f"- `{k}`: ...{snippet}...")
                if len(hits) >= limit:
                    break
        elif isinstance(data, list):
            for i, item in enumerate(data):
                try:
                    blob = json.dumps(item, default=str, ensure_ascii=False).lower()
                except Exception:
                    blob = str(item).lower()
                if q in blob:
                    snippet = _snippet_around(blob, q, width=120)
                    hits.append(f"- [{i}]: ...{snippet}...")
                if len(hits) >= limit:
                    break
        if not hits:
            return ToolResult(content=f"No matches for '{query}'.", data={"matches": 0})
        return ToolResult(
            content=f"## Search '{query}' ({len(hits)} matches)\n\n" + "\n".join(hits),
            data={"matches": len(hits)},
        )

    def _generic_summary(self, data, root: str):
        """For non-trace data, a summary is a key listing plus sample values."""
        if not isinstance(data, dict):
            return self._keys(data)
        lines = [f"## {root.capitalize()} summary", ""]
        size = len(json.dumps(data, default=str))
        lines.append(f"**Total size:** {size:,} chars")
        lines.append(f"**Top-level keys:** {len(data)}")
        lines.append("")
        lines.append("**Key overview:**")
        for k in sorted(data.keys()):
            v = data[k]
            if isinstance(v, list):
                lines.append(f"- `{k}`: list[{len(v)} items]")
            elif isinstance(v, dict):
                lines.append(f"- `{k}`: dict[{len(v)} keys]")
            elif isinstance(v, str):
                if len(v) < 80:
                    lines.append(f"- `{k}`: {v!r}")
                else:
                    lines.append(f"- `{k}`: str[{len(v)} chars] — {v[:60]!r}...")
            elif v is None:
                lines.append(f"- `{k}`: null")
            else:
                lines.append(f"- `{k}`: {v}")
        lines.append("")
        lines.append(
            "Use action='get' query='<key>' to read a field, or "
            "action='search' query='...' to find text across the whole blob."
        )
        return ToolResult(content="\n".join(lines), data={"key_count": len(data)})

    def _summary(self, data, spans):
        """High-level trace overview."""
        total_spans = len(spans)
        error_count = sum(1 for s in spans if s.get("status") == "ERROR")
        total_latency = data.get(
            "total_latency_ms", sum(s.get("latency_ms") or 0 for s in spans)
        )
        total_tokens = sum(s.get("total_tokens") or 0 for s in spans)
        total_cost = sum(s.get("cost") or 0 for s in spans)
        types = {}
        models = set()
        for s in spans:
            t = s.get("observation_type", "unknown")
            types[t] = types.get(t, 0) + 1
            m = s.get("model")
            if m:
                models.add(m)

        _status = "error" if error_count > 0 else "ok"
        lines = [
            f"## Trace Summary",
            f"**Name:** {data.get('trace_name', data.get('name', 'unnamed'))}",
            f"**Status:** {_status}",
            f"**Total spans:** {total_spans}",
            f"**Errors:** {error_count}",
            f"**Total latency:** {total_latency}ms",
            f"**Total tokens:** {total_tokens}",
            f"**Total cost:** ${total_cost:.4f}",
            f"**Models:** {', '.join(models) if models else 'none'}",
            f"**Span types:** {json.dumps(types)}",
            "",
            "**Span list:**",
        ]
        for i, s in enumerate(spans):
            status_icon = "❌" if s.get("status") == "ERROR" else "✓"
            model_str = f" model={s['model']}" if s.get("model") else ""
            tokens_str = f" {s['total_tokens']}tok" if s.get("total_tokens") else ""
            lines.append(
                f"  {i+1}. {status_icon} [{s.get('observation_type','?')}] {s.get('name','?')} "
                f"({s.get('latency_ms') or 0}ms{model_str}{tokens_str}) "
                f"id=`{s.get('id', '?')}`"
            )

        return ToolResult(
            content="\n".join(lines),
            data={"span_count": total_spans, "error_count": error_count},
        )

    def _search(self, spans, query, limit):
        """Search spans by name, type, status, or content."""
        if not query:
            return ToolResult.error(
                "Provide a search query (e.g., 'error', 'llm', span name)"
            )

        q = query.lower()
        matches = []
        for s in spans:
            searchable = json.dumps(s, default=str).lower()
            if q in searchable:
                matches.append(s)

        if not matches:
            return ToolResult(
                content=f"No spans matching '{query}' in span names/types/models. "
                f"Note: search only checks span summary fields (name, type, model, status), "
                f"not full input/output. Use `span_detail` to see the full content of a specific span.",
                data={"matches": 0},
            )

        lines = [f"## Search Results for '{query}' ({len(matches)} matches)\n"]
        for s in matches[:limit]:
            lines.append(self._format_span_summary(s))

        return ToolResult(content="\n".join(lines), data={"matches": len(matches)})

    def _span_detail(self, spans, span_id):
        """Get full details of a specific span.

        If the span data in the store is a lightweight summary (from enriched
        trace/session context), fetches the full span from the database to
        provide input, output, span_attributes, etc.
        """
        if not span_id:
            return ToolResult.error("Provide a span ID or span name")

        target = None
        for s in spans:
            if s.get("id") == span_id or s.get("name") == span_id:
                target = s
                break

        # Try partial match
        if not target:
            q = span_id.lower()
            for s in spans:
                if q in s.get("name", "").lower() or q in s.get("id", "").lower():
                    target = s
                    break

        if not target:
            return ToolResult.error(f"Span '{span_id}' not found")

        # If the span is a lightweight summary (no input/output), fetch
        # the full span from DB for detailed inspection.
        if target.get("id") and "input" not in target and "output" not in target:
            try:
                full_span = _fetch_full_span(target["id"])
                if full_span:
                    target = full_span
            except Exception as e:
                logger.warning(f"Failed to fetch full span {target.get('id')}: {e}")

        lines = [f"## Span Detail: {target.get('name', '?')}\n"]
        for key, val in target.items():
            if val is None or val == "" or val == 0 or val == []:
                continue
            val_str = (
                json.dumps(val, default=str, indent=2)
                if isinstance(val, (dict, list))
                else str(val)
            )
            if len(val_str) > 1000:
                val_str = (
                    val_str[:1000] + f"\n... [truncated, {len(val_str)} chars total]"
                )
            lines.append(f"**{key}:** {val_str}")

        return ToolResult(content="\n".join(lines), data={"span_id": target.get("id")})

    def _errors(self, spans, limit):
        """List all error spans."""
        errors = [s for s in spans if s.get("status") == "ERROR"]
        if not errors:
            return ToolResult(
                content="No error spans found in this trace.", data={"error_count": 0}
            )

        lines = [f"## Error Spans ({len(errors)} found)\n"]
        for s in errors[:limit]:
            lines.append(self._format_span_summary(s))
            if s.get("status_message"):
                lines.append(f"  **Error:** {s['status_message']}")

        return ToolResult(content="\n".join(lines), data={"error_count": len(errors)})

    def _slow_spans(self, spans, limit):
        """Find the slowest spans."""
        sorted_spans = sorted(spans, key=lambda s: s.get("latency_ms") or 0, reverse=True)

        lines = [f"## Slowest Spans (top {limit})\n"]
        for s in sorted_spans[:limit]:
            lines.append(self._format_span_summary(s))

        return ToolResult(content="\n".join(lines), data={"total_spans": len(spans)})

    def _span_tree(self, spans):
        """Show the span hierarchy."""
        # Build parent-child map
        children = {}
        roots = []
        for s in spans:
            parent = s.get("parent_span_id")
            if parent:
                children.setdefault(parent, []).append(s)
            else:
                roots.append(s)

        if not roots:
            roots = spans  # Flat list, no hierarchy

        lines = ["## Span Tree\n"]

        def _render(span, depth=0):
            indent = "  " * depth
            status = "❌" if span.get("status") == "ERROR" else "✓"
            lines.append(
                f"{indent}{status} [{span.get('observation_type','?')}] {span.get('name','?')} "
                f"({span.get('latency_ms') or 0}ms) id=`{span.get('id', '?')}`"
            )
            for child in children.get(span.get("id"), []):
                _render(child, depth + 1)

        for root in roots:
            _render(root)

        return ToolResult(content="\n".join(lines))

    def _filter_spans(self, spans, query, limit):
        """Filter spans by field=value criteria.

        Examples:
          observation_type=llm
          status=ERROR
          model=gpt-4o
          observation_type=guardrail
        """
        if not query or "=" not in query:
            return ToolResult.error(
                "Provide filter as field=value. Examples:\n"
                "- observation_type=llm (LLM calls)\n"
                "- observation_type=guardrail (guardrail checks)\n"
                "- observation_type=agent (agent spans)\n"
                "- observation_type=retriever (RAG retrieval)\n"
                "- status=ERROR (failed spans)\n"
                "- model=gpt-4o (specific model)"
            )

        field, value = query.split("=", 1)
        field = field.strip()
        value = value.strip().lower()

        matches = [
            s for s in spans
            if str(s.get(field, "")).lower() == value
        ]

        if not matches:
            # Show available values for that field to help the agent
            available = sorted(set(
                str(s.get(field, "")) for s in spans if s.get(field)
            ))
            return ToolResult(
                content=f"No spans match `{field}={value}`. "
                f"Available values for `{field}`: {', '.join(available[:15])}",
                data={"matches": 0, "available_values": available[:15]},
            )

        lines = [f"## Filtered: {field}={value} ({len(matches)} spans)\n"]
        for s in matches[:limit]:
            lines.append(self._format_span_summary(s))
            if s.get("status_message"):
                lines.append(f"  → {s['status_message'][:100]}")

        if len(matches) > limit:
            lines.append(f"\n... and {len(matches) - limit} more. Increase `limit` to see more.")

        lines.append(
            f"\nUse action='span_detail' query='<span_id>' to see "
            f"full input/output of a specific span."
        )

        return ToolResult(
            content="\n".join(lines),
            data={"matches": len(matches), "field": field, "value": value},
        )

    def _list_trace_spans(self, trace_id, limit):
        """Fetch spans for a specific trace from DB on demand.

        Useful when exploring session context — the agent sees trace
        summaries and drills into a specific trace to see its spans.

        Data isolation: scoped by project → organization via request context.
        """
        if not trace_id:
            return ToolResult.error(
                "Provide a trace_id in `query` to list its spans."
            )
        try:
            from tracer.models.observation_span import ObservationSpan
            from tfc.middleware.workspace_context import get_current_organization

            # Org scoping is the only authorization check between an
            # agent-supplied trace_id and the DB. If we don't have an org
            # context, refuse rather than running an unscoped query.
            org = get_current_organization()
            if not org:
                logger.warning(
                    "_list_trace_spans called without organization context; refusing"
                )
                return ToolResult.error(
                    "Cannot list spans without an authenticated organization context."
                )

            qs = ObservationSpan.objects.filter(
                trace_id=trace_id, deleted=False, project__organization=org
            )

            spans = list(
                qs
                .order_by("start_time")
                .values(
                    "id", "name", "observation_type", "status",
                    "status_message", "latency_ms", "model",
                    "total_tokens", "cost", "parent_span_id",
                )[:limit * 10]  # Allow more for tree view
            )
            if not spans:
                return ToolResult(
                    content=f"No spans found for trace `{trace_id}`.",
                    data={"span_count": 0},
                )

            # Build a tree view
            children = {}
            roots = []
            for s in spans:
                parent = s.get("parent_span_id")
                if parent:
                    children.setdefault(parent, []).append(s)
                else:
                    roots.append(s)
            if not roots:
                roots = spans

            error_count = sum(1 for s in spans if s.get("status") == "ERROR")
            lines = [
                f"## Spans for trace `{trace_id}` "
                f"({len(spans)} spans, {error_count} errors)\n",
            ]

            def _render(span, depth=0):
                indent = "  " * depth
                status = "ERR" if span.get("status") == "ERROR" else "OK"
                lines.append(
                    f"{indent}- [{status}] [{span.get('observation_type', '?')}] "
                    f"**{span.get('name', '?')}** "
                    f"({span.get('latency_ms', 0)}ms"
                    f"{', ' + span['model'] if span.get('model') else ''}) "
                    f"id=`{span.get('id', '?')}`"
                )
                for child in children.get(span.get("id"), []):
                    _render(child, depth + 1)

            for root in roots:
                _render(root)

            lines.append(
                f"\nUse action='span_detail' query='<span_id>' to see "
                f"full input/output of a specific span."
            )

            return ToolResult(
                content="\n".join(lines),
                data={"span_count": len(spans), "trace_id": trace_id},
            )
        except Exception as e:
            logger.warning(f"_list_trace_spans failed for {trace_id}: {e}")
            return ToolResult.error(f"Failed to fetch spans for trace: {e}")

    def _format_span_summary(self, s):
        """Format a single span as a summary line with ID for drill-down."""
        parts = [f"- **{s.get('name','?')}** [{s.get('observation_type','?')}]"]
        if s.get("model"):
            parts.append(f"model={s['model']}")
        parts.append(f"{s.get('latency_ms') or 0}ms")
        if s.get("total_tokens"):
            parts.append(f"{s['total_tokens']} tokens")
        if s.get("status") and s["status"] != "OK":
            parts.append(f"status={s['status']}")
        parts.append(f"id=`{s.get('id', '?')}`")
        return " | ".join(parts)


def _extract_spans(data):
    """Extract flat list of spans from various data formats."""
    if isinstance(data, list):
        # Could be a list of spans, or a list of items that isn't spans.
        # If any entry looks like a span, treat it as a span list.
        if (
            data
            and isinstance(data[0], dict)
            and (data[0].get("observation_type") or data[0].get("span_attributes"))
        ):
            return data
        return []

    if not isinstance(data, dict):
        return []

    # Direct spans list
    spans = data.get("spans") or data.get("observation_spans") or []
    if spans:
        # Unwrap nested format
        flat = []
        for s in spans:
            if isinstance(s, dict):
                if "observationSpan" in s:
                    flat.append(s["observationSpan"])
                else:
                    flat.append(s)
        return flat

    # Single span (has span-specific fields at top level)
    if data.get("observation_type") or data.get("span_attributes"):
        return [data]

    return []


# ------------------------------------------------------------------
# Path navigation helpers for the `get` action
# ------------------------------------------------------------------


class _MissingKey(Exception):
    """Raised when a dotted path segment is missing from a dict."""


def _dict_get(obj, key: str):
    """Read `key` from a dict, raising _MissingKey with a helpful message."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        # Try camelCase <-> snake_case fallback
        alt = _camel_to_snake(key)
        if alt != key and alt in obj:
            return obj[alt]
        alt2 = _snake_to_camel(key)
        if alt2 != key and alt2 in obj:
            return obj[alt2]
        raise _MissingKey(f"key '{key}' not in dict (keys: {list(obj.keys())[:20]})")
    # Pydantic / dataclass-like objects — try attribute lookup
    if hasattr(obj, key):
        return getattr(obj, key)
    raise _MissingKey(f"cannot read key '{key}' from {type(obj).__name__}")


def _camel_to_snake(s: str) -> str:
    import re as _re

    return _re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()


def _snake_to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def _snippet_around(blob: str, needle: str, width: int = 120) -> str:
    """Return a short substring around the first match of `needle` in `blob`."""
    idx = blob.find(needle)
    if idx == -1:
        return blob[:width]
    start = max(0, idx - width // 2)
    end = min(len(blob), idx + len(needle) + width // 2)
    return blob[start:end]


def _fetch_full_span(span_id: str) -> dict | None:
    """Fetch full span details from DB for on-demand drill-down.

    Called when the agent requests span_detail on a lightweight summary
    (from enriched trace/session context that only has id/name/status/latency).
    Returns a rich dict matching the format of _build_span_context().

    Data isolation: scoped by project → organization via the request context.
    Org scoping is mandatory — if no org is in context (e.g. background worker
    without the workspace middleware), the call refuses rather than running
    an unscoped query against an LLM-supplied span_id.
    """
    try:
        from tracer.models.observation_span import ObservationSpan
        from tfc.middleware.workspace_context import get_current_organization

        org = get_current_organization()
        if not org:
            logger.warning(
                "_fetch_full_span called without organization context; refusing"
            )
            return None

        span = ObservationSpan.objects.filter(
            id=str(span_id), deleted=False, project__organization=org
        ).first()
        if not span:
            return None

        result = {
            "id": span.id,
            "trace_id": str(span.trace_id) if span.trace_id else None,
            "name": span.name,
            "observation_type": span.observation_type,
            "input": span.input,
            "output": span.output,
            "status": span.status,
            "status_message": span.status_message,
            "model": span.model,
            "provider": span.provider,
            "start_time": str(span.start_time) if span.start_time else None,
            "end_time": str(span.end_time) if span.end_time else None,
            "latency_ms": span.latency_ms,
            "cost": float(span.cost) if span.cost is not None else None,
            "prompt_tokens": span.prompt_tokens,
            "completion_tokens": span.completion_tokens,
            "total_tokens": span.total_tokens,
            "metadata": span.metadata or {},
            "tags": span.tags or [],
            "parent_span_id": span.parent_span_id,
            "span_attributes": span.span_attributes or {},
        }
        return result
    except Exception as e:
        logger.warning(f"_fetch_full_span failed for {span_id}: {e}")
        return None
