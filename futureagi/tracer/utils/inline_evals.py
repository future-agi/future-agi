import structlog
from django.db import transaction
from django.db.models import F

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

logger = structlog.get_logger(__name__)


INLINE_EVAL_TARGET_NOT_IN_SCOPE = "inline_eval_target_not_in_scope"


def _target_spans(organization_id, workspace_id):
    """Resolve targets only with an explicit, complete persisted tenant binding."""
    if organization_id is None or workspace_id is None:
        return ObservationSpan.objects.none()
    return ObservationSpan.objects.filter(
        project__organization_id=organization_id,
        project__workspace_id=workspace_id,
        project__deleted=False,
        trace__project_id=F("project_id"),
        trace__deleted=False,
    )


@temporal_activity(
    max_retries=0,
    time_limit=3600 * 3,
    queue="tasks_s",
)
def process_in_line_evals():
    """Validate pending targets before writing results or waiting for a localizer."""
    # Keep the claim, target validation and result writes in one transaction.
    # Target/project/trace locks prevent a tenant or parent change between the
    # scoped read and the writes. A failed batch rolls back its claims as well.
    with transaction.atomic():
        evals_to_process = list(
            InlineEval.objects.prefetch_related(
                "evaluation", "evaluation__error_localizer"
            )
            .select_for_update(skip_locked=True)
            .filter(status=InLineEvalStatus.PENDING, evaluation__isnull=False)
        )

        if not evals_to_process:
            logger.info("No pending inline evaluations found after locking")
            return

        span_ids_by_scope = {}
        for inline_eval in evals_to_process:
            scope = (inline_eval.organization_id, inline_eval.workspace_id)
            span_ids_by_scope.setdefault(scope, set()).add(inline_eval.span_id)

        # A span found for one tenant must never satisfy another tenant's job,
        # including jobs for the same span in a mixed-tenant batch.
        spans_map = {}
        for scope, span_ids in span_ids_by_scope.items():
            spans = (
                _target_spans(*scope)
                .filter(id__in=span_ids)
                .select_related("project", "trace")
                .order_by("project_id", "id")
                .select_for_update(of=("self", "project", "trace"))
            )
            for span in spans:
                spans_map[(*scope, str(span.id))] = span

        eval_loggers_to_create = []
        inline_evals_to_update = []

        for inline_eval in evals_to_process:
            span = spans_map.get(
                (
                    inline_eval.organization_id,
                    inline_eval.workspace_id,
                    str(inline_eval.span_id),
                )
            )
            if span is None:
                logger.warning(
                    "Skipping inline eval with unavailable target",
                    reason=INLINE_EVAL_TARGET_NOT_IN_SCOPE,
                    inline_eval_id=str(inline_eval.id),
                )
                inline_eval.status = InLineEvalStatus.FAILED
                inline_evals_to_update.append(inline_eval)
                continue

            evaluation = inline_eval.evaluation
            if evaluation.error_localizer_enabled and (
                not evaluation.error_localizer
                or evaluation.error_localizer.status
                not in (
                    ErrorLocalizerStatus.COMPLETED,
                    ErrorLocalizerStatus.FAILED,
                    ErrorLocalizerStatus.SKIPPED,
                )
            ):
                # Valid targets retain the existing localizer wait behavior.
                # Invalid targets above must not remain pending indefinitely.
                continue

            try:
                logger_kwargs = _get_logger_kwargs(inline_eval, span)
                eval_loggers_to_create.append(EvalLogger(**logger_kwargs))
                inline_eval.status = InLineEvalStatus.COMPLETED
            except Exception as e:
                logger.error(f"Failed to process inline eval {inline_eval.id}: {e}")
                inline_eval.status = InLineEvalStatus.FAILED
            inline_evals_to_update.append(inline_eval)

        if eval_loggers_to_create:
            EvalLogger.objects.bulk_create(eval_loggers_to_create)

        if inline_evals_to_update:
            InlineEval.objects.bulk_update(inline_evals_to_update, ["status"])

    logger.info(f"Processed {len(inline_evals_to_update)} inline evaluations")


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

    if (
        not _target_spans(evaluation.organization_id, evaluation.workspace_id)
        .filter(id=span_id)
        .exists()
    ):
        logger.warning(
            "Skipping inline eval creation with unavailable target",
            reason=INLINE_EVAL_TARGET_NOT_IN_SCOPE,
            evaluation_id=str(evaluation.id),
        )
        return

    inline_eval = InlineEval.objects.create(
        organization_id=evaluation.organization_id,
        workspace_id=evaluation.workspace_id,
        span_id=span_id,
        custom_eval_name=custom_eval_name,
        evaluation=evaluation,
    )

    logger.info(
        f"Created inline eval {inline_eval.id} for span {span_id} with custom eval {custom_eval_name}"
    )
