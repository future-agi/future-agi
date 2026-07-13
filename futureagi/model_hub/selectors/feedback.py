"""Feedback read helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypedDict
from uuid import UUID

from model_hub.models.develop_dataset import Column
from model_hub.models.evals_metric import Feedback
from model_hub.models.experiments import ExperimentsTable


class FeedbackEditContext(TypedDict):
    """The identifiers each surface's edit endpoint requires.

    Sourced from the Feedback record itself where possible, and from
    Column -> ExperimentsTable for experiment surface routing.
    """

    user_eval_metric_id: str
    custom_eval_config_id: str
    experiment_id: str


def resolve_feedback_edit_contexts(
    feedbacks: Iterable[Feedback],
) -> dict[UUID, FeedbackEditContext]:
    """Resolve the routing identifiers for a page of feedbacks.

    Runs at most two extra queries per call (Column + ExperimentsTable),
    scoped to the source_ids present with source="experiment". Callers
    should pass the exact iterable they will render, not a queryset that
    would re-fetch.
    """

    feedbacks = list(feedbacks)

    experiment_source_ids = [
        str(fb.source_id)
        for fb in feedbacks
        if fb.source == "experiment" and fb.source_id
    ]

    experiment_id_by_source: dict[str, str] = {}
    if experiment_source_ids:
        dataset_by_column: dict[str, str] = {
            str(col_id): str(dataset_id) if dataset_id else ""
            for col_id, dataset_id in Column.objects.filter(
                id__in=experiment_source_ids
            ).values_list("id", "dataset_id")
        }
        snapshot_ids = [v for v in set(dataset_by_column.values()) if v]
        experiment_by_snapshot: dict[str, str] = {
            str(snapshot_id): str(exp_id)
            for snapshot_id, exp_id in ExperimentsTable.objects.filter(
                snapshot_dataset_id__in=snapshot_ids
            ).values_list("snapshot_dataset_id", "id")
        }
        for source_id in experiment_source_ids:
            snapshot_id = dataset_by_column.get(source_id, "")
            experiment_id_by_source[source_id] = experiment_by_snapshot.get(
                snapshot_id, ""
            )

    contexts: dict[UUID, FeedbackEditContext] = {}
    for fb in feedbacks:
        contexts[fb.id] = FeedbackEditContext(
            user_eval_metric_id=str(fb.user_eval_metric_id or ""),
            custom_eval_config_id=str(fb.custom_eval_config_id or ""),
            experiment_id=experiment_id_by_source.get(str(fb.source_id or ""), ""),
        )
    return contexts
