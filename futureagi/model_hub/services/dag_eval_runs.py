"""Workspace-scoped reads and writes around decision-tree execution.

Every lookup is filtered by the caller's organization and workspace: trees,
the evals a tree asks, datasets, rows, columns and stored runs. A run is one
parent ``Evaluation`` with one child per row; the paths report only reads
those stored results and never asks a judge again.
"""

import time
from collections import OrderedDict

from django.db import transaction
from django.db.models import Q

from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from model_hub.services.dag_eval_definition import DagInvalid, variables_of
from model_hub.services.dag_eval_executor import ask_judge, execute

MAX_RUN_ROWS = 20
RECENT_RUNS = 50


def tree_queryset(org, workspace):
    return EvalTemplate.no_workspace_objects.filter(
        organization=org,
        workspace=workspace,
        deleted=False,
        owner=OwnerChoices.USER.value,
        eval_type="dag",
    )


def askable_eval(template_id, org, workspace):
    """An eval a tree may ask: a system eval or one of this workspace's own."""
    return (
        EvalTemplate.no_workspace_objects.filter(id=template_id, deleted=False)
        .filter(
            Q(owner=OwnerChoices.SYSTEM.value)
            | Q(organization=org, workspace=workspace)
        )
        .exclude(eval_type="dag")
        .exclude(template_type="composite")
        .first()
    )


def eval_names(dag, org, workspace):
    """Names of the evals a definition asks; DagInvalid when one is not askable."""
    names, missing = {}, []
    for node in dag["nodes"]:
        if node["kind"] != "eval":
            continue
        template = askable_eval(node["template_id"], org, workspace)
        if template is None:
            missing.append(f"{node['template_id']} is not an eval in this workspace")
        else:
            names[node["template_id"]] = template.name
            # Resolve live scoped requirements, never trust client-supplied metadata.
            node["required_keys"] = list(
                (template.config or {}).get("required_keys") or ["output"]
            )
    if missing:
        raise DagInvalid(missing)
    return names


def require_inputs(dag, values):
    missing = [
        f"{{{{{name}}}}} is not mapped"
        for name in variables_of(dag)
        if name != "output" and name not in values
    ]
    if missing:
        raise DagInvalid(missing)


def scoped_dataset(dataset_id, org, workspace):
    return Dataset.no_workspace_objects.filter(
        id=dataset_id, organization=org, workspace=workspace, deleted=False
    ).first()


def column_ids(dataset, mapping):
    ids = set(mapping.values())
    found = {
        str(pk)
        for pk in Column.no_workspace_objects.filter(
            dataset=dataset, id__in=ids, deleted=False
        ).values_list("id", flat=True)
    }
    if found != ids:
        raise DagInvalid(["Map each variable to a column of this dataset"])


def row_values(dataset, rows, mapping):
    """{row_id: {variable: cell text}} for the mapped columns of these rows."""
    by_column = {}
    for variable, column_id in mapping.items():
        by_column.setdefault(column_id, []).append(variable)
    values = {str(row.id): {} for row in rows}
    cells = Cell.no_workspace_objects.filter(
        dataset=dataset,
        row__in=rows,
        column_id__in=list(by_column),
        deleted=False,
    ).values_list("row_id", "column_id", "value")
    for row_id, column_id, value in cells:
        for variable in by_column.get(str(column_id), []):
            values[str(row_id)][variable] = value or ""
    return values


def scoped_rows(dataset, row_ids=None, limit=MAX_RUN_ROWS):
    rows = Row.no_workspace_objects.filter(dataset=dataset, deleted=False)
    if row_ids is not None:
        rows = rows.filter(id__in=row_ids)
    return list(rows.order_by("order", "id")[:limit])


class TreeCalls:
    """The judge and eval calls of one execution, in the caller's scope."""

    def __init__(self, org, workspace, tree_name, tree_model):
        self.org, self.workspace = org, workspace
        self.tree_name, self.tree_model = tree_name, tree_model

    def judge(self, *, question, inputs, model):
        return ask_judge(
            question=question,
            inputs=inputs,
            model=model,
            org=self.org,
            workspace=self.workspace,
            tree_name=self.tree_name,
        )

    def evaluate(self, template_id, values):
        from model_hub.utils.scoring import score_eval_output
        from model_hub.views.utils import evals as eval_utils

        template = askable_eval(template_id, self.org, self.workspace)
        if template is None:
            return {"passed": None, "reason": "the eval is not available"}
        keys = (template.config or {}).get("required_keys") or ["output"]
        missing = [key for key in keys if key not in values]
        if missing:
            return {"passed": None, "reason": f"{{{{{missing[0]}}}}} is not mapped"}
        started = time.monotonic()
        try:
            result = eval_utils.run_eval_func(
                {},
                {key: values[key] for key in keys},
                template,
                self.org,
                model=template.model or self.tree_model,
                workspace=self.workspace,
                source="dag_eval",
            )
        except Exception as exc:  # noqa: BLE001 - one failed eval skips the row
            return {"passed": None, "reason": f"the eval failed: {str(exc)[:200]}"}
        score = score_eval_output(result.get("output"), template, default_score=None)
        threshold = (
            template.pass_threshold if template.pass_threshold is not None else 0.5
        )
        return {
            "name": template.name,
            "score": score,
            "passed": None if score is None else score >= threshold,
            "reason": str(result.get("reason") or "")[:1000],
            "latency_ms": round((time.monotonic() - started) * 1000),
        }

    def execute(self, dag, *, output, values, threshold):
        return execute(
            dag,
            output=output,
            values=values,
            threshold=threshold,
            judge=self.judge,
            evaluate=self.evaluate,
            tree_model=self.tree_model,
        )


