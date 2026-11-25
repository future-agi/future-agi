"""Pure regression coverage for the bounded get-eval-metrics read path."""

import inspect
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import DatabaseError

from model_hub.serializers.contracts import (
    EvalMetricQuerySerializer,
    EvalMetricRequestSerializer,
    EvalMetricResponseSerializer,
)
from model_hub.views import separate_evals
from tracer.services.clickhouse.query_builders.base import BoundedDateTimeRange


def _between(start: datetime, end: datetime):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        }
    ]


def _request(payload, *, method_name="get"):
    organization = SimpleNamespace(id="org-1")
    attribute = "validated_query_data" if method_name == "get" else "validated_data"
    return SimpleNamespace(
        organization=organization,
        workspace=None,
        user=SimpleNamespace(organization=organization),
        **{attribute: payload},
    )


def test_eval_metric_serializers_share_the_finite_filter_contract():
    template_id = "11111111-1111-4111-8111-111111111111"
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=365)
    filters = _between(start, end)

    body = EvalMetricRequestSerializer(
        data={"eval_template_id": template_id, "filters": filters}
    )
    query = EvalMetricQuerySerializer(
        data={
            "eval_template_id": template_id,
            "filters": json.dumps(filters),
        }
    )

    assert body.is_valid(), body.errors
    assert query.is_valid(), query.errors
    assert body.validated_data["filters"] == query.validated_data["filters"]


def test_eval_metric_window_defaults_to_30_days_and_caps_at_365():
    default_window = separate_evals._resolve_eval_metric_window([])
    assert (
        timedelta(days=29, hours=23)
        < (default_window.end - default_window.start)
        <= timedelta(days=30)
    )

    start = datetime(2025, 1, 1, tzinfo=UTC)
    allowed = separate_evals._resolve_eval_metric_window(
        _between(start, start + timedelta(days=365))
    )
    assert allowed.end - allowed.start == timedelta(days=365)

    with pytest.raises(separate_evals.EvalMetricScopeError) as exc_info:
        separate_evals._resolve_eval_metric_window(
            _between(start, start + timedelta(days=365, microseconds=1))
        )
    assert exc_info.value.code == "eval_metric_window_too_wide"


def test_eval_metric_window_rejects_non_time_attribute_filters():
    with pytest.raises(separate_evals.EvalMetricScopeError) as exc_info:
        separate_evals._resolve_eval_metric_window(
            [
                {
                    "column_id": "model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-5",
                    },
                }
            ]
        )
    assert exc_info.value.code == "eval_metric_filter_unsupported"


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def fetchall(self):
        return self.rows


class _FakeConnection:
    vendor = "postgresql"
    ops = SimpleNamespace(quote_name=lambda name: f'"{name}"')

    def __init__(self, rows):
        self.fake_cursor = _FakeCursor(rows)

    def cursor(self):
        return self.fake_cursor


class _Deadline:
    def __init__(self, values=None):
        self.values = iter(values or [8_000] * 100)

    def remaining_ms(self, **_kwargs):
        return next(self.values)


def test_eval_metric_query_is_one_bounded_daily_aggregate_with_tenant_scope(
    monkeypatch,
):
    bucket = datetime(2025, 1, 1)
    fake_connection = _FakeConnection([(bucket, 12, 10, Decimal("7.5"))])
    monkeypatch.setattr(separate_evals, "connection", fake_connection)
    monkeypatch.setattr(
        separate_evals,
        "APICallLog",
        SimpleNamespace(_meta=SimpleNamespace(db_table="usage_apicalllog")),
    )
    template = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        config={"output": "score"},
        output_type_normalized="percentage",
        choice_scores=None,
    )
    workspace = SimpleNamespace(id="workspace-1", is_default=True)
    window = BoundedDateTimeRange(
        start=datetime(2025, 1, 1),
        end=datetime(2025, 2, 1),
        exclusions=(),
        empty=False,
    )

    rows, output_type = separate_evals._fetch_eval_metric_buckets(
        eval_template=template,
        organization_id="org-1",
        workspace=workspace,
        window=window,
        deadline=_Deadline(),
    )

    assert rows == [(bucket, 12, 10, Decimal("7.5"))]
    assert output_type == "score"
    [(sql, params)] = fake_connection.fake_cursor.executions
    compact_sql = " ".join(sql.split())
    assert "GROUP BY bucket" in compact_sql
    assert "COUNT(*)::bigint" in compact_sql
    assert "COUNT(score_value)::bigint" in compact_sql
    assert "LIMIT 367" in compact_sql
    assert "log.organization_id = %s" in compact_sql
    assert "log.workspace_id = %s OR log.workspace_id IS NULL" in compact_sql
    assert "log.source_id = %s" in compact_sql
    assert "log.created_at >= %s" in compact_sql
    assert "log.created_at < %s" in compact_sql
    assert "org-1" not in sql
    assert params[:3] == [
        "org-1",
        "11111111-1111-4111-8111-111111111111",
        "success",
    ]
    assert params[5] == "workspace-1"


