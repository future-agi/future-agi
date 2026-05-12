from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    format_number,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class GetExperimentComparisonInput(PydanticBaseModel):
    experiment_id: str = Field(
        default="",
        description="Name or UUID of the experiment. Omit to list candidate experiments with IDs.",
    )


@register_tool
class GetExperimentComparisonTool(BaseTool):
    name = "get_experiment_comparison"
    description = (
        "Returns detailed A/B comparison results for an experiment, "
        "including normalized scores, weights, token usage, response times, "
        "and final rankings across all variants."
    )
    category = "experiments"
    input_model = GetExperimentComparisonInput

    def execute(
        self, params: GetExperimentComparisonInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.tools.experiments._utils import (
            candidate_experiments_result,
            resolve_experiment_for_tool,
        )
        from model_hub.models.experiments import ExperimentComparison, ExperimentsTable

        if not params.experiment_id:
            return candidate_experiments_result(
                context,
                "Experiment Required For Comparison",
            )

        experiment_obj, unresolved = resolve_experiment_for_tool(
            params.experiment_id,
            context,
            title="Experiment Required For Comparison",
        )
        if unresolved:
            return unresolved

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
            return ToolResult.not_found("Experiment", str(params.experiment_id))

        comparisons = (
            ExperimentComparison.objects.filter(experiment=experiment)
            .select_related("experiment_dataset")
            .order_by("rank")
        )

        if not comparisons.exists():
            info = key_value_block(
                [
                    ("Experiment", experiment.name),
                    ("Status", experiment.status),
                    (
                        "Link",
                        dashboard_link(
                            "experiment", str(experiment.id), label="View in Dashboard"
                        ),
                    ),
                ]
            )
            content = section(f"Experiment: {experiment.name}", info)
            content += "\n\n_No comparison results available. The experiment may still be running._"
            return ToolResult(
                content=content,
                data={"experiment_id": str(experiment.id), "comparisons": []},
            )

        content = f"## Experiment Comparison: {experiment.name}\n\n"

        # Raw metrics table
        content += "### Raw Metrics\n\n"
        raw_rows = []
        for comp in comparisons:
            variant_name = (
                comp.experiment_dataset.name if comp.experiment_dataset else "—"
            )
            raw_rows.append(
                [
                    str(comp.rank),
                    variant_name,
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
        content += markdown_table(
            [
                "Rank",
                "Variant",
                "Avg Score",
                "Avg Response Time",
                "Avg Tokens",
                "Avg Completion Tokens",
            ],
            raw_rows,
        )

        # Normalized scores table
        content += "\n\n### Normalized Scores\n\n"
        norm_rows = []
        for comp in comparisons:
            variant_name = (
                comp.experiment_dataset.name if comp.experiment_dataset else "—"
            )
            norm_rows.append(
                [
                    str(comp.rank),
                    variant_name,
                    (
                        format_number(comp.normalized_score)
                        if comp.normalized_score is not None
                        else "—"
                    ),
                    (
                        format_number(comp.normalized_response_time)
                        if comp.normalized_response_time is not None
                        else "—"
                    ),
                    (
                        format_number(comp.normalized_total_tokens)
                        if comp.normalized_total_tokens is not None
                        else "—"
                    ),
                    (
                        format_number(comp.overall_rating)
                        if comp.overall_rating is not None
                        else "—"
                    ),
                ]
            )
        content += markdown_table(
            [
                "Rank",
                "Variant",
                "Norm Score",
                "Norm Response Time",
                "Norm Tokens",
                "Overall Rating",
            ],
            norm_rows,
        )

        # Weights
        if comparisons:
            first = comparisons[0]
            content += "\n\n### Weights\n\n"
            weight_pairs = []
            if first.scores_weight:
                weight_pairs.append(("Scores Weight", str(first.scores_weight)))
            if first.response_time_weight is not None:
                weight_pairs.append(
                    ("Response Time Weight", format_number(first.response_time_weight))
                )
            if first.total_tokens_weight is not None:
                weight_pairs.append(
                    ("Total Tokens Weight", format_number(first.total_tokens_weight))
                )
            if weight_pairs:
                content += key_value_block(weight_pairs)

        # Winner
        winner = comparisons[0]
        winner_name = (
            winner.experiment_dataset.name if winner.experiment_dataset else "—"
        )
        content += f"\n\n### Winner\n\n**{winner_name}** (Rank #1, Overall Rating: {format_number(winner.overall_rating)})"

        comp_data = []
        for comp in comparisons:
            comp_data.append(
                {
                    "rank": comp.rank,
                    "variant": (
                        comp.experiment_dataset.name
                        if comp.experiment_dataset
                        else None
                    ),
                    "avg_score": (
                        float(comp.avg_score) if comp.avg_score is not None else None
                    ),
                    "avg_response_time": (
                        float(comp.avg_response_time)
                        if comp.avg_response_time is not None
                        else None
                    ),
                    "overall_rating": (
                        float(comp.overall_rating)
                        if comp.overall_rating is not None
                        else None
                    ),
                }
            )

        return ToolResult(
            content=content,
            data={
                "experiment_id": str(experiment.id),
                "experiment_name": experiment.name,
                "comparisons": comp_data,
            },
        )
