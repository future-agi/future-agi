"""Adapters into existing dashboard rendering and monitor notification delivery."""

from __future__ import annotations

import math
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from django.db import transaction
from django.db.models import F, Q

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricMonitorDelivery,
    DerivedMetricRun,
)
from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.queries import SCOPE_SQL, Scope
from tracer.services.derived_metrics.service import read_results
from tracer.utils.workspace_scope import project_queryset_for_request


def dashboard_result(config: dict, workspace) -> dict:
    """A saved run is one window-level observation, not an average of daily ratios."""
    start, end = DashboardQueryBuilder(config).parse_time_range()
    scope_request = SimpleNamespace(
        organization=workspace.organization, workspace=workspace
    )
    projects = project_queryset_for_request(scope_request)
    output = []
    for item in config["metrics"]:
        run = (
            DerivedMetricRun.objects.select_related(
                "definition__metric__project", "definition__metric__workspace"
            )
            .filter(
                pk=item["derived_run_id"],
                definition__metric__workspace=workspace,
                definition__metric__organization=workspace.organization,
                definition__metric__project__in=projects,
                definition__metric__deleted=False,
                result_id__isnull=False,
            )
            .first()
        )
        if run is None or (
            config["project_ids"]
            and str(run.definition.metric.project_id) not in config["project_ids"]
        ):
            raise MetricInputError(
                "The saved metric run is unavailable in this dashboard scope."
            )
        points = []
        if start <= run.window_end <= end:
            results = read_results(run, include_uncertainty=True)["results"]
            if results:
                interval = results[0]["uncertainty"]
                estimable = interval["status"] == "ok"
                points = [
                    {
                        "timestamp": run.window_end.isoformat(),
                        "value": results[0]["value"],
                        "ci_low": interval["ci_low"] if estimable else None,
                        "ci_high": interval["ci_high"] if estimable else None,
                        "ci_reason": "" if estimable else interval["reason"],
                    }
                ]
        output.append(
            {
                "id": item.get("id", str(run.id)),
                "name": item.get("display_name") or run.definition.metric.name,
                "aggregation": "avg",
                "unit": item.get("unit", ""),
                "series": [
                    {
                        "name": item.get("display_name") or run.definition.metric.name,
                        "data": points,
                    }
                ],
                "series_total": 1,
                "series_truncated": False,
                "query_complete": True,
                "query_exact": True,
                "query_sampled": False,
                "query_status": "complete",
                "query_provenance": "materialized_rollup",
            }
        )
    return {
        "metrics": output,
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
        "granularity": config["granularity"],
        "query_complete": True,
        "query_status": "complete",
        "query_exact": True,
        "query_sampled": False,
        "query_provenance": "materialized_rollup",
    }


