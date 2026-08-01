from datetime import UTC, datetime, timedelta
from unittest import mock


def _call_trace_field_values(
    *,
    metric_name="final_status",
    metric_type="custom_attribute",
    rows=None,
    error=None,
):
    from model_hub.views import ai_filter

    execute = mock.Mock()
    if error is not None:
        execute.side_effect = error
    else:
        execute.return_value = mock.Mock(data=rows or [])

    analytics = mock.Mock(execute_ch_query=execute)
    with (
        mock.patch(
            "tracer.services.clickhouse.client.is_clickhouse_enabled",
            return_value=True,
        ),
        mock.patch(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService",
            return_value=analytics,
        ),
    ):
        values = ai_filter._fetch_trace_field_values(
            ["project-a", "project-b"],
            metric_name,
            metric_type,
        )
    return values, execute


def test_custom_attribute_values_use_bounded_ch25_query(monkeypatch):
    from model_hub.views import ai_filter

    now = datetime(2026, 7, 31, 8, 30, tzinfo=UTC)
    monkeypatch.setattr(ai_filter.timezone, "now", lambda: now)

    values, execute = _call_trace_field_values(
        rows=[
            {"val": "Rechazado"},
            {"val": "Aprobado"},
            {"val": "Rechazado"},
            {"val": ""},
        ]
    )

    assert values == ["Aprobado", "Rechazado"]
    execute.assert_called_once()
    sql = " ".join(execute.call_args.args[0].split())
    params = execute.call_args.args[1]

    assert "SELECT attrs_string[%(attr_key)s] AS val FROM spans" in sql
    assert "PREWHERE project_id IN %(project_ids)s" in sql
    assert "start_time >= %(window_start)s" in sql
    assert "start_time < %(window_end)s" in sql
    assert "WHERE is_deleted = 0" in sql
    assert "mapContains(attrs_string, %(attr_key)s)" in sql
    assert "LIMIT %(sample_limit)s" in sql
    assert "span_attr_str" not in sql
    assert "_peerdb_is_deleted" not in sql
    assert "DISTINCT" not in sql
    assert "ORDER BY" not in sql

    assert params == {
        "project_ids": ("project-a", "project-b"),
        "attr_key": "final_status",
        "window_start": now - timedelta(days=7),
        "window_end": now,
        "sample_limit": 1000,
    }
    assert execute.call_args.kwargs["timeout_ms"] == 750
    assert execute.call_args.kwargs["settings"] == {
        "timeout_overflow_mode": "throw",
        "max_threads": 2,
        "max_memory_usage": 268_435_456,
        "max_bytes_to_read": 1_073_741_824,
        "read_overflow_mode": "throw",
        "max_result_rows": 1000,
        "result_overflow_mode": "throw",
    }


def test_custom_attribute_values_cap_deduplicated_llm_context():
    rows = [{"val": f"value-{index:03d}"} for index in range(120)]
    rows.extend([{"val": "value-000"}, {"val": None}])

    values, _execute = _call_trace_field_values(rows=rows)

    assert len(values) == 100
    assert len(set(values)) == 100
    assert values == sorted(values, key=str.casefold)


def test_custom_attribute_value_failure_is_sanitized(monkeypatch):
    from model_hub.views import ai_filter

    warning = mock.Mock()
    monkeypatch.setattr(ai_filter.logger, "warning", warning)
    raw_error = "Code: 159 DB::Exception secret-project timeout stack"

    values, _execute = _call_trace_field_values(error=RuntimeError(raw_error))

    assert values == []
    warning.assert_called_once_with(
        "smart_filter_values_failed",
        metric_name="final_status",
        metric_type="custom_attribute",
        error_type="RuntimeError",
    )
    assert raw_error not in repr(warning.call_args)


def test_system_metric_values_use_bounded_ch25_query(monkeypatch):
    from model_hub.views import ai_filter

    now = datetime(2026, 7, 31, 8, 30, tzinfo=UTC)
    monkeypatch.setattr(ai_filter.timezone, "now", lambda: now)

    values, execute = _call_trace_field_values(
        metric_name="model",
        metric_type="system_metric",
        rows=[{"val": "gpt-4o"}, {"val": "gpt-4o"}, {"val": "claude"}],
    )

    assert values == ["claude", "gpt-4o"]
    sql = " ".join(execute.call_args.args[0].split())
    params = execute.call_args.args[1]
    assert "SELECT toString(model) AS val FROM spans" in sql
    assert "PREWHERE project_id IN %(project_ids)s" in sql
    assert "start_time >= %(window_start)s" in sql
    assert "start_time < %(window_end)s" in sql
    assert "WHERE is_deleted = 0" in sql
    assert "span_attr_str" not in sql
    assert "_peerdb_is_deleted" not in sql
    assert "DISTINCT" not in sql
    assert "ORDER BY" not in sql
    assert params == {
        "project_ids": ("project-a", "project-b"),
        "window_start": now - timedelta(days=7),
        "window_end": now,
        "sample_limit": 1000,
    }
    assert execute.call_args.kwargs["timeout_ms"] == 750
    assert execute.call_args.kwargs["settings"]["max_bytes_to_read"] == 1_073_741_824


def test_system_tag_values_use_canonical_bounded_trace_source():
    _values, execute = _call_trace_field_values(
        metric_name="tag",
        metric_type="system_metric",
        rows=[{"val": "production"}],
    )

    sql = " ".join(execute.call_args.args[0].split())
    assert "FROM traces FINAL" in sql
    assert "arrayJoin(JSONExtract(tags, 'Array(String)'))" in sql
    assert "created_at >= %(window_start)s" in sql
    assert "created_at < %(window_end)s" in sql
    assert "trace_tags" not in sql


def test_system_metric_value_failure_is_sanitized(monkeypatch):
    from model_hub.views import ai_filter

    warning = mock.Mock()
    monkeypatch.setattr(ai_filter.logger, "warning", warning)
    raw_error = "Code: 47 DB::Exception secret-project unknown identifier"

    values, _execute = _call_trace_field_values(
        metric_name="model",
        metric_type="system_metric",
        error=RuntimeError(raw_error),
    )

    assert values == []
    warning.assert_called_once_with(
        "smart_filter_values_failed",
        metric_name="model",
        metric_type="system_metric",
        error_type="RuntimeError",
    )
    assert raw_error not in repr(warning.call_args)
