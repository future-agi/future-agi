from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import dashboard_link, key_value_block, section
from ai_tools.registry import register_tool


class CreateDashboardInput(PydanticBaseModel):
    name: str = Field(description="Name of the dashboard")
    description: str = Field(default="", description="Optional description")


@register_tool
class CreateDashboardTool(BaseTool):
    name = "create_dashboard"
    description = (
        "Creates an empty saved dashboard in the current workspace. It is listed "
        "on the Dashboards page straight away, with no widgets: the user adds "
        "widgets from the dashboard editor. Use this whenever the user asks to "
        "create or build a dashboard."
    )
    category = "tracing"
    input_model = CreateDashboardInput

    def execute(self, params: CreateDashboardInput, context: ToolContext) -> ToolResult:
        from tracer.serializers.dashboard import DashboardCreateUpdateSerializer

        serializer = DashboardCreateUpdateSerializer(
            data={"name": params.name, "description": params.description}
        )
        if not serializer.is_valid():
            return ToolResult.error(
                str(serializer.errors), error_code="VALIDATION_ERROR"
            )

        dashboard = serializer.save(
            workspace=context.workspace,
            created_by=context.user,
            updated_by=context.user,
        )

        info = key_value_block(
            [
                ("ID", f"`{dashboard.id}`"),
                ("Name", dashboard.name),
                ("Widgets", "none yet, add them from the dashboard editor"),
                (
                    "Link",
                    dashboard_link(
                        "dashboard", str(dashboard.id), label="Open dashboard"
                    ),
                ),
            ]
        )
        return ToolResult(
            content=section("Dashboard Created", info),
            data={"dashboard_id": str(dashboard.id), "name": dashboard.name},
        )
