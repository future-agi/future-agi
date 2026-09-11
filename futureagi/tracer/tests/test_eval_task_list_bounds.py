from types import SimpleNamespace

import pytest

from tracer.serializers.eval_task import (
    EvalTaskListQuerySerializer,
    EvalTaskListWithProjectNameQuerySerializer,
)
from tracer.views.eval_task import (
    _EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT,
    _EVAL_TASK_LIST_MAX_OFFSET,
    _EVAL_TASK_LIST_MAX_RESPONSE_UNITS,
    _EVAL_TASK_ROOT_MAX_PAGE_SIZE,
    EvalTaskCompatibilityScopeTooBroad,
    EvalTaskPageDepthExceeded,
    EvalTaskResponseTooLarge,
    EvalTaskView,
    _bounded_eval_task_compatibility_rows,
    _bounded_eval_task_read,
    _BoundedEvalTaskListQuerySerializer,
    _BoundedEvalTaskListWithProjectNameQuerySerializer,
    _ensure_eval_task_response_bounded,
    _eval_task_progress_by_id,
    _EvalTaskPageNumberPagination,
    _validate_eval_task_page_depth,
)


class _SliceRecordingQueryset:
    def __init__(self, row_count):
        self.row_count = row_count
        self.requested_slice = None

    def __getitem__(self, requested_slice):
        self.requested_slice = requested_slice
        return range(min(self.row_count, requested_slice.stop))


def test_eval_task_root_limit_is_bounded():
    assert _EvalTaskPageNumberPagination.max_page_size == _EVAL_TASK_ROOT_MAX_PAGE_SIZE
    assert _EVAL_TASK_ROOT_MAX_PAGE_SIZE == 100


def test_eval_task_root_openapi_advertises_runtime_page_bounds():
    paginator = _EvalTaskPageNumberPagination()
    parameters = {
        parameter["name"]: parameter
        for parameter in paginator.get_schema_operation_parameters(None)
    }

    assert parameters["page"]["schema"]["minimum"] == 1
    assert "50000" in parameters["page"]["description"]
    assert parameters["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 10,
    }


@pytest.mark.parametrize(
    "serializer_class",
    [
        EvalTaskListQuerySerializer,
        EvalTaskListWithProjectNameQuerySerializer,
        _BoundedEvalTaskListQuerySerializer,
        _BoundedEvalTaskListWithProjectNameQuerySerializer,
    ],
)
def test_eval_task_custom_list_contract_rejects_oversized_pages(serializer_class):
    serializer = serializer_class(data={"page_size": 101})

    assert not serializer.is_valid()
    assert "page_size" in serializer.errors


def test_compatibility_filter_reads_only_one_bounded_candidate_set():
    queryset = _SliceRecordingQueryset(_EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT)

    rows = _bounded_eval_task_compatibility_rows(queryset)

    assert len(rows) == _EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT
    assert queryset.requested_slice == slice(
        None, _EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT + 1, None
    )


def test_compatibility_filter_fails_closed_on_sentinel_row():
    queryset = _SliceRecordingQueryset(_EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT + 100)

    with pytest.raises(EvalTaskCompatibilityScopeTooBroad):
        _bounded_eval_task_compatibility_rows(queryset)

    assert queryset.requested_slice.stop == (
        _EVAL_TASK_LIST_COMPATIBILITY_SCAN_LIMIT + 1
    )


def test_each_eval_task_query_checks_wall_without_statement_timer(monkeypatch):
    from tracer.tests.test_postgres_application_read_policy import FakePostgres
    from tracer.views import eval_task

    pg = FakePostgres(outer=True)
    checks = []
    deadline = SimpleNamespace(remaining_ms=lambda **kw: checks.append(kw) or 8_100)
    monkeypatch.setattr(eval_task, "connection", pg)
    monkeypatch.setattr(eval_task, "transaction", SimpleNamespace(atomic=pg.atomic))
    with eval_task._bounded_eval_task_read_transaction(deadline):
        assert pg.execute("SELECT 1") == "SELECT 1"
        assert pg.execute("SELECT 2") == "SELECT 2"
    assert pg.query_timeouts == ["0", "0"] and pg.timeout == "750ms"
    assert checks and all(check == {"floor_ms": 1} for check in checks)


def test_eval_task_progress_is_batched_for_the_finite_page(monkeypatch):
    tasks = [
        SimpleNamespace(id="task-historical", run_type="historical"),
        SimpleNamespace(id="task-continuous", run_type="continuous"),
    ]
    calls = []

    class _Rows:
        def values(self, *fields):
            calls.append(("values", fields))
            return self

        def annotate(self, **kwargs):
            calls.append(("annotate", tuple(kwargs)))
            return self

        def order_by(self):
            calls.append(("order_by",))
            return [
                {
                    "eval_task_id": "task-historical",
                    "status": "completed",
                    "n": 7,
                },
                {
                    "eval_task_id": "task-historical",
                    "status": "pending",
                    "n": 3,
                },
            ]

    class _Manager:
        def filter(self, **kwargs):
            calls.append(("filter", kwargs))
            return _Rows()

    monkeypatch.setattr("tracer.views.eval_task.EvalLogger.objects", _Manager())

    progress = _eval_task_progress_by_id(tasks)

    assert progress == {
        "task-historical": {
            "dispatched": 10,
            "completed": 7,
            "missing": 3,
            "percent": 70.0,
        }
    }
    assert calls[0] == (
        "filter",
        {"eval_task_id__in": ["task-historical"]},
    )
    assert sum(call[0] == "filter" for call in calls) == 1


def test_eval_task_numbered_page_has_a_finite_offset():
    assert _validate_eval_task_page_depth(500, 100) == _EVAL_TASK_LIST_MAX_OFFSET

    with pytest.raises(EvalTaskPageDepthExceeded):
        _validate_eval_task_page_depth(501, 100)

    with pytest.raises(EvalTaskResponseTooLarge):
        _validate_eval_task_page_depth(0, 101)


def test_eval_task_response_complexity_fails_closed_before_rendering():
    with pytest.raises(EvalTaskResponseTooLarge):
        _ensure_eval_task_response_bounded(
            {"table": [{"filters_applied": "x" * _EVAL_TASK_LIST_MAX_RESPONSE_UNITS}]}
        )


def test_changed_eval_task_detail_reads_share_the_action_deadline():
    wrapper_code = _bounded_eval_task_read(lambda *_args: None).__code__

    assert EvalTaskView.retrieve.__code__ is wrapper_code
    assert EvalTaskView.get_eval_details.__wrapped__.__code__ is wrapper_code
