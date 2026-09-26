import re
from typing import List, Literal, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section, truncate
from ai_tools.registry import register_tool
from ai_tools.tools.datasets.add_run_prompt_column import (
    VARIABLE_PATTERN,
    MessageInput,
)

logger = structlog.get_logger(__name__)


class PreviewRunPromptColumnInput(PydanticBaseModel):
    dataset_id: UUID = Field(description="The UUID of the dataset")
    model: str = Field(
        description="Model to use (e.g., 'gpt-4o', 'claude-sonnet-4-20250514')",
        min_length=1,
    )
    messages: List[MessageInput] = Field(
        description="List of prompt messages with {{column_name}} substitution.",
        min_length=1,
    )
    first_n_rows: Optional[int] = Field(
        default=None,
        ge=1,
        description="Preview on the first N rows. Mutually exclusive with row_indices.",
    )
    row_indices: Optional[List[int]] = Field(
        default=None,
        description="Specific 0-based row indices to preview. Mutually exclusive with first_n_rows.",
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=65536)
    output_format: Literal["array", "string", "number", "object", "audio", "image"] = (
        Field(default="string")
    )
    frequency_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    presence_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    response_format: Optional[dict] = Field(default=None)
    tool_choice: Optional[Literal["auto", "required"]] = Field(default=None)
    tools: Optional[List[UUID]] = Field(default=None)

    @model_validator(mode="after")
    def validate_row_selection(self):
        has_first_n = self.first_n_rows is not None
        has_indices = self.row_indices is not None and len(self.row_indices) > 0

        if not has_first_n and not has_indices:
            raise ValueError("Either 'first_n_rows' or 'row_indices' must be provided.")
        if has_first_n and has_indices:
            raise ValueError("'first_n_rows' and 'row_indices' are mutually exclusive.")

        # First message cannot be assistant
        if self.messages and self.messages[0].role == "assistant":
            raise ValueError("First message cannot be from 'assistant'.")

        # User messages must have non-empty content
        for msg in self.messages:
            if msg.role == "user" and not msg.content.strip():
                raise ValueError("User messages must have non-empty content.")

        return self


@register_tool
class PreviewRunPromptColumnTool(BaseTool):
    name = "preview_run_prompt_column"
    description = (
        "Previews a run-prompt on a few sample rows without creating a column. "
        "Returns the LLM response for each selected row. Useful for testing "
        "prompt configurations before committing."
    )
    category = "datasets"
    input_model = PreviewRunPromptColumnInput

    def execute(
        self, params: PreviewRunPromptColumnInput, context: ToolContext
    ) -> ToolResult:
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
        from model_hub.models.choices import SourceChoices
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
        from model_hub.views.run_prompt import populate_placeholders

        # Validate dataset (organization-filtered)
        try:
            dataset = Dataset.objects.get(
                id=params.dataset_id,
                deleted=False,
                organization=context.organization,
            )
        except Dataset.DoesNotExist:
            return ToolResult.not_found("Dataset", str(params.dataset_id))

        # Validate referenced columns
        messages = [
            {"role": msg.role, "content": msg.content} for msg in params.messages
        ]
        dataset_columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
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
                f"Column(s) not found: {', '.join(missing_vars)}. "
                f"Available: {', '.join(sorted(col_names))}",
                error_code="VALIDATION_ERROR",
            )

        # Get rows
        all_rows = Row.objects.filter(dataset=dataset, deleted=False).order_by("order")

        if params.first_n_rows:
            rows = list(all_rows[: params.first_n_rows])
        else:
            total_rows = all_rows.count()
            invalid_indices = [i for i in params.row_indices if i >= total_rows]
            if invalid_indices:
                return ToolResult.error(
                    f"Row indices out of range (dataset has {total_rows} rows): "
                    f"{invalid_indices}",
                    error_code="VALIDATION_ERROR",
                )
            rows_list = list(all_rows)
            rows = [rows_list[i] for i in params.row_indices]

        if not rows:
            return ToolResult.error(
                "No rows found to preview.",
                error_code="VALIDATION_ERROR",
            )

        # Validate tools if provided
        tools_to_send = []
        if params.tools:
            from model_hub.models.openai_tools import Tools as ToolsModel

            for tool_id in params.tools:
                try:
                    tool_obj = ToolsModel.objects.get(
                        id=tool_id, organization=context.organization
                    )
                    if tool_obj.config:
                        tools_to_send.append(tool_obj.as_openai_tool())
                except ToolsModel.DoesNotExist:
                    return ToolResult.not_found("Tool", str(tool_id))

        # Execute prompt for each row
        results = []
        columns = list(dataset_columns)

        for row_idx, row in enumerate(rows):
            try:
                # Populate placeholders for this row
                row_messages = populate_placeholders(messages, row, dataset, columns)

                run_prompt = RunPrompt(
                    model=params.model,
                    organization_id=context.organization.id,
                    messages=row_messages,
                    temperature=params.temperature,
                    frequency_penalty=params.frequency_penalty,
                    presence_penalty=params.presence_penalty,
                    max_tokens=params.max_tokens,
                    top_p=params.top_p,
                    response_format=params.response_format,
                    tool_choice=params.tool_choice,
                    tools=tools_to_send if tools_to_send else None,
                    output_format=params.output_format,
                    ws_manager=None,
                    workspace_id=context.workspace.id if context.workspace else None,
                )

                response_content, value_info = run_prompt.litellm_response(
                    streaming=False,
                    run_type="preview",
                )

                metadata = value_info.get("metadata", {})
                usage = metadata.get("usage", {})

                results.append(
                    {
                        "row_index": row_idx,
                        "row_id": str(row.id),
                        "response": str(response_content),
                        "usage": usage,
                        "error": None,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "row_index": row_idx,
                        "row_id": str(row.id),
                        "response": None,
                        "usage": None,
                        "error": str(e),
                    }
                )

        # Build response
        content_parts = [f"## Preview Results ({len(results)} rows)\n"]
        for r in results:
            header = f"### Row {r['row_index']}"
            if r["error"]:
                content_parts.append(f"{header}\n**Error:** {r['error']}\n")
            else:
                response_text = truncate(r["response"], 500)
                usage = r.get("usage", {}) or {}
                tokens = usage.get("total_tokens", "—")
                content_parts.append(f"{header}\n{response_text}\n_Tokens: {tokens}_\n")

        return ToolResult(
            content="\n".join(content_parts),
            data={
                "dataset_id": str(dataset.id),
                "model": params.model,
                "rows_previewed": len(results),
                "results": results,
            },
        )
