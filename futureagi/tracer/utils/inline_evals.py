import structlog
from django.db import transaction
from django.db.models import Exists, OuterRef, Q

logger = structlog.get_logger(__name__)
from model_hub.models.error_localizer_model import (
    ErrorLocalizerStatus,
)
from model_hub.models.evaluation import Evaluation, StatusChoices
from tfc.temporal import temporal_activity
from tracer.models.custom_eval_config import (
    CustomEvalConfig,
    InlineEval,
    InLineEvalStatus,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan


@temporal_activity(
    max_retries=0,
    time_limit=3600 * 3,
    queue="tasks_s",
)
def process_in_line_evals():
    """
    Process all pending inline evaluations that have a corresponding ObservationSpan.
    For evaluations with error_localizer_enabled=True, wait until the error_localizer task is completed.
    """
    evals_to_process = []
    with transaction.atomic():
        processible_eval_ids = (
            InlineEval.objects.filter(
                status=InLineEvalStatus.PENDING,
            )
            .filter(evaluation__isnull=False)
            .filter(
                Exists(
                    ObservationSpan.objects.filter(
                        id=OuterRef("span_id"), deleted=False
                    )
                )
            )
            .filter(
                # Either error_localizer is not enabled, or it's enabled and in a final state (completed/failed/skipped)
                Q(evaluation__error_localizer_enabled=False)
                | Q(evaluation__error_localizer_enabled__isnull=True)
                | Q(
                    evaluation__error_localizer_enabled=True,
                    evaluation__error_localizer__status__in=[
                        ErrorLocalizerStatus.COMPLETED,
                        ErrorLocalizerStatus.FAILED,
                        ErrorLocalizerStatus.SKIPPED,
                    ],
                )
            )
            .values_list("id", flat=True)
        )

        # Now lock and fetch the objects
        evals_to_process = list(
            InlineEval.objects.prefetch_related(
                "evaluation", "evaluation__error_localizer"
            )
            .select_for_update(skip_locked=True)
            .filter(id__in=processible_eval_ids, status=InLineEvalStatus.PENDING)
        )

        if not evals_to_process:
            logger.info("No pending inline evaluations found after locking")
            return

        eval_ids = [e.id for e in evals_to_process]

        InlineEval.objects.filter(id__in=eval_ids).update(
            status=InLineEvalStatus.PROCESSING
        )

    span_ids = [eval.span_id for eval in evals_to_process]
    spans = ObservationSpan.objects.filter(id__in=span_ids).select_related(
        "project", "trace"
    )
    spans_map = {str(span.id): span for span in spans}

    eval_loggers_to_create = []
    inline_evals_to_update = []

    for inline_eval in evals_to_process:
        span = spans_map.get(str(inline_eval.span_id))

        # this case should not happen, but just in case
        if not span:
            logger.warning(
                f"Span for inline eval {inline_eval.id} disappeared during processing."
            )
            inline_eval.status = InLineEvalStatus.FAILED
            inline_evals_to_update.append(inline_eval)
            continue

        try:
            logger_kwargs = _get_logger_kwargs(inline_eval, span)
            eval_loggers_to_create.append(EvalLogger(**logger_kwargs))

            inline_eval.status = InLineEvalStatus.COMPLETED
            inline_evals_to_update.append(inline_eval)

        except Exception as e:
            logger.error(f"Failed to process inline eval {inline_eval.id}: {e}")
            inline_eval.status = InLineEvalStatus.FAILED
            inline_evals_to_update.append(inline_eval)

    if eval_loggers_to_create:
        EvalLogger.objects.bulk_create(eval_loggers_to_create)

    if inline_evals_to_update:
        InlineEval.objects.bulk_update(inline_evals_to_update, ["status"])

    logger.info(f"Processed {len(evals_to_process)} inline evaluations")


def _get_logger_kwargs(inline_eval: InlineEval, span: ObservationSpan):
    """
    Get the logger kwargs for the inline eval.
    """
    custom_eval_config, _ = CustomEvalConfig.objects.get_or_create(
        name=inline_eval.custom_eval_name,
        project=span.project,
        defaults={"eval_template": inline_eval.evaluation.eval_template},
    )

    failure = inline_eval.evaluation.status == StatusChoices.FAILED
    output_metadata = inline_eval.evaluation.metadata or {}

    if (
        inline_eval.evaluation.error_localizer_enabled
        and inline_eval.evaluation.error_localizer
        and inline_eval.evaluation.error_localizer.status
        == ErrorLocalizerStatus.COMPLETED
    ):

        error_localizer_task = inline_eval.evaluation.error_localizer
        output_metadata.update(
            {
                "error_analysis": error_localizer_task.error_analysis,
                "selected_input_key": error_localizer_task.selected_input_key,
                "input_types": error_localizer_task.input_types,
                "input_data": error_localizer_task.input_data,
            }
        )

    logger_kwargs = {
        "trace": span.trace,
        "observation_span": span,
        # Denormalised; used by the CH `has_eval` filter to scope by project
        # without an INNER JOIN to spans. Required for bulk_create paths
        # because the model's save() override doesn't fire there.
        "project": span.project,
        "custom_eval_config": custom_eval_config,
        "output_metadata": output_metadata,
        "results_explanation": inline_eval.evaluation.value,
        "eval_explanation": inline_eval.evaluation.reason,
        "output_float": inline_eval.evaluation.output_float,
        "output_bool": inline_eval.evaluation.output_bool,
        "output_str_list": inline_eval.evaluation.output_str_list or [],
        "output_str": inline_eval.evaluation.output_str,
        "error": failure,
    }

    return logger_kwargs


def trigger_inline_eval(evaluation: Evaluation):
    if not evaluation.trace_data:
        logger.info(
            f"Skipping inline eval creation for evaluation {evaluation.id}: trace_data is None"
        )
        return

    span_id = evaluation.trace_data.get("span_id")
    custom_eval_name = evaluation.trace_data.get("custom_eval_name")

    if not span_id or not custom_eval_name:
        logger.warning(
            f"Skipping inline eval creation for evaluation {evaluation.id}: missing span_id or custom_eval_name"
        )
        return

    inline_eval = InlineEval.objects.create(
        organization=evaluation.organization,
        workspace=evaluation.workspace,
        span_id=span_id,
        custom_eval_name=custom_eval_name,
        evaluation=evaluation,
    )

    logger.info(
        f"Created inline eval {inline_eval.id} for span {span_id} with custom eval {custom_eval_name}"
    )
