import json
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool

VALID_WIDGET_TYPES = [
    "bar_chart",
    "line_chart",
    "area_chart",
    "pie_chart",
    "donut_chart",
    "heatmap",
    "radar_chart",
    "metric_card",
    "key_value",
    "markdown",
    "code_block",
    "data_table",
    "json_tree",
    "timeline",
    "agent_graph",
    "span_tree",
    "screenshot_annotated",
]


class WidgetPosition(PydanticBaseModel):
    row: int = Field(default=0, description="Grid row (0-indexed)")
    col: int = Field(default=0, description="Grid column (0-11, 12-column grid)")
    colSpan: int = Field(default=6, description="How many columns to span (1-12)")
    rowSpan: int = Field(default=1, description="How many rows to span")


class WidgetConfig(PydanticBaseModel):
    id: Optional[str] = Field(
        default=None,
        description="Widget ID. Auto-generated if not provided. Use same ID to update.",
    )
    type: str = Field(
        description=f"Widget type. One of: {', '.join(VALID_WIDGET_TYPES)}"
    )
    title: Optional[str] = Field(
        default=None, description="Widget title displayed above the visualization"
    )
    position: Optional[WidgetPosition] = Field(
        default=None,
        description="Grid position. Uses 12-column grid. row=0,col=0,colSpan=6 takes left half of first row.",
    )
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Static widget config (data embedded directly). Use for one-off display.\n"
            "- bar_chart/line_chart/area_chart: {series: [{name, data}], categories: []}\n"
            "- pie_chart/donut_chart: {series: [numbers], labels: []}\n"
            "- metric_card: {value, subtitle?, trend?, trendDirection?}\n"
            "- key_value: {items: [{key, value}]}\n"
            "- markdown: {content: 'markdown string'}\n"
            "- data_table: {columns: [{field, headerName}], rows: [{...}]}\n"
            "- timeline/agent_graph/span_tree: {spans: 'from_trace'}\n"
        ),
    )
    dataBinding: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "PREFERRED: Dynamic data binding that resolves against any trace.\n"
            "Makes saved views reusable across traces. Use INSTEAD of static config.\n\n"
            "Binding formats by type:\n"
            "- bar_chart/line_chart/area_chart:\n"
            "  {seriesFromSpans: {name: 'Latency', valuePath: 'latency_ms'},\n"
            "   categoryPath: 'name', labelFormat: '{name} ({observation_type})'}\n"
            "- pie_chart/donut_chart:\n"
            "  {groupBy: 'observation_type', aggregate: 'count'}\n"
            "  (aggregate can be 'count' or 'sum:field_name')\n"
            "- metric_card:\n"
            "  Simple: {valuePath: 'summary.totalDurationMs', valueFormat: '{value}ms',\n"
            "   subtitlePath: 'summary.totalSpans', subtitleFormat: '{value} spans'}\n"
            "  Computed: {compute: 'max(spans.latency_ms, observation_type=llm)', valueFormat: '{value}ms'}\n"
            "  Computed: {compute: 'summary.totalDurationMs - max(spans.latency_ms, observation_type=llm)', valueFormat: '{value}ms'}\n"
            "  Aggregates: max/min/sum/avg/count(spans.field) or filtered: max(spans.field, type=llm)\n"
            "- key_value:\n"
            "  {items: [{key: 'Trace ID', valuePath: 'trace.id'},\n"
            "           {key: 'Input', valuePath: 'rootSpan.input', format: 'truncate:80'}]}\n"
            "- data_table:\n"
            "  {rowsFromSpans: true,\n"
            "   columns: [{field: 'name', headerName: 'Span'},\n"
            "             {field: 'latency_ms', headerName: 'Latency'}]}\n\n"
            "Path format: dot-separated, e.g. 'summary.totalDurationMs', 'rootSpan.input'.\n"
            "Span fields: name, observation_type, latency_ms, total_tokens, status, model, input, output.\n"
            "Summary fields: totalSpans, totalDurationMs, totalTokens, totalCost.\n"
            "Trace fields: id, name, project_name, created_at, tags."
        ),
    )
    dynamicAnalysis: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "For markdown widgets: triggers LLM re-analysis when view opens on a new trace.\n"
            "{prompt: 'Summarize key findings...', contextFields: ['summary', 'rootSpan.input']}\n"
            "Use this for analysis/summary widgets that need LLM reasoning."
        ),
    )


