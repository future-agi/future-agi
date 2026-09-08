from contextlib import contextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from model_hub.queries.prompt.prompt_metrics import (
    _prompt_eval_cte,
    _prompt_metric_system_having,
    _prompt_span_filter_conditions,
    _prompt_span_projection,
    _validate_prompt_metric_filters,
    completed_prompt_eval_logs,
    fetch_prompt_metrics_query_sql_cte,
    fetch_prompt_metrics_span_query,
)
from model_hub.schema.prompt.prompt_metrics import (
    FetchPromptMetricsRequest,
    FetchPromptSpanMetricsRequest,
)
from model_hub.serializers.contracts import (
    PromptAggregateMetricsQuerySerializer,
    PromptSpanMetricsQuerySerializer,
)
from model_hub.services.prompt_metrics import (
    PROMPT_METRICS_MAX_EVAL_COLUMNS,
    PROMPT_METRICS_MAX_OFFSET,
    PROMPT_METRICS_MAX_PAGE_SIZE,
    PROMPT_METRICS_MAX_RESPONSE_UNITS,
    PromptMetricsReadLimitExceeded,
    _bounded_prompt_metric_configs,
    _ensure_prompt_metrics_response_bounded,
    _execute_prompt_metrics_query_with_deadline,
    _format_prompt_span_row,
    _publish_prompt_filter_contract,
    _validate_prompt_metrics_page,
)


class _SliceRecordingQueryset:
    def __init__(self, row_count):
        self.row_count = row_count
        self.requested_slice = None

    def __getitem__(self, requested_slice):
        self.requested_slice = requested_slice
        return [
            SimpleNamespace(
                id=f"eval-{index}",
                eval_template=SimpleNamespace(config={"output": "score"}, choices=[]),
            )
            for index in range(min(self.row_count, requested_slice.stop))
        ]


def test_prompt_metrics_eval_width_is_bounded_before_sql_generation():
    queryset = _SliceRecordingQueryset(PROMPT_METRICS_MAX_EVAL_COLUMNS)

    configs = _bounded_prompt_metric_configs(queryset)

    assert len(configs) == PROMPT_METRICS_MAX_EVAL_COLUMNS
    assert queryset.requested_slice == slice(
        None, PROMPT_METRICS_MAX_EVAL_COLUMNS + 1, None
    )


def test_prompt_metrics_eval_width_fails_closed_on_sentinel_column():
    queryset = _SliceRecordingQueryset(PROMPT_METRICS_MAX_EVAL_COLUMNS + 1)

    with pytest.raises(PromptMetricsReadLimitExceeded):
        _bounded_prompt_metric_configs(queryset)


def test_each_prompt_metrics_query_uses_the_one_remaining_wall():
    class _Deadline:
        def __init__(self):
            self.remaining = iter((8_250, 7_900))
            self.calls = []

        def remaining_ms(self, *, floor_ms):
            self.calls.append(floor_ms)
            return next(self.remaining)

    class _RawCursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((sql, params))

    deadline = _Deadline()
    raw_cursor = _RawCursor()
    executed = []

    def execute(sql, params, many, context):
        executed.append((sql, params, many, context))
        return "page"

    context = {"cursor": SimpleNamespace(cursor=raw_cursor)}
    result = _execute_prompt_metrics_query_with_deadline(
        deadline, execute, "SELECT page", (), False, context
    )

    assert result == "page"
    assert raw_cursor.calls == [
        (
            "SELECT set_config('statement_timeout', %s, true)",
            ("8250ms",),
        )
    ]
    assert executed == [("SELECT page", (), False, context)]
    assert deadline.calls == [1, 1]


def test_prompt_metrics_expanded_choice_columns_are_bounded():
    config = SimpleNamespace(
        id="eval-choice",
        eval_template=SimpleNamespace(
            config={"output": "choices"},
            choices=[f"choice-{index}" for index in range(51)],
        ),
    )

    with pytest.raises(PromptMetricsReadLimitExceeded):
        _bounded_prompt_metric_configs([config])


def test_prompt_metrics_response_complexity_fails_closed_before_rendering():
    with pytest.raises(PromptMetricsReadLimitExceeded):
        _ensure_prompt_metrics_response_bounded(
            {"table": [{"input": "x" * PROMPT_METRICS_MAX_RESPONSE_UNITS}]}
        )


def test_prompt_metrics_deep_offset_is_rejected():
    assert _validate_prompt_metrics_page(500, 100) == PROMPT_METRICS_MAX_OFFSET

    with pytest.raises(PromptMetricsReadLimitExceeded):
        _validate_prompt_metrics_page(501, 100)

    with pytest.raises(PromptMetricsReadLimitExceeded):
        _validate_prompt_metrics_page(0, PROMPT_METRICS_MAX_PAGE_SIZE + 1)


