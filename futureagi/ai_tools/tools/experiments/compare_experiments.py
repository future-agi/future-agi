from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_number,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class CompareExperimentsInput(PydanticBaseModel):
    experiment_id: str = Field(
        default="",
        description="Experiment UUID or exact experiment name. Omit to list candidates."
    )
    weights: dict[str, float] | None = Field(
        default=None,
        description=(
            "Custom weights for comparison metrics. "
            "Example: {'scores': 0.4, 'response_time': 0.3, 'total_tokens': 0.3}"
        ),
    )


@register_tool
class CompareExperimentsTool(BaseTool):
    name = "compare_experiments"
    description = (
        "Compares experiment variants by computing weighted rankings. "
        "Normalizes metrics (scores, response time, tokens) to a 0-10 scale "
        "and calculates overall ratings and rankings. Call without an experiment "
        "ID to list candidate experiments."
    )
    category = "experiments"
    input_model = CompareExperimentsInput

    def execute(
        self, params: CompareExperimentsInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_dataset import Cell, Column
        from model_hub.models.experiments import ExperimentComparison

        from ai_tools.tools.experiments._utils import resolve_experiment_for_tool

        experiment, unresolved = resolve_experiment_for_tool(
            params.experiment_id,
            context,
            title="Experiment Required For Comparison",
        )
        if unresolved:
            return unresolved

        variant_datasets = list(experiment.experiments_datasets.all())
        if not variant_datasets:
            return ToolResult(
                content=section(
                    "Experiment Has No Variants",
                    (
                        f"Experiment `{experiment.name}` exists, but it has no "
                        "variant datasets to compare yet. Add variants or run the "
                        "experiment before requesting a ranked comparison."
                    ),
                ),
                data={
                    "experiment_id": str(experiment.id),
                    "experiment_name": experiment.name,
                    "variant_count": 0,
                    "requires_variants": True,
                },
            )

        # Compute raw metrics per variant
        variant_metrics = []
        for vds in variant_datasets:
            v_cols = list(
                vds.columns.filter(deleted=False).values_list("id", flat=True)
            )
            cells = (
                Cell.objects.filter(column_id__in=v_cols, deleted=False)
                .exclude(value="")
                .exclude(value__isnull=True)
                if v_cols
                else Cell.objects.none()
            )

            response_times = []
            token_counts = []
            scores = []

            for cell in cells[:300]:
                if cell.value_infos and isinstance(cell.value_infos, dict):
                    rt = cell.value_infos.get("response_time")
                    if rt:
                        try:
                            response_times.append(float(rt))
                        except (ValueError, TypeError):
                            pass
                    tok = cell.value_infos.get("total_tokens")
                    if tok:
                        try:
                            token_counts.append(int(tok))
                        except (ValueError, TypeError):
                            pass

            # Get eval scores
            eval_cols = Column.objects.filter(
                dataset=experiment.dataset,
                source="evaluation",
                deleted=False,
            )
            for ec in eval_cols:
                eval_cells = (
                    Cell.objects.filter(column=ec, deleted=False)
                    .exclude(value="")
                    .exclude(value__isnull=True)[:100]
                )
                for c in eval_cells:
                    try:
                        scores.append(float(c.value))
                    except (ValueError, TypeError):
                        pass

            variant_metrics.append(
                {
                    "vds": vds,
                    "avg_time": (
                        sum(response_times) / len(response_times)
                        if response_times
                        else None
                    ),
                    "avg_tokens": (
                        sum(token_counts) / len(token_counts) if token_counts else None
                    ),
                    "avg_score": sum(scores) / len(scores) if scores else None,
                }
            )

        # Normalize to 0-10 scale
        def normalize(values, higher_is_better=True):
            valid = [v for v in values if v is not None]
            if not valid:
                return [5.0 if v is None else 5.0 for v in values]
            min_v, max_v = min(valid), max(valid)
            if min_v == max_v:
                return [5.0 for _ in values]
            result = []
            for v in values:
                if v is None:
                    result.append(5.0)
                else:
                    norm = (v - min_v) / (max_v - min_v) * 10
                    if not higher_is_better:
                        norm = 10 - norm
                    result.append(norm)
            return result

        norm_scores = normalize(
            [m["avg_score"] for m in variant_metrics], higher_is_better=True
        )
        norm_times = normalize(
            [m["avg_time"] for m in variant_metrics], higher_is_better=False
        )
        norm_tokens = normalize(
            [m["avg_tokens"] for m in variant_metrics], higher_is_better=False
        )

        # Calculate overall rating
        w = params.weights or {
            "scores": 0.5,
            "response_time": 0.25,
            "total_tokens": 0.25,
        }
        sw = w.get("scores", 0.5)
        tw = w.get("response_time", 0.25)
        tkw = w.get("total_tokens", 0.25)

        ratings = []
        for i, _vm in enumerate(variant_metrics):
            rating = norm_scores[i] * sw + norm_times[i] * tw + norm_tokens[i] * tkw
            ratings.append(rating)

        # Rank
        ranked = sorted(enumerate(ratings), key=lambda x: -x[1])
        ranking = {idx: rank + 1 for rank, (idx, _) in enumerate(ranked)}

        # Save comparisons and build output
        rows = []
        data_list = []
        for i, vm in enumerate(variant_metrics):
            vds = vm["vds"]
            rank = ranking[i]
            rating = ratings[i]

            # Save/update comparison
            comp, _ = ExperimentComparison.objects.update_or_create(
                experiment=experiment,
                experiment_dataset=vds,
                defaults={
                    "avg_response_time": vm["avg_time"] or 0,
                    "avg_total_tokens": vm["avg_tokens"] or 0,
                    "avg_completion_tokens": vm["avg_tokens"] or 0,
                    "avg_score": vm["avg_score"],
                    "normalized_response_time": norm_times[i],
                    "normalized_total_tokens": norm_tokens[i],
                    "normalized_completion_tokens": norm_tokens[i],
                    "normalized_score": norm_scores[i],
                    "overall_rating": rating,
                    "rank": rank,
                    "response_time_weight": tw,
                    "total_tokens_weight": tkw,
                    "completion_tokens_weight": tkw,
                    "scores_weight": {"score": sw},
                },
            )

            rows.append(
                [
                    str(rank),
                    vds.name[:30],
                    format_number(rating),
                    (
                        format_number(vm["avg_score"])
                        if vm["avg_score"] is not None
                        else "—"
                    ),
                    (
                        f"{format_number(vm['avg_time'])}s"
                        if vm["avg_time"] is not None
                        else "—"
                    ),
                    str(int(vm["avg_tokens"])) if vm["avg_tokens"] is not None else "—",
                ]
            )

            data_list.append(
                {
                    "rank": rank,
                    "variant": vds.name,
                    "overall_rating": round(rating, 2),
                    "avg_score": vm["avg_score"],
                    "avg_time": vm["avg_time"],
                    "avg_tokens": vm["avg_tokens"],
                }
            )

        table = markdown_table(
            [
                "Rank",
                "Variant",
                "Overall Rating",
                "Avg Score",
                "Avg Time",
                "Avg Tokens",
            ],
            rows,
        )

        info = key_value_block(
            [
                ("Experiment", experiment.name),
                ("Variants Compared", str(len(variant_metrics))),
                ("Weights", f"score={sw}, time={tw}, tokens={tkw}"),
                (
                    "Link",
                    dashboard_link(
                        "experiment", str(experiment.id), label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section(f"Experiment Comparison: {experiment.name}", info)
        content += f"\n\n{table}"

        return ToolResult(
            content=content,
            data={"rankings": data_list},
        )
