from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class GetErrorLocalizationResultsInput(PydanticBaseModel):
    task_id: str = Field(
        default="",
        description="The UUID of the ErrorLocalizerTask to retrieve results for",
    )


@register_tool
class GetErrorLocalizationResultsTool(BaseTool):
    name = "get_error_localization_results"
    description = (
        "Retrieves the results of a completed error localization task. "
        "Returns ranked error segments with reasons, improvements, and severity weights. "
        "Only works when the task status is COMPLETED."
    )
    category = "evaluations"
    input_model = GetErrorLocalizationResultsInput

    def execute(
        self, params: GetErrorLocalizationResultsInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.error_localizer_model import ErrorLocalizerTask

        def candidate_tasks_result(title: str, detail: str = "") -> ToolResult:
            tasks = list(
                ErrorLocalizerTask.objects.filter(
                    organization=context.organization,
                    deleted=False,
                ).order_by("-created_at")[:10]
            )
            rows = [
                [
                    f"`{task.id}`",
                    task.status,
                    task.source,
                    format_datetime(task.created_at),
                ]
                for task in tasks
            ]
            body = detail or ""
            if rows:
                body = (body + "\n\n" if body else "") + markdown_table(
                    ["Task ID", "Status", "Source", "Created"], rows
                )
            else:
                body = body or "No error localization tasks found."
            return ToolResult(
                content=section(title, body),
                data={
                    "requires_task_id": True,
                    "tasks": [
                        {
                            "id": str(task.id),
                            "status": task.status,
                            "source": task.source,
                        }
                        for task in tasks
                    ],
                },
            )

        task_ref = str(params.task_id or "").strip()
        if not task_ref:
            return candidate_tasks_result(
                "Error Localization Task Required",
                "Provide `task_id` to retrieve localization results.",
            )
        if not is_uuid(task_ref):
            return candidate_tasks_result(
                "Error Localization Task Not Found",
                f"`{task_ref}` is not a task UUID. Use one of these task IDs.",
            )

        try:
            task = ErrorLocalizerTask.objects.get(
                id=task_ref,
                organization=context.organization,
            )
        except ErrorLocalizerTask.DoesNotExist:
            return candidate_tasks_result(
                "Error Localization Task Not Found",
                f"Task `{task_ref}` was not found in this workspace.",
            )

        if task.status != "completed":
            return ToolResult(
                content=f"Task is not yet completed. Current status: **{task.status.upper()}**"
                + (f"\n\nError: {task.error_message}" if task.error_message else ""),
                data={"task_id": str(task.id), "status": task.status},
            )

        error_analysis = task.error_analysis or {}
        selected_key = task.selected_input_key or "—"

        # Build header
        header = key_value_block(
            [
                ("Task ID", f"`{task.id}`"),
                ("Status", "COMPLETED"),
                ("Analyzed Input Key", selected_key),
                ("Template", task.eval_template.name if task.eval_template else "—"),
            ]
        )

        content = section("Error Localization Results", header)

        # Format error analysis entries
        if isinstance(error_analysis, dict):
            for input_key, entries in error_analysis.items():
                if not entries or not isinstance(entries, list):
                    continue
                content += f"\n\n### Input: `{input_key}`\n"
                for i, entry in enumerate(entries, 1):
                    rank = entry.get("rank", i)
                    weight = entry.get("weight", "—")
                    reason = entry.get("reason", "No reason provided")
                    improvement = entry.get("improvement", "—")

                    content += f"\n**#{rank}** (severity: {weight})\n"
                    content += f"- **Reason:** {truncate(reason, 300)}\n"
                    if improvement and improvement != "—":
                        content += f"- **Suggestion:** {truncate(improvement, 300)}\n"

                    # Show original segment info
                    org_sen = entry.get("orgSen")
                    org_patch = entry.get("orgPatch")
                    org_segment = entry.get("orgSegment")
                    if org_sen:
                        content += f'- **Text:** "{truncate(org_sen, 200)}"\n'
                    if org_patch:
                        content += f"- **Patch:** {org_patch}\n"
                    if org_segment:
                        content += f"- **Segment:** {org_segment}\n"
        elif isinstance(error_analysis, list):
            content += "\n\n### Error Segments\n"
            for i, entry in enumerate(error_analysis, 1):
                rank = entry.get("rank", i)
                weight = entry.get("weight", "—")
                reason = entry.get("reason", "No reason provided")
                improvement = entry.get("improvement", "—")
                content += f"\n**#{rank}** (severity: {weight})\n"
                content += f"- **Reason:** {truncate(reason, 300)}\n"
                if improvement and improvement != "—":
                    content += f"- **Suggestion:** {truncate(improvement, 300)}\n"

        if not error_analysis:
            content += "\n\nNo error segments found — the evaluation may have passed or the input was too short to localize."

        return ToolResult(
            content=content,
            data={
                "task_id": str(task.id),
                "status": task.status,
                "selected_input_key": selected_key,
                "error_analysis": error_analysis,
                "input_data": task.input_data,
                "input_types": task.input_types,
            },
        )
