import time
from datetime import UTC, datetime, timedelta

import structlog
from django.db.models import F, Q

from integrations.models.integration_connection import EXPORT_PLATFORMS
from integrations.services.export_failures import (
    describe_export_failure,
    exception_fields,
)
from tfc.temporal.drop_in.decorator import temporal_activity
from tracer.utils.langfuse_upsert import upsert_langfuse_trace

logger = structlog.get_logger(__name__)

# Datadog's log intake accepts a log dated up to 18 hours in the past and
# drops older ones while answering 2xx
# (https://docs.datadoghq.com/api/latest/logs/#send-logs), so an export never
# sends one older than that and never counts it as exported.
DATADOG_LOG_MAX_AGE = timedelta(hours=18)
# The window a Datadog sync sends from, measured back from the sync's own
# time: the intake's 18 hours less a minute for the request to reach it.
DATADOG_LOG_WINDOW = DATADOG_LOG_MAX_AGE - timedelta(minutes=1)


@temporal_activity(time_limit=120, queue="tasks_s")
def poll_active_integrations():
    """Dispatch a sync for every connection that is due: an active one once
    its sync interval has passed, one in Error once its retry delay has passed
    (integrations/services/sync_retry.py)."""
    from integrations.models import (
        ACTION_ONLY_PLATFORMS,
        ConnectionStatus,
        IntegrationConnection,
    )
    from integrations.services.sync_retry import retry_due

    now = datetime.now(UTC)

    # Slack and Linear receive alerts and issues; they have nothing to sync.
    scheduled = IntegrationConnection.no_workspace_objects.filter(
        deleted=False
    ).exclude(platform__in=sorted(ACTION_ONLY_PLATFORMS))
    connections = scheduled.filter(status=ConnectionStatus.ACTIVE).filter(
        Q(last_synced_at__isnull=True)
        | Q(last_synced_at__lte=now - timedelta(seconds=60))
    )

    def dispatch(conn, **kwargs):
        try:
            sync_integration_connection.delay(str(conn.id), **kwargs)
            return 1
        except Exception as e:
            logger.warning(
                "Failed to dispatch sync for connection",
                connection_id=str(conn.id),
                **exception_fields(e),
            )
            return 0

    dispatched = 0
    for conn in connections:
        # Check if enough time has passed since last sync
        if conn.last_synced_at:
            elapsed = (now - conn.last_synced_at).total_seconds()
            if elapsed < conn.sync_interval_seconds:
                continue
        dispatched += dispatch(conn)

    retried = 0
    for conn in scheduled.filter(status=ConnectionStatus.ERROR):
        if retry_due(conn, now):
            retried += dispatch(conn, retry=True)

    logger.info(
        "poll_active_integrations_complete", dispatched=dispatched, retried=retried
    )


