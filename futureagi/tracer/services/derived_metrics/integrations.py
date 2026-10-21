"""Adapters into existing dashboard rendering and monitor notification delivery."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from django.db import transaction
from django.db.models import F

from tracer.models.derived_metric import DerivedMetric, DerivedMetricRun
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
            results = read_results(run)["results"]
            if results:
                points = [
                    {
                        "timestamp": run.window_end.isoformat(),
                        "value": results[0]["value"],
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


def notify_run_monitors(run: DerivedMetricRun) -> None:
    """Use the existing alert delivery path, once per committed run per monitor."""
    from tracer.models.monitor import UserAlertMonitor
    from tracer.utils.monitor import _check_static_threshold

    metric = run.definition.metric
    if run.definition.version != metric.current_version:
        return
    values = read_results(run)["results"]
    if not values or values[0]["value"] is None:
        return
    ids = UserAlertMonitor.objects.filter(
        metric_type="derived_metric",
        metric=str(metric.id),
        workspace_id=metric.workspace_id,
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        is_mute=False,
        deleted=False,
    ).values_list("id", flat=True)
    for monitor_id in ids.iterator(chunk_size=100):
        with transaction.atomic():
            monitor = UserAlertMonitor.objects.select_for_update().get(pk=monitor_id)
            if monitor.last_derived_run_id == run.id or monitor.is_mute:
                continue
            DerivedMonitorQueryBuilder(
                monitor
            )  # Reauthorize and validate before delivery.
            _check_static_threshold(
                monitor, values[0]["value"], run.window_start, run.window_end
            )
            monitor.last_derived_run_id = run.id
            monitor.save(update_fields=["last_derived_run_id"])


def process_derived_monitor(monitor, now) -> None:
    """A scheduled check consumes the newest run; it does not rescan telemetry."""
    from datetime import timedelta

    DerivedMonitorQueryBuilder(monitor)
    run = (
        DerivedMetricRun.objects.filter(
            definition__metric_id=monitor.metric,
            definition__version=F("definition__metric__current_version"),
            result_id__isnull=False,
            window_end__gt=now - timedelta(minutes=monitor.alert_frequency),
            window_end__lte=now,
        )
        .select_related("definition__metric__project", "definition__metric__workspace")
        .order_by("-as_of", "-id")
        .first()
    )
    if run:
        notify_run_monitors(run)
