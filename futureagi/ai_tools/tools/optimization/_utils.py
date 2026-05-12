from typing import Any

from django.db.models import Q

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    markdown_table,
    section,
    truncate,
)
from ai_tools.resolvers import is_uuid


def _optimization_run_qs(context: ToolContext):
    from model_hub.models.optimize_dataset import OptimizeDataset

    qs = OptimizeDataset.objects.select_related("column", "column__dataset")
    if context.organization:
        qs = qs.filter(
            Q(column__dataset__organization=context.organization)
            | Q(column__isnull=True)
        )
    return qs.order_by("-created_at")


def candidate_optimization_runs_result(
    context: ToolContext,
    title: str = "Optimization Run Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = _optimization_run_qs(context)
    search = str(search or "").strip()
    if search and not is_uuid(search):
        qs = qs.filter(name__icontains=search)
    runs = list(qs[:10])
    rows = []
    data = []
    for run in runs:
        dataset = run.column.dataset if run.column else None
        rows.append(
            [
                f"`{run.id}`",
                truncate(run.name, 36),
                format_status(run.status),
                dataset.name if dataset else "—",
                format_datetime(run.created_at),
            ]
        )
        data.append({"id": str(run.id), "name": run.name, "status": run.status})
    body = detail or "Provide `optimization_id` to inspect an optimization run."
    if rows:
        body += "\n\n" + markdown_table(
            ["ID", "Name", "Status", "Dataset", "Created"],
            rows,
        )
    else:
        body += "\n\nNo optimization runs found in this workspace."
    return ToolResult(
        content=section(title, body),
        data={"requires_optimization_id": True, "runs": data},
    )


def resolve_optimization_run(
    optimization_ref: Any,
    context: ToolContext,
    title: str = "Optimization Run Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = str(optimization_ref or "").strip()
    if not ref:
        return None, candidate_optimization_runs_result(context, title)

    qs = _optimization_run_qs(context)
    if is_uuid(ref):
        run = qs.filter(id=ref).first()
        if run:
            return run, None
        return None, candidate_optimization_runs_result(
            context,
            "Optimization Run Not Found",
            f"Optimization run `{ref}` was not found.",
        )

    exact = qs.filter(name__iexact=ref)
    if exact.count() == 1:
        return exact.first(), None
    if exact.count() > 1:
        return None, candidate_optimization_runs_result(
            context,
            "Multiple Optimization Runs Matched",
            f"More than one optimization run matched `{ref}`. Use one of these IDs.",
            search=ref,
        )

    fuzzy = qs.filter(name__icontains=ref)
    if fuzzy.count() == 1:
        return fuzzy.first(), None
    return None, candidate_optimization_runs_result(
        context,
        "Optimization Run Not Found",
        f"No optimization run matched `{ref}`.",
        search=ref,
    )


def candidate_trials_result(
    run,
    title: str = "Optimization Trial Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial

    qs = DatasetOptimizationTrial.objects.filter(optimization_run=run).order_by(
        "trial_number", "created_at"
    )
    search = str(search or "").strip()
    if search and not is_uuid(search):
        try:
            qs = qs.filter(trial_number=int(search))
        except ValueError:
            pass
    trials = list(qs[:20])
    rows = []
    data = []
    for trial in trials:
        rows.append(
            [
                f"`{trial.id}`",
                "baseline" if trial.is_baseline else str(trial.trial_number),
                str(trial.average_score) if trial.average_score is not None else "—",
                truncate(trial.prompt or "—", 52),
            ]
        )
        data.append(
            {
                "id": str(trial.id),
                "trial_number": trial.trial_number,
                "is_baseline": trial.is_baseline,
                "average_score": (
                    float(trial.average_score)
                    if trial.average_score is not None
                    else None
                ),
            }
        )
    body = detail or "Provide `trial_id` from one of these trials."
    if rows:
        body += "\n\n" + markdown_table(
            ["Trial ID", "Trial", "Score", "Prompt Preview"],
            rows,
        )
    else:
        body += "\n\nNo trials found for this optimization run."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_trial_id": True,
            "optimization_id": str(run.id),
            "trials": data,
        },
    )


def resolve_trial(trial_ref: Any, run) -> tuple[Any | None, str | None]:
    from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial

    ref = str(trial_ref or "").strip()
    if not ref:
        return None, "trial id is required"
    qs = DatasetOptimizationTrial.objects.filter(optimization_run=run)
    if is_uuid(ref):
        trial = qs.filter(id=ref).first()
        return (trial, None) if trial else (None, f"Trial `{ref}` was not found.")
    lowered = ref.lower()
    if lowered == "baseline":
        trial = qs.filter(is_baseline=True).first()
        return (trial, None) if trial else (None, "Baseline trial was not found.")
    if lowered in {"best", "top"}:
        trial = (
            qs.exclude(average_score__isnull=True).order_by("-average_score").first()
        )
        return (trial, None) if trial else (None, "No scored trial was found.")
    try:
        trial_number = int(ref.replace("trial", "").strip())
    except ValueError:
        return None, f"Trial `{ref}` was not found."
    trial = qs.filter(trial_number=trial_number).first()
    return (trial, None) if trial else (None, f"Trial `{ref}` was not found.")


def candidate_columns_result(
    context: ToolContext,
    title: str = "Dataset Column Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    from model_hub.models.develop_dataset import Column

    qs = Column.objects.select_related("dataset").filter(deleted=False)
    if context.organization:
        qs = qs.filter(dataset__organization=context.organization)
    if context.workspace:
        qs = qs.filter(dataset__workspace=context.workspace)
    search = str(search or "").strip()
    if search and not is_uuid(search):
        qs = qs.filter(name__icontains=search)
    columns = list(qs.order_by("-created_at")[:15])
    rows = []
    data = []
    for column in columns:
        rows.append(
            [
                f"`{column.id}`",
                truncate(column.name, 36),
                column.dataset.name if column.dataset else "—",
                getattr(column, "data_type", None) or "—",
            ]
        )
        data.append(
            {
                "id": str(column.id),
                "name": column.name,
                "dataset_id": str(column.dataset_id) if column.dataset_id else None,
                "dataset_name": column.dataset.name if column.dataset else None,
            }
        )
    body = detail or "Provide `column_id` for the dataset output column to optimize."
    if rows:
        body += "\n\n" + markdown_table(
            ["Column ID", "Column", "Dataset", "Type"],
            rows,
        )
    else:
        body += "\n\nNo dataset columns found in this workspace."
    return ToolResult(
        content=section(title, body),
        data={"requires_column_id": True, "columns": data},
    )


def resolve_column(column_ref: Any, context: ToolContext):
    from model_hub.models.develop_dataset import Column

    ref = str(column_ref or "").strip()
    if not ref:
        return None, candidate_columns_result(context)
    qs = Column.objects.select_related("dataset").filter(deleted=False)
    if context.organization:
        qs = qs.filter(dataset__organization=context.organization)
    if context.workspace:
        qs = qs.filter(dataset__workspace=context.workspace)
    if is_uuid(ref):
        column = qs.filter(id=ref).first()
        if column:
            return column, None
        return None, candidate_columns_result(
            context,
            "Dataset Column Not Found",
            f"Column `{ref}` was not found.",
        )
    exact = qs.filter(name__iexact=ref)
    if exact.count() == 1:
        return exact.first(), None
    if exact.count() > 1:
        return None, candidate_columns_result(
            context,
            "Multiple Columns Matched",
            f"More than one column matched `{ref}`. Use one of these IDs.",
            search=ref,
        )
    fuzzy = qs.filter(name__icontains=ref)
    if fuzzy.count() == 1:
        return fuzzy.first(), None
    return None, candidate_columns_result(
        context,
        "Dataset Column Not Found",
        f"Column `{ref}` was not found.",
        search=ref,
    )
