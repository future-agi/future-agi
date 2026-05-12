from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.simulation.create_simulate_eval_config import VALID_EVAL_MODELS
from ai_tools.tools.tracing._utils import uuid_text


class UpdateSimulateEvalConfigInput(PydanticBaseModel):
    run_test_id: str = Field(
        default="",
        description="RunTest UUID or exact/fuzzy name that owns the eval config. Omit to list candidates.",
    )
    eval_config_id: str = Field(
        default="",
        description="SimulateEvalConfig UUID or exact/fuzzy name to update. Omit to list candidates.",
    )
    name: Optional[str] = Field(
        default=None,
        description="New name for the eval config",
    )
    config: Optional[dict] = Field(
        default=None,
        description="Updated runtime configuration for the evaluation",
    )
    mapping: Optional[dict] = Field(
        default=None,
        description=(
            "Updated mapping of eval template input keys to call execution fields. "
            "Valid voice fields: transcript, voice_recording, assistant_recording, "
            "customer_recording, stereo_recording, agent_prompt. "
            "Valid text fields: transcript, agent_prompt, user_chat_transcript, "
            "assistant_chat_transcript."
        ),
    )
    model: Optional[str] = Field(
        default=None,
        description="Model to use for evaluation",
    )

    @field_validator("model")
    @classmethod
    def check_model(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_EVAL_MODELS:
            raise ValueError(
                f"Invalid model '{v}'. Must be one of: {sorted(VALID_EVAL_MODELS)}"
            )
        return v

    error_localizer: Optional[bool] = Field(
        default=None,
        description="Enable or disable error localization for this eval config",
    )


@register_tool
class UpdateSimulateEvalConfigTool(BaseTool):
    name = "update_simulate_eval_config"
    description = (
        "Updates an evaluation config on a simulation run test. "
        "Can change the name, config, mapping, model, or error_localizer setting. "
        "Call with partial input to list run tests, eval configs, or required update fields."
    )
    category = "simulation"
    input_model = UpdateSimulateEvalConfigInput

    def execute(
        self, params: UpdateSimulateEvalConfigInput, context: ToolContext
    ) -> ToolResult:
        from simulate.models.eval_config import SimulateEvalConfig
        from ai_tools.tools.agents._utils import resolve_run_test

        # Get the run test
        run_test, unresolved = resolve_run_test(
            params.run_test_id,
            context,
            title="Run Test Required To Update Eval Config",
        )
        if unresolved:
            return unresolved

        # Get the eval config
        config_ref = str(params.eval_config_id or "").strip()
        if not config_ref:
            return _candidate_simulate_eval_configs_result(
                run_test,
                "Eval Config Required For Update",
                "Provide `eval_config_id` to update an evaluation config.",
            )

        qs = SimulateEvalConfig.objects.select_related("eval_template").filter(
            run_test=run_test,
            deleted=False,
        )
        config_uuid = uuid_text(config_ref)
        if config_uuid:
            eval_config = qs.filter(id=config_uuid).first()
        else:
            exact = qs.filter(name__iexact=config_ref)
            eval_config = exact.first() if exact.count() == 1 else None
            if eval_config is None:
                fuzzy = qs.filter(name__icontains=config_ref)
                eval_config = fuzzy.first() if fuzzy.count() == 1 else None
        if eval_config is None:
            return _candidate_simulate_eval_configs_result(
                run_test,
                "Eval Config Not Found",
                f"Eval config `{config_ref}` was not found on `{run_test.name}`.",
                search="" if config_uuid else config_ref,
            )

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
            return ToolResult.needs_input(
                section(
                    "Eval Config Update Fields Required",
                    (
                        f"Eval config `{eval_config.name}` was resolved. Provide at "
                        "least one of `name`, `config`, `mapping`, `model`, or "
                        "`error_localizer`."
                    ),
                ),
                data={
                    "run_test_id": str(run_test.id),
                    "eval_config_id": str(eval_config.id),
                    "requires_update_fields": True,
                },
                missing_fields=["name|config|mapping|model|error_localizer"],
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
                "eval_config_id": str(eval_config.id),
                "eval_config_name": eval_config.name,
                "run_test_id": str(run_test.id),
                "updated_fields": updated_fields,
            },
        )


def _candidate_simulate_eval_configs_result(
    run_test,
    title: str,
    detail: str = "",
    search: str = "",
) -> ToolResult:
    from simulate.models.eval_config import SimulateEvalConfig

    qs = SimulateEvalConfig.objects.select_related("eval_template").filter(
        run_test=run_test,
        deleted=False,
    )
    search = str(search or "").strip()
    if search and not uuid_text(search):
        qs = qs.filter(name__icontains=search)
    configs = list(qs.order_by("-created_at")[:10])
    rows = [
        [
            f"`{config.id}`",
            config.name or "-",
            config.eval_template.name if config.eval_template else "-",
            config.model or "-",
            format_datetime(config.created_at),
        ]
        for config in configs
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["ID", "Name", "Template", "Model", "Created"], rows
        )
    else:
        body = body or f"No eval configs found on `{run_test.name}`."
    return ToolResult.needs_input(
        section(title, body),
        data={
            "requires_eval_config_id": True,
            "run_test_id": str(run_test.id),
            "configs": [
                {"id": str(config.id), "name": config.name}
                for config in configs
            ],
        },
        missing_fields=["eval_config_id"],
    )
