from typing import List, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, markdown_table, section, truncate
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class RunPromptForRowsInput(PydanticBaseModel):
    run_prompt_ids: List[str | UUID] = Field(
        description="List of run prompt column IDs to execute.",
        min_length=1,
    )
    row_ids: Optional[List[str | UUID]] = Field(
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

        run_prompt_ids, invalid_run_prompt_ids = _coerce_uuid_list(
            params.run_prompt_ids
        )
        if not run_prompt_ids:
            invalid_text = (
                "Invalid IDs: "
                + ", ".join(f"`{rid}`" for rid in invalid_run_prompt_ids)
                + ". "
                if invalid_run_prompt_ids
                else ""
            )
            return _candidate_run_prompters_result(
                context,
                title="Run Prompt Column Required",
                detail=(
                    "No valid run prompt column IDs were provided. "
                    + invalid_text
                    + "Use one of these candidate run prompt IDs, or create one "
                    "with `add_run_prompt_column` first."
                ),
            )

        # Validate all run_prompters exist and belong to user's organization
        run_prompters = RunPrompter.objects.filter(id__in=run_prompt_ids)

        if run_prompters.count() != len(run_prompt_ids):
            found_ids = set(str(rp.id) for rp in run_prompters)
            missing = [
                str(rid) for rid in run_prompt_ids if str(rid) not in found_ids
            ]
            return _candidate_run_prompters_result(
                context,
                title="Run Prompt Column Not Found",
                detail=(
                    "These run prompt column IDs were not found in this workspace: "
                    + ", ".join(f"`{rid}`" for rid in missing)
                    + ". Use one of these candidate run prompt IDs, or create one "
                    "with `add_run_prompt_column` first."
                ),
            )

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
                exclude_ids, _invalid_exclude_ids = _coerce_uuid_list(params.row_ids)
                exclude_set = set(exclude_ids)
                row_ids = [rid for rid in all_row_ids if rid not in exclude_set]
            else:
                row_ids = all_row_ids
        else:
            row_ids, invalid_row_ids = _coerce_uuid_list(params.row_ids or [])
            if invalid_row_ids and not row_ids:
                return ToolResult.needs_input(
                    "No valid row IDs were provided. Use `get_dataset_rows` to choose row IDs, or set `selected_all_rows=true`.",
                    data={
                        "invalid_row_ids": invalid_row_ids,
                        "dataset_id": str(dataset.id),
                    },
                    missing_fields=["row_ids"],
                )

        if not row_ids:
            return ToolResult.error(
                "No rows to process after applying selection.",
                error_code="VALIDATION_ERROR",
            )

        # Queue async task
        run_prompt_id_strings = [str(rid) for rid in run_prompt_ids]
        run_all_prompts_task.apply_async(args=(run_prompt_id_strings, row_ids))

        prompt_names = [rp.name for rp in run_prompters]
        info = key_value_block(
            [
                ("Run Prompts", ", ".join(prompt_names)),
                ("Rows Queued", str(len(row_ids))),
                ("Dataset", dataset.name),
                ("Mode", "All rows" if params.selected_all_rows else "Selected rows"),
            ]
        )

        content = section("Run Prompts Queued", info)
        if not params.selected_all_rows and "invalid_row_ids" in locals() and invalid_row_ids:
            content += (
                "\n\n_Some malformed row IDs were skipped: "
                + ", ".join(f"`{row_id}`" for row_id in invalid_row_ids)
                + "._"
            )
        content += "\n\n_Prompts are being processed asynchronously. Results will appear in the dataset as each row completes._"

        data = {
            "run_prompt_ids": run_prompt_id_strings,
            "rows_queued": len(row_ids),
            "dataset_id": str(dataset.id),
        }
        if not params.selected_all_rows and "invalid_row_ids" in locals() and invalid_row_ids:
            data["skipped_invalid_row_ids"] = invalid_row_ids

        return ToolResult(content=content, data=data)


def _coerce_uuid_list(values: list[str | UUID]) -> tuple[list[UUID], list[str]]:
    valid = []
    invalid = []
    for value in values:
        try:
            valid.append(value if isinstance(value, UUID) else UUID(str(value)))
        except (TypeError, ValueError):
            invalid.append(str(value))
    return valid, invalid


def _candidate_run_prompters_result(
    context: ToolContext,
    title: str = "Candidate Run Prompt Columns",
    detail: str = "",
) -> ToolResult:
    from model_hub.models.run_prompt import RunPrompter

    run_prompters = (
        RunPrompter.objects.filter(
            organization=context.organization,
            deleted=False,
        )
        .select_related("dataset")
        .order_by("-created_at")[:10]
    )
    rows = []
    data = []
    for run_prompter in run_prompters:
        dataset_name = run_prompter.dataset.name if run_prompter.dataset else "-"
        rows.append(
            [
                f"`{run_prompter.id}`",
                truncate(run_prompter.name, 40),
                truncate(dataset_name, 40),
                run_prompter.status or "-",
            ]
        )
        data.append(
            {
                "id": str(run_prompter.id),
                "name": run_prompter.name,
                "dataset_id": (
                    str(run_prompter.dataset_id)
                    if getattr(run_prompter, "dataset_id", None)
                    else None
                ),
            }
        )

    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Run Prompt ID", "Name", "Dataset", "Status"],
            rows,
        )
    else:
        body = (
            body + "\n\n" if body else ""
        ) + "No run prompt columns found. Use `add_run_prompt_column` first."

    return ToolResult(
        content=section(title, body),
        data={"requires_run_prompt_ids": True, "run_prompts": data},
    )
