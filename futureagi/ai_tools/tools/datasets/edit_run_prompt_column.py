import re
from typing import List, Literal, Optional

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
from ai_tools.tools.datasets.get_run_prompt_column_config import (
    _candidate_run_prompt_columns_result,
)
from ai_tools.tools.tracing._utils import uuid_text

logger = structlog.get_logger(__name__)


class EditRunPromptColumnInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset UUID or exact dataset name. Omit if column_id uniquely identifies the run-prompt column.",
    )
    column_id: str = Field(
        default="",
        description="UUID of the run-prompt column to edit. Omit to list candidates.",
    )
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
    tools: Optional[List[str]] = Field(
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
        "parameters) and optionally re-runs the prompt on all rows. "
        "Call without a column ID to list run-prompt column candidates."
    )
    category = "datasets"
    input_model = EditRunPromptColumnInput

    def execute(
        self, params: EditRunPromptColumnInput, context: ToolContext
    ) -> ToolResult:
        from django.db import transaction

        from model_hub.models.choices import SourceChoices, StatusType
        from model_hub.models.develop_dataset import Cell, Column, Dataset
        from model_hub.models.run_prompt import RunPrompter
        from model_hub.tasks.run_prompt import process_prompts_single

        column_ref = str(params.column_id or "").strip()
        column_uuid = uuid_text(column_ref)
        if not column_uuid:
            return _candidate_run_prompt_columns_result(
                context,
                "Run Prompt Column Required For Edit",
                (
                    "Provide `column_id` for the run-prompt column to edit. "
                    "These are recent candidates."
                ),
            )

        # Validate column exists and is a run-prompt column
        column_qs = Column.objects.select_related("dataset").filter(
            id=column_uuid,
            deleted=False,
            dataset__deleted=False,
            dataset__organization=context.organization,
        )
        if context.workspace:
            column_qs = column_qs.filter(dataset__workspace=context.workspace)
        column = column_qs.first()
        if column is None:
            return _candidate_run_prompt_columns_result(
                context,
                "Run Prompt Column Not Found",
                f"Column `{column_ref}` was not found in this workspace.",
            )
        dataset = column.dataset

        dataset_ref = str(params.dataset_id or "").strip()
        if dataset_ref:
            from ai_tools.tools.datasets._utils import resolve_dataset_for_tool

            requested_dataset, dataset_result = resolve_dataset_for_tool(
                dataset_ref,
                context,
                "Dataset Required For Run Prompt Edit",
            )
            if dataset_result:
                return dataset_result
            if requested_dataset.id != dataset.id:
                return _candidate_run_prompt_columns_result(
                    context,
                    "Run Prompt Column Not In Dataset",
                    (
                        f"Column `{column_ref}` belongs to dataset `{dataset.name}`, "
                        f"not `{requested_dataset.name}`."
                    ),
                )

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
                tool_uuid = uuid_text(tool_id)
                if not tool_uuid:
                    return ToolResult.not_found("Tool", str(tool_id))
                try:
                    tool_obj = Tools.objects.get(
                        id=tool_uuid, organization=context.organization
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

            # Clear existing cells for rerun
            if params.run:
                Cell.objects.filter(dataset=dataset, column=column).update(
                    value=None, status=StatusType.RUNNING.value
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
