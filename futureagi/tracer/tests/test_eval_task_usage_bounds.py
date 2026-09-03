import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from inspect import getsource
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError

from tracer.serializers.eval_task import (
    EvalTaskUsageQuerySerializer,
    EvalTaskUsageResponseSerializer,
)
from tracer.views.eval_task import (
    _USAGE_AGGREGATION_JSON_MAX_UNITS,
    EvalTaskResponseTooLarge,
    EvalTaskView,
    _aggregate_usage_chart_rows,
    _bounded_eval_task_error_groups_queryset,
    _bounded_eval_task_read,
    _bounded_eval_task_warning_rows_queryset,
    _bounded_usage_aggregation_candidates_queryset,
    _bounded_usage_config_mappings,
    _bounded_usage_logs_queryset,
    _bounded_usage_span_context,
    _bounded_usage_span_projection,
    _build_eval_task_warning_groups,
    _build_usage_chart,
    _compute_eval_aggregation,
    _compute_span_aggregation,
    _ensure_usage_aggregation_json_bounded,
    _parse_usage_json_preview,
    _terminal_usage_queryset,
    _usage_logs_page_metadata,
)


def _aggregation_row(
    *,
    config_id,
    name,
    output_type,
    span_id=None,
    output_bool=None,
    output_float=None,
    output_str_list=None,
    status="completed",
    error=False,
):
    return {
        "id": uuid4(),
        "created_at": datetime(2026, 8, 12, tzinfo=UTC),
        "status": status,
        "error": error,
        "observation_span_id": span_id,
        "custom_eval_config_id": config_id,
        "custom_eval_config__name": name,
        "custom_eval_config__eval_template_id": uuid4(),
        "custom_eval_config__eval_template__output_type_normalized": output_type,
        "output_bool": output_bool,
        "output_float": output_float,
        "output_str_list": output_str_list or [],
    }


