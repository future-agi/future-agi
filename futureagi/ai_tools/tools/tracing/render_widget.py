import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, model_validator

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

LEGACY_WIDGET_TYPE_ALIASES = {
    "table": "data_table",
    "chart": "bar_chart",
    "bar": "bar_chart",
    "line": "line_chart",
    "area": "area_chart",
    "pie": "pie_chart",
    "donut": "donut_chart",
    "metric": "metric_card",
    "summary": "key_value",
    "summary_stats": "key_value",
    "stats": "key_value",
    "json": "json_tree",
}


class WidgetPosition(PydanticBaseModel):
    row: int = Field(default=0, description="Grid row (0-indexed)")
    col: int = Field(default=0, description="Grid column (0-11, 12-column grid)")
    colSpan: int = Field(default=6, description="How many columns to span (1-12)")
    rowSpan: int = Field(default=1, description="How many rows to span")


class WidgetConfig(PydanticBaseModel):
    id: str | None = Field(
        default=None,
        description="Widget ID. Auto-generated if not provided. Use same ID to update.",
    )
    type: str = Field(
        description=f"Widget type. One of: {', '.join(VALID_WIDGET_TYPES)}"
    )
    title: str | None = Field(
        default=None, description="Widget title displayed above the visualization"
    )
    position: WidgetPosition | None = Field(
        default=None,
        description="Grid position. Uses 12-column grid. row=0,col=0,colSpan=6 takes left half of first row.",
    )
    config: dict[str, Any] = Field(
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
    dataBinding: dict[str, Any] | None = Field(
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
    dynamicAnalysis: dict[str, Any] | None = Field(
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
    widget: WidgetConfig | None = Field(
        default=None,
        description="Widget configuration. Required for add/update/remove.",
    )
    widgets: list[WidgetConfig] | None = Field(
        default=None,
        description="Multiple widgets. Used with replace_all to set entire canvas.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_widget_payload(cls, data):
        """Accept common flat chart/table payloads older prompts still emit."""
        if not isinstance(data, dict):
            return data
        if data.get("widget") or data.get("widgets"):
            return data

        flat_widget_keys = {
            "id",
            "type",
            "title",
            "position",
            "config",
            "dataBinding",
            "dynamicAnalysis",
            "data",
            "rows",
            "columns",
            "content",
            "series",
            "labels",
            "value",
            "subtitle",
        }
        if not (flat_widget_keys & set(data)):
            return data

        widget_type = data.get("type") or "markdown"
        if isinstance(widget_type, str):
            widget_type = LEGACY_WIDGET_TYPE_ALIASES.get(
                widget_type.strip().lower(), widget_type
            )

        config = data.get("config")
        if not isinstance(config, dict):
            config = {}

        if "data" in data and "data" not in config:
            config["data"] = data["data"]
        if "rows" in data and "rows" not in config:
            config["rows"] = data["rows"]
        if "columns" in data and "columns" not in config:
            config["columns"] = data["columns"]
        if "content" in data and "content" not in config:
            config["content"] = data["content"]
        if "series" in data and "series" not in config:
            config["series"] = data["series"]
        if "labels" in data and "labels" not in config:
            config["labels"] = data["labels"]
        if "value" in data and "value" not in config:
            config["value"] = data["value"]
        if "subtitle" in data and "subtitle" not in config:
            config["subtitle"] = data["subtitle"]
        if widget_type == "key_value":
            stats_data = config.get("data")
            if isinstance(stats_data, str):
                try:
                    stats_data = json.loads(stats_data)
                except (TypeError, ValueError, json.JSONDecodeError):
                    stats_data = None
            if isinstance(stats_data, dict) and isinstance(stats_data.get("stats"), list):
                config.setdefault(
                    "items",
                    [
                        {
                            "key": str(item.get("label", "")),
                            "value": str(item.get("value", "")),
                        }
                        for item in stats_data["stats"]
                        if isinstance(item, dict)
                    ],
                )

        widget = {
            "id": data.get("id"),
            "type": widget_type,
            "title": data.get("title"),
            "position": data.get("position"),
            "config": config,
            "dataBinding": data.get("dataBinding"),
            "dynamicAnalysis": data.get("dynamicAnalysis"),
        }
        return {
            "action": data.get("action", "add"),
            "widget": widget,
        }


@register_tool
class RenderWidgetTool(BaseTool):
    name = "render_widget"
    description = (
        "Render a visualization widget in the user's Imagine view. "
        "Call this after analyzing trace data to create visual representations. "
        "You can call this multiple times to build a multi-widget dashboard.\n\n"
        "IMPORTANT: Always fetch trace data first (get_trace, get_span, etc.).\n\n"
        "PREFER using dataBinding over static config — it makes views reusable across traces. "
        "Use dataBinding for charts, metrics, tables, key-value cards. "
        "Use static config only for one-off markdown analysis.\n\n"
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
            return ToolResult.needs_input(
                "Widget configuration is required. Provide `widget` or a flat payload with `type`, `title`, and data/config.",
                missing_fields=["widget"],
            )

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
