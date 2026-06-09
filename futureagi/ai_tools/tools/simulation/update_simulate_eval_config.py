from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from tracer.models.custom_eval_config import ModelChoices

VALID_EVAL_MODELS = {choice.value for choice in ModelChoices}


class UpdateSimulateEvalConfigInput(PydanticBaseModel):
    run_test_id: UUID = Field(
        description="The UUID of the RunTest that owns the eval config"
    )
    eval_config_id: UUID = Field(
        description="The UUID of the SimulateEvalConfig to update"
    )
    name: str | None = Field(
        default=None,
        description="New name for the eval config",
    )
    config: dict | None = Field(
        default=None,
        description="Updated runtime configuration for the evaluation",
    )
    mapping: dict | None = Field(
        default=None,
        description=(
            "Updated mapping of eval template input keys to call execution fields. "
            "Valid voice fields: transcript, voice_recording, assistant_recording, "
            "customer_recording, stereo_recording, agent_prompt. "
            "Valid text fields: transcript, agent_prompt, user_chat_transcript, "
            "assistant_chat_transcript."
        ),
    )
    model: str | None = Field(
        default=None,
        description="Model to use for evaluation",
    )

    @field_validator("model")
    @classmethod
    def check_model(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_EVAL_MODELS:
            raise ValueError(
                f"Invalid model '{v}'. Must be one of: {sorted(VALID_EVAL_MODELS)}"
            )
        return v

    error_localizer: bool | None = Field(
        default=None,
        description="Enable or disable error localization for this eval config",
    )


@register_tool
class UpdateSimulateEvalConfigTool(BaseTool):
    name = "update_simulate_eval_config"
    description = (
        "Updates an evaluation config on a simulation run test. "
        "Can change the name, config, mapping, model, or error_localizer setting."
    )
    category = "simulation"
    input_model = UpdateSimulateEvalConfigInput

    def execute(
        self, params: UpdateSimulateEvalConfigInput, context: ToolContext
    ) -> ToolResult:
        from simulate.models.eval_config import SimulateEvalConfig
        from simulate.models.run_test import RunTest

        # Get the run test
        try:
            run_test = RunTest.objects.get(
                id=params.run_test_id,
                organization=context.organization,
                deleted=False,
            )
        except RunTest.DoesNotExist:
            return ToolResult.not_found("Run Test", str(params.run_test_id))

        # Get the eval config
        try:
            eval_config = SimulateEvalConfig.objects.select_related(
                "eval_template"
            ).get(
                id=params.eval_config_id,
                run_test=run_test,
                deleted=False,
            )
        except SimulateEvalConfig.DoesNotExist:
            return ToolResult.not_found("Eval Config", str(params.eval_config_id))

        updated_fields = []

        if params.name is not None:
            eval_config.name = params.name
            updated_fields.append("name")

        if params.config is not None:
            from model_hub.utils.function_eval_params import (
                normalize_eval_runtime_config,
            )

            eval_config.config = normalize_eval_runtime_config(
                eval_config.eval_template.config,
                params.config,
            )
            updated_fields.append("config")

        if params.mapping is not None:
            eval_config.mapping = params.mapping
            updated_fields.append("mapping")

        if params.model is not None:
            eval_config.model = params.model
            updated_fields.append("model")

        if params.error_localizer is not None:
            eval_config.error_localizer = params.error_localizer
            updated_fields.append("error_localizer")

        if not updated_fields:
            return ToolResult.error(
                "No fields provided to update.",
                error_code="VALIDATION_ERROR",
            )

        eval_config.save(update_fields=updated_fields)

        info = key_value_block(
            [
                ("Eval Config", eval_config.name),
                ("ID", f"`{params.eval_config_id}`"),
                ("Test", run_test.name),
                ("Updated Fields", ", ".join(updated_fields)),
            ]
        )

        content = section("Evaluation Config Updated", info)

        return ToolResult(
            content=content,
            data={
                "eval_config_id": str(params.eval_config_id),
                "eval_config_name": eval_config.name,
                "run_test_id": str(params.run_test_id),
                "updated_fields": updated_fields,
            },
        )
