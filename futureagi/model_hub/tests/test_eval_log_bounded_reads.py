"""Pure regression coverage for the bounded eval-log table read path."""

import inspect
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_hub.serializers.contracts import (
    EvalApiLogTableQuerySerializer,
    EvalApiLogTableResponseSerializer,
)
from model_hub.views import separate_evals
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded


class _Deadline:
    def __init__(self, values=None):
        self.values = iter(values or [8_000] * 100)

    def remaining_ms(self, **_kwargs):
        return next(self.values)


class _FinitePageQuery:
    def __init__(self, rows):
        self.rows = rows
        self.count_calls = 0
        self.only_fields = None
        self.page_slice = None

    def count(self):
        self.count_calls += 1
        return 123

    def only(self, *fields):
        self.only_fields = fields
        return self

    def __getitem__(self, page_slice):
        assert isinstance(page_slice, slice)
        self.page_slice = page_slice
        return self.rows

    def __iter__(self):
        raise AssertionError("the unsliced APICallLog relation must not be hydrated")


def _payload(**overrides):
    return {
        "eval_template_id": "11111111-1111-4111-8111-111111111111",
        "page_size": 10,
        "current_page_index": 0,
        "source": "logs",
        "search": {},
        "filters": [],
        "sort": [],
        **overrides,
    }


def test_eval_log_page_scope_caps_hydration_and_offset():
    assert (
        separate_evals._validate_eval_log_page_scope(
            page_size=100,
            current_page=10_000,
        )
        == 1_000_000
    )

    with pytest.raises(separate_evals.EvalLogScopeError) as exc_info:
        separate_evals._validate_eval_log_page_scope(
            page_size=101,
            current_page=0,
        )
    assert exc_info.value.code == "eval_log_page_too_large"

    with pytest.raises(separate_evals.EvalLogScopeError) as exc_info:
        separate_evals._validate_eval_log_page_scope(
            page_size=100,
            current_page=10_001,
        )
    assert exc_info.value.code == "eval_log_page_out_of_range"


def test_eval_log_query_contract_rejects_pages_above_runtime_cap():
    serializer = EvalApiLogTableQuerySerializer(
        data={
            "eval_template_id": "11111111-1111-4111-8111-111111111111",
            "page_size": 101,
        }
    )

    assert not serializer.is_valid()
    assert set(serializer.errors) == {"page_size"}


def test_eval_log_openapi_and_generated_client_preserve_page_size_cap():
    repository_root = Path(__file__).resolve().parents[3]
    swagger = json.loads(
        (repository_root / "api_contracts/openapi/swagger.json").read_text()
    )
    operation = swagger["paths"]["/model-hub/get-eval-logs-details"]["get"]
    page_size = {parameter["name"]: parameter for parameter in operation["parameters"]}[
        "page_size"
    ]

    assert page_size["minimum"] == 1
    assert page_size["maximum"] == 100

    generated_types = (
        repository_root / "frontend/src/generated/api-contracts/api.schemas.ts"
    ).read_text()
    params_start = generated_types.index(
        "export type ModelHubGetEvalLogsDetailsListParams = {"
    )
    params_end = generated_types.index("\n};", params_start)
    params_contract = generated_types[params_start:params_end]
    assert "@maximum 100" in params_contract

    generated_zod = (
        repository_root / "frontend/src/generated/api-contracts/api.zod.ts"
    ).read_text()
    assert "modelHubGetEvalLogsDetailsListQueryPageSizeMax = 100" in generated_zod
    assert ".max(modelHubGetEvalLogsDetailsListQueryPageSizeMax)" in generated_zod


def test_eval_log_fetch_hydrates_only_the_requested_slice():
    rows = [SimpleNamespace(log_id="page-row")]
    query = _FinitePageQuery(rows)

    page, total = separate_evals._fetch_eval_log_page(
        query,
        offset=40,
        page_size=20,
        deadline=_Deadline(),
    )

    assert page == rows
    assert total == 123
    assert query.count_calls == 1
    assert query.page_slice == slice(40, 60)
    assert "config" in query.only_fields


def test_eval_log_dynamic_filters_and_sorts_fail_closed_before_materialization():
    with pytest.raises(separate_evals.EvalLogScopeError) as exc_info:
        separate_evals._apply_eval_log_filters_to_queryset(
            SimpleNamespace(),
            [
                {
                    "column_id": "column2",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "needle",
                    },
                }
            ],
            {"column2": "prompt"},
        )
    assert exc_info.value.code == "eval_log_filter_unsupported"

    with pytest.raises(separate_evals.EvalLogScopeError) as exc_info:
        separate_evals._apply_eval_log_sort_to_queryset(
            SimpleNamespace(),
            [{"column_id": "column2", "type": "ascending"}],
            {"column2": "prompt"},
        )
    assert exc_info.value.code == "eval_log_sort_unsupported"


def test_eval_log_search_rejects_unbounded_shapes_before_querying():
    with pytest.raises(separate_evals.EvalLogScopeError) as exc_info:
        separate_evals._apply_eval_log_search_to_queryset(
            SimpleNamespace(),
            {"key": "needle", "type": ["text"], "legacy": True},
            request=SimpleNamespace(),
            organization=SimpleNamespace(),
            eval_template=SimpleNamespace(),
            column_data=[],
        )
    assert exc_info.value.code == "eval_log_search_unsupported"

    with pytest.raises(separate_evals.EvalLogScopeError) as exc_info:
        separate_evals._apply_eval_log_search_to_queryset(
            SimpleNamespace(),
            {"key": "needle", "type": ["text"]},
            request=SimpleNamespace(),
            organization=SimpleNamespace(),
            eval_template=SimpleNamespace(name="quality", criteria="", eval_tags=[]),
            column_data=[
                {"id": f"column{index}", "name": f"field{index}", "is_visible": True}
                for index in range(65)
            ],
        )
    assert exc_info.value.code == "eval_log_search_unsupported"


