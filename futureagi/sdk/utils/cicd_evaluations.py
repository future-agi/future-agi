import structlog
from django.db import transaction
from django.db.models import Avg, Case, Count, Q, When
from django.db.models.functions import JSONObject, NullIf

from accounts.models import Organization, User
from tfc.middleware.workspace_context import get_current_organization

logger = structlog.get_logger(__name__)
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.utils.async_evaluations import _handle_async_eval
from tracer.models.eval_ci_cd import EvaluationResult, EvaluationRun


def create_evaluations(evaluation_run_id, organization_id, user_id, eval_data_list):
    """
    Create Evaluation and EvaluationResult objects for a given EvaluationRun.

    Note: This is now a regular function (not a Celery task). The actual evaluation
    processing is handled by Temporal workflows triggered from _handle_async_eval.
    """
    try:
        evaluation_run = EvaluationRun.objects.get(id=evaluation_run_id)
        organization = Organization.objects.get(id=organization_id)
        user = User.objects.get(id=user_id)

        all_eval_ids = []
        with transaction.atomic():
            for item in eval_data_list:
                eval_template_name = item["eval_template"]
                eval_template = EvalTemplate.no_workspace_objects.get(
                    Q(name=eval_template_name)
                    & (Q(organization=organization) | Q(organization__isnull=True))
                )

                # _handle_async_eval now triggers Temporal workflows internally
                result = _handle_async_eval(
                    eval_template=eval_template,
                    inputs=item["inputs"],
                    model=item.get("model_name"),
                    user=user,
                    workspace=None,
                    span_id=None,
                    custom_eval_name=None,
                    trace_eval=False,
                    is_batched=True,
                    eval_config=item.get("config", {}),
                )

                eval_ids = [
                    ev.get("eval_id")
                    for group in result
                    for ev in group.get("evaluations", [])
                ]
                all_eval_ids.extend(eval_ids)

            evaluations_to_link = Evaluation.no_workspace_objects.filter(
                id__in=all_eval_ids
            )
            evaluation_results = [
                EvaluationResult(
                    evaluation_run=evaluation_run,
                    evaluation=ev,
                    organization=organization,
                )
                for ev in evaluations_to_link
            ]
            EvaluationResult.objects.bulk_create(evaluation_results)

    except Exception as e:
        logger.exception(f"Error in create_evaluations: {e}")


def create_evaluation_run(validated_data, user):
    """
    Synchronously creates an EvaluationRun and dispatches an async task
    to create the associated evaluations and results.
    """
    project = validated_data["project"]
    version = validated_data["version"]
    eval_data_list = validated_data["eval_data"]

    _org = get_current_organization() or user.organization
    evaluation_run = EvaluationRun.objects.create(
        project=project,
        version=version,
        organization=_org,
    )

    eval_data_for_task = [
        {
            "eval_template": item["eval_template"],
            "inputs": item["inputs"],
            "model_name": item.get("model_name"),
            "config": item.get("config", {}),
        }
        for item in eval_data_list
    ]

    # Call directly (Temporal workflows are started internally by _handle_async_eval)
    create_evaluations(
        evaluation_run_id=str(evaluation_run.id),
        organization_id=str(_org.id),
        user_id=str(user.id),
        eval_data_list=eval_data_for_task,
    )

    return evaluation_run


def is_evaluation_run_processing(evaluation_run):
    exists = (
        Evaluation.objects.filter(ci_cd_result__evaluation_run=evaluation_run)
        .exclude(status__in=[StatusChoices.COMPLETED, StatusChoices.FAILED])
        .exists()
    )

    return exists


def get_evaluation_run_summary(evaluation_run):
    eval_results = Evaluation.objects.filter(
        ci_cd_result__evaluation_run=evaluation_run, status=StatusChoices.COMPLETED
    )
    templates = eval_results.values(
        "eval_template__name", "eval_template__config", "eval_template__choices"
    ).distinct()

    aggregations = {}
    for template in templates:
        template_name = template["eval_template__name"]
        output_type = template["eval_template__config"].get("output", "score")

        if output_type == "score":
            aggregations[f"{template_name}"] = Avg(
                "output_float", filter=Q(eval_template__name=template_name)
            )

        elif output_type == "Pass/Fail":
            aggregations[f"{template_name}"] = (
                100.0
                * Count(
                    Case(When(output_bool=True, then=1)),
                    filter=Q(eval_template__name=template_name),
                )
                / Count("id", filter=Q(eval_template__name=template_name))
            )

        elif output_type == "choices":
            choices = template["eval_template__choices"] or []
            choice_aggregations = {}
            for choice in choices:
                choice_aggregations[choice] = (
                    100.0
                    * Count(
                        Case(When(output_str_list__contains=[choice], then=1)),
                        filter=Q(eval_template__name=template_name),
                    )
                    / Count("id", filter=Q(eval_template__name=template_name))
                )
            aggregations[f"{template_name}"] = JSONObject(**choice_aggregations)

    summary = eval_results.aggregate(**aggregations)
    return summary


