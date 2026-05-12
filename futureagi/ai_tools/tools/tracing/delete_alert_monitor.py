from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class DeleteAlertMonitorInput(PydanticBaseModel):
    monitor_id: str = Field(
        default="",
        description="Alert monitor name or UUID to delete",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms this deletion",
    )


@register_tool
class DeleteAlertMonitorTool(BaseTool):
    name = "delete_alert_monitor"
    description = (
        "Deletes an alert monitor by ID. This is a soft delete "
        "(marks as deleted, does not permanently remove)."
    )
    category = "tracing"
    input_model = DeleteAlertMonitorInput

    def execute(
        self, params: DeleteAlertMonitorInput, context: ToolContext
    ) -> ToolResult:

        from tracer.models.monitor import UserAlertMonitor

        def candidate_monitors_result(title: str, detail: str = "") -> ToolResult:
            monitors = list(
                UserAlertMonitor.objects.filter(
                    organization=context.organization
                ).order_by("-created_at")[:10]
            )
            rows = [
                [f"`{monitor.id}`", monitor.name, format_datetime(monitor.created_at)]
                for monitor in monitors
            ]
            body = detail or ""
            if rows:
                body = (body + "\n\n" if body else "") + markdown_table(
                    ["ID", "Name", "Created"], rows
                )
            else:
                body = body or "No alert monitors found."
            return ToolResult(
                content=section(title, body),
                data={
                    "requires_monitor_id": True,
                    "monitors": [
                        {"id": str(monitor.id), "name": monitor.name}
                        for monitor in monitors
                    ],
                },
            )

        monitor_ref = str(params.monitor_id or "").strip()
        if not monitor_ref:
            return candidate_monitors_result(
                "Alert Monitor Required",
                "Provide `monitor_id` to preview deletion.",
            )

        qs = UserAlertMonitor.objects.filter(organization=context.organization)
        if is_uuid(monitor_ref):
            monitor = qs.filter(id=monitor_ref).first()
        else:
            exact = qs.filter(name__iexact=monitor_ref)
            monitor = exact.first() if exact.count() == 1 else None
            if monitor is None:
                fuzzy = qs.filter(name__icontains=monitor_ref)
                monitor = fuzzy.first() if fuzzy.count() == 1 else None
        if monitor is None:
            return candidate_monitors_result(
                "Alert Monitor Not Found",
                f"Alert monitor `{monitor_ref}` was not found.",
            )

        if not params.confirm_delete:
            return ToolResult(
                content=section(
                    "Alert Monitor Delete Preview",
                    (
                        f"Deletion is ready for `{monitor.name}` (`{monitor.id}`). "
                        "Set `confirm_delete=true` after user confirmation to delete it."
                    ),
                ),
                data={
                    "requires_confirmation": True,
                    "monitor_id": str(monitor.id),
                    "name": monitor.name,
                },
            )

        monitor_name = monitor.name
        monitor_id = str(monitor.id)

        # Soft delete
        monitor.delete()

        info = key_value_block(
            [
                ("Monitor ID", f"`{monitor_id}`"),
                ("Name", monitor_name),
                ("Status", "Deleted"),
            ]
        )

        content = section("Alert Monitor Deleted", info)

        return ToolResult(
            content=content,
            data={
                "monitor_id": monitor_id,
                "name": monitor_name,
                "deleted": True,
            },
        )