def test_prompt_choice_labels_are_bound_parameters_not_sql_fragments():
    choice = "owner's choice'); SELECT pg_sleep(10); --"
    config = SimpleNamespace(
        id="1372e742-a10b-4d98-9ca4-31ef4d67115f",
        eval_template=SimpleNamespace(
            config={"output": "choices"},
            choices=[choice],
        ),
    )

    sql, _cte_name, params = _prompt_eval_cte(config)

    assert choice not in sql
    assert params == [choice, choice, str(config.id)]
    assert "INNER JOIN base" in sql
    assert "el.deleted = FALSE" in sql
    assert "el.status = 'completed'" in sql
    assert "el.error = FALSE" in sql
    assert "el.skipped_reason IS NULL" in sql


def test_prompt_metric_filter_must_bind_to_an_emitted_column():
    malicious_column = "x); SELECT pg_sleep(10); --"
    filters = [
        {
            "column_id": malicious_column,
            "filter_config": {
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 1,
            },
        }
    ]

    with pytest.raises(ValueError, match="Unsupported prompt metric filter column"):
        _validate_prompt_metric_filters(filters, [])


def test_prompt_span_filter_must_bind_to_a_returned_or_eval_column():
    filters = [
        {
            "column_id": "private_column__gte",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 1,
            },
        }
    ]

    with pytest.raises(ValueError, match="Unsupported prompt span filter column"):
        _prompt_span_filter_conditions(filters, [])


def test_prompt_span_score_filter_targets_the_primitive_annotation():
    config = SimpleNamespace(
        id="1372e742-a10b-4d98-9ca4-31ef4d67115f",
        eval_template=SimpleNamespace(config={"output": "score"}, choices=[]),
    )
    filters = [
        {
            "column_id": str(config.id),
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 80,
            },
        }
    ]

    condition = _prompt_span_filter_conditions(filters, [config])

    assert condition.children == [(f"metric_{config.id}__gt", 80.0)]


def test_prompt_metrics_empty_deep_page_keeps_the_true_total():
    class _Cursor:
        description = (
            ("prompt_version_id",),
            ("__total_rows",),
            ("__resolved_total_rows",),
        )

        def execute(self, sql, params):
            self.sql = sql
            self.params = params

        def fetchall(self):
            return [(None, None, 37)]

    cursor = _Cursor()

    @contextmanager
    def cursor_context():
        yield cursor

    with patch(
        "model_hub.queries.prompt.prompt_metrics.connection.cursor",
        side_effect=cursor_context,
    ):
        rows, total_count = fetch_prompt_metrics_query_sql_cte(
            SimpleNamespace(id="prompt-template-1"),
            [],
            [],
            page_number=500,
            page_size=100,
        )

    assert rows == []
    assert total_count == 37
    assert "LEFT JOIN paged ON TRUE" in cursor.sql


def test_completed_prompt_eval_logs_uses_one_terminal_success_contract():
    class _RecordingQueryset:
        def __init__(self):
            self.filter_kwargs = None
            self.exclude_kwargs = None

        def filter(self, **kwargs):
            self.filter_kwargs = kwargs
            return self

        def exclude(self, **kwargs):
            self.exclude_kwargs = kwargs
            return self

    queryset = _RecordingQueryset()

    assert completed_prompt_eval_logs(queryset) is queryset
    assert queryset.filter_kwargs == {
        "deleted": False,
        "status": "completed",
        "error": False,
        "skipped_reason__isnull": True,
    }
    assert queryset.exclude_kwargs == {"output_str": "ERROR"}


def test_prompt_aggregate_uuid_filter_never_accepts_contains():
    prompt_label_id = "1372e742-a10b-4d98-9ca4-31ef4d67115f"
    contains_filter = [
        {
            "column_id": "prompt_label_id",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "contains",
                "filter_value": prompt_label_id,
            },
        }
    ]

    with pytest.raises(ValueError, match="Unsupported prompt metric operation"):
        _validate_prompt_metric_filters(contains_filter, [])

    equals_filter = [
        {
            "column_id": "prompt_label_id",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": prompt_label_id,
            },
        }
    ]
    having, params = _prompt_metric_system_having(equals_filter, [])

    assert "os.prompt_label_id = %s" in having
    assert "ILIKE" not in having
    assert params == [prompt_label_id]


def test_prompt_aggregate_datetime_filters_use_calendar_date_semantics():
    having, params = _prompt_metric_system_having(
        [
            {
                "column_id": "first_used",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "equals",
                    "filter_value": "2026-08-14",
                },
            }
        ],
        [],
    )

    assert "(MIN(os.created_at))::date = %s" in having
    assert params == [date(2026, 8, 14)]


def test_prompt_span_uuid_columns_reject_text_search_operations():
    with pytest.raises(ValueError, match="Unsupported filter operation"):
        _prompt_span_filter_conditions(
            [
                {
                    "column_id": "trace_id",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                    },
                }
            ],
            [],
        )


