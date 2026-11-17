"""Historical threshold preview: bounded, read-only and save-validation parity."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog


@pytest.fixture
def definition(observe_project):
    return {
        "name": "Preview only",
        "project": str(observe_project.id),
        "metric_type": "count_of_errors",
        "threshold_type": "static",
        "threshold_operator": "greater_than",
        "critical_threshold_value": 5,
        "warning_threshold_value": 2,
        "alert_frequency": 60,
        "filters": {},
    }


@pytest.mark.django_db
class TestThresholdPreviewAPI:
    def test_returns_fires_without_saving(self, auth_client, definition):
        before = (UserAlertMonitor.objects.count(), UserAlertMonitorLog.objects.count())
        result = {"series": [], "fired_buckets": [], "fire_count": 0}
        with patch("tracer.views.monitor.get_threshold_preview", return_value=result) as preview:
            response = auth_client.post("/tracer/user-alerts/preview/", definition, format="json")
        assert response.status_code == 200
        assert response.json()["result"] == result
        assert preview.call_args.args[0].project_id == definition["project"] or str(preview.call_args.args[0].project_id) == definition["project"]
        assert before == (UserAlertMonitor.objects.count(), UserAlertMonitorLog.objects.count())

    @pytest.mark.parametrize("locked_flag", ["server_enforced_readonly", "server_profile_locked"])
    def test_locked_profile_refuses_preview_before_query(self, auth_client, definition, locked_flag):
        client = Mock(server_enforced_readonly=False, server_profile_locked=False)
        setattr(client, locked_flag, True)
        client.execute_read_with_progress.return_value = ([], [], 0, 0, 0)
        builder = Mock()
        builder.build_time_series_query.return_value = ("SELECT series", {})
        with patch(
            "tracer.services.clickhouse.query_service.get_clickhouse_client",
            return_value=client,
        ), patch(
            "tracer.utils.monitor_preview._build_monitor_graph_ch_builder",
            return_value=builder,
        ):
            response = auth_client.post("/tracer/user-alerts/preview/", definition, format="json")
        assert response.status_code == 503
        assert response.json()["code"] == "monitor_preview_unavailable"
        client.execute_read_with_progress.assert_not_called()

    def test_unauthenticated_refused(self, api_client, definition):
        response = api_client.post("/tracer/user-alerts/preview/", definition, format="json")
        assert response.status_code == 401

    @pytest.mark.parametrize("scope", ["organization", "workspace"])
    def test_foreign_project_refused_before_query(self, auth_client, definition, observe_project, organization, user, scope):
        from accounts.models.organization import Organization
        from accounts.models.workspace import Workspace
        from tracer.models.project import Project

        org = Organization.objects.create(name="Other preview org") if scope == "organization" else organization
        workspace = Workspace.no_workspace_objects.create(
            name="Other preview workspace", organization=org, created_by=user, is_default=False,
        )
        project = Project.no_workspace_objects.create(
            name="Other preview project", organization=org, workspace=workspace,
            model_type=observe_project.model_type, trace_type="observe", metadata={},
        )
        definition["project"] = str(project.id)
        with patch("tracer.views.monitor.get_threshold_preview") as preview:
            response = auth_client.post("/tracer/user-alerts/preview/", definition, format="json")
        assert response.status_code == 400
        preview.assert_not_called()

    @pytest.mark.parametrize("invalid", [
        {"alert_frequency": 0}, {"metric_type": "invalid"},
        {"critical_threshold_value": None}, {"filters": ["invalid"]},
        {"critical_threshold_value": 1, "warning_threshold_value": 2},
    ])
    def test_validation_matches_save(self, auth_client, definition, invalid):
        definition.update(invalid)
        with patch("tfc.ee_gating.check_ee_can_create"), patch("tracer.views.monitor.get_threshold_preview") as preview:
            saved = auth_client.post("/tracer/user-alerts/", definition, format="json")
            response = auth_client.post("/tracer/user-alerts/preview/", definition, format="json")
        assert response.status_code == saved.status_code == 400
        assert response.json() == saved.json()
        preview.assert_not_called()

    def test_edit_preview_accepts_unchanged_name(self, auth_client, definition, observe_project, organization, workspace):
        monitor = UserAlertMonitor.objects.create(
            **{key: value for key, value in definition.items() if key != "project"},
            project=observe_project, organization=organization, workspace=workspace,
        )
        with patch("tracer.views.monitor.get_threshold_preview", return_value={"fire_count": 0}):
            response = auth_client.post(f"/tracer/user-alerts/{monitor.id}/preview/", definition, format="json")
        assert response.status_code == 200

    @pytest.mark.parametrize("change_threshold", [False, True])
    def test_edit_preview_matches_saved_rule_without_mutating_saved_monitor(
        self, auth_client, definition, observe_project, organization, workspace,
        change_threshold,
    ):
        definition.update(threshold_type="percentage_change", auto_threshold_time_window=60)
        monitor = UserAlertMonitor.objects.create(
            **{key: value for key, value in definition.items() if key != "project"},
            project=observe_project, organization=organization, workspace=workspace,
            threshold_rule="earlier",
        )
        if change_threshold:
            definition["critical_threshold_value"] = 10
        else:
            definition["name"] = "Renamed preview"
        with patch("tracer.views.monitor.get_threshold_preview", return_value={"fire_count": 0}) as preview:
            response = auth_client.post(f"/tracer/user-alerts/{monitor.id}/preview/", definition, format="json")
        assert response.status_code == 200
        assert preview.call_args.args[0].threshold_rule == ("current" if change_threshold else "earlier")
        monitor.refresh_from_db()
        assert monitor.threshold_rule == "earlier"
        assert monitor.critical_threshold_value == 5
        assert monitor.name == "Preview only"


def test_preview_computes_strict_fires_and_critical_precedence():
    from tracer.utils.monitor_preview import get_threshold_preview

    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    monitor = UserAlertMonitor(project_id="00000000-0000-0000-0000-000000000001", metric_type="count_of_errors", threshold_type="static", threshold_operator="greater_than", critical_threshold_value=5, warning_threshold_value=2, alert_frequency=60)
    builder = Mock()
    builder.build_time_series_query.return_value = ("SELECT bounded_series", {})
    rows = [{"timestamp": now - timedelta(hours=5-i), "value": value} for i, value in enumerate([0, 2, 3, 5, 6])]
    with patch("tracer.utils.monitor_preview._build_monitor_graph_ch_builder", return_value=builder), patch("tracer.utils.monitor_preview.AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=rows)
        result = get_threshold_preview(monitor, now=now)
    assert result["fire_count"] == 3
    assert [point["status"] for point in result["series"][-5:]] == ["healthy", "healthy", "warning", "warning", "critical"]
    assert len(result["fired_buckets"]) == 3
    assert result["series"][-1]["critical_threshold"] == 5


def test_preview_bounds_window_buckets_and_query_rows():
    from tracer.utils.monitor_preview import MAX_PREVIEW_BUCKETS, get_threshold_preview

    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    monitor = UserAlertMonitor(project_id="00000000-0000-0000-0000-000000000001", metric_type="count_of_errors", threshold_type="static", threshold_operator="greater_than", critical_threshold_value=1, alert_frequency=5, auto_threshold_time_window=999999)
    builder = Mock()
    builder.build_time_series_query.return_value = ("SELECT bounded_series", {})
    with patch("tracer.utils.monitor_preview._build_monitor_graph_ch_builder", return_value=builder), patch("tracer.utils.monitor_preview.AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=[])
        result = get_threshold_preview(monitor, now=now)
    _, start, end, frequency = builder.build_time_series_query.call_args.args
    assert end - start <= timedelta(days=7)
    assert (end - start).total_seconds() / frequency <= MAX_PREVIEW_BUCKETS
    assert result["window_limited"] is True
    kwargs = service.return_value.execute_ch_query.call_args.kwargs
    assert kwargs["settings"]["max_result_rows"] <= MAX_PREVIEW_BUCKETS
    assert kwargs["settings"]["result_overflow_mode"] == "throw"
    assert kwargs["timeout_ms"] > 0


@pytest.mark.parametrize("total_ms", [750, 60_000])
def test_preview_sends_remaining_execution_cap_through_real_service(total_ms):
    from tracer.services.clickhouse.read_budget import ReadDeadline
    from tracer.utils.monitor_preview import (
        MONITOR_GRAPH_CH_TIMEOUT_CAP_MS,
        get_threshold_preview,
    )

    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    monitor = UserAlertMonitor(
        metric_type="span_response_time", threshold_type="percentage_change",
        threshold_operator="greater_than", critical_threshold_value=0,
        warning_threshold_value=None, alert_frequency=60,
        auto_threshold_time_window=60,
    )
    builder = Mock()
    builder.build_time_series_query.return_value = ("SELECT series", {})
    builder.build_historical_stats_query.return_value = ("SELECT stats", {})
    client = Mock(server_enforced_readonly=False, server_profile_locked=False)
    responses = iter([
        ([(now - timedelta(hours=1), 10)], ["timestamp", "value"], 0, 1, 1),
        ([(5, 1)], ["mean", "stddev"], 0, 1, 1),
    ])
    # Keep the real deadline, service and policy; replace only time and I/O.
    with patch.object(ReadDeadline, "elapsed_ms", return_value=0) as elapsed, patch(
        "tracer.services.clickhouse.query_service.get_clickhouse_client",
        return_value=client,
    ), patch(
        "tracer.utils.monitor_preview._build_monitor_graph_ch_builder",
        return_value=builder,
    ):
        def execute(*args, **kwargs):
            elapsed.return_value = total_ms - 125
            return next(responses)

        client.execute_read_with_progress.side_effect = execute
        result = get_threshold_preview(
            monitor, now=now, deadline=ReadDeadline.start(total_ms)
        )

    assert result["fire_count"] == 1
    calls = client.execute_read_with_progress.call_args_list
    assert len(calls) == 2
    assert [call.kwargs["settings"]["max_execution_time"] for call in calls] == [
        min(total_ms, MONITOR_GRAPH_CH_TIMEOUT_CAP_MS) / 1000,
        min(125, MONITOR_GRAPH_CH_TIMEOUT_CAP_MS) / 1000,
    ]
    assert all(call.kwargs["settings"]["timeout_overflow_mode"] == "throw" for call in calls)


@pytest.mark.parametrize(
    ("rule", "expected_thresholds"),
    [("earlier", [6, 21]), ("current", [5, 20])],
)
def test_percentage_preview_uses_each_buckets_own_historical_band(rule, expected_thresholds):
    from tracer.utils.monitor_preview import get_threshold_preview

    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    monitor = UserAlertMonitor(project_id="00000000-0000-0000-0000-000000000001", metric_type="span_response_time", threshold_type="percentage_change", threshold_operator="greater_than", critical_threshold_value=0, warning_threshold_value=None, alert_frequency=60, auto_threshold_time_window=60, threshold_rule=rule)
    builder = Mock()
    builder.build_time_series_query.return_value = ("SELECT series", {})
    history_query = builder.build_earlier_band_query if rule == "earlier" else builder.build_historical_stats_query
    history_query.return_value = ("SELECT stats", {})
    rows = [{"timestamp": now-timedelta(hours=2), "value": 10}, {"timestamp": now-timedelta(hours=1), "value": 10}]
    with patch("tracer.utils.monitor_preview._build_monitor_graph_ch_builder", return_value=builder), patch("tracer.utils.monitor_preview.AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.side_effect = [SimpleNamespace(data=rows), SimpleNamespace(data=[{"mean": 5, "stddev": 1}]), SimpleNamespace(data=[{"mean": 20, "stddev": 1}])]
        result = get_threshold_preview(monitor, now=now)
    assert result["fire_count"] == 1
    assert [p["critical_threshold"] for p in result["series"]] == expected_thresholds
    assert [c.args[2] for c in history_query.call_args_list] == [row["timestamp"] for row in rows]
    assert all(
        call.kwargs == ({"interval_kind": "hour"} if rule == "earlier" else {"periods": 1.0})
        for call in history_query.call_args_list
    )


@pytest.mark.parametrize("rule", ["earlier", "current"])
def test_daily_preview_history_precedes_trailing_day(rule):
    from tracer.utils.monitor_preview import get_threshold_preview

    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    monitor = UserAlertMonitor(
        metric_type="daily_tokens_spent", threshold_type="percentage_change",
        threshold_operator="greater_than", critical_threshold_value=20,
        warning_threshold_value=None, alert_frequency=60,
        auto_threshold_time_window=60, threshold_rule=rule,
    )
    builder = Mock()
    builder.build_metric_value_query.return_value = ("SELECT metric", {})
    history_query = builder.build_earlier_band_query if rule == "earlier" else builder.build_historical_stats_query
    history_query.return_value = ("SELECT history", {})

    def query_result(query, params, **kwargs):
        return SimpleNamespace(data=[{"value": 10}] if query == "SELECT metric" else [
            {"mean": 5, "stddev": 1 if rule == "earlier" else None}
        ])

    with patch("tracer.utils.monitor_preview._build_monitor_graph_ch_builder", return_value=builder), patch("tracer.utils.monitor_preview.AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.side_effect = query_result
        result = get_threshold_preview(monitor, now=now)
    assert result["fire_count"] > 0
    assert history_query.call_args.args[2] == now - timedelta(days=1)
    assert all(call.args[1] >= now - timedelta(days=7) for call in history_query.call_args_list)
    assert history_query.call_args.kwargs == (
        {"interval_kind": "day"} if rule == "earlier" else {"periods": 1 / 24}
    )
    assert result["series"][-1]["critical_threshold"] == (6.2 if rule == "earlier" else 6)


def test_monthly_preview_does_not_query_outside_seven_day_budget():
    from tracer.utils.monitor_preview import get_threshold_preview

    monitor = UserAlertMonitor(metric_type="monthly_tokens_spent", threshold_type="static", threshold_operator="greater_than", critical_threshold_value=1)
    with patch("tracer.utils.monitor_preview.AnalyticsQueryService") as service:
        result = get_threshold_preview(monitor)
    assert result["fire_count"] == 0
    assert result["series"] == []
    assert result["reason"] == "interval_exceeds_window"
    service.assert_not_called()


@pytest.mark.parametrize(
    ("metric_type", "rows", "expected_fires", "expected_evaluated"),
    [
        ("count_of_errors", [], 168, 168),
        ("token_usage", [{"value": 0}], 0, 0),
        ("span_response_time", [{"value": None}], 0, 0),
        ("span_response_time", [{"value": float("nan")}], 0, 0),
    ],
)
def test_empty_interval_semantics_match_evaluator(
    metric_type, rows, expected_fires, expected_evaluated
):
    from tracer.utils.monitor_preview import get_threshold_preview

    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    monitor = UserAlertMonitor(
        metric_type=metric_type, threshold_type="static",
        threshold_operator="less_than", critical_threshold_value=1,
        alert_frequency=60,
    )
    rows = [{**row, "timestamp": now - timedelta(hours=1)} for row in rows]
    builder = Mock()
    builder.build_time_series_query.return_value = ("SELECT series", {})
    with patch(
        "tracer.utils.monitor_preview._build_monitor_graph_ch_builder",
        return_value=builder,
    ), patch("tracer.utils.monitor_preview.AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=rows)
        result = get_threshold_preview(monitor, now=now)
    assert result["fire_count"] == expected_fires
    assert result["evaluated_count"] == expected_evaluated
    assert len(result["series"]) <= 168


def test_deadline_failure_does_not_return_a_partial_fire_count():
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
    from tracer.utils.monitor_graphs import MonitorGraphUnavailable
    from tracer.utils.monitor_preview import get_threshold_preview

    monitor = UserAlertMonitor(
        metric_type="count_of_errors", threshold_type="static",
        threshold_operator="greater_than", critical_threshold_value=1,
    )
    builder = Mock()
    builder.build_time_series_query.return_value = ("SELECT series", {})
    with patch(
        "tracer.utils.monitor_preview._build_monitor_graph_ch_builder",
        return_value=builder,
    ), patch("tracer.utils.monitor_preview.AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.side_effect = ReadDeadlineExceeded()
        with pytest.raises(MonitorGraphUnavailable):
            get_threshold_preview(monitor)


def test_oversized_baseline_is_not_read_or_silently_shortened():
    from tracer.utils.monitor_preview import get_threshold_preview

    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    monitor = UserAlertMonitor(
        metric_type="span_response_time", threshold_type="percentage_change",
        threshold_operator="greater_than", critical_threshold_value=1,
        auto_threshold_time_window=999999,
    )
    builder = Mock()
    builder.build_time_series_query.return_value = ("SELECT series", {})
    with patch(
        "tracer.utils.monitor_preview._build_monitor_graph_ch_builder",
        return_value=builder,
    ), patch("tracer.utils.monitor_preview.AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=[{"timestamp": now - timedelta(hours=1), "value": 10}]
        )
        result = get_threshold_preview(monitor, now=now)
    builder.build_historical_stats_query.assert_not_called()
    assert result["series"][0]["status"] == "insufficient_data"
    assert result["evaluated_count"] == result["fire_count"] == 0
