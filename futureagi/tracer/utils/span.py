import structlog

logger = structlog.get_logger(__name__)
from model_hub.models.choices import StatusType
from tfc.temporal import temporal_activity
from tracer.models.observation_span import ObservationSpan
from tracer.utils.eval import eval_observation_span_runner


@temporal_activity(
    max_retries=0,
    time_limit=3600,
    queue="tasks_s",
)
def run_evals_on_spans():
    # TODO(tech-debt): This queries by eval_status on the span, which is a denormalized
    # flag that goes stale when eval configs change. A span marked "Completed" will never
    # be re-evaluated if new evals are added. Should instead query for spans that are
    # missing EvalLogger rows for their configured eval configs.
    # Primary: the previous poll's RUNNING marks and the runner's results must
    # be visible, or a span is dispatched again and its stale row saved back.
    spans = ObservationSpan.objects.using("default").filter(
        eval_status=StatusType.NOT_STARTED.value, project_version__isnull=False
    )

    try:
        for span in spans:
            project_version = span.project_version
            eval_tags = project_version.eval_tags

            if eval_tags is not None and len(eval_tags) > 0:
                span.eval_status = StatusType.RUNNING.value
                span.save()
                eval_observation_span_runner.delay(span.id, eval_tags)
            else:
                span.eval_status = StatusType.COMPLETED.value
                span.save()

    except Exception as e:
        logger.exception(f"Error in running evals task on spans: {str(e)}")
