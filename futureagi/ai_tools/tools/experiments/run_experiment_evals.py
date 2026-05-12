from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class RunExperimentEvalsInput(PydanticBaseModel):
    experiment_id: str = Field(
        default="",
        description="Name or UUID of the experiment to run additional evaluations on"
    )
    eval_template_ids: list[str] | str | None = Field(
        default=None,
        description=(
            "List of UserEvalMetric names or IDs to run on the experiment. "
            "These must already be configured on the experiment via add_experiment_eval."
        ),
    )


@register_tool
class RunExperimentEvalsTool(BaseTool):
    name = "run_experiment_evals"
    description = (
        "Runs additional evaluations on an existing experiment's results. "
        "The eval templates must already be added to the experiment via add_experiment_eval. "
        "This triggers async processing of the specified evals on all experiment rows."
    )
    category = "experiments"
    input_model = RunExperimentEvalsInput

    def execute(
        self, params: RunExperimentEvalsInput, context: ToolContext
    ) -> ToolResult:
        import structlog

        from ai_tools.resolvers import is_uuid
        from ai_tools.tools.experiments._utils import (
            candidate_experiment_evals_result,
            resolve_experiment_for_tool,
        )
        from model_hub.models.choices import StatusType
        from model_hub.models.evals_metric import UserEvalMetric
        from model_hub.models.experiments import ExperimentsTable
        from model_hub.views.experiment_runner import ExperimentRunner

        logger = structlog.get_logger(__name__)

        experiment_obj, unresolved = resolve_experiment_for_tool(
            params.experiment_id,
            context,
            title="Experiment Required To Run Evals",
        )
        if unresolved:
            return unresolved

        try:
            experiment = (
                ExperimentsTable.objects.select_related("dataset")
                .prefetch_related("experiments_datasets", "user_eval_template_ids")
                .get(id=experiment_obj.id, deleted=False)
            )
        except ExperimentsTable.DoesNotExist:
            return ToolResult.not_found("Experiment", str(experiment_obj.id))

        # Verify organization access
        if (
            experiment.dataset
            and experiment.dataset.organization != context.organization
        ):
            return ToolResult.not_found("Experiment", str(params.experiment_id))

        configured_evals = list(experiment.user_eval_template_ids.all())
        eval_refs = _normalize_eval_refs(params.eval_template_ids)
        if not eval_refs:
            return candidate_experiment_evals_result(
                experiment,
                "Experiment Eval Required To Run",
            )

        evals = []
        missing = []
        seen = set()
        for ref in eval_refs:
            ref_str = str(ref)
            matched = None
            if is_uuid(ref_str):
                matched = next(
                    (em for em in configured_evals if str(em.id) == ref_str), None
                )
            if not matched:
                ref_lower = ref_str.strip().lower()
                exact = [
                    em
                    for em in configured_evals
                    if (em.name or "").lower() == ref_lower
                    or (
                        em.template
                        and (em.template.name or "").lower() == ref_lower
                    )
                ]
                if len(exact) == 1:
                    matched = exact[0]
                elif len(exact) > 1:
                    missing.append(
                        f"{ref_str}: multiple evals match; use one of "
                        + ", ".join(f"`{em.name}` ({em.id})" for em in exact[:5])
                    )
                    continue
            if matched:
                key = str(matched.id)
                if key not in seen:
                    evals.append(matched)
                    seen.add(key)
            else:
                missing.append(ref_str)

        if not evals:
            return candidate_experiment_evals_result(
                experiment,
                "Experiment Eval Not Found",
                "No matching experiment evals found. "
                f"Configured evals: {', '.join(em.name for em in configured_evals) or 'None'}.",
            )

        eval_template_ids = [str(e.id) for e in evals]

        try:
            # Update source_id on the eval metrics
            UserEvalMetric.objects.filter(id__in=eval_template_ids).update(
                source_id=experiment.id
            )

            # Initialize the experiment runner and process evals
            experiment_runner = ExperimentRunner(experiment_id=experiment.id)
            experiment_runner.load_experiment()
            experiment_runner.empty_or_create_evals_column(
                eval_template_ids=eval_template_ids
            )

            # Update eval status
            experiment.user_eval_template_ids.all().filter(
                id__in=eval_template_ids
            ).update(status=StatusType.EXPERIMENT_EVALUATION.value)

            logger.info(
                "mcp_experiment_evals_triggered",
                experiment_id=str(experiment.id),
                eval_count=len(eval_template_ids),
            )

        except Exception as e:
            logger.exception(
                "mcp_experiment_evals_failed",
                experiment_id=str(experiment.id),
                error=str(e),
            )
            return ToolResult.error(
                f"Failed to run additional evaluations: {str(e)}",
                error_code="INTERNAL_ERROR",
            )

        info = key_value_block(
            [
                ("Experiment", experiment.name),
                ("Evals Triggered", str(len(eval_template_ids))),
                ("Skipped", str(len(missing)) if missing else "None"),
                ("Status", "Running"),
            ]
        )

        content = section("Experiment Evals Started", info)
        content += (
            "\n\n_Evaluations are running asynchronously. "
            "Use `get_experiment_results` to check progress._"
        )

        return ToolResult(
            content=content,
            data={
                "experiment_id": str(experiment.id),
                "eval_template_ids": eval_template_ids,
                "missing": missing,
                "status": "running",
            },
        )


def _normalize_eval_refs(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    return [str(item).strip() for item in value if str(item).strip()]
