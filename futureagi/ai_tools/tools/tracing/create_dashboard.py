from typing import Any, Literal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import dashboard_link, key_value_block, section
from ai_tools.registry import register_tool


class DashboardWidgetInput(PydanticBaseModel):
    name: str = Field(description="Widget title")
    description: str = Field(default="", description="Optional widget description")
    position: int | None = Field(
        default=None, ge=0, description="Widget order on the dashboard"
    )
    width: int = Field(default=6, ge=1, le=12, description="Grid width from 1 to 12")
    height: int = Field(default=4, ge=1, description="Widget height in grid units")
    chart_type: Literal[
        "line",
        "stacked_line",
        "column",
        "stacked_column",
        "bar",
        "stacked_bar",
        "pie",
        "table",
        "metric",
    ] = Field(default="metric", description="Dashboard chart type")
    query_config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dashboard query config. If omitted, Falcon creates a placeholder "
            "widget the user can configure in the dashboard editor."
        ),
    )
    chart_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional chart configuration for the dashboard widget",
    )

    @field_validator("name")
    @classmethod
    def _name_required(cls, value):
        value = (value or "").strip()
        if not value:
            raise ValueError("Widget name cannot be empty")
        return value


class CreateDashboardInput(PydanticBaseModel):
    name: str = Field(description="Dashboard name")
    description: str = Field(default="", description="Dashboard description")
    widgets: list[DashboardWidgetInput] = Field(
        default_factory=list,
        description=(
            "Optional widgets to create immediately. Leave empty if the user only "
            "asked for a dashboard shell or did not specify metrics."
        ),
    )

    @field_validator("name")
    @classmethod
    def _name_required(cls, value):
        value = (value or "").strip()
        if not value:
            raise ValueError("Dashboard name cannot be empty")
        return value


@register_tool
class CreateDashboardTool(BaseTool):
    name = "create_dashboard"
    description = (
        "Create a real dashboard in the Future AGI Dashboards UI. Use this when "
        "the user asks Falcon to create, build, or set up a dashboard. Optionally "
        "create starter widgets when the requested metrics are clear."
    )
    category = "tracing"
    input_model = CreateDashboardInput

    def execute(self, params: CreateDashboardInput, context: ToolContext) -> ToolResult:
        if not context.workspace:
            return ToolResult.error("A workspace is required to create a dashboard.")

        from django.db import transaction
        from tracer.models.dashboard import Dashboard, DashboardWidget

        with transaction.atomic():
            dashboard = Dashboard.objects.create(
                name=params.name,
                description=params.description,
                workspace=context.workspace,
                created_by=context.user,
                updated_by=context.user,
            )

            created_widgets = []
            for idx, widget_params in enumerate(params.widgets):
                chart_config = dict(widget_params.chart_config or {})
                chart_config.setdefault("chart_type", widget_params.chart_type)

                widget = DashboardWidget.objects.create(
                    dashboard=dashboard,
                    name=widget_params.name,
                    description=widget_params.description,
                    position=widget_params.position
                    if widget_params.position is not None
                    else idx,
                    width=widget_params.width,
                    height=widget_params.height,
                    query_config=widget_params.query_config or {},
                    chart_config=chart_config,
                    created_by=context.user,
                )
                created_widgets.append(widget)

        link = dashboard_link("dashboard", str(dashboard.id), label="Open dashboard")
        details = key_value_block(
            [
                ("ID", f"`{dashboard.id}`"),
                ("Name", dashboard.name),
                ("Widgets", str(len(created_widgets))),
                ("Link", link),
            ]
        )
        content = section("Dashboard Created", details)

        return ToolResult(
            content=content,
            data={
                "id": str(dashboard.id),
                "name": dashboard.name,
                "widget_count": len(created_widgets),
                "url": link,
                "widgets": [
                    {
                        "id": str(widget.id),
                        "name": widget.name,
                        "chart_type": widget.chart_config.get("chart_type"),
                    }
                    for widget in created_widgets
                ],
            },
        )
