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


class GetExperimentStatsInput(PydanticBaseModel):
    experiment_id: str = Field(
        default="",
        description="Name or UUID of the experiment. Omit to list candidate experiments with IDs.",
    )


@register_tool
class GetExperimentStatsTool(BaseTool):
    name = "get_experiment_stats"
    description = (
        "Returns aggregate statistics for an experiment including "
        "per-variant average response time, token counts, eval scores, "
        "and rankings if comparisons exist."
    )
    category = "experiments"
    input_model = GetExperimentStatsInput

    def execute(
        self, params: GetExperimentStatsInput, context: ToolContext
    ) -> ToolResult:
        from django.db.models import Avg

        from ai_tools.tools.experiments._utils import (
            candidate_experiments_result,
            resolve_experiment_for_tool,
        )
        from model_hub.models.develop_dataset import Cell, Column
        from model_hub.models.experiments import (
            ExperimentComparison,
            ExperimentsTable,
        )

        if not params.experiment_id:
            return candidate_experiments_result(context, "Experiment Required For Stats")

        experiment_obj, unresolved = resolve_experiment_for_tool(
            params.experiment_id,
            context,
            title="Experiment Required For Stats",
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

        variant_datasets = list(experiment.experiments_datasets.all())

        # Get comparison data
        comparisons = (
            ExperimentComparison.objects.filter(experiment=experiment)
            .select_related("experiment_dataset")
            .order_by("rank")
        )

        # Build stats per variant
        variant_rows = []
        variant_data = []

        for vds in variant_datasets:
            # Get cells to compute averages
            v_cols = vds.columns.filter(deleted=False)
            v_col_ids = list(v_cols.values_list("id", flat=True))

            avg_response_time = None
            avg_tokens = None
            avg_score = None

            if v_col_ids:
                cells = (
                    Cell.objects.filter(column_id__in=v_col_ids, deleted=False)
                    .exclude(value="")
                    .exclude(value__isnull=True)
                )

                # Compute avg response_time from value_infos
                response_times = []
                token_counts = []
                for cell in cells[:200]:  # Limit for performance
                    if cell.value_infos and isinstance(cell.value_infos, dict):
                        rt = cell.value_infos.get("response_time")
                        if rt is not None:
                            try:
                                response_times.append(float(rt))
                            except (ValueError, TypeError):
                                pass
                        tokens = cell.value_infos.get("total_tokens")
                        if tokens is not None:
                            try:
                                token_counts.append(int(tokens))
                            except (ValueError, TypeError):
                                pass

                if response_times:
                    avg_response_time = sum(response_times) / len(response_times)
                if token_counts:
                    avg_tokens = sum(token_counts) / len(token_counts)

            # Get comparison rank if available
            comp = comparisons.filter(experiment_dataset=vds).first()
            rank = comp.rank if comp else None
            overall_rating = comp.overall_rating if comp else None

            model_name = "—"
            if vds.prompt_config and isinstance(vds.prompt_config, dict):
                model_name = vds.prompt_config.get("model", "—")

            variant_rows.append(
                [
                    str(rank) if rank is not None else "—",
                    vds.name[:30],
                    model_name,
                    (
                        format_number(overall_rating)
                        if overall_rating is not None
                        else "—"
                    ),
                    (
                        f"{format_number(avg_response_time)}s"
                        if avg_response_time is not None
                        else "—"
                    ),
                    str(int(avg_tokens)) if avg_tokens is not None else "—",
                    vds.status or "—",
                ]
            )

            variant_data.append(
                {
                    "id": str(vds.id),
                    "name": vds.name,
                    "model": model_name,
                    "rank": rank,
                    "overall_rating": (
                        float(overall_rating) if overall_rating is not None else None
                    ),
                    "avg_response_time": avg_response_time,
                    "avg_tokens": int(avg_tokens) if avg_tokens is not None else None,
                    "status": vds.status,
                }
            )

        table = markdown_table(
            [
                "Rank",
                "Variant",
                "Model",
                "Overall Rating",
                "Avg Time",
                "Avg Tokens",
                "Status",
            ],
            variant_rows,
        )

        info = key_value_block(
            [
                ("Experiment", experiment.name),
                ("Status", experiment.status or "—"),
                ("Dataset", experiment.dataset.name if experiment.dataset else "—"),
                ("Variants", str(len(variant_datasets))),
                (
                    "Link",
                    dashboard_link(
                        "experiment", str(experiment.id), label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section(f"Experiment Stats: {experiment.name}", info)
        content += f"\n\n### Variant Statistics\n\n{table}"

        return ToolResult(
            content=content,
            data={"variants": variant_data},
        )
