import json
from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class TriggerErrorLocalizationInput(PydanticBaseModel):
    eval_log_id: Optional[UUID] = Field(
        default=None,
        description="The log_id from an eval log entry (APICallLog). Use this for playground/dataset evals.",
    )
    evaluation_id: Optional[UUID] = Field(
        default=None,
        description="The UUID of a standalone Evaluation record. Use this for SDK/standalone evals.",
    )


@register_tool
class TriggerErrorLocalizationTool(BaseTool):
    name = "trigger_error_localization"
    description = (
        "Triggers error localization on an evaluation result to pinpoint which parts "
        "of the input caused the evaluation to fail. Supports text (sentence-level), "
        "image (patch-level), and audio (segment-level) analysis. "
        "Returns a task_id to poll for results."
    )
    category = "evaluations"
    input_model = TriggerErrorLocalizationInput

    def execute(
        self, params: TriggerErrorLocalizationInput, context: ToolContext
    ) -> ToolResult:
        from tfc.ee_gating import EEFeature, is_oss

        if is_oss():
            return ToolResult.feature_unavailable(EEFeature.AGENTIC_EVAL.value)

        from model_hub.models.error_localizer_model import (
            ErrorLocalizerSource,
            ErrorLocalizerTask,
        )

        if not params.eval_log_id and not params.evaluation_id:
            return ToolResult.error(
                "Either eval_log_id or evaluation_id must be provided.",
                error_code="VALIDATION_ERROR",
            )

        try:
            if params.evaluation_id:
                return self._trigger_for_evaluation(params.evaluation_id, context)
            else:
                return self._trigger_for_log(params.eval_log_id, context)
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            return ToolResult.error(
                f"Failed to trigger error localization: {str(e)}",
                error_code=code_from_exception(e),
            )

    def _trigger_for_evaluation(self, evaluation_id, context):
        from model_hub.models.error_localizer_model import ErrorLocalizerTask
        from model_hub.models.evaluation import Evaluation

        try:
            evaluation = Evaluation.objects.select_related(
                "eval_template", "organization", "workspace"
            ).get(id=evaluation_id, organization=context.organization)
        except Evaluation.DoesNotExist:
            return ToolResult.not_found("Evaluation", str(evaluation_id))

        # Check if task already exists for this source
        existing = ErrorLocalizerTask.objects.filter(
            source_id=evaluation_id, deleted=False
        ).first()
        if existing:
            info = key_value_block(
                [
                    ("Task ID", f"`{existing.id}`"),
                    ("Status", existing.status),
                    ("Source", "evaluation"),
                ]
            )
            return ToolResult(
                content=section("Error Localization Task (existing)", info),
                data={
                    "task_id": str(existing.id),
                    "status": existing.status,
                    "existing": True,
                },
            )

        from model_hub.tasks.user_evaluation import (
            trigger_error_localization_for_standalone,
        )

        task = trigger_error_localization_for_standalone(evaluation)
        if not task:
            return ToolResult.error(
                "Failed to create error localization task. Check that the evaluation has input data and results.",
                error_code="PROCESSING_ERROR",
            )

        info = key_value_block(
            [
                ("Task ID", f"`{task.id}`"),
                ("Status", task.status),
                ("Source", "standalone"),
                (
                    "Eval Template",
                    evaluation.eval_template.name if evaluation.eval_template else "—",
                ),
            ]
        )
        return ToolResult(
            content=section("Error Localization Triggered", info),
            data={"task_id": str(task.id), "status": task.status},
        )

    def _trigger_for_log(self, log_id, context):
        import json

        from model_hub.models.error_localizer_model import (
            ErrorLocalizerSource,
            ErrorLocalizerTask,
        )
        try:
            from ee.usage.models.usage import APICallLog
        except ImportError:
            APICallLog = None

        try:
            log = APICallLog.objects.get(
                log_id=log_id, organization=context.organization
            )
        except APICallLog.DoesNotExist:
            return ToolResult.not_found("Eval Log", str(log_id))

        # Check if task already exists
        existing = ErrorLocalizerTask.objects.filter(
            source_id=log_id, deleted=False
        ).first()
        if existing:
            info = key_value_block(
                [
                    ("Task ID", f"`{existing.id}`"),
                    ("Status", existing.status),
                    ("Source", "eval_log"),
                ]
            )
            return ToolResult(
                content=section("Error Localization Task (existing)", info),
                data={
                    "task_id": str(existing.id),
                    "status": existing.status,
                    "existing": True,
                },
            )

        # Parse config to get eval data
        config = log.config
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (json.JSONDecodeError, ValueError):
                return ToolResult.error(
                    "Malformed config on log entry — expected JSON.",
                    error_code="VALIDATION_ERROR",
                )

        reference_id = config.get("reference_id") or log.reference_id
        if not reference_id:
            return ToolResult.error(
                "Could not determine eval template from this log entry.",
                error_code="VALIDATION_ERROR",
            )

        from model_hub.models.evals_metric import EvalTemplate

        try:
            template = EvalTemplate.no_workspace_objects.get(id=reference_id)
        except EvalTemplate.DoesNotExist:
            return ToolResult.error(
                f"Eval template {reference_id} not found.",
                error_code="NOT_FOUND",
            )

        # Extract data from the log config
        mappings = config.get("mappings", {})
        output_data = config.get("output", {})
        eval_result = (
            output_data.get("output", "")
            if isinstance(output_data, dict)
            else str(output_data)
        )
        eval_explanation = (
            output_data.get("reason", "") if isinstance(output_data, dict) else ""
        )

        from model_hub.tasks.user_evaluation import (
            trigger_error_localization_for_playground,
        )

        task = trigger_error_localization_for_playground(
            eval_template=template,
            log=log,
            value=eval_result,
            mapping=mappings,
            eval_explanation=eval_explanation,
        )
        if not task:
            return ToolResult.error(
                "Failed to create error localization task. Check the eval log has valid input and result data.",
                error_code="PROCESSING_ERROR",
            )

        info = key_value_block(
            [
                ("Task ID", f"`{task.id}`"),
                ("Status", task.status),
                ("Source", "playground"),
                ("Eval Template", template.name),
            ]
        )
        return ToolResult(
            content=section("Error Localization Triggered", info),
            data={"task_id": str(task.id), "status": task.status},
        )
