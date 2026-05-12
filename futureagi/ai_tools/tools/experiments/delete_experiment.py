from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import section
from ai_tools.registry import register_tool


class DeleteExperimentInput(PydanticBaseModel):
    experiment_ids: list[str] = Field(
        default_factory=list,
        description="List of experiment UUIDs or exact names to delete. Omit to list candidates.",
        max_length=20,
    )


@register_tool
class DeleteExperimentTool(BaseTool):
    name = "delete_experiment"
    description = (
        "Soft-deletes one or more experiments. "
        "Deleted experiments will no longer appear in listings. "
        "Call without IDs to list candidate experiments first."
    )
    category = "experiments"
    input_model = DeleteExperimentInput

    def execute(
        self, params: DeleteExperimentInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.experiments import ExperimentsTable
        from ai_tools.tools.experiments._utils import candidate_experiments_result
        from ai_tools.tools.experiments._utils import resolve_experiment_for_tool

        if not params.experiment_ids:
            return candidate_experiments_result(
                context,
                "Experiment Required For Delete",
                "Choose one or more experiment IDs to delete.",
            )

        resolved_ids = []
        for identifier in params.experiment_ids:
            experiment, unresolved = resolve_experiment_for_tool(
                identifier,
                context,
                title="Experiment Required For Delete",
            )
            if unresolved:
                return unresolved
            resolved_ids.append(experiment.id)

        experiments = ExperimentsTable.objects.filter(
            id__in=resolved_ids,
            dataset__organization=context.organization,
            deleted=False,
        )
        names = list(experiments.values_list("name", flat=True))
        count = experiments.count()

        if count == 0:
            return ToolResult.error(
                "No matching experiments found.",
                error_code="NOT_FOUND",
            )

        for exp in experiments:
            exp.delete()  # BaseModel.delete() sets deleted + deleted_at

        lines = [f"**Deleted {count} experiment(s):**"]
        for name in names:
            lines.append(f"- {name}")

        return ToolResult(
            content=section("Experiments Deleted", "\n".join(lines)),
            data={"deleted": count, "names": names},
        )