@temporal_activity(time_limit=3600, queue="tasks_l", max_retries=2, retry_delay=60)
def sync_integration_connection(connection_id: str, retry: bool = False):
    """Sync one connection. With `retry`, a connection in Error is synced
    too: the scheduler's retry and Sync now's Retry; a sync that succeeds makes
    it active again."""
    # Ensure service/transformer modules are loaded
    import integrations.services.langfuse_service  # noqa: F401
    import integrations.transformers.langfuse_transformer  # noqa: F401
    from integrations.models import (
        ACTION_ONLY_PLATFORMS,
        ConnectionStatus,
        IntegrationConnection,
        SyncLog,
        SyncStatus,
    )
    from integrations.services.base import get_integration_service
    from integrations.services.credentials import CredentialManager
    from integrations.transformers.base import get_transformer

    try:
        connection = IntegrationConnection.no_workspace_objects.get(
            id=connection_id,
            deleted=False,
        )
    except IntegrationConnection.DoesNotExist:
        logger.warning("Connection not found or deleted", connection_id=connection_id)
        return

    syncable = {
        ConnectionStatus.ACTIVE,
        ConnectionStatus.SYNCING,
        ConnectionStatus.BACKFILLING,
    }
    if retry:
        syncable.add(ConnectionStatus.ERROR)
    if connection.status not in syncable:
        logger.info(
            "Connection not in syncable state",
            connection_id=connection_id,
            status=connection.status,
        )
        return

    # Slack and Linear are action-only destinations. They have no project to
    # import into and stay active for alert and issue delivery instead of
    # entering the sync path.
    if connection.platform in ACTION_ONLY_PLATFORMS:
        return

    # Route push-based export platforms to their own handler.
    if connection.platform in EXPORT_PLATFORMS:
        _sync_export_integration(connection)
        return

    if not connection.project_id:
        logger.warning(
            "Connection project deleted, pausing",
            connection_id=connection_id,
        )
        connection.status = ConnectionStatus.PAUSED
        connection.status_message = "Target project was deleted. Please reconfigure."
        connection.save(update_fields=["status", "status_message", "updated_at"])
        return

    # Set status to syncing
    connection.status = ConnectionStatus.SYNCING
    connection.save(update_fields=["status", "updated_at"])

    now = datetime.now(UTC)
    # For backfill: use backfill_from date if set, otherwise sync from epoch
    # For incremental: use last_synced_at with overlap buffer
    if not connection.last_synced_at:
        sync_from = connection.backfill_from or datetime(2020, 1, 1, tzinfo=UTC)
    else:
        # Overlap buffer: re-scan the last 5 minutes to catch traces that
        # were in-flight during the previous sync (some sources index async,
        # so traces may not appear in the list API immediately).
        # Upsert (update_or_create) makes this idempotent.
        sync_from = connection.last_synced_at - timedelta(minutes=5)
    sync_to = now

    # Create sync log (SyncLog extends BaseModel but has no org/workspace fields)
    sync_log = SyncLog.objects.create(
        connection=connection,
        started_at=now,
        sync_from=sync_from,
        sync_to=sync_to,
    )

    total_traces_fetched = 0
    total_traces_created = 0
    total_traces_updated = 0
    total_spans = 0
    total_scores = 0
    failed_trace_ids: list = []  # Track rate-limited traces for retry

    try:
        # Decrypt credentials
        credentials = CredentialManager.decrypt(bytes(connection.encrypted_credentials))
        ca_cert = connection.ca_certificate or None

        # Get service and transformer
        service = get_integration_service(connection.platform)
        transformer = get_transformer(connection.platform)

        page = 1
        has_more = True

        while has_more:
            # Check if project was deleted mid-sync (FK is SET_NULL on delete)
            still_linked = IntegrationConnection.no_workspace_objects.filter(
                id=connection_id, project__isnull=False, deleted=False
            ).exists()
            if not still_linked:
                logger.warning(
                    "project_deleted_during_sync",
                    connection_id=connection_id,
                    traces_synced_before_abort=total_traces_fetched,
                )
                connection.refresh_from_db()
                connection.status = ConnectionStatus.PAUSED
                connection.status_message = (
                    "Target project was deleted during sync. Please reconfigure."
                )
                connection.save(
                    update_fields=["status", "status_message", "updated_at"]
                )
                sync_log.status = (
                    SyncStatus.PARTIAL
                    if total_traces_fetched > 0
                    else SyncStatus.FAILED
                )
                sync_log.completed_at = datetime.now(UTC)
                sync_log.traces_fetched = total_traces_fetched
                sync_log.traces_created = total_traces_created
                sync_log.traces_updated = total_traces_updated
                sync_log.spans_synced = total_spans
                sync_log.scores_synced = total_scores
                sync_log.error_message = "Project deleted during sync"
                sync_log.save()
                return

            # Fetch page of traces (with 429 retry)
            result = _retry_on_429(
                service.fetch_traces,
                host_url=connection.host_url,
                credentials=credentials,
                from_timestamp=sync_from.isoformat(),
                to_timestamp=sync_to.isoformat(),
                page=page,
                limit=100,
                ca_certificate=ca_cert,
                label=f"fetch_traces_page_{page}",
            )

            traces = result.get("traces", [])
            has_more = result.get("has_more", False)
            page = result.get("next_page", page + 1)

            for trace_summary in traces:
                try:
                    # Fetch full trace detail with retry on rate limit
                    full_trace = _retry_on_429(
                        service.fetch_trace_detail,
                        host_url=connection.host_url,
                        credentials=credentials,
                        trace_id=trace_summary["id"],
                        ca_certificate=ca_cert,
                        label=f"trace_detail_{trace_summary['id'][:8]}",
                    )

                    # Transform and upsert
                    created, spans_count, scores_count = upsert_langfuse_trace(
                        assembled_trace=full_trace,
                        transformer=transformer,
                        project_id=str(connection.project_id),
                        org=connection.organization,
                        workspace=connection.workspace,
                        org_id=connection.organization_id,
                    )

                    total_traces_fetched += 1
                    if created:
                        total_traces_created += 1
                    else:
                        total_traces_updated += 1
                    total_spans += spans_count
                    total_scores += scores_count

                except Exception as e:
                    failed_trace_ids.append(trace_summary["id"])
                    logger.warning(
                        "Failed to sync individual trace",
                        connection_id=connection_id,
                        trace_id=trace_summary.get("id"),
                        **exception_fields(e),
                    )

                # Rate limit courtesy: 500ms between trace detail fetches
                time.sleep(0.5)

            # Rate limit courtesy: 1s between pages
            if has_more:
                time.sleep(1)

        # Retry failed traces with longer backoff (rate limits should have reset)
        if failed_trace_ids:
            logger.info(
                "retrying_failed_traces",
                connection_id=connection_id,
                count=len(failed_trace_ids),
            )
            time.sleep(5)  # Wait for rate limit window to reset
            for trace_id in failed_trace_ids:
                try:
                    full_trace = _retry_on_429(
                        service.fetch_trace_detail,
                        host_url=connection.host_url,
                        credentials=credentials,
                        trace_id=trace_id,
                        ca_certificate=ca_cert,
                        label=f"retry_{trace_id[:8]}",
                        max_retries=5,
                    )
                    created, spans_count, scores_count = upsert_langfuse_trace(
                        assembled_trace=full_trace,
                        transformer=transformer,
                        project_id=str(connection.project_id),
                        org=connection.organization,
                        workspace=connection.workspace,
                        org_id=connection.organization_id,
                    )
                    total_traces_fetched += 1
                    if created:
                        total_traces_created += 1
                    else:
                        total_traces_updated += 1
                    total_spans += spans_count
                    total_scores += scores_count
                except Exception as e:
                    logger.warning(
                        "retry_also_failed",
                        trace_id=trace_id,
                        **exception_fields(e),
                    )
                time.sleep(1)  # 1s between retries

        # Backfill incomplete traces (detail fetched before observations were ready)
        from tracer.models.observation_span import ObservationSpan as ObsSpan
        from tracer.models.trace import Trace

        _project_id = str(connection.project_id)
        traces_with_spans = (
            ObsSpan.no_workspace_objects.filter(trace__project_id=_project_id)
            .values_list("trace_id", flat=True)
            .distinct()
        )
        incomplete_traces = list(
            Trace.no_workspace_objects.filter(
                project_id=_project_id, external_id__isnull=False
            )
            .exclude(external_id="")
            .exclude(id__in=traces_with_spans)
        )
        if incomplete_traces:
            logger.info(
                "backfilling_incomplete_traces",
                connection_id=connection_id,
                count=len(incomplete_traces),
            )
            # Wait for Langfuse to finish processing observations
            time.sleep(5)
            for inc_trace in incomplete_traces:
                try:
                    full_trace = _retry_on_429(
                        service.fetch_trace_detail,
                        host_url=connection.host_url,
                        credentials=credentials,
                        trace_id=inc_trace.external_id,
                        ca_certificate=ca_cert,
                        label=f"backfill_{inc_trace.external_id[:8]}",
                        max_retries=5,
                    )
                    if full_trace.get("observations"):
                        _, spans_count, scores_count = upsert_langfuse_trace(
                            assembled_trace=full_trace,
                            transformer=transformer,
                            project_id=str(connection.project_id),
                            org=connection.organization,
                            workspace=connection.workspace,
                            org_id=connection.organization_id,
                        )
                        total_spans += spans_count
                        total_scores += scores_count
                    else:
                        logger.info(
                            "backfill_still_empty",
                            trace_id=inc_trace.external_id,
                        )
                    time.sleep(1)
                except Exception as e:
                    logger.warning(
                        "Failed to backfill incomplete trace",
                        trace_id=inc_trace.external_id,
                        **exception_fields(e),
                    )

        # Success: update connection state
        update_fields = ["status", "last_synced_at", "status_message", "updated_at"]
        connection.status = ConnectionStatus.ACTIVE
        connection.last_synced_at = sync_to
        connection.status_message = ""
        # Mark backfill complete if this was a backfill run
        if not connection.backfill_completed:
            connection.backfill_completed = True
            update_fields.append("backfill_completed")
        connection.save(update_fields=update_fields)

        # Atomically update counters
        IntegrationConnection.no_workspace_objects.filter(id=connection_id).update(
            total_traces_synced=F("total_traces_synced")
            + total_traces_created
            + total_traces_updated,
            total_spans_synced=F("total_spans_synced") + total_spans,
            total_scores_synced=F("total_scores_synced") + total_scores,
        )

        # Finalize sync log
        sync_log.status = (
            SyncStatus.SUCCESS if total_traces_fetched > 0 else SyncStatus.NO_NEW_DATA
        )
        sync_log.completed_at = datetime.now(UTC)
        sync_log.traces_fetched = total_traces_fetched
        sync_log.traces_created = total_traces_created
        sync_log.traces_updated = total_traces_updated
        sync_log.spans_synced = total_spans
        sync_log.scores_synced = total_scores
        sync_log.save()

        logger.info(
            "sync_complete",
            connection_id=connection_id,
            traces=total_traces_fetched,
            spans=total_spans,
            scores=total_scores,
        )

    except Exception as e:
        failure = describe_export_failure(connection, e)
        logger.warning(
            "sync_failed",
            connection_id=connection_id,
            traces_ingested_before_failure=total_traces_fetched,
            **exception_fields(e),
        )
        connection.status = (
            ConnectionStatus.ACTIVE if failure.rate_limited else ConnectionStatus.ERROR
        )
        connection.status_message = "" if failure.rate_limited else failure.message
        sync_log.status = (
            SyncStatus.RATE_LIMITED if failure.rate_limited else SyncStatus.FAILED
        )

        # Credit traces that were already ingested before the failure.
        # Data was upserted per-trace, so the DB has them even if sync didn't complete.
        if total_traces_fetched > 0:
            connection.status = ConnectionStatus.ACTIVE
            connection.status_message = (
                f"Partial sync: {total_traces_fetched} traces ingested before error. "
                f"{failure.message}"
            )
            connection.last_synced_at = datetime.now(UTC)
            sync_log.status = SyncStatus.PARTIAL
            IntegrationConnection.no_workspace_objects.filter(id=connection_id).update(
                total_traces_synced=F("total_traces_synced")
                + total_traces_created
                + total_traces_updated,
                total_spans_synced=F("total_spans_synced") + total_spans,
                total_scores_synced=F("total_scores_synced") + total_scores,
            )

        connection.save(
            update_fields=["status", "status_message", "last_synced_at", "updated_at"]
        )

        sync_log.error_message = failure.message
        sync_log.error_details = failure.details(e)
        sync_log.completed_at = datetime.now(UTC)
        sync_log.traces_fetched = total_traces_fetched
        sync_log.traces_created = total_traces_created
        sync_log.traces_updated = total_traces_updated
        sync_log.spans_synced = total_spans
        sync_log.scores_synced = total_scores
        sync_log.save()


