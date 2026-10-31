"""Retry demo seeding without repeating committed stages or losing publication."""

import structlog
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from temporalio.common import WorkflowIDConflictPolicy

from accounts.models.demo_seed import DemoSeedRequest, DemoTracePublication

logger = structlog.get_logger(__name__)


def seed_pending_demo_data(organization_id):
    from accounts.user_onboard import create_demo_traces_and_spans, upload_demo_dataset

    # Lock and commit each stage with its marker. A crash rolls back the whole
    # stage; concurrent delivery observes the marker instead of duplicating data.
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
            if intent is None or getattr(intent, field):
                continue
            result = seed(str(intent.organization_id), str(intent.user_id))
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(result["error"])
            setattr(intent, field, True)
            intent.save(update_fields=[field])
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