class TestEvalTaskUsageQuerySerializer:
    def test_accepts_required_one_hour_window(self):
        serializer = EvalTaskUsageQuerySerializer(
            data={"eval_task_id": str(uuid4()), "period": "1h"}
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["period"] == "1h"

    def test_accepts_bounded_12_month_window_and_page(self):
        serializer = EvalTaskUsageQuerySerializer(
            data={
                "eval_task_id": str(uuid4()),
                "period": "365d",
                "page": 2,
                "page_size": 100,
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["period"] == "365d"
        assert serializer.validated_data["page"] == 2
        assert serializer.validated_data["page_size"] == 100

    @pytest.mark.parametrize(
        "payload,field",
        [
            ({"period": "all"}, "period"),
            ({"page": 0}, "page"),
            ({"page": 101}, "page"),
            ({"page_size": 101}, "page_size"),
            ({"unexpected": "value"}, "unexpected"),
        ],
    )
    def test_rejects_unbounded_or_unknown_query_contract(self, payload, field):
        serializer = EvalTaskUsageQuerySerializer(
            data={"eval_task_id": str(uuid4()), **payload}
        )

        assert not serializer.is_valid()
        assert field in serializer.errors

    def test_rejects_partial_or_overlong_custom_window(self):
        partial = EvalTaskUsageQuerySerializer(
            data={
                "eval_task_id": str(uuid4()),
                "start_date": "2026-08-01T00:00:00Z",
            }
        )
        overlong = EvalTaskUsageQuerySerializer(
            data={
                "eval_task_id": str(uuid4()),
                "start_date": "2025-08-01T00:00:00Z",
                "end_date": "2026-08-03T00:00:00Z",
            }
        )

        assert not partial.is_valid()
        assert "non_field_errors" in partial.errors
        assert not overlong.is_valid()
        assert "non_field_errors" in overlong.errors

    def test_legacy_limit_alias_and_independent_aggregation_bounds(self):
        legacy = EvalTaskUsageQuerySerializer(
            data={"eval_task_id": str(uuid4()), "limit": 75}
        )
        aggregation = EvalTaskUsageQuerySerializer(
            data={
                "eval_task_id": str(uuid4()),
                "eval_aggregation": True,
                "start_date": "2026-08-01T00:00:00Z",
            }
        )

        assert legacy.is_valid(), legacy.errors
        assert legacy.validated_data["page_size"] == 75
        assert aggregation.is_valid(), aggregation.errors

    def test_rejects_ambiguous_limit_and_page_size(self):
        serializer = EvalTaskUsageQuerySerializer(
            data={
                "eval_task_id": str(uuid4()),
                "limit": 25,
                "page_size": 50,
            }
        )

        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors


def test_usage_response_contract_accepts_complete_empty_period():
    serializer = EvalTaskUsageResponseSerializer(
        data={
            "status": True,
            "result": {
                "eval_task_id": str(uuid4()),
                "stats": {
                    "total_runs": 10,
                    "runs_period": 0,
                    "success_count": 0,
                    "error_count": 0,
                    "pass_rate": 0,
                },
                "evals": [],
                "chart": [],
                "logs": {
                    "count": 0,
                    "next": None,
                    "previous": None,
                    "results": [],
                    "total_pages": 1,
                    "current_page": 1,
                },
                "period_requested": "30d",
                "period_used": "30d",
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_usage_response_contract_accepts_sampled_aggregation_metadata():
    serializer = EvalTaskUsageResponseSerializer(
        data={
            "status": True,
            "result": {
                "eval_task_id": str(uuid4()),
                "eval_aggregation": {},
                "aggregation_metadata": {
                    "query_complete": False,
                    "sampled": True,
                    "error": "sample_limit",
                    "provenance": "newest_eval_task_candidates",
                    "row_limit": 5_000,
                    "rows_scanned": 5_000,
                    "rows_matched": 0,
                },
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_usage_response_contract_validates_populated_non_uuid_span_row():
    task_id = uuid4()
    eval_id = uuid4()
    serializer = EvalTaskUsageResponseSerializer(
        data={
            "status": True,
            "result": {
                "eval_task_id": str(task_id),
                "logs": {
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": str(uuid4()),
                            "input": "hello",
                            "result": "Passed",
                            "score": 1.0,
                            "reason": "grounded",
                            "status": "success",
                            "source": "eval_task",
                            "warnings": [],
                            "created_at": "2026-08-12T00:00:00Z",
                            "span_id": "span_01HXYZ",
                            "trace_id": "trace_01HXYZ",
                            "session_id": None,
                            "eval_id": str(eval_id),
                            "eval_name": "Quality",
                            "model": None,
                            "detail": {
                                "detail_complete": False,
                                "omitted_fields": ["warnings", "input_variables"],
                                "eval_name": "Quality",
                                "model": None,
                                "warnings": [],
                                "output_type": "pass_fail",
                                "target_type": "span",
                                "span_name": "root",
                                "span_id": "span_01HXYZ",
                                "trace_id": "trace_01HXYZ",
                                "session_id": None,
                                "session_name": None,
                                "output_bool": True,
                                "output_float": None,
                                "output_str": "",
                                "results_explanation": {},
                                "error_message": "",
                                "input_variables": {"prompt": "hello"},
                            },
                        }
                    ],
                    "total_pages": 1,
                    "current_page": 1,
                    "has_more": False,
                    "count_is_lower_bound": False,
                },
                "period_requested": "30d",
                "period_used": "30d",
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_usage_chart_zero_fill_is_finite_and_preserves_aggregates():
    start = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=10)
    chart = _build_usage_chart(
        [
            {
                "bucket": start + timedelta(minutes=5),
                "calls": 3,
                "pass_count": 2,
                "fail_count": 1,
                "avg_score": 0.75,
            }
        ],
        start,
        end,
        timedelta(minutes=5),
    )

    assert len(chart) == 2
    assert chart[0]["calls"] == 0
    assert chart[1]["calls"] == 3
    assert chart[1]["avg_score"] == 0.75


def test_usage_aggregations_consume_only_the_supplied_finite_rows():
    percentage_id = uuid4()
    deterministic_id = uuid4()
    span_id = uuid4()
    rows = [
        _aggregation_row(
            config_id=percentage_id,
            name="Faithfulness",
            output_type="percentage",
            span_id=span_id,
            output_float=0.4,
        ),
        _aggregation_row(
            config_id=percentage_id,
            name="Faithfulness",
            output_type="percentage",
            span_id=span_id,
            output_float=0.8,
        ),
        _aggregation_row(
            config_id=deterministic_id,
            name="Sentiment",
            output_type="deterministic",
            span_id=span_id,
            output_str_list=["positive", "safe"],
        ),
    ]

    by_eval = _compute_eval_aggregation(rows)
    by_span = _compute_span_aggregation(rows)

    assert by_eval["Faithfulness"]["aggregated_score"] == pytest.approx(0.6)
    assert by_eval["Sentiment"]["aggregated_score"] == {
        "positive": 100.0,
        "safe": 100.0,
    }
    assert by_span[str(span_id)]["Faithfulness"]["value"] == 0.4
    assert by_span[str(span_id)]["Sentiment"]["value"] == ["positive", "safe"]


def test_usage_aggregations_exclude_nonterminal_errored_and_skipped_rows():
    config_id = uuid4()
    completed_span_id = uuid4()
    rows = [
        _aggregation_row(
            config_id=config_id,
            name="Quality",
            output_type="percentage",
            span_id=completed_span_id,
            output_float=0.8,
        ),
        _aggregation_row(
            config_id=config_id,
            name="Quality",
            output_type="percentage",
            span_id=uuid4(),
            output_float=1.0,
            status="pending",
        ),
        _aggregation_row(
            config_id=config_id,
            name="Quality",
            output_type="percentage",
            span_id=uuid4(),
            output_float=1.0,
            status="running",
        ),
        _aggregation_row(
            config_id=config_id,
            name="Quality",
            output_type="percentage",
            span_id=uuid4(),
            output_float=1.0,
            status="skipped",
        ),
        _aggregation_row(
            config_id=config_id,
            name="Quality",
            output_type="percentage",
            span_id=uuid4(),
            output_float=1.0,
            status="errored",
            error=True,
        ),
    ]

    by_eval = _compute_eval_aggregation(rows)
    by_span = _compute_span_aggregation(rows)

    assert by_eval["Quality"]["aggregated_score"] == pytest.approx(0.8)
    assert set(by_span) == {str(completed_span_id)}


def test_usage_chart_counts_only_completed_and_errored_lifecycle_rows():
    created_at = datetime(2026, 8, 12, tzinfo=UTC)
    rows = [
        {
            "created_at": created_at,
            "status": "completed",
            "output_bool": True,
            "output_float": None,
        },
        {
            "created_at": created_at,
            "status": "errored",
            "output_bool": None,
            "output_float": None,
        },
        {
            "created_at": created_at,
            "status": "pending",
            "output_bool": None,
            "output_float": None,
        },
        {
            "created_at": created_at,
            "status": "skipped",
            "output_bool": None,
            "output_float": None,
        },
    ]

    [bucket] = _aggregate_usage_chart_rows(rows, timedelta(minutes=5))

    assert bucket["calls"] == 2
    assert bucket["pass_count"] == 1
    assert bucket["fail_count"] == 1


def test_usage_endpoint_does_not_widen_empty_period_or_iterate_log_queryset():
    source = getsource(EvalTaskView.get_usage)

    assert 'period_used = "all"' not in source
    assert "for log in period_qs" not in source
    assert "_bounded_usage_aggregation_rows" in source
    assert (
        "observation_span_id__isnull=False"
        not in source.split("_bounded_usage_aggregation_rows(agg_base_qs)", 1)[
            0
        ].rsplit("if is_aggregation:", 1)[-1]
    )
    assert "@_bounded_eval_task_read" in source
    assert "_ensure_eval_task_response_bounded" in source


def test_sampled_summary_page_metadata_uses_published_lower_bound():
    count, total_pages, count_is_lower_bound = _usage_logs_page_metadata(
        include_summary=True,
        runs_period=5_000,
        period_sampled=True,
        page_number=1,
        page_size=1,
        page_row_count=1,
        more_rows_exist=True,
    )

    assert count == 5_000
    assert total_pages == 5_000
    assert count_is_lower_bound is True


def test_exact_summary_count_stays_exact_while_page_has_more():
    count, total_pages, count_is_lower_bound = _usage_logs_page_metadata(
        include_summary=True,
        runs_period=70,
        period_sampled=False,
        page_number=1,
        page_size=50,
        page_row_count=50,
        more_rows_exist=True,
    )

    assert count == 70
    assert total_pages == 2
    assert count_is_lower_bound is False


def test_log_only_terminal_partial_page_publishes_exact_count():
    count, total_pages, count_is_lower_bound = _usage_logs_page_metadata(
        include_summary=False,
        runs_period=0,
        period_sampled=False,
        page_number=2,
        page_size=50,
        page_row_count=20,
        more_rows_exist=False,
    )

    assert count == 70
    assert total_pages == 2
    assert count_is_lower_bound is False


def test_exact_summary_empty_deep_page_never_fabricates_count():
    count, total_pages, count_is_lower_bound = _usage_logs_page_metadata(
        include_summary=True,
        runs_period=1,
        period_sampled=False,
        page_number=100,
        page_size=50,
        page_row_count=0,
        more_rows_exist=False,
    )

    assert count == 1
    assert total_pages == 1
    assert count_is_lower_bound is False


def test_log_only_empty_deep_page_is_rejected_as_out_of_range():
    with pytest.raises(ValueError, match="out of range"):
        _usage_logs_page_metadata(
            include_summary=False,
            runs_period=0,
            period_sampled=False,
            page_number=100,
            page_size=50,
            page_row_count=0,
            more_rows_exist=False,
        )


def test_usage_log_projection_excludes_heavy_json_columns():
    from tracer.models.observation_span import EvalLogger

    query = str(_bounded_usage_logs_queryset(EvalLogger.objects.all()).query)

    assert '"span_attributes"' not in query
    assert '"tracer_eval_logger"."output_metadata" AS' not in query
    assert '"tracer_eval_logger"."results_explanation",' not in query
    assert "LEFT(" in query.upper()
    source = getsource(EvalTaskView.get_usage)
    assert "log.output_str" not in source
    assert "log.usage_output_str" in source


def test_aggregation_candidate_query_is_terminal_span_only_without_span_join():
    from tracer.models.observation_span import EvalLogger

    query = str(
        _bounded_usage_aggregation_candidates_queryset(
            EvalLogger.objects.filter(eval_task_id=str(uuid4()))
        ).query
    )

    assert "tracer_observation_span" not in query
    assert "observation_span_id" in query
    assert "completed" in query
    assert "errored" not in query
    assert "pending" not in query
    assert "LIMIT 5001" in query


def test_usage_querysets_defensively_keep_only_terminal_result_rows():
    from tracer.models.observation_span import EvalLogger

    terminal_query = str(_terminal_usage_queryset(EvalLogger.objects.all()).query)
    logs_query = str(_bounded_usage_logs_queryset(EvalLogger.objects.all()).query)

    for query in (terminal_query, logs_query):
        assert 'status" IN (completed, errored)' in query


def test_aggregation_json_preflight_fails_before_oversized_hydration():
    class _PreflightRows:
        def aggregate(self, **expressions):
            assert set(expressions) == {
                "usage_output_chars",
                "usage_output_max_chars",
            }
            return {
                "usage_output_chars": _USAGE_AGGREGATION_JSON_MAX_UNITS,
                "usage_output_max_chars": 10,
            }

    with pytest.raises(EvalTaskResponseTooLarge):
        _ensure_usage_aggregation_json_bounded(_PreflightRows())


def test_error_and_warning_group_queries_project_bounded_terminal_values():
    from tracer.models.observation_span import EvalLogger

    error_query = str(
        _bounded_eval_task_error_groups_queryset(EvalLogger.objects.all())[:51].query
    )
    warning_query = str(
        _bounded_eval_task_warning_rows_queryset(EvalLogger.objects.all())[:1000].query
    )

    assert "LEFT(" in error_query.upper()
    assert "errored" in error_query
    assert "MAX(" in error_query.upper()
    assert "LIMIT 51" in error_query
    assert "JSONB_EXTRACT_PATH" in warning_query.upper()
    assert "LEFT(" in warning_query.upper()
    assert "completed" in warning_query
    assert "LIMIT 1000" in warning_query

    endpoint_source = getsource(EvalTaskView.get_eval_task_logs)
    assert "@_bounded_eval_task_read" in endpoint_source
    assert "_ensure_eval_task_response_bounded" in endpoint_source


def test_warning_groups_drop_truncated_json_and_bound_keys_and_message():
    long_keys = [f"key-{index}-" + "x" * 200 for index in range(40)]
    warnings = [
        {
            "type": "partial_input",
            "empty_keys": long_keys,
            "filled_keys": ["prompt"],
            "message": "m" * 5000,
        }
    ]
    preview = json.dumps(warnings)
    rows = [
        {
            "usage_warnings": preview,
            "usage_warnings_length": len(preview),
        },
        {
            "usage_warnings": "[",
            "usage_warnings_length": 50_000,
        },
    ]

    groups, group_count, text_truncated = _build_eval_task_warning_groups(
        rows, group_limit=20
    )

    assert group_count == 1
    assert text_truncated is True
    assert len(groups[0]["empty_keys"]) == 32
    assert all(len(key) <= 128 for key in groups[0]["empty_keys"])
    assert len(groups[0]["message"]) == 1024


def test_eval_task_read_budget_returns_sanitized_typed_503(monkeypatch):
    @contextmanager
    def passthrough_transaction(_deadline):
        yield

    monkeypatch.setattr(
        "tracer.views.eval_task._bounded_eval_task_read_transaction",
        passthrough_transaction,
    )

    class _Responses:
        def custom_error_response(self, status_code, result, code=None):
            return SimpleNamespace(
                status_code=status_code,
                data={"result": result, "code": code},
            )

    @_bounded_eval_task_read
    def failing_read(_view, _request):
        raise DatabaseError("private SQL and tenant details")

    response = failing_read(SimpleNamespace(_gm=_Responses()), SimpleNamespace())

    assert response.status_code == 503
    assert response.data["code"] == "eval_task_read_unavailable"
    assert "private SQL" not in str(response.data)


def test_span_context_projects_only_bounded_requested_json_paths():
    query = str(
        _bounded_usage_span_projection(
            {"span_01"},
            ["messages.0.content"],
        ).query
    )

    assert '"tracer_observation_span"."span_attributes" AS' not in query
    assert "JSONB_EXTRACT_PATH" in query.upper()
    assert "messages" in query and "content" in query
    literal_key_query = str(
        _bounded_usage_span_projection({"span_01"}, ["foo__bar"]).query
    )
    assert "jsonb_extract_path" in literal_key_query
    assert "foo__bar" in literal_key_query
    assert "['foo', 'bar']" not in literal_key_query
    numeric_root_query = str(_bounded_usage_span_projection({"span_01"}, ["0"]).query)
    assert "jsonb_extract_path" in numeric_root_query
    assert " -> 0" not in numeric_root_query
    assert "LEFT(" in query.upper()
    assert "ORDER BY" not in query.upper()


def test_span_context_preserves_input_precedence_and_nested_mapping(monkeypatch):
    first_log = SimpleNamespace(
        id="log-1",
        observation_span_id="span-1",
        custom_eval_config_id="config-1",
    )
    fallback_log = SimpleNamespace(
        id="log-2",
        observation_span_id="span-2",
        custom_eval_config_id="config-2",
    )

    monkeypatch.setattr(
        "tracer.views.eval_task._bounded_usage_config_mappings",
        lambda _config_ids: {
            "config-1": {
                "mapping": {
                    "prompt": "messages.0.content",
                    "missing": "missing.path",
                },
                "oversized": False,
            },
            "config-2": {
                "mapping": {"literal": "input.value"},
                "oversized": False,
            },
        },
    )

    def fake_projection(span_ids, mapping_paths, *, project_id=None):
        assert project_id is None
        assert span_ids == {"span-1", "span-2"}
        assert mapping_paths == ["messages.0.content", "missing.path", "input.value"]
        return [
            {
                "id": "span-1",
                "name": "first span",
                "trace_id": "trace-1",
                "usage_input": '"primary input"',
                "usage_input_length": len('"primary input"'),
                "usage_input_value": '"ignored fallback"',
                "usage_input_value_length": len('"ignored fallback"'),
                "usage_mapping_0": '"nested prompt"',
                "usage_mapping_0_length": len('"nested prompt"'),
                "usage_mapping_1": "",
                "usage_mapping_1_length": 0,
                "usage_mapping_2": "",
                "usage_mapping_2_length": 0,
            },
            {
                "id": "span-2",
                "name": "fallback span",
                "trace_id": "trace-2",
                "usage_input": "",
                "usage_input_length": 0,
                "usage_input_value": '"literal fallback"',
                "usage_input_value_length": len('"literal fallback"'),
                "usage_mapping_0": "",
                "usage_mapping_0_length": 0,
                "usage_mapping_1": "",
                "usage_mapping_1_length": 0,
                "usage_mapping_2": '"nested mapping value"',
                "usage_mapping_2_length": len('"nested mapping value"'),
            },
        ]

    monkeypatch.setattr(
        "tracer.views.eval_task._bounded_usage_span_projection", fake_projection
    )

    contexts = _bounded_usage_span_context([first_log, fallback_log])

    assert contexts["log-1"] == {
        "name": "first span",
        "trace_id": "trace-1",
        "input": "primary input",
        "input_variables": {"prompt": "nested prompt"},
        "omitted_fields": [],
    }
    assert contexts["log-2"] == {
        "name": "fallback span",
        "trace_id": "trace-2",
        "input": "literal fallback",
        "input_variables": {"literal": "nested mapping value"},
        "omitted_fields": [],
    }


def test_span_context_marks_truncated_and_unprojected_mapping_values(monkeypatch):
    mapping = {f"variable_{ordinal}": f"path.{ordinal}" for ordinal in range(17)}
    log = SimpleNamespace(
        id="log-1",
        observation_span_id="span-1",
        custom_eval_config_id="config-1",
    )
    monkeypatch.setattr(
        "tracer.views.eval_task._bounded_usage_config_mappings",
        lambda _config_ids: {"config-1": {"mapping": mapping, "oversized": False}},
    )

    def fake_projection(_span_ids, mapping_paths, *, project_id=None):
        assert project_id is None
        assert len(mapping_paths) == 16
        row = {
            "id": "span-1",
            "name": "span",
            "trace_id": "trace",
            "usage_input": '"oversized',
            "usage_input_length": 10_000,
            "usage_input_value": "",
            "usage_input_value_length": 0,
        }
        for ordinal in range(16):
            row[f"usage_mapping_{ordinal}"] = '"value"'
            row[f"usage_mapping_{ordinal}_length"] = len('"value"')
        return [row]

    monkeypatch.setattr(
        "tracer.views.eval_task._bounded_usage_span_projection", fake_projection
    )

    context = _bounded_usage_span_context([log])["log-1"]

    assert context["input"].endswith("[truncated]")
    assert "span_input_tail" in context["omitted_fields"]
    assert "input_variables.additional_entries" in context["omitted_fields"]
    assert len(context["input_variables"]) == 16


def test_span_context_skips_malformed_mapping_values(monkeypatch):
    log = SimpleNamespace(
        id="log-1",
        observation_span_id="span-1",
        custom_eval_config_id="config-1",
    )
    monkeypatch.setattr(
        "tracer.views.eval_task._bounded_usage_config_mappings",
        lambda _config_ids: {
            "config-1": {
                "mapping": {"bad_list": [], "bad_object": {}, "good": "value"},
                "oversized": False,
            }
        },
    )
    monkeypatch.setattr(
        "tracer.views.eval_task._bounded_usage_span_projection",
        lambda _span_ids, mapping_paths, **_kwargs: (
            [
                {
                    "id": "span-1",
                    "name": "span",
                    "trace_id": "trace",
                    "usage_input": "false",
                    "usage_input_length": 5,
                    "usage_input_value": '"fallback"',
                    "usage_input_value_length": len('"fallback"'),
                    "usage_mapping_0": '"mapped"',
                    "usage_mapping_0_length": len('"mapped"'),
                }
            ]
            if mapping_paths == ["value"]
            else pytest.fail(f"unexpected paths: {mapping_paths}")
        ),
    )

    context = _bounded_usage_span_context([log])["log-1"]

    assert context["input"] == "fallback"
    assert context["input_variables"] == {"good": "mapped"}
    assert "input_variables.invalid_entries" in context["omitted_fields"]


def test_span_context_fails_closed_for_dangling_span(monkeypatch):
    log = SimpleNamespace(
        id="log-1",
        observation_span_id="missing-span",
        custom_eval_config_id=None,
    )
    monkeypatch.setattr(
        "tracer.views.eval_task._bounded_usage_span_projection",
        lambda _span_ids, _mapping_paths, **_kwargs: [],
    )

    assert _bounded_usage_span_context([log]) == {}


@pytest.mark.parametrize(
    "json_text,expected",
    [
        ('"true"', "true"),
        ('"123"', "123"),
        ('"null"', "null"),
        ('""', ""),
        ("true", True),
        ("123", 123),
        ("null", None),
        ("[]", []),
    ],
)
def test_json_projection_preserves_scalar_types(json_text, expected):
    assert _parse_usage_json_preview(json_text, len(json_text)) == expected


def test_config_mapping_projection_is_bounded_in_sql():
    source = getsource(_bounded_usage_config_mappings)
    compact_source = "".join(source.split())

    assert "_USAGE_MAPPING_JSON_MAX_CHARS" in source
    assert '.values("id","usage_mapping_json","usage_mapping_length")' in compact_source


def test_usage_log_projection_restores_blank_reason_fallback():
    from tracer.models.observation_span import EvalLogger

    query = str(_bounded_usage_logs_queryset(EvalLogger.objects.all()).query)

    assert "NULLIF" in query.upper()
    assert "eval_explanation" in query
    assert "error_message" in query


def test_log_only_task_lookup_does_not_prefetch_full_eval_json():
    source = getsource(EvalTaskView.get_usage)
    compact_source = "".join(source.split())

    assert '.only("id","project_id")' in compact_source
    assert 'if query_data["include_summary"] and not is_aggregation:' in source
    assert '"custom_eval_config__mapping"' not in getsource(
        _bounded_usage_logs_queryset
    )


def test_usage_index_tracks_bounded_newest_first_query_shape():
    from tracer.models.observation_span import EvalLogger

    usage_index = next(
        index
        for index in EvalLogger._meta.indexes
        if index.name == "eval_logger_task_created_idx"
    )
    assert tuple(usage_index.fields) == ("eval_task_id", "created_at", "id")
    condition_text = str(usage_index.condition)
    assert "eval_task_id__isnull" in condition_text
    assert "deleted" in condition_text

    eval_usage_index = next(
        index
        for index in EvalLogger._meta.indexes
        if index.name == "eval_log_task_cfg_created_idx"
    )
    assert tuple(eval_usage_index.fields) == (
        "eval_task_id",
        "custom_eval_config",
        "created_at",
        "id",
    )
