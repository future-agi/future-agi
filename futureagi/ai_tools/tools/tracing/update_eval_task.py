from typing import List, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_status,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class UpdateEvalTaskInput(PydanticBaseModel):
    eval_task_id: str = Field(
        default="",
        description="Eval task UUID or exact task name to update.",
    )
    edit_type: str = Field(
        default="edit_rerun",
        description=(
            "Type of update: 'fresh_run' (delete all results, restart from scratch) "
            "or 'edit_rerun' (preserve existing results, fill gaps)"
        )
    )
    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New name for the eval task",
    )
    sampling_rate: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=100.0,
        description="New sampling rate (1-100)",
    )
    spans_limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000000,
        description="New spans limit",
    )
    run_type: Optional[str] = Field(
        default=None,
        description="New run type: 'historical' or 'continuous'",
    )
    evals: Optional[List[UUID]] = Field(
        default=None,
        min_length=1,
        description="New list of CustomEvalConfig IDs to link to this task",
    )
    filters: Optional[dict] = Field(
        default=None,
        description="New filter configuration",
    )


@register_tool
class UpdateEvalTaskTool(BaseTool):
    name = "update_eval_task"
    description = (
        "Updates an eval task's configuration and optionally re-runs it. "
        "Use 'fresh_run' to clear all results and start over, or "
        "'edit_rerun' to preserve existing results and only run missing evaluations."
    )
    category = "tracing"
    input_model = UpdateEvalTaskInput

    def execute(self, params: UpdateEvalTaskInput, context: ToolContext) -> ToolResult:

        from django.db import transaction
        from django.utils import timezone

        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.eval_task import (
            EvalTask,
            EvalTaskLogger,
            EvalTaskStatus,
        )
        from tracer.models.observation_span import EvalLogger
        from tracer.serializers.eval_task import EditEvalTaskSerializer

        # Validate edit_type
        if params.edit_type not in ("fresh_run", "edit_rerun"):
            return ToolResult.error(
                f"Invalid edit_type '{params.edit_type}'. "
                "Must be 'fresh_run' or 'edit_rerun'.",
                error_code="VALIDATION_ERROR",
            )

        # Validate run_type if provided
        if params.run_type and params.run_type not in ("historical", "continuous"):
            return ToolResult.error(
                f"Invalid run_type '{params.run_type}'. "
                "Must be 'historical' or 'continuous'.",
                error_code="VALIDATION_ERROR",
            )

        from ai_tools.tools.tracing._utils import (
            candidate_eval_tasks_result,
            resolve_eval_tasks,
        )

        eval_tasks, missing, unresolved = resolve_eval_tasks(
            [params.eval_task_id],
            context,
        )
        if unresolved:
            return unresolved
        if missing or not eval_tasks:
            return candidate_eval_tasks_result(
                context,
                "Eval Task Required To Update",
                f"Eval task `{params.eval_task_id}` was not found.",
            )
        eval_task = (
            EvalTask.objects.select_related("project")
            .prefetch_related("evals")
            .get(id=eval_tasks[0].id)
        )

        # Status checks
        if eval_task.status == EvalTaskStatus.RUNNING:
            return ToolResult.error(
                "Cannot update a running eval task. Pause it first.",
                error_code="VALIDATION_ERROR",
            )
        if eval_task.status == EvalTaskStatus.DELETED:
            return ToolResult.error(
                "Cannot update a deleted eval task.",
                error_code="VALIDATION_ERROR",
            )

        # Validate evals if provided
        new_eval_ids = None
        if params.evals is not None:
            eval_id_strs = [str(eid) for eid in params.evals]
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=eval_id_strs, deleted=False
            )
            found_ids = {str(ec.id) for ec in eval_configs}
            missing_ids = set(eval_id_strs) - found_ids
            if missing_ids:
                return ToolResult.error(
                    f"CustomEvalConfig(s) not found: {', '.join(missing_ids)}",
                    error_code="NOT_FOUND",
                )
            new_eval_ids = eval_id_strs

        # Build serializer data for validation
        update_data = {"edit_type": params.edit_type}
        changes = []

        if params.name is not None:
            update_data["name"] = params.name
            changes.append(f"name: '{eval_task.name}' -> '{params.name}'")
        if params.sampling_rate is not None:
            update_data["sampling_rate"] = params.sampling_rate
            changes.append(
                f"sampling_rate: {eval_task.sampling_rate} -> {params.sampling_rate}"
            )
        if params.spans_limit is not None:
            update_data["spans_limit"] = params.spans_limit
            changes.append(
                f"spans_limit: {eval_task.spans_limit} -> {params.spans_limit}"
            )
        if params.run_type is not None:
            update_data["run_type"] = params.run_type
            changes.append(f"run_type: '{eval_task.run_type}' -> '{params.run_type}'")
        if params.filters is not None:
            update_data["filters"] = params.filters
            changes.append("filters: updated")
        if new_eval_ids is not None:
            update_data["evals"] = [UUID(eid) for eid in new_eval_ids]
            changes.append(f"evals: updated ({len(new_eval_ids)} configs)")

        # Validate via serializer
        serializer = EditEvalTaskSerializer(data=update_data)
        if not serializer.is_valid():
            return ToolResult.error(
                f"Validation error: {serializer.errors}",
                error_code="VALIDATION_ERROR",
            )

        # Apply updates in transaction
        with transaction.atomic():
            if params.edit_type == "fresh_run":
                # Delete all previous results and reset
                EvalLogger.objects.filter(
                    eval_task_id=str(eval_task.id), deleted=False
                ).update(deleted=True, deleted_at=timezone.now())

                eval_task_logger, _ = EvalTaskLogger.objects.get_or_create(
                    eval_task_id=eval_task.id,
                    defaults={
                        "offset": 0,
                        "status": EvalTaskStatus.PENDING,
                        "spanids_processed": [],
                    },
                )
                eval_task_logger.spanids_processed = []
                eval_task_logger.offset = 0
                eval_task_logger.status = EvalTaskStatus.PENDING
                eval_task_logger.save(
                    update_fields=["spanids_processed", "offset", "status"]
                )

            # Apply field updates
            if params.name is not None:
                eval_task.name = params.name
            if params.sampling_rate is not None:
                eval_task.sampling_rate = params.sampling_rate
            if params.spans_limit is not None:
                eval_task.spans_limit = params.spans_limit
            if params.run_type is not None:
                eval_task.run_type = params.run_type
            if params.filters is not None:
                eval_task.filters = params.filters

            eval_task.status = EvalTaskStatus.PENDING
            eval_task.last_run = timezone.now()
            eval_task.save()

            if new_eval_ids is not None:
                eval_task.evals.set(new_eval_ids)

        info = key_value_block(
            [
                ("Eval Task ID", f"`{eval_task.id}`"),
                ("Name", eval_task.name or "—"),
                ("Edit Type", params.edit_type),
                ("Status", format_status(EvalTaskStatus.PENDING)),
                ("Changes", "; ".join(changes) if changes else "None"),
            ]
        )

        content = section("Eval Task Updated", info)
        content += (
            "\n\n_The eval task has been updated and will be picked up "
            "by the eval runner._"
        )

        return ToolResult(
            content=content,
            data={
                "id": str(eval_task.id),
                "edit_type": params.edit_type,
                "status": "pending",
                "changes": changes,
            },
        )
