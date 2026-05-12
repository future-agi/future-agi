from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    key_value_block,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetEvalTaskInput(PydanticBaseModel):
    eval_task_id: str = Field(
        default="", description="The UUID of the eval task to retrieve"
    )


@register_tool
class GetEvalTaskTool(BaseTool):
    name = "get_eval_task"
    description = (
        "Gets detailed information about a specific eval task including its "
        "configuration, status, linked evals, and timing information."
    )
    category = "tracing"
    input_model = GetEvalTaskInput

    def _candidate_tasks_result(
        self, context: ToolContext, message: str = ""
    ) -> ToolResult:
        from tracer.models.eval_task import EvalTask

        tasks = (
            EvalTask.objects.select_related("project")
            .filter(project__organization=context.organization, deleted=False)
            .order_by("-created_at")[:10]
        )
        rows = [
            (
                f"- `{task.id}` — {task.name or 'Untitled'} "
                f"({format_status(task.status)}, project: {task.project.name})"
            )
            for task in tasks
        ]
        return ToolResult(
            content=section(
                "Eval Task Candidates",
                (
                    (message + "\n\n" if message else "")
                    + "Provide `eval_task_id` to inspect a specific eval task.\n\n"
                    + ("\n".join(rows) if rows else "No eval tasks found.")
                ),
            ),
            data={
                "requires_eval_task_id": True,
                "tasks": [
                    {
                        "id": str(task.id),
                        "name": task.name,
                        "status": task.status,
                        "project_id": str(task.project_id),
                    }
                    for task in tasks
                ],
            },
        )

    def execute(self, params: GetEvalTaskInput, context: ToolContext) -> ToolResult:

        from tracer.models.eval_task import EvalTask

        if not params.eval_task_id:
            return self._candidate_tasks_result(context)

        try:
            task = (
                EvalTask.objects.select_related("project")
                .prefetch_related("evals__eval_template")
                .get(
                    id=params.eval_task_id,
                    project__organization=context.organization,
                )
            )
        except EvalTask.DoesNotExist:
            return self._candidate_tasks_result(
                context,
                f"EvalTask `{params.eval_task_id}` was not found in this workspace.",
            )

        evals_info = []
        eval_names = []
        for e in task.evals.filter(deleted=False):
            template_name = e.eval_template.name if e.eval_template else None
            evals_info.append(
                {
                    "id": str(e.id),
                    "name": e.name,
                    "template": template_name,
                }
            )
            eval_names.append(e.name or template_name or str(e.id))

        info = key_value_block(
            [
                ("Eval Task ID", f"`{task.id}`"),
                ("Name", task.name or "—"),
                ("Project", f"{task.project.name} (`{task.project.id}`)"),
                ("Status", format_status(task.status)),
                ("Run Type", task.run_type or "—"),
                ("Sampling Rate", f"{task.sampling_rate}%"),
                ("Spans Limit", str(task.spans_limit) if task.spans_limit else "—"),
                ("Evals", ", ".join(eval_names) if eval_names else "None"),
                (
                    "Filters",
                    truncate(str(task.filters), 200) if task.filters else "None",
                ),
                ("Last Run", format_datetime(task.last_run)),
                ("Start Time", format_datetime(task.start_time)),
                ("End Time", format_datetime(task.end_time)),
                ("Created", format_datetime(task.created_at)),
            ]
        )

        content = section(f"Eval Task: {task.name or task.id}", info)

        return ToolResult(
            content=content,
            data={
                "id": str(task.id),
                "name": task.name,
                "project_id": str(task.project.id),
                "project_name": task.project.name,
                "status": task.status,
                "run_type": task.run_type,
                "sampling_rate": task.sampling_rate,
                "spans_limit": task.spans_limit,
                "filters": task.filters,
                "evals": evals_info,
                "last_run": str(task.last_run) if task.last_run else None,
                "start_time": str(task.start_time) if task.start_time else None,
                "end_time": str(task.end_time) if task.end_time else None,
                "created_at": str(task.created_at) if task.created_at else None,
            },
        )
