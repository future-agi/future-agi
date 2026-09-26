import re
from typing import List, Literal, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import dashboard_link, key_value_block, section
from ai_tools.registry import register_tool
from ai_tools.tools.datasets.add_run_prompt_column import (
    VALID_ROLES,
    VARIABLE_PATTERN,
    MessageInput,
)

logger = structlog.get_logger(__name__)


class EditRunPromptColumnInput(PydanticBaseModel):
    dataset_id: UUID = Field(description="The UUID of the dataset")
    column_id: UUID = Field(description="The UUID of the run-prompt column to edit")
    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New name for the column. If omitted, name is unchanged.",
    )
    model: Optional[str] = Field(
        default=None,
        min_length=1,
        description="New model to use. If omitted, model is unchanged.",
    )
    messages: Optional[List[MessageInput]] = Field(
        default=None,
        min_length=1,
        description="New messages list. If omitted, messages are unchanged.",
    )
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="New temperature (0-2).",
    )
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        le=65536,
        description="New max tokens.",
    )
    output_format: Optional[
        Literal["array", "string", "number", "object", "audio", "image"]
    ] = Field(default=None, description="New output format.")
    concurrency: Optional[int] = Field(
        default=None,
        ge=1,
        le=10,
        description="New concurrency (1-10).",
    )
    frequency_penalty: Optional[float] = Field(
        default=None, ge=-2.0, le=2.0, description="New frequency penalty."
    )
    presence_penalty: Optional[float] = Field(
        default=None, ge=-2.0, le=2.0, description="New presence penalty."
    )
    top_p: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="New top_p."
    )
    response_format: Optional[dict] = Field(
        default=None, description="New response format schema."
    )
    tool_choice: Optional[Literal["auto", "required"]] = Field(
        default=None, description="New tool choice mode."
    )
    tools: Optional[List[UUID]] = Field(
        default=None, description="New list of tool UUIDs."
    )
    run: bool = Field(
        default=True,
        description="If true, re-run the prompt on all rows after editing. Default true.",
    )

    @model_validator(mode="after")
    def validate_messages_if_provided(self):
        if self.messages:
            if self.messages[0].role == "assistant":
                raise ValueError("First message cannot be from 'assistant'.")
            for msg in self.messages:
                if msg.role == "user" and not msg.content.strip():
                    raise ValueError("User messages must have non-empty content.")
        return self


