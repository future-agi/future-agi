"""Primary-backed Datadog delivery ownership, snapshots and checkpoints."""

from contextlib import contextmanager
from datetime import UTC, datetime

import structlog
from django.db.models import Case, F, Q, Value, When

logger = structlog.get_logger(__name__)
_DATADOG_METRIC_MAX_AGE_SECONDS = 59 * 60


class _DatadogExportOwnershipLost(RuntimeError):
    """A stale exporter must not write over a newer owner's delivery progress."""


@contextmanager
def _datadog_export_owner(connection):
    """Serialize a destination without holding delivery checkpoints uncommitted.

    A separate transaction pins the advisory lock even with transaction-pooling
    PgBouncer. Normal ORM writes still commit each acknowledged batch. A durable
    token fences those writes if the lock connection dies and another worker
    takes over; the old worker can never clear the new worker's snapshot.
    """
    import uuid

    from django.db import connections, transaction
    from django.db.models.expressions import RawSQL

    from integrations.models import ConnectionStatus, IntegrationConnection

    token = str(uuid.uuid4())
    lock_db = connections["default"].copy()
    try:
        lock_db.set_autocommit(False)
        with lock_db.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                [f"datadog_export:{connection.pk}"],
            )
            acquired = cursor.fetchone()[0]
        if not acquired:
            logger.info(
                "datadog_export_already_running", connection_id=str(connection.pk)
            )
            yield False
            return

        with transaction.atomic():
            # Refresh only after acquiring ownership: callers may hold stale objects.
            connection.refresh_from_db(
                from_queryset=IntegrationConnection.no_workspace_objects.using(
                    "default"
                ).select_for_update()
            )
            syncable = not connection.deleted and connection.status in (
                ConnectionStatus.ACTIVE,
                ConnectionStatus.SYNCING,
                ConnectionStatus.BACKFILLING,
            )
            if syncable:
                connection.sync_cursor["datadog_export_owner"] = token
                IntegrationConnection.no_workspace_objects.using("default").filter(
                    pk=connection.pk
                ).update(sync_cursor=connection.sync_cursor)
        if not syncable:
            yield False
            return
        connection._datadog_export_owner = token
        connection._datadog_export_lock = lock_db
        try:
            yield True
        except _DatadogExportOwnershipLost:
            # Fail closed. In particular, do not run the generic error handler's
            # connection.save() after another owner has replaced our token.
            logger.warning(
                "datadog_export_ownership_lost",
                connection_id=str(connection.pk),
                exc_info=True,
            )
        finally:
            IntegrationConnection.no_workspace_objects.using("default").filter(
                pk=connection.pk, sync_cursor__datadog_export_owner=token
            ).update(
                sync_cursor=RawSQL("sync_cursor - %s", ["datadog_export_owner"]),
                # Restore only our own abandoned SYNCING state, never a pause or
                # a successor's status. Hard process death is recovered by polling.
                status=Case(
                    When(
                        status=ConnectionStatus.SYNCING,
                        then=Value(ConnectionStatus.ACTIVE),
                    ),
                    default=F("status"),
                ),
            )
            del connection._datadog_export_owner
            del connection._datadog_export_lock
    finally:
        # Closing rolls back the otherwise read-only transaction and releases the
        # lock on success, failure or cancellation. Never return a session lock to
        # PgBouncer's pool.
        lock_db.close()