def test_eval_metric_response_zero_fills_only_the_bounded_bucket_range(monkeypatch):
    template = SimpleNamespace(id="11111111-1111-4111-8111-111111111111")
    window = BoundedDateTimeRange(
        start=datetime(2025, 1, 1),
        end=datetime(2025, 1, 3),
        exclusions=(),
        empty=False,
    )
    monkeypatch.setattr(
        separate_evals,
        "_fetch_eval_metric_buckets",
        lambda **_kwargs: (
            [(datetime(2025, 1, 1), 3, 2, Decimal("1.5"))],
            "score",
        ),
    )

    response = separate_evals.get_eval_metric_data(
        template,
        organization_id="org-1",
        workspace=None,
        window=window,
        deadline=_Deadline(),
    )

    assert response["api_call_count"]["api_call_count"] == 3
    assert response["average"]["average"] == 75.0
    assert response["api_call_count"]["count_graph_data"] == [
        {"timestamp": "2025-01-01T00:00:00Z", "value": 3},
        {"timestamp": "2025-01-02T00:00:00Z", "value": 0},
    ]
    assert response["metadata"] == {
        "window_start": "2025-01-01T00:00:00+00:00",
        "window_end": "2025-01-03T00:00:00+00:00",
        "interval": "day",
        "bucket_count": 2,
        "valid_output_count": 2,
        "invalid_output_count": 1,
        "output_type": "score",
        "query_complete": True,
        "query_sampled": False,
        "has_more": False,
        "max_window_days": 365,
    }
    envelope = {"status": True, "result": response}
    serializer = EvalMetricResponseSerializer(data=envelope)
    assert serializer.is_valid(), serializer.errors


def test_eval_metric_timeout_is_reset_to_the_shrinking_request_remainder():
    timeout_calls = []
    executed = []
    dbapi_cursor = SimpleNamespace(
        execute=lambda sql, params: timeout_calls.append((sql, params))
    )
    context = {"cursor": SimpleNamespace(cursor=dbapi_cursor)}
    deadline = _Deadline([8_000, 7_990, 5_000, 4_990])

    def execute(sql, params, many, _context):
        executed.append((sql, params, many))
        return "ok"

    for sql in ("SELECT first", "SELECT second"):
        assert (
            separate_evals._execute_eval_metric_query_with_deadline(
                deadline,
                execute,
                sql,
                [],
                False,
                context,
            )
            == "ok"
        )

    assert timeout_calls == [
        ("SELECT set_config('statement_timeout', %s, true)", ("8000",)),
        ("SELECT set_config('statement_timeout', %s, true)", ("5000",)),
    ]
    assert [item[0] for item in executed] == ["SELECT first", "SELECT second"]


@pytest.mark.parametrize(
    ("method_name", "payload_attribute"),
    (("get", "validated_query_data"), ("post", "validated_data")),
)
def test_eval_metric_view_returns_typed_422_for_an_oversized_window(
    monkeypatch, method_name, payload_attribute
):
    monkeypatch.setattr(separate_evals, "APICallLog", SimpleNamespace())
    start = datetime(2025, 1, 1, tzinfo=UTC)
    payload = {
        "eval_template_id": "11111111-1111-4111-8111-111111111111",
        "filters": _between(start, start + timedelta(days=366)),
    }
    request = _request(payload, method_name=method_name)
    assert hasattr(request, payload_attribute)

    response = inspect.unwrap(getattr(separate_evals.EvalMetricView, method_name))(
        separate_evals.EvalMetricView(), request
    )

    assert response.status_code == 422
    assert response.data["code"] == "eval_metric_window_too_wide"


@pytest.mark.parametrize("method_name", ("get", "post"))
def test_eval_metric_view_sanitizes_database_failures_as_typed_503(
    monkeypatch, method_name
):
    private_error = "private SQL and database host"
    monkeypatch.setattr(separate_evals, "APICallLog", SimpleNamespace())
    monkeypatch.setattr(
        separate_evals,
        "_bounded_eval_metric_read",
        lambda _deadline: nullcontext(),
    )
    monkeypatch.setattr(
        separate_evals,
        "_get_eval_metric_template",
        lambda *_args: SimpleNamespace(id="11111111-1111-4111-8111-111111111111"),
    )

    def fail(*_args, **_kwargs):
        raise DatabaseError(private_error)

    monkeypatch.setattr(separate_evals, "get_eval_metric_data", fail)
    request = _request(
        {
            "eval_template_id": "11111111-1111-4111-8111-111111111111",
            "filters": [],
        },
        method_name=method_name,
    )

    response = inspect.unwrap(getattr(separate_evals.EvalMetricView, method_name))(
        separate_evals.EvalMetricView(), request
    )

    assert response.status_code == 503
    assert response.data["code"] == "eval_metric_read_unavailable"
    assert private_error not in str(response.data)


def test_eval_metric_path_has_no_graph_engine_or_per_log_average():
    source = inspect.getsource(separate_evals.get_eval_metric_data)
    query_source = inspect.getsource(separate_evals._fetch_eval_metric_buckets)
    template_source = inspect.getsource(separate_evals._get_eval_metric_template)

    assert "GraphEngine" not in source
    assert "calculate_eval_average" not in source
    assert ".filter(" not in source
    assert "fetchall" in query_source
    assert "organization" in template_source
    assert "_request_workspace_filter" in template_source
    assert separate_evals.EvalMetricView.workspace_write_exempt is True
