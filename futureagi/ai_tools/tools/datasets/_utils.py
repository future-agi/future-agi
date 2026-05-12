from typing import Any

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import format_status, markdown_table, section
from ai_tools.resolvers import is_uuid, resolve_dataset


def candidate_datasets_result(
    context: ToolContext,
    title: str = "Dataset Required",
    detail: str = "",
) -> ToolResult:
    from model_hub.models.develop_dataset import Dataset, Row

    datasets = list(
        Dataset.objects.filter(
            organization=context.organization,
            deleted=False,
        ).order_by("-created_at")[:10]
    )
    rows = [
        [
            f"`{dataset.id}`",
            dataset.name,
            str(Row.objects.filter(dataset=dataset, deleted=False).count()),
        ]
        for dataset in datasets
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Dataset ID", "Name", "Rows"],
            rows,
        )
    else:
        body = body or "No datasets found."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_dataset_id": True,
            "datasets": [
                {"id": str(dataset.id), "name": dataset.name}
                for dataset in datasets
            ],
        },
    )


def resolve_dataset_for_tool(
    dataset_ref: Any,
    context: ToolContext,
    title: str = "Dataset Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = str(dataset_ref or "").strip()
    if not ref:
        return None, candidate_datasets_result(context, title)
    dataset, error = resolve_dataset(ref, context.organization, context.workspace)
    if error:
        return None, candidate_datasets_result(
            context,
            "Dataset Not Found",
            f"{error} Use one of these dataset IDs or exact names.",
        )
    return dataset, None


def dataset_eval_candidates(dataset, search: str = ""):
    from model_hub.models.evals_metric import UserEvalMetric

    qs = (
        UserEvalMetric.objects.filter(dataset=dataset, deleted=False)
        .select_related("template")
        .order_by("-created_at")
    )
    search = str(search or "").strip()
    if search:
        qs = qs.filter(name__icontains=search)
    return list(qs[:20])


def candidate_dataset_evals_result(
    dataset,
    title: str = "Dataset Eval Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    evals = dataset_eval_candidates(dataset, search)
    rows = []
    for user_eval in evals:
        template_name = user_eval.template.name if user_eval.template else "-"
        rows.append(
            [
                f"`{user_eval.id}`",
                user_eval.name or "-",
                template_name,
                format_status(user_eval.status),
            ]
        )
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Eval ID", "Name", "Template", "Status"],
            rows,
        )
    else:
        body = body or f"No configured evals found on dataset `{dataset.name}`."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_eval_id": True,
            "dataset_id": str(dataset.id),
            "evals": [
                {
                    "id": str(user_eval.id),
                    "name": user_eval.name,
                    "template": (
                        user_eval.template.name if user_eval.template else None
                    ),
                    "status": user_eval.status,
                }
                for user_eval in evals
            ],
        },
    )


def resolve_dataset_evals(dataset, eval_refs: list[Any]):
    candidates = dataset_eval_candidates(dataset)
    resolved = []
    missing = []
    seen = set()
    for ref in eval_refs:
        ref_str = str(ref or "").strip()
        matched = None
        if not ref_str:
            missing.append("empty eval reference")
            continue
        if is_uuid(ref_str):
            matched = next((em for em in candidates if str(em.id) == ref_str), None)
        if not matched:
            ref_lower = ref_str.lower()
            exact = [
                em
                for em in candidates
                if (em.name or "").lower() == ref_lower
                or (em.template and (em.template.name or "").lower() == ref_lower)
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
                resolved.append(matched)
                seen.add(key)
        else:
            missing.append(ref_str)
    return resolved, missing


def resolve_dataset_eval(
    dataset,
    eval_ref: Any,
    title: str = "Dataset Eval Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = str(eval_ref or "").strip()
    if not ref:
        return None, candidate_dataset_evals_result(dataset, title)
    resolved, missing = resolve_dataset_evals(dataset, [ref])
    if resolved:
        return resolved[0], None
    return None, candidate_dataset_evals_result(
        dataset,
        "Dataset Eval Not Found",
        f"Eval `{ref}` was not found on dataset `{dataset.name}`. "
        "Use one of these configured eval IDs or names.",
        search="" if is_uuid(ref) else ref,
    )
