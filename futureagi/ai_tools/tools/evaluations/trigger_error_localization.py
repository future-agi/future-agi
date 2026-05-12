import json

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class TriggerErrorLocalizationInput(PydanticBaseModel):
    eval_log_id: str | None = Field(
        default=None,
        description="The log_id from an eval log entry (APICallLog). Use this for playground/dataset evals.",
    )
    evaluation_id: str | None = Field(
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

        if not params.eval_log_id and not params.evaluation_id:
            return self._candidate_sources_result(
                context,
                "Error Localization Source Required",
                (
                    "Provide either `eval_log_id` for playground/dataset eval logs "
                    "or `evaluation_id` for standalone evaluations."
                ),
            )

        try:
            if params.evaluation_id:
                if not is_uuid(params.evaluation_id):
                    return self._candidate_sources_result(
                        context,
                        "Evaluation ID Required",
                        f"`{params.evaluation_id}` is not a valid evaluation UUID.",
                    )
                return self._trigger_for_evaluation(params.evaluation_id, context)
            else:
                if params.eval_log_id and not is_uuid(params.eval_log_id):
                    return self._candidate_sources_result(
                        context,
                        "Eval Log ID Required",
                        f"`{params.eval_log_id}` is not a valid eval log UUID.",
                    )
                return self._trigger_for_log(params.eval_log_id, context)
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            return ToolResult.error(
                f"Failed to trigger error localization: {str(e)}",
                error_code=code_from_exception(e),
            )

    def _candidate_sources_result(
        self, context: ToolContext, title: str, detail: str = ""
    ) -> ToolResult:
        rows = []
        log_data = []
        evaluation_data = []

        try:
            from django.db.models import Q

            from ee.usage.models.usage import APICallLog

            logs = list(
                APICallLog.objects.filter(organization=context.organization)
                .filter(Q(source__icontains="eval") | Q(source_id__isnull=False))
                .order_by("-created_at")[:8]
            )
            for log in logs:
                rows.append(
                    [
                        "eval_log",
                        f"`{log.log_id}`",
                        truncate(log.source or "—", 36),
                        getattr(log, "status", "—") or "—",
                        format_datetime(log.created_at),
                    ]
                )
                log_data.append(
                    {
                        "eval_log_id": str(log.log_id),
                        "source": log.source,
                        "status": getattr(log, "status", None),
                    }
                )
        except Exception:
            pass

        try:
            from model_hub.models.evaluation import Evaluation

            evaluations = list(
                Evaluation.objects.select_related("eval_template")
                .filter(organization=context.organization)
                .order_by("-created_at")[:8]
            )
            for evaluation in evaluations:
                template_name = (
                    evaluation.eval_template.name
                    if evaluation.eval_template
                    else "evaluation"
                )
                rows.append(
                    [
                        "evaluation",
                        f"`{evaluation.id}`",
                        truncate(template_name, 36),
                        getattr(evaluation, "status", "—") or "—",
                        format_datetime(evaluation.created_at),
                    ]
                )
                evaluation_data.append(
                    {
                        "evaluation_id": str(evaluation.id),
                        "template_name": template_name,
                        "status": getattr(evaluation, "status", None),
                    }
                )
        except Exception:
            pass

        body = detail or "Choose a valid source before triggering localization."
        if rows:
            body += "\n\n" + markdown_table(
                ["Type", "ID", "Template/Source", "Status", "Created"],
                rows,
            )
        else:
            body += "\n\nNo recent eval logs or standalone evaluations were found."

        return ToolResult(
            content=section(title, body),
            data={
                "requires_eval_log_id_or_evaluation_id": True,
                "eval_logs": log_data,
                "evaluations": evaluation_data,
                "next_tools": ["get_eval_logs", "list_evaluations"],
            },
        )

    def _trigger_for_evaluation(self, evaluation_id, context):
        from model_hub.models.error_localizer_model import ErrorLocalizerTask
        from model_hub.models.evaluation import Evaluation

        try:
            evaluation = Evaluation.objects.select_related(
                "eval_template", "organization", "workspace"
            ).get(id=evaluation_id, organization=context.organization)
        except Evaluation.DoesNotExist:
            return self._candidate_sources_result(
                context,
                "Evaluation Not Found",
                f"Evaluation `{evaluation_id}` was not found.",
            )

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

        from model_hub.models.error_localizer_model import (
            ErrorLocalizerTask,
        )

        try:
            from ee.usage.models.usage import APICallLog
        except ImportError:
            APICallLog = None

        if APICallLog is None:
            return self._candidate_sources_result(
                context,
                "Eval Logs Unavailable",
                "Eval log storage is unavailable in this environment.",
            )

        try:
            log = APICallLog.objects.get(
                log_id=log_id, organization=context.organization
            )
        except APICallLog.DoesNotExist:
            return self._candidate_sources_result(
                context,
                "Eval Log Not Found",
                f"Eval log `{log_id}` was not found.",
            )

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
            config = json.loads(config)

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
