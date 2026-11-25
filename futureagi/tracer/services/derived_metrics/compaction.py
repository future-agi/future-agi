"""Checkpoint layout coordination, independent of attempt-owned history GC.

A physical tuple is shared across projects and generations. Advisory gates use
its project-hash group AND actual shard identity, not one project's lifecycle
lock. Writers hold a shared gate; bounded layout maintenance holds it exclusively.
The maintenance obligation is committed before sending OPTIMIZE. A missing
response is never permission to issue another OPTIMIZE or discard a pin.
"""

from contextlib import contextmanager
from hashlib import sha256
from time import monotonic
from uuid import uuid4

from django.db import connection, connections, transaction
from django.utils import timezone

from tracer.models.derived_metric import MetricErasureRequest
from tracer.services.derived_metrics.deletion_protocol import _intent

LAYOUT_PROTOCOL = "metric_checkpoint_layout"
LAYOUT_WALL_SECONDS = 600
LAYOUT_STATEMENT_SECONDS = 60


@contextmanager
def checkpoint_gate(group, shard, *, exclusive=False, deadline=None):
    """Order: project lifecycle (if any), then one checkpoint group gate.

    Try rather than wait indefinitely: the caller retries the already committed
    checkpoint, never allocates a replacement merely because maintenance waited.
    No worker holds two groups simultaneously.
    """
    from tracer.services.derived_metrics.publication import MetricVisibilityError

    if deadline is not None and monotonic() >= deadline:
        raise MetricVisibilityError("checkpoint layout deadline exceeded")
    key = int.from_bytes(
        sha256(f"metric-checkpoint-layout:{shard!r}:{group}".encode()).digest()[:8],
        "big",
        signed=True,
    )
    # Like the fleet/run execution gates, this transaction owns ONLY its
    # advisory namespace. Never retain an ORM lifecycle/row transaction through
    # native submission or maintenance, and never use a pooled session lock.
    database = connections["default"].copy(alias="metric_checkpoint_gate")
    try:
        database.set_autocommit(False)
        function = "pg_try_advisory_xact_lock" + ("" if exclusive else "_shared")
        with database.cursor() as cursor:
            cursor.execute(f"SELECT {function}(%s)", [key])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise MetricVisibilityError("checkpoint layout gate is busy")
        yield
    finally:
        try:
            database.rollback()
        finally:
            database.close()


def layout_obligations(group, shard):
    return MetricErasureRequest.objects.filter(
        store_payload__protocol=LAYOUT_PROTOCOL,
        store_payload__kind="partition",
        store_payload__group=group,
        store_payload__shard=shard,
    ).exclude(status="done")


def require_layout_drained(group, shard):
    from tracer.services.derived_metrics.publication import MetricVisibilityError

    if layout_obligations(group, shard).exists():
        raise MetricVisibilityError("checkpoint layout reconciliation required")


def _completed(intent):
    MetricErasureRequest.objects.filter(id=intent.id).update(
        status="done",
        completed_at=timezone.now(),
        receipts={"layout": {"drained": 1}},
    )


def reconcile_layout(store, group, shard):
    """Successful synchronous completion, not merely an empty processes view.

    An exception may leave queued replicated merge work, absent from merges at
    the instant of inspection. Only QueryFinish for our synchronous operation
    permits automated reconciliation. Unsubmitted, expired-log and failed-query
    obligations stay closed pending an explicit operational drain proof.
    """
    from tracer.services.derived_metrics.publication import MetricVisibilityError

    for intent in layout_obligations(group, shard).iterator(chunk_size=32):
        payload = intent.store_payload
        finished = store.execute(
            "SELECT count() FROM system.query_log WHERE query_id = %(query)s "
            "AND type = 'QueryFinish'",
            {"query": payload["query_id"]},
        )[0][0]
        running = store.execute(
            "SELECT count() FROM system.processes WHERE query_id = %(query)s",
            {"query": payload["query_id"]},
        )[0][0]
        merging = store.execute(
            "SELECT count() FROM system.merges WHERE database = currentDatabase() "
            "AND table = %(table)s AND partition_id = %(partition)s",
            {"table": payload["table"], "partition": payload["partition_id"]},
        )[0][0]
        if not finished or running or merging:
            raise MetricVisibilityError("checkpoint layout reconciliation required")
        _completed(intent)


