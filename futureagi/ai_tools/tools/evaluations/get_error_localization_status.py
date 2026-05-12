from typing import Any

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, key_value_block, section
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class GetErrorLocalizationStatusInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str = Field(
        default="",
        description="The UUID of the ErrorLocalizerTask to check status for",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["task_id"] = (
            normalized.get("task_id")
            or normalized.get("error_localization_task_id")
            or normalized.get("id")
            or ""
        )
        return normalized


@register_tool
class GetErrorLocalizationStatusTool(BaseTool):
    name = "get_error_localization_status"
    description = (
        "Checks the status of an error localization task. "
        "Returns PENDING, RUNNING, COMPLETED, FAILED, or SKIPPED."
    )
    category = "evaluations"
    input_model = GetErrorLocalizationStatusInput

    def execute(
        self, params: GetErrorLocalizationStatusInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.error_localizer_model import ErrorLocalizerTask

        task_id = str(params.task_id or "").strip()
        if not task_id:
            return _task_candidates(context)
        if not is_uuid(task_id):
            result = _task_candidates(context)
            result.data = result.data or {}
            result.data["invalid_task_id"] = task_id
            result.content += (
                "\n\nThe supplied task ID was not a valid UUID. Use one of the "
                "candidate task IDs above."
            )
            return result

        try:
            task = ErrorLocalizerTask.objects.get(
                id=task_id,
                organization=context.organization,
            )
        except ErrorLocalizerTask.DoesNotExist:
            return ToolResult(
                content=section(
                    "Error Localization Task Not Found",
                    (
                        f"Task `{task_id}` was not found in this workspace. "
                        "Call without `task_id` to list recent candidates."
                    ),
                ),
                data={"task_id": task_id, "requires_valid_task_id": True},
            )

        info = key_value_block(
            [
                ("Task ID", f"`{task.id}`"),
                ("Status", task.status.upper()),
                ("Source", task.source),
                ("Template", task.eval_template.name if task.eval_template else "—"),
                ("Selected Input Key", task.selected_input_key or "—"),
                ("Created", format_datetime(task.created_at)),
            ]
        )

        if task.error_message:
            info += f"\n**Error:** {task.error_message}"

        return ToolResult(
            content=section("Error Localization Status", info),
            data={
                "task_id": str(task.id),
                "status": task.status,
                "source": task.source,
                "error_message": task.error_message,
                "selected_input_key": task.selected_input_key,
            },
        )


def _task_candidates(context: ToolContext) -> ToolResult:
    from model_hub.models.error_localizer_model import ErrorLocalizerTask

    tasks = ErrorLocalizerTask.objects.filter(
        organization=context.organization
    ).order_by("-created_at")[:10]
    rows = [
        (f"- `{task.id}` — {task.status.upper()} ({format_datetime(task.created_at)})")
        for task in tasks
    ]
    return ToolResult(
        content=section(
            "Error Localization Task Required",
            (
                "Provide `task_id` to check a specific error localization task.\n\n"
                + ("\n".join(rows) if rows else "No error localization tasks found.")
            ),
        ),
        data={
            "requires_task_id": True,
            "tasks": [{"id": str(task.id), "status": task.status} for task in tasks],
        },
    )