def test_prompt_filter_contract_is_published_per_endpoint():
    aggregate = _publish_prompt_filter_contract(
        [
            {
                "id": "unique_traces",
                "property_kind": "system_attribute",
            },
            {
                "id": "1372e742-a10b-4d98-9ca4-31ef4d67115f**good",
                "property_kind": "eval_config",
                "output_type": "choices",
            },
        ],
        aggregate=True,
    )
    span = _publish_prompt_filter_contract(
        [
            {
                "id": "trace_id",
                "property_kind": "system_attribute",
            },
            {
                "id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "property_kind": "eval_config",
                "output_type": "Pass/Fail",
            },
        ],
        aggregate=False,
    )

    assert aggregate[0]["filter_type"] == "number"
    assert aggregate[1]["filter_type"] == "number"
    assert "between" in aggregate[1]["supported_filter_ops"]
    assert span[0] == {
        "id": "trace_id",
        "property_kind": "system_attribute",
        "filter_type": "text",
        "supported_filter_ops": ["equals", "not_equals"],
    }
    assert span[1]["filter_type"] == "boolean"


def test_prompt_query_schemas_only_publish_search_for_linked_spans():
    aggregate_fields = PromptAggregateMetricsQuerySerializer().fields
    span_fields = PromptSpanMetricsQuerySerializer().fields

    assert "search_term" not in aggregate_fields
    assert "search_term" in span_fields

    common = {
        "prompt_template_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
        "organization_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
    }
    with pytest.raises(ValidationError):
        FetchPromptMetricsRequest(**common, search_term="response")
    assert (
        FetchPromptSpanMetricsRequest(**common, search_term="response").search_term
        == "response"
    )


def test_prompt_span_projection_hydrates_only_response_fields_and_project_id():
    config_id = "1372e742-a10b-4d98-9ca4-31ef4d67115f"
    config = SimpleNamespace(
        id=config_id,
        eval_template=SimpleNamespace(config={"output": "score"}, choices=[]),
    )
    row = {
        "prompt_template_version": "v1",
        "id": "span-1",
        "prompt_label_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
        "prompt_label_name": "production",
        "input": {"question": "hello"},
        "output": {"answer": "world"},
        "name": "generation",
        "observation_type": "generation",
        "session_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
        "created_at": datetime(2026, 8, 14, tzinfo=UTC),
        "trace_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
        "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
        f"metric_{config_id}": 93.2,
    }

    projection = _prompt_span_projection([config])
    formatted = _format_prompt_span_row(row, [config])

    assert set(projection) == set(row)
    assert "model_parameters" not in projection
    assert "metadata" not in projection
    assert formatted["project_id"] == row["project_id"]
    assert formatted[config_id] == 93.2


def test_prompt_span_query_returns_values_projection_after_payload_preflight():
    config_id = "1372e742-a10b-4d98-9ca4-31ef4d67115f"
    config = SimpleNamespace(
        id=config_id,
        eval_template=SimpleNamespace(config={"output": "score"}, choices=[]),
    )
    projected_row = {
        "prompt_template_version": "v1",
        "id": "span-1",
        "prompt_label_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
        "prompt_label_name": "production",
        "input": {"question": "hello"},
        "output": {"answer": "world"},
        "name": "generation",
        "observation_type": "generation",
        "session_id": None,
        "created_at": datetime(2026, 8, 14, tzinfo=UTC),
        "trace_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
        "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
        f"metric_{config_id}": 91.0,
    }

    class _RecordingSpanQueryset:
        def __init__(self):
            self.values_fields = None
            self.annotation_names = []

        def filter(self, *args, **kwargs):
            return self

        def annotate(self, **kwargs):
            self.annotation_names.extend(kwargs)
            return self

        def order_by(self, *args):
            return self

        def count(self):
            return 1

        def values_list(self, *fields):
            assert fields == ("id", "_response_payload_bytes")
            return [("span-1", 512)]

        def values(self, *fields):
            self.values_fields = fields
            return [projected_row]

    queryset = _RecordingSpanQueryset()
    prompt_template = SimpleNamespace(
        id="1372e742-a10b-4d98-9ca4-31ef4d67115f",
        organization_id="1372e742-a10b-4d98-9ca4-31ef4d67115f",
    )
    with (
        patch(
            "model_hub.queries.prompt.prompt_metrics.ObservationSpan.objects",
            queryset,
        ),
        patch(
            "model_hub.queries.prompt.prompt_metrics.request_workspace_filter",
            return_value=None,
        ),
    ):
        rows, total_count = fetch_prompt_metrics_span_query(
            prompt_template,
            [config],
            [],
            page_number=0,
            page_size=10,
        )

    assert rows == [projected_row]
    assert total_count == 1
    assert queryset.values_fields == _prompt_span_projection([config])
    assert "_response_payload_bytes" in queryset.annotation_names