def _check_datadog_delivery_owner(connection):
    """Fail closed immediately before a sink call, not only after its response."""
    from django.db import DatabaseError, InterfaceError

    from integrations.models import ConnectionStatus, IntegrationConnection

    if (
        not IntegrationConnection.no_workspace_objects.using("default")
        .filter(
            pk=connection.pk,
            deleted=False,
            status__in=(
                ConnectionStatus.ACTIVE,
                ConnectionStatus.SYNCING,
                ConnectionStatus.BACKFILLING,
            ),
            sync_cursor__datadog_export_owner=connection._datadog_export_owner,
        )
        .exists()
    ):
        raise _DatadogExportOwnershipLost()

    lock_db = connection._datadog_export_lock
    # Do not let cursor() reconnect a closed wrapper: the new session would not
    # own the transaction-scoped advisory lock. A dead but still attached socket
    # must also fail, even if no successor has replaced the durable token yet.
    if lock_db.connection is None or lock_db.get_autocommit():
        raise _DatadogExportOwnershipLost()
    try:
        with lock_db.cursor() as cursor:
            cursor.execute("SELECT 1")
    except (DatabaseError, InterfaceError) as exc:
        raise _DatadogExportOwnershipLost() from exc


def _save_export_progress(connection, update_fields):
    """Fence each Datadog state write, including snapshot creation and completion."""
    from django.db import transaction

    from integrations.models import ConnectionStatus, IntegrationConnection

    if connection.platform != "datadog":
        connection.save(update_fields=update_fields)
        return
    with transaction.atomic():
        owner = (
            IntegrationConnection.no_workspace_objects.using("default")
            .select_for_update()
            .filter(
                pk=connection.pk,
                deleted=False,
                sync_cursor__datadog_export_owner=connection._datadog_export_owner,
            )
            .only("id", "status")
            .first()
        )
        if owner is None:
            raise _DatadogExportOwnershipLost()
        if owner.status == ConnectionStatus.PAUSED:
            # An accepted batch still needs its checkpoint when the user pauses
            # in flight. Preserve the pause; the next pre-send check stops work.
            update_fields = [
                field
                for field in update_fields
                if field not in {"status", "status_message"}
            ]
        connection.save(using="default", update_fields=update_fields)