def consolidate_checkpoint(store, project_id, *, deadline=None):
    """Sequential bounded tuple maintenance with durable ambiguous-call tracking."""
    from django.apps import apps

    from tracer.services.derived_metrics.publication import MetricVisibilityError
    from tracer.services.derived_metrics.storage import FIELD_CHECKPOINT

    engine = store.execute(
        "SELECT engine FROM system.tables WHERE database = currentDatabase() AND name = %(table)s",
        {"table": FIELD_CHECKPOINT.name},
    )
    if engine != [("ReplacingMergeTree",)]:
        # A server-local gate cannot fence another replica's inserts or queued
        # replication fetches. Do not claim the tuple byte/part bound until a
        # logical-shard gate plus replication-drain protocol is qualified.
        raise MetricVisibilityError("checkpoint maintenance topology is not qualified")
    if connection.in_atomic_block:
        # A nested atomic block would not make the obligation durable.
        raise MetricVisibilityError("checkpoint maintenance needs a commit boundary")
    deadline = min(
        float("inf") if deadline is None else deadline,
        monotonic() + LAYOUT_WALL_SECONDS,
    )
    group = store.checkpoint_group(project_id)
    shard = store.identity()
    project = apps.get_model("tracer", "Project")._base_manager.get(id=project_id)
    # Reconciliation has its own short transaction/gate; it never sends OPTIMIZE.
    with checkpoint_gate(group, shard, exclusive=True, deadline=deadline):
        reconcile_layout(store, group, shard)
    for part in store.checkpoint_layout(project_id):
        if part.active_parts <= 1:
            continue
        query_id = "metric-checkpoint-layout-" + uuid4().hex
        with transaction.atomic(durable=True):
            intent = _intent(
                organization_id=project.organization_id,
                project_id=project_id,
                scope="history",
                store_payload={
                    "protocol": LAYOUT_PROTOCOL,
                    "kind": "partition",
                    "query_id": query_id,
                    "group": group,
                    "shard": shard,
                    "table": FIELD_CHECKPOINT.name,
                    "partition_id": part.partition_id,
                },
            )
        submitted = False
        try:
            with checkpoint_gate(group, shard, exclusive=True, deadline=deadline):
                # An earlier pending request or write can outlive its PG lock.
                if layout_obligations(group, shard).exclude(id=intent.id).exists():
                    raise MetricVisibilityError(
                        "checkpoint layout reconciliation required"
                    )
                pending = MetricErasureRequest.objects.filter(
                    store_payload__protocol="metric_submission",
                    store_payload__table=FIELD_CHECKPOINT.name,
                    store_payload__shard=shard,
                ).exclude(status="done")
                # Old submissions without group metadata conservatively block
                # every group on their shard until independently drained.
                from django.db.models import Q

                if pending.filter(
                    Q(store_payload__checkpoint_group=group)
                    | Q(store_payload__checkpoint_group__isnull=True)
                ).exists():
                    raise MetricVisibilityError("checkpoint writes are not drained")
                current = {
                    item.partition_id: item
                    for item in store.checkpoint_layout(project_id)
                }.get(part.partition_id)
                if current is None or current.active_parts <= 1:
                    continue
                if current.active_parts > 512 or current.bytes_on_disk > 2 * 1024**3:
                    raise MetricVisibilityError(
                        "checkpoint partition consolidation cap exceeded"
                    )
                seconds = min(LAYOUT_STATEMENT_SECONDS, int(deadline - monotonic()))
                if seconds < 1:
                    raise MetricVisibilityError("checkpoint layout deadline exceeded")
                submitted = True
                store.optimize_checkpoint(part.partition_id, query_id, seconds)
                # The synchronous response drains this one submission; another
                # retry may maintain layout but must not repeat an ambiguous call.
                _completed(intent)
        finally:
            if not submitted:
                _completed(intent)
    if any(part.active_parts > 1 for part in store.checkpoint_layout(project_id)):
        # A colliding project may have written after a gate was released. This is
        # a measurement-point qualification, not a permanent physical invariant.
        raise MetricVisibilityError("committed checkpoint layout is pending")
