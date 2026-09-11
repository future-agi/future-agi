import re
from typing import List, Literal, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)

VALID_OUTPUT_FORMATS = ("array", "string", "number", "object", "audio", "image")
VALID_ROLES = ("user", "assistant", "system")

# Regex that supports simple {{col}}, JSON path {{col.field.name}}, and indexed {{col[0]}}
VARIABLE_PATTERN = re.compile(r"\{\{\s*([^{}\s]+(?:\.[^{}\s]+)*(?:\[\d+\])?)\s*\}\}")


class MessageInput(PydanticBaseModel):
    role: str = Field(description="Message role: 'system', 'user', or 'assistant'")
    content: str = Field(
        description=(
            "Message content. Use {{column_name}} to reference dataset columns. "
            "Example: 'Say hi to {{name}}'"
        )
    )

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(VALID_ROLES)}")
        return v


class AddRunPromptColumnInput(PydanticBaseModel):
    dataset_id: UUID = Field(description="The UUID of the dataset")
    name: str = Field(
        description="Name for the new run-prompt column",
        min_length=1,
        max_length=255,
    )
    model: str = Field(
        description="Model to use for the prompt (e.g., 'gpt-4o', 'claude-sonnet-4-20250514')",
        min_length=1,
    )
    messages: List[MessageInput] = Field(
        description=(
            "List of prompt messages. Each message has a role and content. "
            "Use {{column_name}} to substitute values from dataset columns. "
            "Example: [{'role': 'user', 'content': 'Say hi to {{name}}'}]"
        ),
        min_length=1,
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="LLM temperature (0-2). Default 0.7.",
    )
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        le=65536,
        description="Maximum tokens in the response. None for model default.",
    )
    output_format: Literal["array", "string", "number", "object", "audio", "image"] = (
        Field(
            default="string",
            description="Output format: 'string', 'object', 'array', 'number', 'audio', 'image'. Default 'string'.",
        )
    )
    run: bool = Field(
        default=True,
        description="If true, immediately start running the prompt on all rows. Default true.",
    )
    concurrency: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Number of concurrent executions (1-10). Default 5.",
    )
    model_type: Literal["llm", "tts", "stt"] = Field(
        default="llm",
        description="Model type: 'llm' (language model), 'tts' (text-to-speech), or 'stt' (speech-to-text). Default 'llm'.",
    )
    voice: Optional[str] = Field(
        default=None,
        description="Voice name for TTS models. Required when model_type is 'tts'.",
    )
    voice_id: Optional[str] = Field(
        default=None,
        description="Voice ID for TTS models (provider-specific identifier).",
    )
    frequency_penalty: Optional[float] = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        description="Penalty for word repetition (-2 to 2). None for model default.",
    )
    presence_penalty: Optional[float] = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        description="Penalty for new topic introduction (-2 to 2). None for model default.",
    )
    top_p: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling parameter (0-1). None for model default.",
    )
    response_format: Optional[dict] = Field(
        default=None,
        description="JSON schema for structured response format.",
    )
    tool_choice: Optional[Literal["auto", "required"]] = Field(
        default=None,
        description="Tool selection mode: 'auto' or 'required'.",
    )
    tools: Optional[List[UUID]] = Field(
        default=None,
        description="List of tool UUIDs to make available during prompt execution.",
    )

    @model_validator(mode="after")
    def validate_cross_fields(self):
        # First message cannot be assistant
        if self.messages and self.messages[0].role == "assistant":
            raise ValueError(
                "First message cannot be from 'assistant'. It must be from 'system' or 'user'."
            )

        # User messages must have non-empty content
        for msg in self.messages:
            if msg.role == "user" and not msg.content.strip():
                raise ValueError("User messages must have non-empty content.")

        # TTS requires voice
        if self.model_type == "tts" and not self.voice:
            raise ValueError("Voice is required when model_type is 'tts'.")

        # Auto-set output_format for TTS/image model types
        if self.model_type == "tts" and self.output_format == "string":
            self.output_format = "audio"
        elif self.model_type == "stt" and self.output_format not in (
            "string",
            "object",
        ):
            self.output_format = "string"

        return self