class RenderWidgetInput(PydanticBaseModel):
    action: Literal["add", "update", "replace_all", "remove"] = Field(
        default="add",
        description=(
            "Action to perform: "
            "'add' appends widget (or replaces if same ID), "
            "'update' merges config into existing widget, "
            "'replace_all' replaces entire canvas, "
            "'remove' deletes widget by ID"
        ),
    )
    widget: Optional[WidgetConfig] = Field(
        default=None,
        description="Widget configuration. Required for add/update/remove.",
    )
    widgets: Optional[List[WidgetConfig]] = Field(
        default=None,
        description="Multiple widgets. Used with replace_all to set entire canvas.",
    )


@register_tool
class RenderWidgetTool(BaseTool):
    name = "render_widget"
    description = (
        "Render a visualization widget (chart/graph/plot/metric/table) — in the "
        "Imagine canvas when the user is on the Imagine view, otherwise as a "
        "chart card directly in the chat. Use it to answer chart-shaped "
        "questions in ANY mode ('chart my token usage', 'plot latency per "
        "model', 'show this as a pie chart'). "
        "Call this after fetching the data with other tools; you can call it "
        "multiple times to build a multi-widget answer.\n\n"
        "IMPORTANT: Always fetch real data first (get_trace, get_cost_breakdown, "
        "execute_dashboard_query, etc.) — never invent values.\n\n"
        "On the Imagine view PREFER dataBinding over static config — it makes "
        "views reusable across traces. In normal chat use STATIC config with "
        "the fetched data embedded (dataBinding only resolves against a trace "
        "on the Imagine canvas).\n\n"
        "Available widget types: bar_chart, line_chart, area_chart, pie_chart, "
        "donut_chart, heatmap, radar_chart, metric_card, key_value, markdown, "
        "code_block, data_table, json_tree, timeline, agent_graph, span_tree\n\n"
        "Position uses a 12-column grid. Example layouts:\n"
        "- Two equal columns: col=0,colSpan=6 and col=6,colSpan=6\n"
        "- Three equal: col=0/4/8,colSpan=4\n"
        "- Full width: col=0,colSpan=12\n"
        "- Sidebar + main: col=0,colSpan=4 and col=4,colSpan=8"
    )
    category = "visualization"
    input_model = RenderWidgetInput

    def execute(self, params: RenderWidgetInput, context: ToolContext) -> ToolResult:
        action = params.action

        if action == "replace_all":
            widgets_data = []
            source = params.widgets or ([params.widget] if params.widget else [])
            for w in source:
                wd = w.model_dump()
                if not wd.get("id"):
                    wd["id"] = f"w-{uuid.uuid4().hex[:8]}"
                if wd["type"] not in VALID_WIDGET_TYPES:
                    return ToolResult.error(
                        f"Invalid widget type: {wd['type']}. "
                        f"Valid types: {', '.join(VALID_WIDGET_TYPES)}"
                    )
                widgets_data.append(wd)
            return ToolResult(
                content=json.dumps({"action": "replace_all", "widgets": widgets_data})
            )

        if not params.widget:
            return ToolResult.error("Widget configuration is required")

        widget_data = params.widget.model_dump()
        if not widget_data.get("id"):
            widget_data["id"] = f"w-{uuid.uuid4().hex[:8]}"

        if widget_data["type"] not in VALID_WIDGET_TYPES:
            return ToolResult.error(
                f"Invalid widget type: {widget_data['type']}. "
                f"Valid types: {', '.join(VALID_WIDGET_TYPES)}"
            )

        if not widget_data.get("position"):
            widget_data["position"] = {"row": 0, "col": 0, "colSpan": 12, "rowSpan": 1}

        return ToolResult(content=json.dumps({"action": action, "widget": widget_data}))
