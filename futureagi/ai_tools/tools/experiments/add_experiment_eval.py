import os

from model_hub.models.choices import ModelChoices
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class AddExperimentEvalInput(PydanticBaseModel):
    experiment_id: str = Field(default="", description="Name or UUID of the experiment")
    name: str = Field(
        default="falcon_experiment_eval",
        description="Name for the evaluation",
        min_length=1,
        max_length=50,
    )
    template_id: str = Field(
        default="", description="Name or UUID of the eval template to use"
    )
    config: dict | None = Field(
        default=None,
        description=(
            "Config overrides: mapping (template key → column ID) "
            "and config (runtime parameters)"
        ),
    )
    model: str | None = Field(
        default=None,
        description="LLM model for evaluation",
    )
    run: bool = Field(
        default=True,
        description="If true, immediately run the eval on experiment variants",
    )


@register_tool
class AddExperimentEvalTool(BaseTool):
    name = "add_experiment_eval"
    description = (
        "Adds an evaluation metric to an experiment. "
        "The eval runs across all experiment variants, "
        "allowing comparison of eval scores between variants."
    )
    category = "experiments"
    input_model = AddExperimentEvalInput

    def _requirements_result(
        self, context: ToolContext, message: str = ""
    ) -> ToolResult:
        from django.db.models import Q
        from model_hub.models.evals_metric import EvalTemplate
        from model_hub.models.experiments import ExperimentsTable

        from ai_tools.formatting import markdown_table

        experiments = (
            ExperimentsTable.objects.filter(
                dataset__organization=context.organization,
                deleted=False,
            )
            .select_related("dataset")
            .order_by("-created_at")[:10]
        )
        templates = EvalTemplate.objects.filter(
            Q(organization=context.organization) | Q(organization__isnull=True),
            deleted=False,
        ).order_by("-created_at")[:10]
        experiment_rows = [
            [
                f"`{experiment.id}`",
                experiment.name,
                experiment.dataset.name if experiment.dataset else "—",
            ]
            for experiment in experiments
        ]
        rows = [[f"`{template.id}`", template.name] for template in templates]
        body = (
            (message + "\n\n" if message else "")
            + "Provide `experiment_id`, `name`, and `template_id` to add an experiment eval."
        )
        if experiment_rows:
            body += "\n\n### Experiments\n\n" + markdown_table(
                ["Experiment ID", "Name", "Dataset"], experiment_rows
            )
        if rows:
            body += "\n\n### Eval Templates\n\n" + markdown_table(
                ["Template ID", "Name"], rows
            )
        return ToolResult(
            content=section(
                "Experiment Eval Requirements",
                body,
            ),
            data={
                "requires_experiment_id": True,
                "requires_template_id": True,
                "experiments": [
                    {"id": str(experiment.id), "name": experiment.name}
                    for experiment in experiments
                ],
                "eval_templates": [
                    {"id": str(template.id), "name": template.name}
                    for template in templates
                ],
            },
        )

    def execute(
        self, params: AddExperimentEvalInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
        from model_hub.models.experiments import ExperimentsTable

        from ai_tools.resolvers import resolve_eval_template, resolve_experiment

        if not params.experiment_id or not params.template_id:
            return self._requirements_result(context)

        # Resolve experiment by name or UUID
        experiment_obj, err = resolve_experiment(
            params.experiment_id, context.organization
        )
        if err:
            return self._requirements_result(context, err)

        try:
            experiment = ExperimentsTable.objects.select_related("dataset").get(
                id=experiment_obj.id
            )
        except ExperimentsTable.DoesNotExist:
            return ToolResult.not_found("Experiment", str(experiment_obj.id))

        if (
            experiment.dataset
            and experiment.dataset.organization_id != context.organization.id
        ):
            return ToolResult.not_found("Experiment", str(experiment_obj.id))

        # Resolve eval template by name or UUID
        template_obj, err = resolve_eval_template(
            params.template_id, context.organization
        )
        if err:
            return self._requirements_result(context, err)

        try:
            template = EvalTemplate.objects.get(id=template_obj.id)
        except EvalTemplate.DoesNotExist:
            return ToolResult.not_found("EvalTemplate", str(template_obj.id))

        # Check for duplicate
        if UserEvalMetric.objects.filter(
            source_id=str(experiment.id),
            name=params.name,
            deleted=False,
        ).exists():
            return ToolResult.error(
                f"An eval named '{params.name}' already exists on this experiment.",
                error_code="VALIDATION_ERROR",
            )

        # Normalize config using template config
        from model_hub.models.choices import StatusType

        from model_hub.utils.function_eval_params import normalize_eval_runtime_config

        selected_template = EvalTemplate.no_workspace_objects.get(id=template_obj.id)
        normalized_config = normalize_eval_runtime_config(
            selected_template.config, params.config or {}
        )

        status = StatusType.EXPERIMENT_EVALUATION.value

        user_eval = UserEvalMetric(
            name=params.name,
            template=template,
            dataset=experiment.dataset,
            config=normalized_config,
            status=status,
            model=(
                params.model
                or os.environ.get("FALCON_AI_MODEL")
                or ModelChoices.TURING_LARGE.value
            ),
            source_id=str(experiment.id),
            organization=context.organization,
            workspace=context.workspace,
            user=context.user,
        )
        user_eval.save()

        # Add to experiment's eval templates
        experiment.user_eval_template_ids.add(user_eval)

        # Trigger execution when run=True
        if params.run:
            from model_hub.views.experiment_runner import ExperimentRunner

            experiment.status = StatusType.RUNNING.value
            experiment.save(update_fields=["status"])
            experiment_runner = ExperimentRunner(experiment_id=experiment.id)
            experiment_runner.load_experiment()
            experiment_runner.empty_or_create_evals_column(
                eval_template_ids=[str(user_eval.id)]
            )
            experiment.user_eval_template_ids.all().filter(
                id__in=[str(user_eval.id)]
            ).update(status=StatusType.EXPERIMENT_EVALUATION.value)

        info = key_value_block(
            [
                ("Eval ID", f"`{user_eval.id}`"),
                ("Name", params.name),
                ("Template", template.name),
                ("Model", user_eval.model),
                ("Status", status),
                ("Experiment", experiment.name),
            ]
        )

        content = section("Experiment Eval Added", info)
        if params.run:
            content += "\n\n_Evaluation is running on all experiment variants._"

        return ToolResult(
            content=content,
            data={
                "eval_id": str(user_eval.id),
                "name": params.name,
                "status": status,
            },
        )
