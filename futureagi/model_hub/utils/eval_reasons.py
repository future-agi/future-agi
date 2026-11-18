from collections.abc import Iterable
from typing import Any

import structlog
from django.utils import timezone

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.explanation_agent.exp_agent import (
        ExplanationAgent,
    )
except ImportError:
    ExplanationAgent = _ee_stub("ExplanationAgent")
from model_hub.models.choices import EvalExplanationSummaryStatus, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import UserEvalMetric
from tfc.temporal import temporal_activity

logger = structlog.get_logger(__name__)

MIN_ROWS_FOR_CRITICAL_ISSUES = 15


def get_eval_reasons(
    *,
    dataset_id: str,
    eval_names: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Returns:
        Dict[str, dict[str, Any]] mapping eval name to all its reason texts and values
    """

    # Build base queryset for reason columns in the dataset
    reason_columns = Column.objects.filter(
        dataset_id=dataset_id, source=SourceChoices.EVALUATION_REASON.value
    )

    # Build base queryset for evaluation value columns in the dataset
    eval_columns = Column.objects.filter(
        dataset_id=dataset_id, source=SourceChoices.EVALUATION.value
    )

    # If specific eval names requested, filter both reason and eval columns
    if eval_names:
        eval_names_list = list(eval_names)
        desired_reason_names = [f"{name}-reason" for name in eval_names_list]
        reason_columns = reason_columns.filter(name__in=desired_reason_names)
        eval_columns = eval_columns.filter(name__in=eval_names_list)
    else:
        # If no specific eval names, get all eval names from reason columns
        # to fetch corresponding eval value columns
        eval_names_list = None

    results: dict[str, dict[str, Any]] = {}
    uem_to_eval_name_map = {}
    user_eval_metric_ids = []

    # Process reason columns
    for column in reason_columns:
        eval_name = column.name.split("-reason")[0]
        # Extract user_eval_metric_id from source_id (format: "prefix-sourceid-id")
        user_eval_metric_id = None
        if column.source_id and "-sourceid-" in str(column.source_id):
            user_eval_metric_id = str(column.source_id).split("-sourceid-")[1]

        if user_eval_metric_id and user_eval_metric_id not in uem_to_eval_name_map:
            uem_to_eval_name_map[str(user_eval_metric_id)] = eval_name
            user_eval_metric_ids.append(user_eval_metric_id)

        # Fetch cell values for this reason column
        values_qs = (
            Cell.objects.filter(dataset_id=dataset_id, column_id=column.id)
            .order_by("row__order")
            .values_list("value", flat=True)
        )

        # Exclude null/empty strings while preserving order
        values: list[str] = [v for v in values_qs if v not in (None, "")]
        if eval_name not in results:
            results[eval_name] = {}
        results[eval_name]["eval_reasons"] = values

    # Process evaluation value columns
    for column in eval_columns:
        eval_name = column.name
        # Extract user_eval_metric_id from source_id
        # Format can be either: "id" or "prefix-sourceid-id"
        user_eval_metric_id = None
        if column.source_id:
            if "-sourceid-" in str(column.source_id):
                user_eval_metric_id = str(column.source_id).split("-sourceid-")[1]
            else:
                # For regular EVALUATION columns, source_id is just the ID
                user_eval_metric_id = str(column.source_id)

        if user_eval_metric_id and user_eval_metric_id not in uem_to_eval_name_map:
            uem_to_eval_name_map[str(user_eval_metric_id)] = eval_name
            user_eval_metric_ids.append(user_eval_metric_id)

        # Fetch cell values for this evaluation column
        values_qs = (
            Cell.objects.filter(dataset_id=dataset_id, column_id=column.id)
            .order_by("row__order")
            .values_list("value", flat=True)
        )

        # Include all values (including nulls) while preserving order
        values: list[Any] = list(values_qs)
        if eval_name not in results:
            results[eval_name] = {}
        results[eval_name]["eval_values"] = values

    if len(user_eval_metric_ids) > 0:
        user_eval_metrics = UserEvalMetric.objects.filter(
            id__in=user_eval_metric_ids
        ).select_related("template")

        for uem in user_eval_metrics:
            eval_name = uem_to_eval_name_map[str(uem.id)]
            result = results[eval_name] if eval_name in results else None
            if result is not None and "eval_template_id" not in result:
                result["eval_template_id"] = str(uem.template.id)
                result["user_eval_metric_id"] = str(uem.id)
                result["eval_template_id_criteria"] = uem.template.criteria
                result["eval_template_name"] = uem.template.name
                results[eval_name] = result

    return results


@temporal_activity(
    time_limit=1800,
    queue="tasks_l",
)
def get_explanation_summary(dataset_id: str) -> None:
    """
    Temporal activity to generate explanation summary for a dataset.
    """
    try:
        dataset = Dataset.objects.get(id=dataset_id)
    except Dataset.DoesNotExist:
        logger.error(f"Dataset {dataset_id} not found")
        return

    row_count = Row.objects.filter(dataset_id=dataset_id).count()
    if row_count < MIN_ROWS_FOR_CRITICAL_ISSUES:
        dataset.eval_reason_status = EvalExplanationSummaryStatus.INSUFFICIENT_DATA
        dataset.save(update_fields=["eval_reason_status"])
        logger.info(
            "insufficient_data_for_critical_issues",
            dataset_id=dataset_id,
            row_count=row_count,
            min_required=MIN_ROWS_FOR_CRITICAL_ISSUES,
        )
        return

    try:
        dataset.eval_reason_status = EvalExplanationSummaryStatus.RUNNING
        dataset.save(update_fields=["eval_reason_status"])

        reasons = get_eval_reasons(dataset_id=dataset.id)

        agent = ExplanationAgent()
        cluster_dict_by_eval = {}

        for reason_name, reason_data in reasons.items():
            reason = reason_data.get("eval_reasons")
            uem = reason_data.get("user_eval_metric_id")

            explanation_summary = agent.evaluate(
                explanation=reason,
                eval_name=reason_data.get("eval_template_name"),
                eval_criteria=reason_data.get("eval_template_id_criteria"),
                eval_values=reason_data.get("eval_values"),
            )

            if not explanation_summary:
                continue

            if isinstance(explanation_summary, list):
                clusters = explanation_summary
            else:
                clusters = (explanation_summary.get("failure_clusters", []) or []) + (
                    explanation_summary.get("success_clusters", []) or []
                )

            eval_config_id = uem
            eval_template_id = reason_data["eval_template_id"]

            for cluster in clusters:
                cluster["eval_config_id"] = eval_config_id
                cluster["eval_template_id"] = eval_template_id
                cluster["eval_name"] = reason_data.get("eval_template_name")

            cluster_dict_by_eval[reason_name] = clusters

        dataset.eval_reasons = cluster_dict_by_eval
        dataset.eval_reason_last_updated = timezone.now()
        dataset.eval_reason_status = EvalExplanationSummaryStatus.COMPLETED
        dataset.save(
            update_fields=[
                "eval_reasons",
                "eval_reason_last_updated",
                "eval_reason_status",
            ]
        )

    except Exception as e:
        dataset.eval_reason_status = EvalExplanationSummaryStatus.FAILED
        dataset.eval_reason_last_updated = timezone.now()
        dataset.save(update_fields=["eval_reason_status", "eval_reason_last_updated"])
        logger.exception(
            f"Error generating explanation summary: {e}", dataset_id=dataset_id
        )
        raise