def _sync_export_integration(connection):
    """Export Agentcc request logs to a push-based integration (Datadog, PostHog, etc.)."""
    from integrations.models import (
        ConnectionStatus,
        SyncLog,
        SyncStatus,
    )
    from integrations.services.base import get_integration_service
    from integrations.services.credentials import CredentialManager
    from integrations.services.registry import ensure_services_loaded

    ensure_services_loaded()

    connection_id = str(connection.id)

    connection.status = ConnectionStatus.SYNCING
    connection.save(update_fields=["status", "updated_at"])

    now = datetime.now(UTC)

    # Determine time window for log export.
    if not connection.last_synced_at:
        # First sync: the last 24 hours (Datadog: its 18-hour intake window),
        # or from backfill_from.
        recent = (
            DATADOG_LOG_WINDOW
            if connection.platform == "datadog"
            else timedelta(hours=24)
        )
        sync_from = connection.backfill_from or (now - recent)
    else:
        # At-least-once delivery: overlap by 1 minute. Not every target deduplicates.
        sync_from = connection.last_synced_at - timedelta(minutes=1)
    sync_to = now

    sync_log = SyncLog.objects.create(
        connection=connection,
        started_at=now,
        sync_from=sync_from,
        sync_to=sync_to,
    )

    try:
        credentials = CredentialManager.decrypt(bytes(connection.encrypted_credentials))
        service = get_integration_service(connection.platform)

        _platform_exporters = {
            "datadog": _export_datadog,
            "posthog": _export_posthog,
            "mixpanel": _export_mixpanel,
            "cloud_storage": _export_cloud_storage,
            "message_queue": _export_message_queue,
        }

        exporter = _platform_exporters.get(connection.platform)
        if exporter:
            exported = exporter(connection, credentials, service, sync_from, sync_to)
        else:
            logger.warning(
                "export_platform_not_implemented",
                platform=connection.platform,
                connection_id=connection_id,
            )
            exported = {"logs_sent": 0, "metrics_sent": 0}

        logs_sent = exported.get("logs_sent", 0)
        metrics_sent = exported.get("metrics_sent", 0)
        # Request logs in the window the destination does not accept (Datadog's
        # older than 18 hours): not sent, not counted, and said in the sync log.
        logs_skipped = exported.get("logs_skipped", 0)
        skipped_note = exported.get("skipped_note", "")

        # Success: update connection state. The connection counts every
        # successful delivery, including overlap repeats; not unique request IDs.
        update_fields = [
            "status",
            "last_synced_at",
            "status_message",
            "total_traces_synced",
            "updated_at",
        ]
        connection.status = ConnectionStatus.ACTIVE
        connection.last_synced_at = sync_to
        connection.status_message = ""
        connection.total_traces_synced = F("total_traces_synced") + logs_sent
        if not connection.backfill_completed:
            connection.backfill_completed = True
            update_fields.append("backfill_completed")
        connection.save(update_fields=update_fields)
        connection.refresh_from_db(fields=["total_traces_synced"])

        # Use traces_fetched to store total items exported.
        if logs_skipped:
            sync_log.status = SyncStatus.PARTIAL
            sync_log.error_message = skipped_note
        else:
            sync_log.status = (
                SyncStatus.SUCCESS if logs_sent > 0 else SyncStatus.NO_NEW_DATA
            )
        sync_log.completed_at = datetime.now(UTC)
        sync_log.traces_fetched = logs_sent
        sync_log.traces_created = logs_sent
        sync_log.spans_synced = metrics_sent
        sync_log.save()

        logger.info(
            "export_complete",
            connection_id=connection_id,
            platform=connection.platform,
            logs_sent=logs_sent,
            metrics_sent=metrics_sent,
        )

    except Exception as e:
        from integrations.services.export_failures import (
            describe_export_failure,
            raised_at,
        )

        failure = describe_export_failure(connection, e)
        details = failure.details(e)
        # The exception's text and traceback name the address the export
        # called, which can carry credentials (userinfo or a token in its
        # query), so the log line holds what error_details holds and where
        # the exception was raised, never the exception itself.
        logger.warning(
            "export_failed",
            connection_id=connection_id,
            platform=connection.platform,
            kind=details["kind"],
            error_type=details["type"],
            http_status=details["http_status"],
            vendor_code=details["vendor_code"],
            raised_at=raised_at(e),
        )

        if failure.rate_limited:
            # The connection stays active; the next sync sends this window.
            connection.status = ConnectionStatus.ACTIVE
            connection.status_message = ""
            sync_log.status = SyncStatus.RATE_LIMITED
        else:
            connection.status = ConnectionStatus.ERROR
            connection.status_message = failure.message
            sync_log.status = SyncStatus.FAILED

        connection.save(update_fields=["status", "status_message", "updated_at"])
        # The page and Sync History show the sentence. error_details keeps the
        # failure's kind, type and HTTP status or vendor code, never the
        # exception's text, which names the address called.
        sync_log.error_message = failure.message
        sync_log.error_details = details
        sync_log.completed_at = datetime.now(UTC)
        sync_log.save()


