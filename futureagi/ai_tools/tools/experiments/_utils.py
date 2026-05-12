from typing import Any

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import format_datetime, markdown_table, section, truncate
from ai_tools.resolvers import is_uuid, resolve_experiment


def experiment_queryset(context: ToolContext):
    from model_hub.models.experiments import ExperimentsTable

    qs = ExperimentsTable.objects.select_related("dataset").filter(
        dataset__organization=context.organization,
        deleted=False,
    )
    if context.workspace:
        qs = qs.filter(dataset__workspace=context.workspace)
    return qs.order_by("-created_at")


def candidate_experiments_result(
    context: ToolContext,
    title: str = "Experiment Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = experiment_queryset(context)
    search = str(search or "").strip()
    if search and not is_uuid(search):
        qs = qs.filter(name__icontains=search)
    experiments = list(qs[:10])
    rows = [
        [
            experiment.name,
            f"`{experiment.id}`",
            experiment.status or "-",
            experiment.dataset.name if experiment.dataset else "-",
            format_datetime(experiment.created_at),
        ]
        for experiment in experiments
    ]
    body = detail or "Provide `experiment_id` to continue."
    if rows:
        body += "\n\n" + markdown_table(
            ["Name", "Experiment ID", "Status", "Dataset", "Created"],
            rows,
        )
    else:
        body += "\n\nNo experiments found in this workspace."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_experiment_id": True,
            "experiments": [
                {
                    "id": str(experiment.id),
                    "name": experiment.name,
                    "status": experiment.status,
                    "dataset": experiment.dataset.name if experiment.dataset else None,
                }
                for experiment in experiments
            ],
        },
    )


def resolve_experiment_for_tool(
    experiment_ref: Any,
    context: ToolContext,
    title: str = "Experiment Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = str(experiment_ref or "").strip()
    if not ref:
        return None, candidate_experiments_result(context, title)

    experiment, error = resolve_experiment(
        ref,
        context.organization,
        context.workspace,
    )
    if not error:
        return experiment, None

    return None, candidate_experiments_result(
        context,
        "Experiment Not Found",
        f"{error} Use one of these experiment IDs or exact names.",
        search="" if is_uuid(ref) else ref,
    )


def candidate_experiment_evals_result(
    experiment,
    title: str = "Experiment Eval Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    configured_evals = list(
        experiment.user_eval_template_ids.select_related("template").order_by(
            "-created_at"
        )
    )
    search = str(search or "").strip().lower()
    if search and not is_uuid(search):
        configured_evals = [
            eval_metric
            for eval_metric in configured_evals
            if search in (eval_metric.name or "").lower()
            or (
                eval_metric.template
                and search in (eval_metric.template.name or "").lower()
            )
        ]
    rows = [
        [
            f"`{eval_metric.id}`",
            truncate(eval_metric.name or "-", 40),
            eval_metric.template.name if eval_metric.template else "-",
            eval_metric.status or "-",
        ]
        for eval_metric in configured_evals[:20]
    ]
    body = detail or "Provide `eval_template_ids` from this experiment."
    if rows:
        body += "\n\n" + markdown_table(
            ["Eval ID", "Name", "Template", "Status"],
            rows,
        )
    else:
        body += "\n\nNo configured evals found on this experiment."
    return ToolResult(
        content=section(title, body),
        data={
            "experiment_id": str(experiment.id),
            "requires_eval_template_ids": True,
            "evals": [
                {
                    "id": str(eval_metric.id),
                    "name": eval_metric.name,
                    "template": (
                        eval_metric.template.name if eval_metric.template else None
                    ),
                    "status": eval_metric.status,
                }
                for eval_metric in configured_evals[:20]
            ],
        },
    )
