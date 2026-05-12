from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    format_number,
    format_status,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetExperimentResultsInput(PydanticBaseModel):
    experiment_id: str = Field(
        default="",
        description="Name or UUID of the experiment to retrieve results for. Omit it to list candidate experiments.",
    )


@register_tool
class GetExperimentResultsTool(BaseTool):
    name = "get_experiment_results"
    description = (
        "Returns experiment results including variant rankings, scores, "
        "response times, and token usage. Shows which variant performed best."
    )
    category = "experiments"
    input_model = GetExperimentResultsInput

    def execute(
        self, params: GetExperimentResultsInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.tools.experiments._utils import (
            candidate_experiments_result,
            resolve_experiment_for_tool,
        )
        from model_hub.models.experiments import (
            ExperimentComparison,
            ExperimentDatasetTable,
            ExperimentsTable,
        )

        if not params.experiment_id:
            return candidate_experiments_result(
                context,
                "Experiment Required For Results",
            )

        experiment_obj, unresolved = resolve_experiment_for_tool(
            params.experiment_id,
            context,
            title="Experiment Required For Results",
        )
        if unresolved:
            return unresolved

        try:
            experiment = ExperimentsTable.objects.select_related("dataset").get(
                id=experiment_obj.id
            )
        except ExperimentsTable.DoesNotExist:
            return candidate_experiments_result(
                context,
                "Experiment Not Found",
                f"Experiment `{experiment_obj.id}` was not found. Use one of these experiments.",
            )

        if (
            experiment.dataset
            and experiment.dataset.organization_id != context.organization.id
        ):
            return candidate_experiments_result(
                context,
                "Experiment Not Found",
                f"Experiment `{params.experiment_id}` is not available in this workspace.",
            )

        dataset_name = experiment.dataset.name if experiment.dataset else "—"

        info = key_value_block(
            [
                ("ID", f"`{experiment.id}`"),
                ("Name", experiment.name),
                ("Status", format_status(experiment.status)),
                ("Dataset", dataset_name),
                ("Created", format_datetime(experiment.created_at)),
                (
                    "Link",
                    dashboard_link(
                        "experiment", str(experiment.id), label="View in Dashboard"
                    ),
                ),
            ]
        )
        content = section(f"Experiment: {experiment.name}", info)

        # Get variant datasets
        variant_datasets = experiment.experiments_datasets.all()

        # Get comparison results (rankings)
        comparisons = (
            ExperimentComparison.objects.filter(experiment=experiment)
            .select_related("experiment_dataset")
            .order_by("rank")
        )

        comparison_data = []
        if comparisons.exists():
            content += "\n\n### Variant Rankings\n\n"

            rows = []
            for comp in comparisons:
                variant_name = (
                    comp.experiment_dataset.name if comp.experiment_dataset else "—"
                )
                rows.append(
                    [
                        str(comp.rank) if comp.rank is not None else "—",
                        truncate(variant_name, 30),
                        (
                            format_number(comp.overall_rating)
                            if comp.overall_rating is not None
                            else "—"
                        ),
                        (
                            format_number(comp.avg_score)
                            if comp.avg_score is not None
                            else "—"
                        ),
                        (
                            f"{format_number(comp.avg_response_time)}s"
                            if comp.avg_response_time is not None
                            else "—"
                        ),
                        (
                            str(int(comp.avg_total_tokens))
                            if comp.avg_total_tokens is not None
                            else "—"
                        ),
                        (
                            str(int(comp.avg_completion_tokens))
                            if comp.avg_completion_tokens is not None
                            else "—"
                        ),
                    ]
                )
                comparison_data.append(
                    {
                        "rank": comp.rank,
                        "variant": variant_name,
                        "overall_rating": (
                            float(comp.overall_rating)
                            if comp.overall_rating is not None
                            else None
                        ),
                        "avg_score": (
                            float(comp.avg_score)
                            if comp.avg_score is not None
                            else None
                        ),
                        "avg_response_time": (
                            float(comp.avg_response_time)
                            if comp.avg_response_time is not None
                            else None
                        ),
                        "avg_total_tokens": (
                            int(comp.avg_total_tokens)
                            if comp.avg_total_tokens is not None
                            else None
                        ),
                    }
                )

            table = markdown_table(
                [
                    "Rank",
                    "Variant",
                    "Overall Rating",
                    "Avg Score",
                    "Avg Response Time",
                    "Avg Tokens",
                    "Avg Completion Tokens",
                ],
                rows,
            )
            content += table
        else:
            content += (
                "\n\n### Variant Rankings\n\n_No comparison results available yet._"
            )

        # Prompt configs
        if experiment.prompt_config:
            content += "\n\n### Prompt Configurations\n\n"
            for i, config in enumerate(experiment.prompt_config):
                model = config.get("model", "—") if isinstance(config, dict) else "—"
                content += f"- **Variant {i + 1}**: Model: `{model}`\n"

        data = {
            "id": str(experiment.id),
            "name": experiment.name,
            "status": experiment.status,
            "dataset": dataset_name,
            "rankings": comparison_data,
            "variant_count": variant_datasets.count(),
        }

        return ToolResult(content=content, data=data)