class DerivedMonitorQueryBuilder:
    """The existing monitor graph protocol over committed stored observations."""

    def __init__(self, monitor):
        from tracer.utils.monitor import MonitorConfigError

        try:
            metric_id = UUID(str(monitor.metric))
        except (ValueError, TypeError) as exc:
            raise MonitorConfigError("Derived metric id is invalid.") from exc
        metric = (
            DerivedMetric.objects.filter(
                pk=metric_id,
                project_id=monitor.project_id,
                organization_id=monitor.organization_id,
                workspace_id=monitor.workspace_id,
                project__deleted=False,
                deleted=False,
            )
            .select_related("project", "workspace")
            .first()
        )
        from tracer.services.derived_metrics.service import _authorized_metric

        if metric is None or not _authorized_metric(metric):
            raise MonitorConfigError(
                "Derived metric is unavailable in this monitor scope."
            )
        if monitor.threshold_type != "static" or monitor.filters:
            raise MonitorConfigError(
                "Stored derived metrics use static thresholds and their frozen run filters."
            )
        self.metric = metric
        self.params = {
            **Scope(
                metric.organization_id, metric.workspace_id, metric.project_id
            ).parameters(),
            "metric_id": str(metric.id),
            "metric_version": metric.current_version,
        }

    def _runs(self, start_time, end_time, *, limit):
        rows = list(
            DerivedMetricRun.objects.filter(
                definition__metric=self.metric,
                definition__version=self.metric.current_version,
                result_id__isnull=False,
                window_end__gt=start_time,
                window_end__lte=end_time,
            )
            .order_by("-as_of", "-id")
            .values_list("id", "result_id")[:limit]
        )
        return tuple((str(run_id), str(result_id)) for run_id, result_id in rows)

    def _source(self) -> str:
        # PostgreSQL supplies a bounded set of committed attempt identities, so
        # even readonly profiles cannot turn this into a scan of every unit rollup.
        return f"""FROM derived_metric_results WHERE {SCOPE_SQL} AND metric_id = %(metric_id)s
            AND (run_id, result_id) IN %(attempts)s AND rollup = 'overall'"""

    def build_metric_value_query(self, metric_type, start_time, end_time):
        # Pick latest first, THEN interpret its null. Never fall back to an older
        # valid number when the newest run has missing data or invalid weights.
        attempts = self._runs(start_time, end_time, limit=1)
        if not attempts:
            return "SELECT CAST(NULL, 'Nullable(Float64)') AS value", {}
        return f"SELECT value {self._source()} LIMIT 1", {
            **self.params,
            "attempts": attempts,
        }

    def build_time_series_query(
        self, metric_type, start_time, end_time, frequency_seconds
    ):
        from tracer.utils.monitor import MonitorConfigError

        attempts = self._runs(start_time, end_time, limit=1001)
        if len(attempts) > 1000:
            raise MonitorConfigError(
                "More than 1000 metric runs match. Choose a smaller graph window."
            )
        if not attempts:
            return (
                "SELECT now() AS timestamp, CAST(NULL, 'Nullable(Float64)') AS value WHERE 0",
                {},
            )
        return (
            f"SELECT window_end AS timestamp, value {self._source()} AND status = 'ok' ORDER BY timestamp",
            {**self.params, "attempts": attempts},
        )


def _superseded_for(monitor_id, run: DerivedMetricRun) -> bool:
    """True when this monitor already evaluated a newer run of the same version.

    "Newer" is the same ``(as_of, id)`` order every derived-monitor read uses. An
    older observation must never step a monitor back: neither a late retry nor a
    recompute of a run the monitor never evaluated may alert after a newer value.
    """
    return (
        DerivedMetricMonitorDelivery.objects.filter(
            monitor_id=monitor_id, run__definition_id=run.definition_id
        )
        .filter(
            Q(run__as_of__gt=run.as_of) | Q(run__as_of=run.as_of, run_id__gt=run.id)
        )
        .exists()
    )


def monitor_comparison_value(monitor, row: dict) -> float | None:
    """Conservative bound for a fixed-window test, not peeking-safe inference."""
    value = row.get("value")
    if value is None or not math.isfinite(value):
        return None
    if getattr(monitor, "alert_when", "point") == "point":
        return value
    interval = row.get("uncertainty") or {}
    low, high = interval.get("ci_low"), interval.get("ci_high")
    if (
        getattr(monitor, "alert_when", None) != "confident"
        or interval.get("status") != "ok"
        or low is None
        or high is None
        or not math.isfinite(low)
        or not math.isfinite(high)
        or low > high
    ):
        return None
    if monitor.threshold_operator == "greater_than":
        return low
    if monitor.threshold_operator == "less_than":
        return high
    return None