def _export_datadog(connection, credentials, service, sync_from, sync_to):
    """Export AgentccRequestLog entries to Datadog as logs + metrics.

    Only request logs inside Datadog's intake window (the last 18 hours) are
    sent, counted and summed into the series; older ones in the sync window
    are reported as not sent."""
    from django.db.models import Avg, Count, Sum

    from agentcc.models import AgentccRequestLog

    BATCH_SIZE = 500

    window_start = max(sync_from, sync_to - DATADOG_LOG_WINDOW)
    skipped = 0
    if window_start > sync_from:
        skipped = _get_export_queryset(connection, sync_from, window_start).count()
        sync_from = window_start

    # Query request logs for this org/workspace in the sync window.
    qs = (
        AgentccRequestLog.no_workspace_objects.filter(
            organization=connection.organization,
            deleted=False,
        )
        .filter(
            Q(started_at__gte=sync_from, started_at__lt=sync_to)
            | Q(
                started_at__isnull=True,
                created_at__gte=sync_from,
                created_at__lt=sync_to,
            )
        )
        .order_by("started_at", "created_at")
    )

    qs = _scope_to_connection_workspace(qs, connection)

    total_logs_sent = 0
    offset = 0

    while True:
        batch = list(qs[offset : offset + BATCH_SIZE])
        if not batch:
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

        result = service.export_logs(credentials, dd_logs)
        total_logs_sent += result.get("sent", 0)
        offset += BATCH_SIZE

        logger.debug(
            "datadog_batch_sent",
            batch_size=len(dd_logs),
            total_sent=total_logs_sent,
        )

    # Export aggregated metrics for the window.
    metrics_sent = 0
    agg = AgentccRequestLog.no_workspace_objects.filter(
        organization=connection.organization,
        deleted=False,
        started_at__gte=sync_from,
        started_at__lt=sync_to,
    )
    agg = _scope_to_connection_workspace(agg, connection)

    agg = agg.aggregate(
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

    if series:
        result = service.export_metrics(credentials, series)
        metrics_sent = result.get("sent", 0)

    exported = {"logs_sent": total_logs_sent, "metrics_sent": metrics_sent}
    if skipped:
        exported["logs_skipped"] = skipped
        many = skipped != 1
        exported["skipped_note"] = (
            f"{skipped} request log{'s' if many else ''} from before Datadog's "
            f"18-hour window {'were' if many else 'was'} not sent: Datadog "
            "accepts logs from the last 18 hours."
        )
    return exported


def _get_export_queryset(connection, sync_from, sync_to):
    """Build the shared AgentccRequestLog queryset for export platforms."""
    from agentcc.models import AgentccRequestLog

    qs = (
        AgentccRequestLog.no_workspace_objects.filter(
            organization=connection.organization,
            deleted=False,
        )
        .filter(
            Q(started_at__gte=sync_from, started_at__lt=sync_to)
            | Q(
                started_at__isnull=True,
                created_at__gte=sync_from,
                created_at__lt=sync_to,
            )
        )
        .order_by("started_at", "created_at")
    )
    return _scope_to_connection_workspace(qs, connection)


def _scope_to_connection_workspace(qs, connection):
    """The request logs Request Logs shows in the connection's workspace.

    The querysets start from no_workspace_objects, so this scope, the
    connection's, is the only one: never the workspace of whatever context the
    sync happens to run in.

    Gateway request logs belong to the organization and carry no workspace, so,
    as on the Request Logs page (tfc.utils.base_viewset.request_workspace_q), a
    connection in the default workspace exports the logs with no workspace, the
    default workspace's and its own; one in another workspace exports that
    workspace's own logs.
    """
    workspace = connection.workspace if connection.workspace_id else None
    if workspace is None:
        return qs
    if workspace.is_default:
        return qs.filter(
            Q(workspace=workspace)
            | Q(
                workspace__is_default=True,
                workspace__organization_id=connection.organization_id,
            )
            | Q(workspace__isnull=True)
        )
    return qs.filter(workspace=workspace)


def _batch_export(qs, transform_fn, send_fn, batch_size=500):
    """Generic batched export loop. Returns total items sent."""
    total_sent = 0
    offset = 0

    while True:
        batch = list(qs[offset : offset + batch_size])
        if not batch:
            break

        payload = [transform_fn(log) for log in batch]
        result = send_fn(payload)
        total_sent += result.get("sent", 0)
        offset += batch_size

    return total_sent


def _export_posthog(connection, credentials, service, sync_from, sync_to):
    """Export AgentccRequestLog entries to PostHog as events."""
    from integrations.schemas import ExportEvent

    qs = _get_export_queryset(connection, sync_from, sync_to)
    total = _batch_export(
        qs,
        lambda log: ExportEvent.from_request_log(log).model_dump(),
        lambda events: service.export_events(connection.host_url, credentials, events),
    )
    return {"logs_sent": total, "metrics_sent": 0}


def _export_mixpanel(connection, credentials, service, sync_from, sync_to):
    """Export AgentccRequestLog entries to Mixpanel as events."""
    from integrations.schemas import ExportEvent

    qs = _get_export_queryset(connection, sync_from, sync_to)
    total = _batch_export(
        qs,
        lambda log: ExportEvent.from_request_log(log).model_dump(),
        lambda events: service.export_events(credentials, events),
    )
    return {"logs_sent": total, "metrics_sent": 0}


def _export_cloud_storage(connection, credentials, service, sync_from, sync_to):
    """Export AgentccRequestLog entries to cloud object storage as gzip JSONL."""
    from integrations.schemas import ExportLogRecord

    qs = _get_export_queryset(connection, sync_from, sync_to)
    total = _batch_export(
        qs,
        lambda log: ExportLogRecord.from_request_log(log).model_dump(),
        lambda logs: service.export_logs(credentials, logs),
    )
    return {"logs_sent": total, "metrics_sent": 0}


def _export_message_queue(connection, credentials, service, sync_from, sync_to):
    """Export AgentccRequestLog entries to a message queue (SQS / Pub/Sub)."""
    from integrations.schemas import ExportLogRecord

    qs = _get_export_queryset(connection, sync_from, sync_to)
    total = _batch_export(
        qs,
        lambda log: ExportLogRecord.from_request_log(log).model_dump(),
        lambda logs: service.publish_logs(credentials, logs),
    )
    return {"logs_sent": total, "metrics_sent": 0}


_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def _retry_on_429(fn, *args, max_retries=3, label="api_call", **kwargs):
    """Call *fn* with exponential backoff on transient HTTP errors (429/502/503/504)."""
    import requests as requests_lib

    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except requests_lib.exceptions.HTTPError as e:
            if (
                e.response is not None
                and e.response.status_code in _RETRYABLE_STATUS_CODES
                and attempt < max_retries
            ):
                wait = 2**attempt  # 1s, 2s, 4s
                logger.info(
                    "retryable_http_error_retrying",
                    label=label,
                    status_code=e.response.status_code,
                    attempt=attempt + 1,
                    wait_seconds=wait,
                )
                time.sleep(wait)
            else:
                raise


@temporal_activity(time_limit=120, queue="tasks_s")
def check_integration_error_alerts():
    """Send email alerts for connections that have been in error state > 1 hour.

    The hour counts from the first failed sync in the connection's current run
    of failures, so the scheduler's retries never postpone the alert."""
    from integrations.models import ConnectionStatus, IntegrationConnection
    from integrations.services.sync_retry import in_error_since

    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

    connections_in_error = IntegrationConnection.no_workspace_objects.filter(
        status=ConnectionStatus.ERROR,
        deleted=False,
    ).filter(
        Q(last_error_notified_at__isnull=True)
        | Q(last_error_notified_at__lte=twenty_four_hours_ago)
    )

    for conn in connections_in_error:
        if in_error_since(conn) > one_hour_ago:
            continue
        try:
            _send_error_notification(conn)
            conn.last_error_notified_at = datetime.now(UTC)
            conn.save(update_fields=["last_error_notified_at"])
        except Exception as e:
            logger.warning(
                "Failed to send error notification",
                connection_id=str(conn.id),
                **exception_fields(e),
            )


def _send_error_notification(connection):
    """Send email notification for a connection in error state."""
    from accounts.models.organization_membership import OrganizationMembership
    from integrations.services.diagnostic_projection import public_status_message
    from tfc.constants.levels import Level
    from tfc.utils.email import email_helper

    # The legacy user.organization FK survives membership removal. Notifications
    # use active membership in this org, including the legacy role fallback.
    memberships = OrganizationMembership.no_workspace_objects.filter(
        organization=connection.organization,
        is_active=True,
        deleted=False,
        user__is_active=True,
    ).select_related("user")
    recipient_emails = sorted(
        {m.user.email for m in memberships if m.level_or_legacy >= Level.ADMIN}
    )
    if not recipient_emails:
        return

    subject = f"Integration Error: {connection.display_name}"

    email_helper(
        subject,
        "integration_error.html",
        {
            "connection_name": connection.display_name,
            "platform": connection.get_platform_display(),
            "error_message": public_status_message(connection) or "Unknown error",
        },
        recipient_emails,
    )


def start_backfill_workflow(connection_id: str):
    """Start a backfill by dispatching the sync activity for the full time range."""
    # For v1, we reuse the sync activity for backfill.
    # A proper Temporal workflow with chunked processing can be added later.
    sync_integration_connection.delay(connection_id)