def test_eval_log_text_search_compiles_to_database_predicates():
    if separate_evals.APICallLog is None:
        pytest.skip("enterprise APICallLog is unavailable")
    organization = SimpleNamespace(id=uuid.uuid4())
    query = separate_evals._apply_eval_log_search_to_queryset(
        separate_evals.APICallLog.objects.all(),
        {"key": "needle", "type": ["text", "image", "audio"]},
        request=SimpleNamespace(workspace=None),
        organization=organization,
        eval_template=SimpleNamespace(
            name="quality",
            criteria="",
            eval_tags=[],
        ),
        column_data=[
            {"id": "column1", "name": "Evaluation ID", "is_visible": True},
            {
                "id": "column2",
                "name": "Evaluation Feedback",
                "is_visible": True,
            },
            {"id": "column3", "name": "prompt", "is_visible": True},
        ],
    )

    sql = str(query.query)
    assert "usage_apicalllog" in sql
    assert "model_hub_feedback" in sql
    assert "EXISTS" in sql
    assert "UPPER" in sql
    assert "mappings" in sql


def test_eval_log_statement_timeout_shrinks_with_the_one_request_wall():
    timeout_calls = []
    executed = []
    context = {
        "cursor": SimpleNamespace(
            cursor=SimpleNamespace(
                execute=lambda sql, params: timeout_calls.append((sql, params))
            )
        )
    }
    deadline = _Deadline([8_000, 7_990, 5_000, 4_990])

    def execute(sql, params, many, _context):
        executed.append((sql, params, many))
        return "ok"

    for sql in ("SELECT first", "SELECT second"):
        assert (
            separate_evals._execute_eval_log_query_with_deadline(
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


def test_eval_log_rows_have_stable_ids_and_use_the_bounded_feedback_map(monkeypatch):
    monkeypatch.setattr(
        separate_evals.Feedback.objects,
        "get",
        lambda **_kwargs: pytest.fail(
            "bounded page population must not issue DB reads"
        ),
    )
    log_id = "11111111-1111-4111-8111-111111111111"
    log = SimpleNamespace(
        log_id=log_id,
        config={"mappings": {"prompt": "hello"}, "output": {"output": "Passed"}},
        status="success",
        source="eval_playground",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        organization=SimpleNamespace(),
    )
    template = SimpleNamespace(
        name="quality",
        criteria="be correct",
        eval_tags=[],
    )

    [row] = separate_evals.populate_log_row_data(
        template,
        [log],
        {
            "column1": "Evaluation ID",
            "column2": "prompt",
            "column3": "Evaluation Feedback",
            "column4": "Feedback Explanation",
        },
        feedback_by_log_id={log_id: ("up", "clear")},
    )

    assert row["row_id"] == log_id
    assert str(row["log_id"]) == log_id
    assert row["column2"]["cell_value"] == "hello"
    assert row["column3"]["cell_value"] == "up"
    assert row["column4"]["cell_value"] == "clear"


def test_eval_log_response_contract_marks_a_complete_exact_page():
    envelope = {
        "status": True,
        "result": {
            "table": [],
            "column_config": [],
            "metadata": {
                "total_rows": 0,
                "total_pages": 0,
                "current_page_index": 0,
                "page_size": 10,
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
            },
        },
    }
    serializer = EvalApiLogTableResponseSerializer(data=envelope)
    assert serializer.is_valid(), serializer.errors


def test_eval_log_view_returns_typed_422_for_an_unbounded_shape(monkeypatch):
    def reject(*_args, **_kwargs):
        raise separate_evals.EvalLogScopeError(
            "unsupported",
            code="eval_log_filter_unsupported",
        )

    monkeypatch.setattr(separate_evals, "_read_eval_log_table", reject)
    request = SimpleNamespace(validated_query_data=_payload())

    response = inspect.unwrap(separate_evals.GetAPICallLogDetailsView.get)(
        separate_evals.GetAPICallLogDetailsView(),
        request,
    )

    assert response.status_code == 422
    assert response.data["code"] == "eval_log_filter_unsupported"


def test_eval_log_view_sanitizes_a_deadline_as_typed_503(monkeypatch):
    private_error = "private database host and SQL"

    def timeout(*_args, **_kwargs):
        raise ReadDeadlineExceeded(private_error)

    monkeypatch.setattr(separate_evals, "_read_eval_log_table", timeout)
    request = SimpleNamespace(validated_query_data=_payload())

    response = inspect.unwrap(separate_evals.GetAPICallLogDetailsView.get)(
        separate_evals.GetAPICallLogDetailsView(),
        request,
    )

    assert response.status_code == 503
    assert response.data["code"] == "eval_log_read_unavailable"
    assert private_error not in str(response.data)


def test_eval_log_table_path_has_no_full_relation_hydration():
    source = inspect.getsource(separate_evals._read_eval_log_table)
    page_source = inspect.getsource(separate_evals._fetch_eval_log_page)
    snapshot_source = inspect.getsource(separate_evals._bounded_eval_log_read)

    assert "batch_queryset" not in source
    assert "ThreadPoolExecutor" not in source
    assert "apply_filters" not in source
    assert "apply_search" not in source
    assert "[offset : offset + page_size]" in page_source
    assert "REPEATABLE READ, READ ONLY" in snapshot_source
    assert "execute_wrapper" in snapshot_source
    assert separate_evals.GetAPICallLogDetailsView.workspace_write_exempt is True