@register_tool
class EditRunPromptColumnTool(BaseTool):
    name = "edit_run_prompt_column"
    description = (
        "Edits an existing run-prompt column's configuration (model, messages, "
        "parameters) and optionally re-runs the prompt on all rows."
    )
    category = "datasets"
    input_model = EditRunPromptColumnInput

    def execute(
        self, params: EditRunPromptColumnInput, context: ToolContext
    ) -> ToolResult:
        from django.db import transaction

        from model_hub.models.choices import CellStatus, SourceChoices, StatusType
        from model_hub.models.develop_dataset import Cell, Column, Dataset
        from model_hub.models.run_prompt import RunPrompter
        from model_hub.tasks.run_prompt import process_prompts_single

        # Validate dataset (organization-filtered)
        try:
            dataset = Dataset.objects.get(
                id=params.dataset_id,
                deleted=False,
                organization=context.organization,
            )
        except Dataset.DoesNotExist:
            return ToolResult.not_found("Dataset", str(params.dataset_id))

        # Validate column exists and is a run-prompt column
        try:
            column = Column.objects.get(
                id=params.column_id, dataset=dataset, deleted=False
            )
        except Column.DoesNotExist:
            return ToolResult.not_found("Column", str(params.column_id))

        if column.source != SourceChoices.RUN_PROMPT.value:
            return ToolResult.error(
                f"Column '{column.name}' is not a run-prompt column (source: {column.source}).",
                error_code="VALIDATION_ERROR",
            )

        # Get RunPrompter
        try:
            run_prompter = RunPrompter.objects.get(id=column.source_id)
        except RunPrompter.DoesNotExist:
            return ToolResult.error(
                "Run prompt configuration not found for this column.",
                error_code="NOT_FOUND",
            )

        # Validate referenced columns if messages are being updated
        if params.messages:
            messages = [
                {"role": msg.role, "content": msg.content} for msg in params.messages
            ]
            dataset_columns = Column.objects.filter(
                dataset=dataset, deleted=False
            ).exclude(
                source__in=[
                    SourceChoices.EVALUATION.value,
                    SourceChoices.EVALUATION_REASON.value,
                ]
            )
            col_names = {col.name for col in dataset_columns}

            referenced_vars = set()
            for msg in messages:
                for match in VARIABLE_PATTERN.findall(msg["content"]):
                    base_name = match.split(".")[0].split("[")[0]
                    referenced_vars.add(base_name)

            missing_vars = referenced_vars - col_names
            if missing_vars:
                return ToolResult.error(
                    f"Column(s) referenced in messages not found: "
                    f"{', '.join(f'{{{{' + v + '}}}}' for v in missing_vars)}. "
                    f"Available columns: {', '.join(sorted(col_names))}",
                    error_code="VALIDATION_ERROR",
                )

        # Validate tools if provided
        tool_objects = []
        if params.tools is not None:
            from model_hub.models.openai_tools import Tools

            for tool_id in params.tools:
                try:
                    tool_obj = Tools.objects.get(
                        id=tool_id, organization=context.organization
                    )
                    tool_objects.append(tool_obj)
                except Tools.DoesNotExist:
                    return ToolResult.not_found("Tool", str(tool_id))

        # Apply updates atomically
        with transaction.atomic():
            rp = RunPrompter.objects.select_for_update(of=("self",)).get(
                id=run_prompter.id
            )

            if params.name is not None:
                rp.name = params.name
                column.name = params.name
                column.save(update_fields=["name"])

            if params.model is not None:
                rp.model = params.model
            if params.messages is not None:
                rp.messages = [
                    {"role": msg.role, "content": msg.content}
                    for msg in params.messages
                ]
            if params.temperature is not None:
                rp.temperature = params.temperature
            if params.max_tokens is not None:
                rp.max_tokens = params.max_tokens
            if params.output_format is not None:
                rp.output_format = params.output_format
            if params.concurrency is not None:
                rp.concurrency = params.concurrency
            if params.frequency_penalty is not None:
                rp.frequency_penalty = params.frequency_penalty
            if params.presence_penalty is not None:
                rp.presence_penalty = params.presence_penalty
            if params.top_p is not None:
                rp.top_p = params.top_p
            if params.response_format is not None:
                rp.response_format = params.response_format
            if params.tool_choice is not None:
                rp.tool_choice = params.tool_choice

            rp.save()

            if params.tools is not None:
                rp.tools.set(tool_objects)

            # Clear existing cells for rerun. Cell.status uses the CellStatus
            # enum ("running"), not StatusType ("Running") — the wrong enum
            # makes the UI ignore the cells and recovery unable to match them.
            if params.run:
                Cell.objects.filter(dataset=dataset, column=column).update(
                    value=None, status=CellStatus.RUNNING.value
                )

        # Trigger re-execution if requested
        workflow_started = False
        if params.run:
            try:
                rp.status = StatusType.RUNNING.value
                rp.save(update_fields=["status"])

                process_prompts_single.apply_async(
                    args=({"type": "editing", "prompt_id": str(rp.id)},)
                )
                workflow_started = True
            except Exception:
                rp.status = StatusType.NOT_STARTED.value
                rp.save(update_fields=["status"])

        info = key_value_block(
            [
                ("Column ID", f"`{column.id}`"),
                ("Column Name", rp.name),
                ("Model", rp.model),
                ("Status", rp.status),
                (
                    "Dataset",
                    dashboard_link("dataset", str(dataset.id), label=dataset.name),
                ),
            ]
        )

        content = section("Run Prompt Column Updated", info)
        if params.run:
            if workflow_started:
                content += "\n\n_Prompt re-execution started on all rows._"
            else:
                content += "\n\n_Re-execution queued. It will be picked up shortly._"
        else:
            content += (
                "\n\n_Configuration updated. Run manually from the dashboard to apply._"
            )

        return ToolResult(
            content=content,
            data={
                "column_id": str(column.id),
                "run_prompter_id": str(rp.id),
                "name": rp.name,
                "model": rp.model,
                "status": rp.status,
            },
        )