@register_tool
class AddRunPromptColumnTool(BaseTool):
    name = "add_run_prompt_column"
    description = (
        "Adds a run-prompt column to a dataset. Creates a RunPrompter that executes "
        "an LLM prompt for each row, storing the output in a new column. "
        "Use {{column_name}} in message content to reference dataset column values. "
        "Example: messages=[{'role': 'user', 'content': 'Summarize: {{text}}'}]"
    )
    category = "datasets"
    input_model = AddRunPromptColumnInput

    def execute(
        self, params: AddRunPromptColumnInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.choices import SourceChoices, StatusType
        from model_hub.models.develop_dataset import Column, Dataset
        from model_hub.models.run_prompt import RunPrompter
        from model_hub.services.column_service import create_run_prompt_column

        # Validate dataset (organization-filtered)
        try:
            dataset = Dataset.objects.get(
                id=params.dataset_id, deleted=False, organization=context.organization
            )
        except Dataset.DoesNotExist:
            return ToolResult.not_found("Dataset", str(params.dataset_id))

        # Convert messages to the format RunPrompter expects
        messages = []
        for msg in params.messages:
            if isinstance(msg, dict):
                messages.append({"role": msg["role"], "content": msg["content"]})
            else:
                messages.append({"role": msg.role, "content": msg.content})

        # Validate that referenced columns exist
        dataset_columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
            source__in=[
                SourceChoices.EVALUATION.value,
                SourceChoices.EVALUATION_REASON.value,
            ]
        )
        col_names = {col.name for col in dataset_columns}

        # Extract {{variable}} references from messages (supports JSON paths and indexed access)
        referenced_vars = set()
        for msg in messages:
            for match in VARIABLE_PATTERN.findall(msg["content"]):
                # Extract the base column name (before any dot or bracket)
                base_name = match.split(".")[0].split("[")[0]
                referenced_vars.add(base_name)

        missing_vars = referenced_vars - col_names
        if missing_vars:
            return ToolResult.error(
                f"Column(s) referenced in messages not found in dataset: "
                f"{', '.join(f'{{{{' + v + '}}}}' for v in missing_vars)}. "
                f"Available columns: {', '.join(sorted(col_names))}",
                error_code="VALIDATION_ERROR",
            )

        # Check for duplicate column name
        if Column.objects.filter(
            dataset=dataset, name=params.name, deleted=False
        ).exists():
            return ToolResult.error(
                f"A column named '{params.name}' already exists in this dataset.",
                error_code="VALIDATION_ERROR",
            )

        # Validate tools exist and belong to org if provided
        tool_objects = []
        if params.tools:
            from model_hub.models.openai_tools import Tools

            for tool_id in params.tools:
                try:
                    tool_obj = Tools.objects.get(
                        id=tool_id, organization=context.organization
                    )
                    tool_objects.append(tool_obj)
                except Tools.DoesNotExist:
                    return ToolResult.not_found("Tool", str(tool_id))

        # Determine initial status
        status = (
            StatusType.NOT_STARTED.value if params.run else StatusType.INACTIVE.value
        )

        # Build run_prompt_config
        run_prompt_config = {
            "modelType": params.model_type,
            "modelName": params.model,
        }
        if params.voice:
            run_prompt_config["voice"] = params.voice
        if params.voice_id:
            run_prompt_config["voiceId"] = params.voice_id

        # Create RunPrompter
        run_prompter = RunPrompter(
            dataset=dataset,
            model=params.model,
            name=params.name,
            messages=messages,
            output_format=params.output_format,
            temperature=params.temperature,
            concurrency=params.concurrency,
            status=status,
            organization=context.organization,
            workspace=context.workspace,
            run_prompt_config=run_prompt_config,
        )
        if params.max_tokens is not None:
            run_prompter.max_tokens = params.max_tokens
        if params.frequency_penalty is not None:
            run_prompter.frequency_penalty = params.frequency_penalty
        if params.presence_penalty is not None:
            run_prompter.presence_penalty = params.presence_penalty
        if params.top_p is not None:
            run_prompter.top_p = params.top_p
        if params.response_format is not None:
            run_prompter.response_format = params.response_format
        if params.tool_choice is not None:
            run_prompter.tool_choice = params.tool_choice
        run_prompter.save()

        # Attach tools if provided
        if tool_objects:
            run_prompter.tools.set(tool_objects)

        # Create the column
        column, created = create_run_prompt_column(
            dataset=dataset,
            source_id=str(run_prompter.id),
            name=params.name,
            output_format=params.output_format,
        )

        # Update column order
        order = dataset.column_order or []
        if str(column.id) not in order:
            order.append(str(column.id))
            dataset.column_order = order
            dataset.save(update_fields=["column_order"])

        # If run=True, trigger execution via Temporal activity
        workflow_started = False
        if params.run:
            try:
                from model_hub.tasks.run_prompt import process_prompts_single

                run_prompter.status = StatusType.RUNNING.value
                run_prompter.save(update_fields=["status"])

                process_prompts_single.apply_async(
                    args=({"type": "not_started", "prompt_id": str(run_prompter.id)},)
                )
                workflow_started = True
            except Exception as e:
                logger.warning(
                    "run_prompt_celery_dispatch_failed",
                    run_prompter_id=str(run_prompter.id),
                    error=str(e),
                )
                # Fallback: mark as not_started, will be picked up by polling
                run_prompter.status = StatusType.NOT_STARTED.value
                run_prompter.save(update_fields=["status"])

        info = key_value_block(
            [
                ("Column ID", f"`{column.id}`"),
                ("Column Name", params.name),
                ("RunPrompter ID", f"`{run_prompter.id}`"),
                ("Model", params.model),
                ("Model Type", params.model_type),
                ("Messages", str(len(messages))),
                ("Temperature", str(params.temperature)),
                ("Concurrency", str(params.concurrency)),
                ("Output Format", params.output_format),
                ("Status", run_prompter.status),
                (
                    "Dataset",
                    dashboard_link("dataset", str(dataset.id), label=dataset.name),
                ),
            ]
        )

        content = section("Run Prompt Column Added", info)
        if params.run:
            if workflow_started:
                content += "\n\n_Prompt execution started. Results will appear in the new column as each row is processed._"
            else:
                content += (
                    "\n\n_Prompt execution queued. It will be picked up shortly._"
                )
        else:
            content += "\n\n_Column created but not running. Trigger execution from the dashboard._"

        return ToolResult(
            content=content,
            data={
                "column_id": str(column.id),
                "run_prompter_id": str(run_prompter.id),
                "name": params.name,
                "model": params.model,
                "status": run_prompter.status,
            },
        )
