import os

from model_hub.models.choices import ModelChoices
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


class RunEvaluationInput(PydanticBaseModel):
    eval_template_id: str = Field(
        default="", description="Name or UUID of the evaluation template to run"
    )
    dataset_id: str = Field(
        default="", description="Name or UUID of the dataset to evaluate against"
    )
    model: str | None = Field(
        default=None,
        description="Model to use for evaluation. Uses FALCON_AI_MODEL or template default if not specified.",
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

    def _requirements_result(
        self, context: ToolContext, message: str = ""
    ) -> ToolResult:
        from django.db.models import Q
        from model_hub.models.develop_dataset import Dataset, Row
        from model_hub.models.evals_metric import EvalTemplate

        templates = EvalTemplate.objects.filter(
            Q(organization=context.organization) | Q(organization__isnull=True),
            deleted=False,
        ).order_by("-created_at")[:5]
        datasets = Dataset.objects.filter(
            organization=context.organization,
            deleted=False,
        ).order_by("-created_at")[:5]
        template_rows = [[f"`{t.id}`", t.name] for t in templates]
        dataset_rows = [
            [
                f"`{d.id}`",
                d.name,
                str(Row.objects.filter(dataset=d, deleted=False).count()),
            ]
            for d in datasets
        ]
        content = (
            message + "\n\n" if message else ""
        ) + "Provide `eval_template_id` and `dataset_id` to start an evaluation."
        if template_rows:
            from ai_tools.formatting import markdown_table

            content += "\n\n### Eval Templates\n\n" + markdown_table(
                ["ID", "Name"], template_rows
            )
        if dataset_rows:
            from ai_tools.formatting import markdown_table

            content += "\n\n### Datasets\n\n" + markdown_table(
                ["ID", "Name", "Rows"], dataset_rows
            )
        return ToolResult(
            content=section("Run Evaluation Requirements", content),
            data={
                "requires_eval_template_id": True,
                "requires_dataset_id": True,
                "eval_templates": [
                    {"id": str(t.id), "name": t.name} for t in templates
                ],
                "datasets": [{"id": str(d.id), "name": d.name} for d in datasets],
            },
        )

    def execute(self, params: RunEvaluationInput, context: ToolContext) -> ToolResult:

        from model_hub.models.develop_dataset import Dataset, Row
        from model_hub.models.evals_metric import EvalTemplate
        from model_hub.models.evaluation import Evaluation

        from ai_tools.resolvers import resolve_dataset, resolve_eval_template

        if not params.eval_template_id or not params.dataset_id:
            return self._requirements_result(context)

        # Resolve template by name or UUID
        template_obj, err = resolve_eval_template(
            params.eval_template_id, context.organization
        )
        if err:
            return self._requirements_result(context, err)

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
            return self._requirements_result(context, err)

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

        model = params.model or os.environ.get("FALCON_AI_MODEL") or ModelChoices.TURING_SMALL.value

        # Create evaluation record
        evaluation = Evaluation(
            user=context.user,
            organization=context.organization,
            workspace=context.workspace,
            eval_template=template,
            model_name=model,
            status="pending",
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
            evaluation.status = "processing"
            evaluation.save(update_fields=["status"])
            workflow_started = True
        except Exception:
            # Workflow failed to start, mark as pending (will be picked up by polling)
            pass

        info = key_value_block(
            [
                ("Evaluation ID", f"`{evaluation.id}`"),
                ("Template", template.name),
                ("Dataset", f"{dataset.name} ({row_count} rows)"),
                ("Model", model),
                ("Status", format_status(evaluation.status)),
                (
                    "Workflow",
                    (
                        "Started"
                        if workflow_started
                        else "Queued (will be picked up shortly)"
                    ),
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
