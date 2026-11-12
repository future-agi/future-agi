"""Retry demo seeding without repeating committed stages or losing publication."""

from datetime import timedelta

import structlog
from django.db import InterfaceError, OperationalError, transaction
from django.db.models import F, Q
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from temporalio.common import WorkflowIDConflictPolicy

from accounts.models.demo_seed import DemoSeedRequest, DemoTracePublication

logger = structlog.get_logger(__name__)

# A seed that keeps failing for a reason a retry cannot fix is retried until
# its signup is this old, then stops: completed_at is set while a stage is
# still unseeded, so the recovery sweep no longer picks it up.
DEMO_SEED_RETRY_WINDOW = timedelta(minutes=30)

# Failures of a service the seed needs (database, cache, queue) and timeouts
# pass with time; they never stop a seed and stay retried.
TRANSIENT_SEED_ERRORS = (
    OperationalError,
    InterfaceError,
    TimeoutError,
    ConnectionError,
    RedisConnectionError,
    RedisTimeoutError,
)


def _is_transient(error):
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, TRANSIENT_SEED_ERRORS):
            return True
        seen.add(id(error))
        error = error.__cause__
    return False


def _stop_after_retry_window(organization_id, error):
    """Stop a seed whose lasting failure outlived the retry window; log once."""
    try:
        with transaction.atomic():
            intent = (
                DemoSeedRequest.objects.select_for_update()
                .filter(organization_id=organization_id, completed_at__isnull=True)
                .first()
            )
            if intent is None:
                return
            signed_up = max(intent.organization.created_at, intent.user.created_at)
            if timezone.now() - signed_up < DEMO_SEED_RETRY_WINDOW:
                return
            intent.completed_at = timezone.now()
            intent.save(update_fields=["completed_at"])
    except Exception as stop_error:
        # The seed's own error is re-raised by the caller either way.
        logger.warning(
            "demo_seed_stop_not_recorded",
            organization_id=str(organization_id),
            reason=type(stop_error).__name__,
        )
        return
    logger.error(
        "demo_seed_stopped",
        organization_id=str(organization_id),
        reason=type(error).__name__,
    )


def seed_pending_demo_data(organization_id):
    from accounts.user_onboard import create_demo_traces_and_spans, upload_demo_dataset

    # Lock and commit each stage with its marker. A crash rolls back the whole
    # stage; concurrent delivery observes the marker instead of duplicating data.
    try:
        for field, seed in (
            ("traces_seeded", create_demo_traces_and_spans),
            ("datasets_seeded", upload_demo_dataset),
        ):
            with transaction.atomic():
                intent = (
                    DemoSeedRequest.objects.select_for_update()
                    .filter(organization_id=organization_id)
                    .first()
                )
                if intent is not None and intent.completed_at is not None:
                    return  # Finished, or stopped until restart_demo_seeding().
                if intent is None or getattr(intent, field):
                    continue
                result = seed(str(intent.organization_id), str(intent.user_id))
                if isinstance(result, dict) and result.get("error"):
                    raise RuntimeError(result["error"])
                setattr(intent, field, True)
                intent.save(update_fields=[field])
    except Exception as error:
        # The stage has rolled back; decide outside its transaction.
        if not _is_transient(error):
            _stop_after_retry_window(organization_id, error)
        raise
    DemoSeedRequest.objects.filter(
        organization_id=organization_id, traces_seeded=True, datasets_seeded=True
    ).update(completed_at=timezone.now())


def publish_pending_demo_trace(trace_id):
    from accounts.user_onboard import _write_demo_trace_to_clickhouse
    from tracer.models.observation_span import ObservationSpan
    from tracer.services.clickhouse.v2.trace_writer import dual_write_enabled

    if not dual_write_enabled():
        return  # Keep the intent if an operator temporarily disables CH writes.
    with transaction.atomic():
        pending = (
            DemoTracePublication.objects.select_for_update()
            .filter(trace_id=trace_id, completed_at__isnull=True)
            .first()
        )
        if pending is None:
            return  # Already published or deleted with its project.
        trace = pending.trace
        spans = list(ObservationSpan.objects.filter(trace=trace))
        _write_demo_trace_to_clickhouse(trace, spans)
        pending.completed_at = timezone.now()
        pending.save(update_fields=["completed_at"])


def recover_pending_demo_data(batch_size=50):
    """Fair, bounded sweep. Failed dispatch never removes pending work."""
    import tfc.temporal.background_tasks.activities  # noqa: F401
    from accounts.user_onboard import _enqueue_demo_trace_publication
    from tfc.temporal.drop_in import start_activity
    from tracer.services.clickhouse.v2.trace_writer import dual_write_enabled

    def dispatch_seed(organization_id):
        start_activity(
            "seed_demo_data_activity",
            args=(str(organization_id),),
            queue="default",
            task_id=f"seed-demo-data-{organization_id}",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            dispatch_timeout_seconds=2,
        )

    work = [(DemoSeedRequest, dispatch_seed)]
    if dual_write_enabled():
        work.append((DemoTracePublication, _enqueue_demo_trace_publication))
    for model, dispatch in work:
        ids = list(
            model.objects.filter(completed_at__isnull=True)
            .order_by(F("last_attempt_at").asc(nulls_first=True), "pk")
            .values_list("pk", flat=True)[:batch_size]
        )
        for pk in ids:
            model.objects.filter(pk=pk).update(last_attempt_at=timezone.now())
            try:
                dispatch(str(pk))
            except Exception:
                logger.exception(
                    "demo_recovery_dispatch_failed", model=model.__name__, id=str(pk)
                )


def restart_demo_seeding(organization_id=None):
    """Run stopped seeds again once their cause is fixed.

    Clears the stop on one organization's request, or on every stopped request
    when no organization is given; the recovery schedule dispatches them on its
    next run. Returns the number of requests restarted. A restarted request
    whose cause is not fixed stops again on its next lasting failure.
    """
    stopped = DemoSeedRequest.objects.filter(completed_at__isnull=False).filter(
        Q(traces_seeded=False) | Q(datasets_seeded=False)
    )
    if organization_id is not None:
        stopped = stopped.filter(organization_id=organization_id)
    restarted = stopped.update(completed_at=None, last_attempt_at=None)
    logger.info(
        "demo_seed_restarted",
        organization_id=str(organization_id) if organization_id else None,
        count=restarted,
    )
    return restarted
