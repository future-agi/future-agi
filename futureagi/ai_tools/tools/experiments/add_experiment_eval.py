from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from model_hub.models.choices import ModelChoices


class AddExperimentEvalInput(PydanticBaseModel):
    experiment_id: str = Field(description="Name or UUID of the experiment")
    name: str = Field(
        description="Name for the evaluation",
        min_length=1,
        max_length=50,
    )
    template_id: str = Field(description="Name or UUID of the eval template to use")
    config: Optional[dict] = Field(
        default=None,
        description=(
            "Config overrides: mapping (template key → column ID) "
            "and config (runtime parameters)"
        ),
    )
    model: Optional[str] = Field(
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

    def execute(
        self, params: AddExperimentEvalInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.resolvers import resolve_eval_template, resolve_experiment
        from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
        from model_hub.models.experiments import ExperimentsTable

        # Resolve experiment by name or UUID
        experiment_obj, err = resolve_experiment(
            params.experiment_id, context.organization
        )
        if err:
            return ToolResult.error(err, error_code="NOT_FOUND")

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
            return ToolResult.error(err, error_code="NOT_FOUND")

        template = template_obj

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
            model=params.model or ModelChoices.TURING_LARGE.value,
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
