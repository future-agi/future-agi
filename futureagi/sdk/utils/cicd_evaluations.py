import structlog
from django.db import DatabaseError, transaction
from django.db.models import Avg, Case, Count, Q, When
from django.db.models.functions import JSONObject, NullIf

from accounts.models import Organization, User
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.utils.api_errors import EVALUATION_COULD_NOT_START
from sdk.utils.async_evaluations import _handle_async_eval, start_evaluation
from sdk.utils.template_lookup import find_cicd_template
from tfc.middleware.workspace_context import get_current_organization
from tracer.models.eval_ci_cd import EvaluationResult, EvaluationRun

logger = structlog.get_logger(__name__)


def _record_item_that_could_not_start(item, eval_template, user, organization):
    """A FAILED Evaluation of the item's template, created failed so the run is
    not left processing. Its reason and error_message are both
    EVALUATION_COULD_NOT_START, never the exception's text: the SDK's
    evaluation results (sdk/views/evaluations.py) return error_message."""
    return Evaluation.objects.create(
        user=user,
        organization=organization,
        eval_template=eval_template,
        model_name=item.get("model_name"),
        input_data=item.get("inputs"),
        eval_config=item.get("config") or {},
        status=StatusChoices.FAILED,
        reason=EVALUATION_COULD_NOT_START,
        error_message=EVALUATION_COULD_NOT_START,
    )


def create_evaluations(evaluation_run_id, organization_id, user_id, eval_data_list):
    """
    Create Evaluation and EvaluationResult objects for a given EvaluationRun.

    Each item starts on its own savepoint, and the other items are created and
    linked as before whatever one item does:
    - An item whose start raises is undone alone, with any row it created, and
      recorded as a FAILED evaluation of its template
      (_record_item_that_could_not_start). The exception is only logged. If that
      record cannot be written either, the item is dropped and logged.
    - An item whose template no longer resolves is dropped and logged: the
      request check resolved the same template just before.

    Note: This is now a regular function (not a Celery task). The actual evaluation
    processing is handled by Temporal workflows triggered from _handle_async_eval.
    """
    try:
        evaluation_run = EvaluationRun.objects.get(id=evaluation_run_id)
        organization = Organization.objects.get(id=organization_id)
        user = User.objects.get(id=user_id)

        all_eval_ids = []
        # Started when this transaction commits (registered below).
        to_start = []
        with transaction.atomic():
            for item in eval_data_list:
                in_scope = Q(organization=organization) | Q(organization__isnull=True)
                try:
                    if item.get("eval_template_id"):
                        # The template the request check accepted.
                        eval_template = EvalTemplate.no_workspace_objects.get(
                            Q(pk=item["eval_template_id"]) & in_scope
                        )
                    else:
                        eval_template = find_cicd_template(
                            item["eval_template"], organization, None
                        )
                except EvalTemplate.DoesNotExist:
                    logger.exception(
                        "cicd_item_template_not_found",
                        evaluation_run_id=evaluation_run_id,
                        eval_template=item["eval_template"],
                    )
                    continue

                started = []
                try:
                    with transaction.atomic():
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
                            start_workflow=started.append,
                        )

                        eval_ids = [
                            ev.get("eval_id")
                            for group in result
                            for ev in group.get("evaluations", [])
                        ]
                    to_start.extend(started)
                except Exception:
                    logger.exception(
                        "cicd_item_could_not_start",
                        evaluation_run_id=evaluation_run_id,
                        eval_template=eval_template.name,
                    )
                    try:
                        with transaction.atomic():
                            failed = _record_item_that_could_not_start(
                                item, eval_template, user, organization
                            )
                    except DatabaseError:
                        logger.exception(
                            "cicd_item_failure_not_recorded",
                            evaluation_run_id=evaluation_run_id,
                            eval_template=eval_template.name,
                        )
                        continue
                    eval_ids = [str(failed.id)]
                all_eval_ids.extend(eval_ids)

            evaluations_to_link = Evaluation.objects.filter(id__in=all_eval_ids)
            evaluation_results = [
                EvaluationResult(
                    evaluation_run=evaluation_run,
                    evaluation=ev,
                    organization=organization,
                )
                for ev in evaluations_to_link
            ]
            EvaluationResult.objects.bulk_create(evaluation_results)

            # The worker reads each evaluation in its own database connection,
            # so each workflow starts once this transaction is committed; a
            # rolled-back request starts nothing.
            def start_committed():
                for evaluation_id in to_start:
                    start_evaluation(evaluation_id)

            transaction.on_commit(start_committed)

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
            "eval_template_id": item.get("eval_template_id"),
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


def get_evaluation_runs_failures(evaluation_runs):
    """The FAILED evaluations of each run, counted per template and reason:
    ``{run id: [{"eval_template", "reason", "count"}]}``, ordered by template
    name, then reason.

    The summaries score only COMPLETED evaluations; this says why the others
    failed. A run's list is bounded by its templates times their reasons.
    A failure after the evaluation returned keeps the evaluator's own reason,
    which can be None. Rows written before failed evaluations had a reason
    hold None or the evaluator's own reason. ``error_message`` is never read.
    """
    if not evaluation_runs:
        return {}

    evaluation_run_ids = [run.id for run in evaluation_runs]
    failures = {str(run_id): [] for run_id in evaluation_run_ids}

    groups = (
        Evaluation.objects.filter(
            ci_cd_result__evaluation_run__in=evaluation_run_ids,
            status=StatusChoices.FAILED,
        )
        .values("ci_cd_result__evaluation_run_id", "eval_template__name", "reason")
        .annotate(count=Count("id"))
        .order_by("eval_template__name", "reason")
    )
    for group in groups:
        failures[str(group["ci_cd_result__evaluation_run_id"])].append(
            {
                "eval_template": group["eval_template__name"],
                "reason": group["reason"],
                "count": group["count"],
            }
        )

    return failures


def get_evaluation_runs_completed(evaluation_runs):
    """The COMPLETED evaluations of each run, counted per template and model:
    ``{run id: [{"eval_template", "model", "count"}]}``, ordered by template
    name, then model.

    ``model`` is the evaluation's model_name, the model it was stored, checked
    and charged with: the item's model_name, or, for an LLM-judge item that
    named none and that the engine had no model for, the model resolved from
    its config and its template's saved model. None when no model was stored,
    for example for code evaluations, a system template without a models
    list, an agent sent without model_name, and an LLM judge sent without
    model_name that runs on its template's configured model. A run's list is
    bounded by its templates times their models, not by its rows.
    """
    if not evaluation_runs:
        return {}

    evaluation_run_ids = [run.id for run in evaluation_runs]
    completed = {str(run_id): [] for run_id in evaluation_run_ids}

    groups = (
        Evaluation.objects.filter(
            ci_cd_result__evaluation_run__in=evaluation_run_ids,
            status=StatusChoices.COMPLETED,
        )
        .values("ci_cd_result__evaluation_run_id", "eval_template__name", "model_name")
        .annotate(count=Count("id"))
        .order_by("eval_template__name", "model_name")
    )
    for group in groups:
        completed[str(group["ci_cd_result__evaluation_run_id"])].append(
            {
                "eval_template": group["eval_template__name"],
                "model": group["model_name"],
                "count": group["count"],
            }
        )

    return completed