def notify_run_monitors(run: DerivedMetricRun, monitor_id=None) -> int:
    """Use the existing alert delivery path, once per committed run per monitor.

    ``monitor_id`` restricts delivery to one monitor (the scheduler retry path).
    Returns how many monitors consumed this run, i.e. committed a ledger row. A
    run whose overall value is missing consumes nothing and returns 0, and so
    does a run that a monitor's newer evaluated run supersedes.
    """
    from tracer.models.monitor import UserAlertMonitor
    from tracer.utils.monitor import _check_static_threshold

    metric = run.definition.metric
    if run.definition.version != metric.current_version:
        return 0
    values = read_results(run, include_uncertainty=True)["results"]
    if not values or values[0]["value"] is None:
        return 0
    ids = UserAlertMonitor.objects.filter(
        metric_type="derived_metric",
        metric=str(metric.id),
        workspace_id=metric.workspace_id,
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        is_mute=False,
        deleted=False,
    ).values_list("id", flat=True)
    if monitor_id is not None:
        ids = ids.filter(pk=monitor_id)
    consumed = 0
    for current_id in ids.iterator(chunk_size=100):
        with transaction.atomic():
            # The monitor lock serializes all deliveries, including different
            # run IDs. The ledger (not the last-run display pointer) is the
            # durable identity, so A -> B -> A remains a no-op for A.
            monitor = (
                UserAlertMonitor.objects.select_for_update()
                .filter(
                    pk=current_id,
                    metric_type="derived_metric",
                    metric=str(metric.id),
                    project_id=metric.project_id,
                    organization_id=metric.organization_id,
                    workspace_id=metric.workspace_id,
                    is_mute=False,
                    deleted=False,
                )
                .first()
            )
            if (
                monitor is None
                or DerivedMetricMonitorDelivery.objects.filter(
                    monitor_id=current_id, run=run
                ).exists()
                or _superseded_for(current_id, run)
            ):
                continue
            current = DerivedMonitorQueryBuilder(monitor)
            if current.metric.current_version != run.definition.version:
                continue
            bound = monitor_comparison_value(monitor, values[0])
            if bound is None:
                continue
            kwargs = (
                {"comparison_value": bound} if monitor.alert_when == "confident" else {}
            )
            alert_type = _check_static_threshold(
                monitor, values[0]["value"], run.window_start, run.window_end, **kwargs
            )
            # A raised delivery error rolls back both the log and this row;
            # retry can proceed. Existing transports retain their best-effort
            # behavior; this ledger does not promise exactly-once email/Slack.
            DerivedMetricMonitorDelivery.objects.create(
                monitor=monitor, run=run, alert_type=alert_type or ""
            )
            monitor.last_derived_run_id = run.id
            monitor.save(update_fields=["last_derived_run_id"])
        consumed += 1
    return consumed


# A notification that failed when its run committed is retried by the monitor
# scheduler for this long after that commit (a recompute commits again and so
# restarts the clock), even when the run's window ended long before the monitor's
# own alert_frequency. Older failures are not replayed: an alert about a window
# that stale would be noise, and the bound keeps the retry set finite.
DELIVERY_RETRY_LOOKBACK = timedelta(hours=48)


def process_derived_monitor(monitor, now) -> None:
    """A scheduled check consumes the newest run; it does not rescan telemetry.

    Only the HEAD is considered: the newest committed run of the current metric
    version whose window has ended, in the ``(as_of, id)`` order every derived
    monitor read uses. It is delivered when this monitor has no
    ``DerivedMetricMonitorDelivery`` row for it and either

    * its window ended inside the monitor's alert window (the original rule), or
    * it was committed while the monitor existed and within
      ``DELIVERY_RETRY_LOOKBACK``: the completion-time notification should have
      reached this monitor, so a missing ledger row means that delivery failed.

    An older run is never delivered once a newer one exists. A newest run whose
    value is missing alerts nothing and does NOT fall back to an older value (the
    same rule as ``build_metric_value_query``). The (monitor, run) ledger keeps
    A -> B -> recompute A a no-op, and a delivery exception leaves no row, so the
    next tick retries the same run. Each tick reads at most one stored result.
    """
    DerivedMonitorQueryBuilder(monitor)
    head = (
        DerivedMetricRun.objects.filter(
            definition__metric_id=monitor.metric,
            definition__version=F("definition__metric__current_version"),
            result_id__isnull=False,
            window_end__lte=now,
        )
        .select_related("definition__metric__project", "definition__metric__workspace")
        .order_by("-as_of", "-id")
        .first()
    )
    if (
        head is None
        or DerivedMetricMonitorDelivery.objects.filter(
            monitor_id=monitor.id, run=head
        ).exists()
    ):
        return
    committed_at = head.completed_at or head.as_of
    in_alert_window = head.window_end > now - timedelta(minutes=monitor.alert_frequency)
    failed_delivery = (
        committed_at >= monitor.created_at
        and committed_at > now - DELIVERY_RETRY_LOOKBACK
    )
    if in_alert_window or failed_delivery:
        notify_run_monitors(head, monitor_id=monitor.id)