def _export_datadog(connection, credentials, service, sync_from, sync_to):
    """Export a durable snapshot of previously unseen, committed request logs.

    created_at is not commit time. Retain the initial backfill floor and exclude
    per-connection receipts instead of advancing a timestamp visibility cutoff.
    Snapshot membership also keeps log delivery and metric accounting identical
    when ingestion commits during an export. HTTP acceptance and saving progress
    are not atomic; a lost response/crash can still replay an unacknowledged send.
    """
    import uuid

    from django.db import transaction

    from agentcc.models import AgentccRequestLog
    from integrations.models import DatadogExportReceipt
    from integrations.services.datadog_snapshot import snapshot_request_ids

    BATCH_SIZE = 500

    cursor = connection.sync_cursor.get("datadog_export")
    if not cursor:
        cursor = {
            "export_id": str(uuid.uuid4()),
            "logs_after": None,
            "sync_from": sync_from.isoformat(),
            "sync_to": sync_to.isoformat(),
            "logs_complete": False,
            "metrics_complete": False,
        }
        # This floor is fixed even when the last completed checkpoint advances.
        # Otherwise an insert started before that checkpoint can commit too late
        # to ever be seen. Do not expire receipts while source rows still exist.
        floor = connection.sync_cursor.get("datadog_export_from", sync_from.isoformat())
        # Bound discovery before receipt lookup; cursor-disabled PgBouncer
        # connections must never buffer an entire history in a DB-API cursor.
        with transaction.atomic():
            ids, scan = snapshot_request_ids(
                connection, datetime.fromisoformat(floor), sync_to
            )
            DatadogExportReceipt.objects.using("default").bulk_create(
                [
                    DatadogExportReceipt(
                        connection_id=connection.pk,
                        request_log_id=pk,
                        export_id=cursor["export_id"],
                    )
                    for pk in ids
                ],
                batch_size=BATCH_SIZE,
            )
            connection.sync_cursor["datadog_scan"] = scan
            connection.sync_cursor["datadog_export_from"] = floor
            connection.sync_cursor["datadog_export"] = cursor
            _save_export_progress(connection, ["sync_cursor", "updated_at"])

    receipts = DatadogExportReceipt.objects.using("default").filter(
        connection=connection, export_id=cursor["export_id"]
    )
    # Both phases use exactly this frozen membership, never a live time query.
    qs = (
        AgentccRequestLog.no_workspace_objects.using("default")
        .filter(pk__in=receipts.values("request_log_id"))
        .order_by("created_at", "id")
    )

    # Freeze values, not just membership. Request logs may be updated or
    # retained away between attempts; retries must send byte-equivalent metrics.
    if "metrics_series" not in cursor:
        cursor["metrics_series"] = _metric_series(qs, connection, sync_to)
        _check_datadog_delivery_owner(connection)
        _save_export_progress(connection, ["sync_cursor", "updated_at"])

    total_logs_sent = 0
    while not cursor["logs_complete"]:
        remaining = qs
        if cursor["logs_after"]:
            after = cursor["logs_after"]
            after_time = datetime.fromisoformat(after["created_at"])
            remaining = remaining.filter(
                Q(created_at__gt=after_time)
                | Q(created_at=after_time, id__gt=after["id"])
            )
        batch = list(remaining[:BATCH_SIZE])
        if not batch:
            cursor["logs_complete"] = True
            _save_export_progress(connection, ["sync_cursor", "updated_at"])
            break

        dd_logs = []
        for log in batch:
            tags = [
                f"model:{log.model}",
                f"provider:{log.provider}",
                f"status_code:{log.status_code}",
            ]
            if log.is_error:
                tags.append("error:true")
            if log.cache_hit:
                tags.append("cache:hit")
            if log.guardrail_triggered:
                tags.append("guardrail:triggered")
            if log.routing_strategy:
                tags.append(f"routing:{log.routing_strategy}")

            status = "error" if log.is_error else "info"
            message = (
                f"[{log.provider}] {log.model} "
                f"status={log.status_code} "
                f"latency={log.latency_ms}ms "
                f"tokens={log.total_tokens} "
                f"cost=${log.cost}"
            )
            if log.error_message:
                message += f" error={log.error_message[:200]}"

            # ISO-8601 date for log timestamp.
            log_date = ""
            if log.started_at:
                log_date = log.started_at.isoformat()
            elif log.created_at:
                log_date = log.created_at.isoformat()

            dd_log = {
                "message": message,
                "ddtags": ",".join(tags),
                "status": status,
                "date": log_date,
                "attributes": {
                    "request_id": log.request_id,
                    "model": log.model,
                    "provider": log.provider,
                    "resolved_model": log.resolved_model,
                    "latency_ms": log.latency_ms,
                    "input_tokens": log.input_tokens,
                    "output_tokens": log.output_tokens,
                    "total_tokens": log.total_tokens,
                    "cost": float(log.cost) if log.cost else 0,
                    "status_code": log.status_code,
                    "is_stream": log.is_stream,
                    "is_error": log.is_error,
                    "cache_hit": log.cache_hit,
                    "fallback_used": log.fallback_used,
                    "guardrail_triggered": log.guardrail_triggered,
                    "started_at": log.started_at.isoformat() if log.started_at else "",
                },
            }
            dd_logs.append(dd_log)

        _check_datadog_delivery_owner(connection)
        result = service.export_logs(credentials, dd_logs)
        if result.get("sent") != len(dd_logs):
            raise RuntimeError("Datadog did not acknowledge the complete log batch")
        total_logs_sent += len(dd_logs)
        cursor["logs_after"] = {
            "created_at": batch[-1].created_at.isoformat(),
            "id": str(batch[-1].id),
        }
        _save_export_progress(connection, ["sync_cursor", "updated_at"])

        logger.debug(
            "datadog_batch_sent",
            batch_size=len(dd_logs),
            total_sent=total_logs_sent,
        )

    # Count each snapshot member once, including logs acknowledged before retry.
    metrics_sent = 0
    if cursor["metrics_complete"]:
        return {
            "logs_sent": total_logs_sent,
            "metrics_sent": 0,
            "metrics_dropped": cursor.get("metrics_dropped"),
        }
    series = cursor["metrics_series"]
    ts = int(sync_to.timestamp())

    # Datadog rejects points older than one hour. Do not keep retrying an
    # expired snapshot or move its counts into a later interval. Drop only its
    # metrics, with the exact values retained in the durable SyncLog audit. The
    # one-minute margin covers request transit near the intake boundary.
    if series and datetime.now(UTC).timestamp() - ts >= _DATADOG_METRIC_MAX_AGE_SECONDS:
        cursor["metrics_dropped"] = {
            "reason": "datadog_intake_age_exceeded",
            "export_id": cursor["export_id"],
            "dropped_at": datetime.now(UTC).isoformat(),
            "sync_from": cursor["sync_from"],
            "sync_to": cursor["sync_to"],
            "series": series,
        }
        logger.warning(
            "datadog_metrics_expired",
            connection_id=str(connection.pk),
            export_id=cursor["export_id"],
            series_dropped=len(series),
            snapshot_timestamp=ts,
        )
    elif series:
        _check_datadog_delivery_owner(connection)
        result = service.export_metrics(credentials, series)
        if result.get("sent") != len(series):
            raise RuntimeError("Datadog did not acknowledge the complete metric batch")
        metrics_sent = len(series)
    cursor["metrics_complete"] = True
    _save_export_progress(connection, ["sync_cursor", "updated_at"])

    return {
        "logs_sent": total_logs_sent,
        "metrics_sent": metrics_sent,
        "metrics_dropped": cursor.get("metrics_dropped"),
    }


