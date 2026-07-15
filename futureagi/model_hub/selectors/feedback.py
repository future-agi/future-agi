"""Feedback read helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypedDict
from uuid import UUID

from model_hub.models.develop_dataset import Column
from model_hub.models.evals_metric import EvalTemplate, Feedback, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.views.utils.constants import EVAL_OUTPUT_TYPES


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


def _metric_config_overrides(user_eval_metric: UserEvalMetric) -> dict[str, Any]:
    """Return the inner override dict (`config.config`), always a plain dict."""

    outer = user_eval_metric.config
    inner = outer.get("config") if isinstance(outer, dict) else None
    return inner if isinstance(inner, dict) else {}


def resolve_feedback_template_data(
    user_eval_metric: UserEvalMetric,
    eval_template: EvalTemplate,
) -> dict[str, Any]:
    """Build the feedback-template payload the FE reads to render the
    "right value" widget.

    Which widget renders is a function of three eval config fields:
      * ``output_type`` — the template's declared shape (score/choices/…)
      * ``choices`` — the choice labels available for picker widgets
      * ``choice_scores`` — a choice→score map; when present, the LLM emits
        choice labels and the FE must render a picker (radio or checkbox)
        regardless of ``output_type``
      * ``multi_choice`` (checkbox vs radio for the CHOICES branch) is
        sourced from the template's canonical ``multi_choice`` field;
        metrics do not override this.
    """

    template_config = eval_template.config or {}
    data: dict[str, Any] = {
        "output_type": template_config.get("output"),
        "eval_description": eval_template.description,
        "eval_name": eval_template.name,
        "user_eval_name": user_eval_metric.name,
        "choice_scores": eval_template.choice_scores or None,
    }

    output_type = data["output_type"]
    if output_type == EVAL_OUTPUT_TYPES["PASS_FAIL"]:
        data["choices"] = ["Passed", "Failed"]
    elif output_type == EVAL_OUTPUT_TYPES["CHOICES"]:
        overrides = _metric_config_overrides(user_eval_metric)

        metric_choices = overrides.get("choices")
        if metric_choices:
            data["choices"] = metric_choices
        elif getattr(eval_template, "choices", None):
            data["choices"] = eval_template.choices
        else:
            data["choices"] = []

        # `multi_choice` is a canonical template field on EvalTemplate,
        # not a nested config key. The nested key is often unset even
        # when the direct field is True (e.g. the `tone` system template).
        data["multi_choice"] = bool(getattr(eval_template, "multi_choice", False))

    return data
