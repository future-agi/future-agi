"""Bounded, tenant-scoped join of saved eval cells and unified human scores."""

from collections import defaultdict

from django.db.models import F, Q
from django.db.models.functions import Coalesce
from rest_framework.exceptions import APIException, NotFound

from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.score import Score
from model_hub.services.eval_agreement_stats import (
    consensus,
    statistics,
    stored_verdict,
)


class AgreementTooLarge(APIException):
    status_code = 413
    default_detail = "Too many records for this report. Select a dataset or label."


def bounded(query, limit):
    result = list(query[: limit + 1])
    if len(result) > limit:
        raise AgreementTooLarge()
    return result


def agreement_report(organization, workspace, template_id, params):
    if organization is None or workspace is None:
        raise NotFound("Evaluation not found.")
    scope = {"organization": organization, "workspace": workspace, "deleted": False}
    template = EvalTemplate.no_workspace_objects.filter(
        Q(**scope)
        | Q(organization__isnull=True, owner=OwnerChoices.SYSTEM.value, deleted=False),
        id=template_id,
    ).first()
    if template is None:
        raise NotFound("Evaluation not found.")
    for field, model in (("dataset_id", Dataset), ("label_id", AnnotationsLabels)):
        if (
            params.get(field)
            and not model.no_workspace_objects.filter(
                id=params[field], **scope
            ).exists()
        ):
            raise NotFound("Label source not found.")
    bindings_query = UserEvalMetric.no_workspace_objects.filter(
        template=template,
        dataset__organization=organization,
        dataset__workspace=workspace,
        dataset__deleted=False,
        column_deleted=False,
        **scope,
    ).select_related("dataset")
    if params.get("dataset_id"):
        bindings_query = bindings_query.filter(dataset_id=params["dataset_id"])
    bindings = {str(b.id): b for b in bounded(bindings_query.order_by("id"), 100)}
    columns = bounded(
        Column.no_workspace_objects.filter(
            source="evaluation",
            source_id__in=bindings,
            dataset_id__in=[b.dataset_id for b in bindings.values()],
            deleted=False,
        ).order_by("-updated_at", "-id"),
        200,
    )
    # The latest live result column for each binding, never one from another dataset.
    columns_by_binding = {}
    for column in columns:
        if column.dataset_id == bindings[column.source_id].dataset_id:
            columns_by_binding.setdefault(column.source_id, column)
    bindings = {key: b for key, b in bindings.items() if key in columns_by_binding}
    scores_query = Score.no_workspace_objects.filter(
        **scope,
        source_type="dataset_row",
        score_source="human",
        annotator__isnull=False,
        dataset_row__deleted=False,
        dataset_row__dataset_id__in=[b.dataset_id for b in bindings.values()],
        dataset_row__dataset__organization=organization,
        dataset_row__dataset__workspace=workspace,
        dataset_row__dataset__deleted=False,
        label__deleted=False,
        label__organization=organization,
        label__workspace=workspace,
    )
    if params.get("label_id"):
        scores_query = scores_query.filter(label_id=params["label_id"])
    # Discovery reads only distinct source metadata. A large, unrelated source
    # must not consume the selected source's vote/cell budget.
    label_sources = bounded(
        scores_query.order_by("dataset_row__dataset_id", "label_id")
        .values("dataset_row__dataset_id", "label_id", "label__name", "label__type")
        .distinct(),
        1000,
    )
    sources = []
    for binding_id, binding in bindings.items():
        for label in label_sources:
            if label["dataset_row__dataset_id"] == binding.dataset_id:
                sources.append(
                    {
                        "key": f"{binding_id}:{label['label_id']}",
                        "binding_name": binding.name,
                        "dataset_name": binding.dataset.name,
                        "label_name": label["label__name"],
                        "binding_id": binding_id,
                        "dataset_id": str(binding.dataset_id),
                        "label_id": str(label["label_id"]),
                        "label_type": label["label__type"],
                    }
                )
    if len(sources) > 1000:
        raise AgreementTooLarge()
    sources.sort(
        key=lambda s: (s["dataset_name"], s["binding_name"], s["label_name"], s["key"])
    )
    selected = next((s for s in sources if s["key"] == params.get("source")), None)
    if params.get("source") and selected is None:
        raise NotFound("Label source not found.")
    selected = selected or (sources[0] if sources else None)
    metadata = [
        {k: s[k] for k in ("key", "binding_name", "dataset_name", "label_name")}
        for s in sources
    ]
    if selected is None:
        return {"sources": metadata, "report": None}
    column = columns_by_binding[selected["binding_id"]]
    try:
        report = selected_report(
            organization, workspace, selected, column, scores_query
        )
    except AgreementTooLarge:
        if params.get("source"):
            raise
        # Keep discovery usable even when the first source is too large.
        report = None
    return {"sources": metadata, "report": report}


def selected_report(organization, workspace, selected, column, scores_query):
    scores = bounded(
        scores_query.filter(
            dataset_row__dataset_id=selected["dataset_id"],
            label_id=selected["label_id"],
        )
        .annotate(value_time=Coalesce("value_updated_at", "created_at"))
        .order_by("-value_time", "-created_at", "-id")
        .only("dataset_row_id", "annotator_id", "value"),
        10000,
    )
    rows = defaultdict(list)
    for score in scores:
        rows[score.dataset_row_id].append(score)
    cells = bounded(
        Cell.no_workspace_objects.filter(
            column=column,
            row_id__in=rows,
            dataset__organization=organization,
            dataset__workspace=workspace,
            dataset__deleted=False,
            dataset_id=F("column__dataset_id"),
            row__dataset_id=F("dataset_id"),
            row__deleted=False,
            column__deleted=False,
            deleted=False,
        )
        .order_by("-updated_at", "-id")
        .only("row_id", "value", "status"),
        20000,
    )
    latest_cells = {}
    for cell in cells:
        latest_cells.setdefault(cell.row_id, cell)
    numeric = selected["label_type"] in {"numeric", "star"}
    allowed_choices = None
    if selected["label_type"] == "categorical":
        settings = (
            AnnotationsLabels.no_workspace_objects.filter(
                id=selected["label_id"],
                organization=organization,
                workspace=workspace,
                deleted=False,
            )
            .values_list("settings", flat=True)
            .first()
            or {}
        )
        allowed_choices = set()
        for option in settings.get("options") or []:
            values = (
                [option.get("label"), option.get("value")]
                if isinstance(option, dict)
                else [option]
            )
            allowed_choices.update(str(value) for value in values if value is not None)
    pairs = []
    for row_id, votes in rows.items():
        human = consensus(votes, selected["label_type"])
        cell = latest_cells.get(row_id)
        judge = (
            stored_verdict(cell.value, numeric, allowed_choices)
            if cell and cell.status == "pass"
            else None
        )
        if human is not None and judge is not None:
            pairs.append({"row_id": str(row_id), "human": human, "judge": judge})
    pairs.sort(key=lambda pair: pair["row_id"])
    try:
        summary = statistics(pairs, numeric)
    except ValueError as exc:
        raise AgreementTooLarge(str(exc)) from exc
    return {
        **selected,
        **summary,
        "excluded": len(rows) - len(pairs),
        "disagreements": [p for p in pairs if p["human"] != p["judge"]][:100],
    }
