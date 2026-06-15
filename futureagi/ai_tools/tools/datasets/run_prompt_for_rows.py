from typing import List, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class RunPromptForRowsInput(PydanticBaseModel):
    run_prompt_ids: List[UUID] = Field(
        description="List of run prompt column IDs to execute.",
        min_length=1,
    )
    row_ids: Optional[List[UUID]] = Field(
        default=None,
        description=(
            "List of row UUIDs to run prompts on. "
            "Required unless selected_all_rows is true."
        ),
    )
    selected_all_rows: bool = Field(
        default=False,
        description=(
            "If true, run prompts on all rows in the dataset. "
            "When true with row_ids provided, those row_ids are excluded."
        ),
    )

    @model_validator(mode="after")
    def validate_row_selection(self):
        if not self.selected_all_rows and (not self.row_ids or len(self.row_ids) == 0):
            raise ValueError(
                "Either 'row_ids' must be provided or 'selected_all_rows' must be true."
            )
        return self


@register_tool
class RunPromptForRowsTool(BaseTool):
    name = "run_prompt_for_rows"
    description = (
        "Executes run-prompt columns on specific rows or all rows in a dataset. "
        "Queues the prompts for async processing. Use add_run_prompt_column first "
        "to create the prompt column, then use this to run it on selected rows."
    )
    category = "datasets"
    input_model = RunPromptForRowsInput

    def execute(
        self, params: RunPromptForRowsInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.develop_dataset import Row
        from model_hub.models.run_prompt import RunPrompter
        from model_hub.views.run_prompt import run_all_prompts_task

        # Validate all run_prompters exist and belong to user's organization
        run_prompters = RunPrompter.objects.filter(id__in=params.run_prompt_ids)

        if run_prompters.count() != len(params.run_prompt_ids):
            found_ids = set(str(rp.id) for rp in run_prompters)
            missing = [
                str(rid) for rid in params.run_prompt_ids if str(rid) not in found_ids
            ]
            return ToolResult.not_found("RunPrompter(s)", ", ".join(missing))

        for rp in run_prompters:
            if rp.organization_id != context.organization.id:
                return ToolResult.not_found("RunPrompter", str(rp.id))

        # Resolve row IDs
        first_rp = run_prompters.first()
        dataset = first_rp.dataset

        if params.selected_all_rows:
            all_row_ids = list(
                Row.objects.filter(dataset=dataset, deleted=False).values_list(
                    "id", flat=True
                )
            )
            if params.row_ids and len(params.row_ids) > 0:
                # Exclude specified rows
                exclude_set = set(params.row_ids)
                row_ids = [rid for rid in all_row_ids if rid not in exclude_set]
            else:
                row_ids = all_row_ids
        else:
            row_ids = list(params.row_ids)

        if not row_ids:
            return ToolResult.error(
                "No rows to process after applying selection.",
                error_code="VALIDATION_ERROR",
            )

        # Queue async task
        run_prompt_ids = [str(rid) for rid in params.run_prompt_ids]
        run_all_prompts_task.apply_async(args=(run_prompt_ids, row_ids))

        prompt_names = [rp.name for rp in run_prompters]
        info = key_value_block(
            [
                ("Run Prompts", ", ".join(prompt_names)),
                ("Rows Queued", str(len(row_ids))),
                ("Dataset", dataset.name),
                ("Mode", "All rows" if params.selected_all_rows else "Selected rows"),
            ]
        )

        return ToolResult(
            content=section("Run Prompts Queued", info)
            + "\n\n_Prompts are being processed asynchronously. Results will appear in the dataset as each row completes._",
            data={
                "run_prompt_ids": run_prompt_ids,
                "rows_queued": len(row_ids),
                "dataset_id": str(dataset.id),
            },
        )
