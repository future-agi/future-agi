from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_status,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from model_hub.models.choices import ModelChoices
from model_hub.models.evaluation import StatusChoices
from sdk.utils.async_evaluations import mark_evaluation_failed


class RunEvaluationInput(PydanticBaseModel):
    eval_template_id: str = Field(
        description="Name or UUID of the evaluation template to run"
    )
    dataset_id: str = Field(
        description="Name or UUID of the dataset to evaluate against"
    )
    model: Optional[str] = Field(
        default=None,
        description="Model to use for evaluation (e.g. 'turing_large', 'turing_small'). Uses template default if not specified.",
    )


@register_tool
class RunEvaluationTool(BaseTool):
    name = "run_evaluation"
    description = (
        "Triggers an evaluation run using a specified template and dataset. "
        "Creates evaluation records and starts async processing. "
        "Returns the evaluation ID for tracking progress."
    )
    category = "evaluations"
    input_model = RunEvaluationInput

    def execute(self, params: RunEvaluationInput, context: ToolContext) -> ToolResult:

        from ai_tools.resolvers import resolve_dataset, resolve_eval_template
        from model_hub.models.develop_dataset import Dataset, Row
        from model_hub.models.evals_metric import EvalTemplate
        from model_hub.models.evaluation import Evaluation

        # Resolve template by name or UUID
        template_obj, err = resolve_eval_template(
            params.eval_template_id, context.organization
        )
        if err:
            return ToolResult.error(err, error_code="NOT_FOUND")

        # Validate template exists (with org-or-null check)
        try:
            from django.db.models import Q

            template = EvalTemplate.no_workspace_objects.get(
                Q(organization=context.organization) | Q(organization__isnull=True),
                id=template_obj.id,
            )
        except EvalTemplate.DoesNotExist:
            return ToolResult.error(
                f"Evaluation template `{template_obj.id}` not found.",
                error_code="NOT_FOUND",
            )

        # Resolve dataset by name or UUID
        dataset_obj, err = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if err:
            return ToolResult.error(err, error_code="NOT_FOUND")

        # Validate dataset exists
        try:
            dataset = Dataset.objects.get(id=dataset_obj.id)
        except Dataset.DoesNotExist:
            return ToolResult.error(
                f"Dataset `{dataset_obj.id}` not found.",
                error_code="NOT_FOUND",
            )

        # Get row count for the dataset
        row_count = Row.objects.filter(dataset=dataset, deleted=False).count()
        if row_count == 0:
            return ToolResult.error(
                "Dataset has no rows. Cannot run evaluation on empty dataset.",
                error_code="VALIDATION_ERROR",
            )

        model = params.model or ModelChoices.TURING_SMALL.value

        # Create evaluation record
        evaluation = Evaluation(
            user=context.user,
            organization=context.organization,
            workspace=context.workspace,
            eval_template=template,
            model_name=model,
            status=StatusChoices.PENDING,
            input_data={"dataset_id": str(dataset.id)},
            eval_config=template.config or {},
        )
        evaluation.save()

        # Try to start async via Temporal
        workflow_started = False
        try:
            from asgiref.sync import async_to_sync

            from tfc.temporal.evaluations.client import start_evaluation_workflow_async

            async_to_sync(start_evaluation_workflow_async)(str(evaluation.id))
            workflow_started = True
        except Exception as e:
            # Nothing sweeps unstarted evaluations, so leaving this pending strands it.
            mark_evaluation_failed(
                str(evaluation.id), f"Evaluation workflow could not be started: {e}"
            )
            evaluation.refresh_from_db()

        if workflow_started:
            evaluation.status = StatusChoices.PROCESSING
            evaluation.save(update_fields=["status"])

        info = key_value_block(
            [
                ("Evaluation ID", f"`{evaluation.id}`"),
                ("Template", template.name),
                ("Dataset", f"{dataset.name} ({row_count} rows)"),
                ("Model", model),
                ("Status", format_status(evaluation.status)),
                (
                    "Workflow",
                    ("Started" if workflow_started else "Failed to start"),
                ),
                (
                    "Link",
                    dashboard_link(
                        "evaluation", str(evaluation.id), label="Track Progress"
                    ),
                ),
            ]
        )

        content = section("Evaluation Started", info)
        content += "\n\n_The evaluation is running asynchronously. Use `get_evaluation` to check progress._"

        return ToolResult(
            content=content,
            data={
                "evaluation_id": str(evaluation.id),
                "template": template.name,
                "dataset": dataset.name,
                "model": model,
                "status": evaluation.status,
            },
        )