def _metric_series(qs, connection, sync_to):
    from django.db.models import Avg, Count, Sum

    agg = qs.aggregate(
        total_requests=Count("id"),
        total_errors=Count("id", filter=Q(is_error=True)),
        avg_latency=Avg("latency_ms"),
        total_input_tokens=Sum("input_tokens"),
        total_output_tokens=Sum("output_tokens"),
        total_cost=Sum("cost"),
    )

    ts = int(sync_to.timestamp())
    series = []
    metric_prefix = "agentcc.gateway"

    if agg["total_requests"]:
        series.append(
            {
                "metric": f"{metric_prefix}.requests",
                "type": 1,  # count
                "points": [{"timestamp": ts, "value": agg["total_requests"]}],
                "tags": [f"org:{connection.organization_id}"],
            }
        )
    if agg["total_errors"]:
        series.append(
            {
                "metric": f"{metric_prefix}.errors",
                "type": 1,
                "points": [{"timestamp": ts, "value": agg["total_errors"]}],
                "tags": [f"org:{connection.organization_id}"],
            }
        )
    if agg["avg_latency"] is not None:
        series.append(
            {
                "metric": f"{metric_prefix}.latency_ms",
                "type": 3,  # gauge
                "points": [{"timestamp": ts, "value": float(agg["avg_latency"])}],
                "tags": [f"org:{connection.organization_id}"],
            }
        )
    if agg["total_input_tokens"]:
        series.append(
            {
                "metric": f"{metric_prefix}.input_tokens",
                "type": 1,
                "points": [{"timestamp": ts, "value": agg["total_input_tokens"]}],
                "tags": [f"org:{connection.organization_id}"],
            }
        )
    if agg["total_output_tokens"]:
        series.append(
            {
                "metric": f"{metric_prefix}.output_tokens",
                "type": 1,
                "points": [{"timestamp": ts, "value": agg["total_output_tokens"]}],
                "tags": [f"org:{connection.organization_id}"],
            }
        )
    if agg["total_cost"]:
        series.append(
            {
                "metric": f"{metric_prefix}.cost",
                "type": 1,
                "points": [{"timestamp": ts, "value": float(agg["total_cost"])}],
                "tags": [f"org:{connection.organization_id}"],
            }
        )

    return series
