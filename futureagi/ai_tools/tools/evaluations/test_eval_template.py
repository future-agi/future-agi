from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section, truncate
from ai_tools.registry import register_tool


class TestEvalTemplateInput(PydanticBaseModel):
    eval_template_id: UUID = Field(description="The UUID of the eval template to test")
    mapping: dict = Field(
        description=(
            "Mapping of template variable names to test values. "
            'Example: {"input": "What is the capital of France?", "output": "Paris is the capital."}'
        )
    )
    model: Optional[str] = Field(
        default=None,
        description="Override model for the test run (optional).",
    )


@register_tool
class TestEvalTemplateTool(BaseTool):
    name = "test_eval_template"
    description = (
        "Runs a dry-run test of an evaluation template with provided input data. "
        "Returns the evaluation result without persisting anything. "
        "Works with LLM, code, and agent eval types. "
        "Use this to validate template configuration before applying to datasets."
    )
    category = "evaluations"
    input_model = TestEvalTemplateInput

    def execute(
        self, params: TestEvalTemplateInput, context: ToolContext
    ) -> ToolResult:
        from django.db.models import Q

        from model_hub.models.evals_metric import EvalTemplate

        # Look up the template (user-owned or system)
        try:
            template = EvalTemplate.no_workspace_objects.get(
                Q(organization=context.organization) | Q(organization__isnull=True),
                id=params.eval_template_id,
            )
        except EvalTemplate.DoesNotExist:
            return ToolResult.not_found("Eval Template", str(params.eval_template_id))

        config = template.config or {}
        required_keys = config.get("required_keys", [])

        # Validate required keys are present in mapping
        missing_keys = [k for k in required_keys if k not in params.mapping]
        if missing_keys:
            return ToolResult.error(
                f"Missing required keys in mapping: {', '.join(f'`{k}`' for k in missing_keys)}. "
                f"Required: {', '.join(f'`{k}`' for k in required_keys)}",
                error_code="VALIDATION_ERROR",
            )

        # Build eval config
        eval_config = {
            "mapping": params.mapping,
            "config": config.get("config", {}),
            "output": config.get("output", "Pass/Fail"),
        }

        if template.criteria:
            eval_config["criteria"] = template.criteria
        if template.choices:
            eval_config["choices"] = template.choices

        model = params.model or template.model

        try:
            from model_hub.models.evals_metric import EvalTemplate as ET
            from model_hub.views.separate_evals import (
                prepare_user_eval_config,
                run_eval_func,
            )

            # Get the base template for eval execution
            try:
                base_template = ET.no_workspace_objects.get(name="deterministic_evals")
            except ET.DoesNotExist:
                return ToolResult.error(
                    "System eval template 'deterministic_evals' not found.",
                    error_code="CONFIGURATION_ERROR",
                )

            # Resolve eval_type_id from the template's eval_type or config
            eval_type_id = config.get("eval_type_id", "")
            if not eval_type_id:
                _type_map = {
                    "agent": "AgentEvaluator",
                    "llm": "CustomPromptEvaluator",
                    "code": "CustomCodeEval",
                }
                eval_type_id = _type_map.get(template.eval_type or "llm", "CustomPromptEvaluator")

            # Build validated_data for prepare_user_eval_config
            validated_data = {
                "template_type": "futureagi" if eval_type_id == "AgentEvaluator" else "llm",
                "config": eval_config,
                "model": model,
                "input_data_types": {},
            }

            prepared_config = prepare_user_eval_config(validated_data, True)
            prepared_config["output"] = config.get("output", "Pass/Fail")

            if eval_type_id in ("CustomPromptEvaluator", "AgentEvaluator"):
                data_config = prepared_config.get("config", {})
                data_config["organization_id"] = str(context.organization.id)
                prepared_config["config"] = data_config

            response = run_eval_func(
                prepared_config,
                params.mapping,
                base_template,
                context.organization,
                input_data_types={},
                type="user_built",
                model=model,
                eval_id=eval_type_id,
                test=True,
                source="mcp_tool_test",
                workspace=context.workspace,
            )

            # Format the response
            result_info = []
            if isinstance(response, dict):
                for key, value in response.items():
                    result_info.append((key, truncate(str(value), 200)))
            else:
                result_info.append(("Result", truncate(str(response), 500)))

            eval_type_labels = {"llm": "LLM", "code": "Code", "agent": "Agent"}
            info = key_value_block(
                [
                    ("Template", f"{template.name} (`{str(template.id)}`)"),
                    ("Type", eval_type_labels.get(template.eval_type or "llm", template.eval_type or "—")),
                    ("Model", model or "default"),
                ]
                + result_info
            )

            return ToolResult(
                content=section("Eval Test Result", info),
                data={"template_id": str(template.id), "result": response},
            )

        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            return ToolResult.error(
                f"Test execution failed: {str(e)}",
                error_code=code_from_exception(e),
            )