def are_evaluation_runs_processing(evaluation_runs):
    """
    Check if any of the given evaluation runs are still processing.
    Returns True if any are processing, False if all are completed.
    """
    if not evaluation_runs:
        return False

    evaluation_run_ids = [run.id for run in evaluation_runs]

    return (
        Evaluation.objects.filter(ci_cd_result__evaluation_run__in=evaluation_run_ids)
        .exclude(status__in=[StatusChoices.COMPLETED, StatusChoices.FAILED])
        .exists()
    )


def get_evaluation_runs_summaries(evaluation_runs):
    """
    Get summaries for multiple evaluation runs using a single optimized database query.
    Returns a dictionary with version as key and summary as value.
    """
    if not evaluation_runs:
        return {}

    evaluation_run_ids = [run.id for run in evaluation_runs]

    eval_templates = EvalTemplate.no_workspace_objects.filter(
        id__in=Evaluation.objects.filter(
            ci_cd_result__evaluation_run__in=evaluation_run_ids,
            status=StatusChoices.COMPLETED,
        )
        .values_list("eval_template_id", flat=True)
        .distinct()
    )

    base_query = EvaluationRun.objects.filter(id__in=evaluation_run_ids).select_related(
        "project"
    )

    for template in eval_templates:
        config = template.config or {}
        output_type = config.get("output", "score")
        choices = template.choices or []

        if output_type == "score":
            base_query = base_query.annotate(
                **{
                    f"template_{template.id}": Avg(
                        "results__evaluation__output_float",
                        filter=Q(
                            results__evaluation__eval_template_id=template.id,
                            results__evaluation__status=StatusChoices.COMPLETED,
                        ),
                    )
                    * 100
                }
            )

        elif output_type == "Pass/Fail":
            total_count = Count(
                "results__evaluation__id",
                filter=Q(
                    results__evaluation__eval_template_id=template.id,
                    results__evaluation__status=StatusChoices.COMPLETED,
                ),
            )
            pass_count = Count(
                "results__evaluation__id",
                filter=Q(
                    results__evaluation__eval_template_id=template.id,
                    results__evaluation__status=StatusChoices.COMPLETED,
                    results__evaluation__output_bool=True,
                ),
            )

            base_query = base_query.annotate(
                **{
                    f"template_{template.id}": 100.0
                    * pass_count
                    / NullIf(total_count, 0)
                }
            )

        elif output_type == "choices":
            choice_annotations = {}
            total_count = Count(
                "results__evaluation__id",
                filter=Q(
                    results__evaluation__eval_template_id=template.id,
                    results__evaluation__status=StatusChoices.COMPLETED,
                ),
            )

            for choice in choices:
                choice_count = Count(
                    "results__evaluation__id",
                    filter=Q(
                        results__evaluation__eval_template_id=template.id,
                        results__evaluation__status=StatusChoices.COMPLETED,
                        results__evaluation__output_str_list__contains=[choice],
                    ),
                )
                choice_annotations[f"{choice}"] = (
                    100.0 * choice_count / NullIf(total_count, 0)
                )

            base_query = base_query.annotate(
                **{
                    f"template_{template.id}_count": total_count,
                    f"template_{template.id}": Case(
                        When(
                            **{f"template_{template.id}_count__gt": 0},
                            then=JSONObject(**choice_annotations),
                        ),
                        default=None,
                    ),
                }
            )

    summaries = {}

    for evaluation_run in base_query:
        version = evaluation_run.version
        summaries[version] = {}

        for template in eval_templates:
            template_name = template.name
            value = getattr(evaluation_run, f"template_{template.id}", None)

            if value is not None:
                summaries[version][template_name] = value

    for run in evaluation_runs:
        if run.version not in summaries:
            summaries[run.version] = {}

    return summaries