def version_label(tree):
    from model_hub.models.evals_metric import EvalTemplateVersion

    version = EvalTemplateVersion.objects.get_default(tree)
    return f"v{version.version_number}" if version else "v1"


def save_run(*, tree, user, org, workspace, dataset, mapping, results):
    """One parent run and one child result per row, written together."""
    label = f"{dataset.name} · {len(results)} rows"
    scored = [r["score"] for _, r in results if r["score"] is not None]
    with transaction.atomic():
        run = Evaluation.objects.create(
            user=user,
            organization=org,
            workspace=workspace,
            eval_template=tree,
            model_name=tree.model,
            status=StatusChoices.COMPLETED,
            input_data={"dataset_id": str(dataset.id), "mapping": mapping},
            eval_config={"dag_run": True, "version": version_label(tree)},
            data={"dag_run": {"dataset_id": str(dataset.id), "label": label}},
            reason=label,
            value=str(sum(scored) / len(scored)) if scored else None,
            output_type="dag",
        )
        for row_id, result in results:
            Evaluation.objects.create(
                user=user,
                organization=org,
                workspace=workspace,
                eval_template=tree,
                parent_evaluation=run,
                model_name=tree.model,
                status=StatusChoices.COMPLETED,
                input_data={"row_id": row_id},
                data={
                    "dag": {
                        "row_id": row_id,
                        **{
                            k: result[k]
                            for k in ("path", "path_key", "path_label", "leaf")
                        },
                    }
                },
                reason=result["reason"],
                value=None if result["value"] is None else str(result["value"]),
                output_float=result["score"],
                output_bool=result["passed"],
                output_type="dag",
            )
    return run, label


def stored_runs(tree, org, workspace):
    return Evaluation.objects.filter(
        eval_template=tree,
        organization=org,
        workspace=workspace,
        parent_evaluation__isnull=True,
        deleted=False,
        data__has_key="dag_run",
    ).order_by("-created_at", "-id")


def paths_report(tree, org, workspace, run_id=None):
    runs = stored_runs(tree, org, workspace)
    recent = list(runs[:RECENT_RUNS])
    if run_id is not None:
        selected = runs.filter(id=run_id).first()
        if selected is None:
            return None
    else:
        selected = recent[0] if recent else None
    listed = [
        {"run_id": str(r.id), "run_label": r.data["dag_run"]["label"]} for r in recent
    ]
    if selected is not None and selected not in recent:
        listed.append(
            {
                "run_id": str(selected.id),
                "run_label": selected.data["dag_run"]["label"],
            }
        )
    base = {
        "template_id": str(tree.id),
        "name": tree.name,
        "runs": listed,
        "experiment_id": None,
    }
    if selected is None:
        return {
            **base,
            "run_id": None,
            "run_label": None,
            "dataset_id": None,
            "n": 0,
            "paths": [],
        }
    cells = Evaluation.objects.filter(
        parent_evaluation=selected,
        organization=org,
        workspace=workspace,
        deleted=False,
        data__has_key="dag",
    ).order_by("created_at", "id")
    groups = OrderedDict()
    for cell in cells:
        typed = cell.data["dag"]
        group = groups.setdefault(
            typed["path_key"],
            {
                "path_key": typed["path_key"],
                "label": typed["path_label"],
                "rows": [],
                "scores": [],
            },
        )
        group["rows"].append(typed["row_id"])
        if cell.output_float is not None:
            group["scores"].append(cell.output_float)
    total = sum(len(g["rows"]) for g in groups.values())
    paths = sorted(
        (
            {
                "path_key": g["path_key"],
                "label": g["label"],
                "rows": g["rows"],
                "n": len(g["rows"]),
                "share": len(g["rows"]) / total if total else 0,
                "mean": sum(g["scores"]) / len(g["scores"]) if g["scores"] else None,
            }
            for g in groups.values()
        ),
        key=lambda p: (-p["n"], p["label"]),
    )
    return {
        **base,
        "run_id": str(selected.id),
        "run_label": selected.data["dag_run"]["label"],
        "dataset_id": selected.data["dag_run"]["dataset_id"],
        "n": total,
        "paths": paths,
    }
