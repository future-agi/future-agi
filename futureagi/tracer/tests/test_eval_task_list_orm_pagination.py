"""DB-pagination contract tests for eval-task list endpoints."""

import json
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType


def _result(response):
    payload = response.json()
    return payload.get("result", payload)


def _tasks(project, eval_config, rows):
    tasks = EvalTask.objects.bulk_create(
        [
            EvalTask(
                project=project,
                name=row["name"],
                filters=row.get("filters", {}),
                sampling_rate=row.get("sampling_rate", 100),
                last_run=row.get("last_run"),
                run_type=row.get("run_type", RunType.CONTINUOUS),
                status=row.get("status", EvalTaskStatus.COMPLETED),
            )
            for row in rows
        ]
    )
    through = EvalTask.evals.through
    through.objects.bulk_create(
        [
            through(
                evaltask_id=task.id,
                customevalconfig_id=eval_config.id,
            )
            for task in tasks
        ]
    )
    return tasks


def _list_params(path, project, **overrides):
    params = dict(overrides)
    if path.endswith("/list_eval_tasks/"):
        params["project_id"] = str(project.id)
    return params


def _assert_bounded_compatibility_task_reads(captured):
    """Prove compatibility filtering preflights JSON before row hydration."""

    task_reads = [
        query["sql"]
        for query in captured.captured_queries
        if 'FROM "tracer_eval_task"' in query["sql"]
    ]
    assert len(task_reads) == 2
    assert all("COUNT(*)" not in statement for statement in task_reads)

    preflight_reads = [
        statement for statement in task_reads if "_compat_filter_chars" in statement
    ]
    assert len(preflight_reads) == 1
    assert "LIMIT" in preflight_reads[0]

    hydration_reads = [
        statement for statement in task_reads if "_compat_filter_chars" not in statement
    ]
    assert len(hydration_reads) == 1


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "path",
    [
        "/tracer/eval-task/list_eval_tasks/",
        "/tracer/eval-task/list_eval_tasks_with_project_name/",
    ],
)
def test_deep_page_is_counted_and_sliced_before_hydration(
    auth_client, project, custom_eval_config, path
):
    _tasks(
        project,
        custom_eval_config,
        [
            {"name": f"Task {index:03d}", "sampling_rate": index + 1}
            for index in range(24)
        ],
    )
    EvalTask.objects.create(
        project=project,
        name="Task without eval",
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.COMPLETED,
    )
    deleted_config = CustomEvalConfig.objects.create(
        project=project,
        eval_template=custom_eval_config.eval_template,
        name="Deleted list config",
    )
    deleted_only_task = _tasks(
        project, deleted_config, [{"name": "Task with deleted eval"}]
    )[0]
    deleted_config.delete()

    params = _list_params(
        path,
        project,
        page_number=3,
        page_size=5,
        sort_params=json.dumps([{"column_id": "sampling_rate", "direction": "asc"}]),
    )
    with CaptureQueriesContext(connection) as captured:
        response = auth_client.get(path, params)

    assert response.status_code == 200, response.json()
    data = _result(response)
    assert data["metadata"]["total_rows"] == 24
    assert [row["name"] for row in data["table"]] == [
        "Task 015",
        "Task 016",
        "Task 017",
        "Task 018",
        "Task 019",
    ]
    assert all(row["created_at"].endswith("Z") for row in data["table"])
    assert str(deleted_only_task.id) not in {row["id"] for row in data["table"]}

    sql = [query["sql"] for query in captured.captured_queries]
    task_reads = [
        statement for statement in sql if 'FROM "tracer_eval_task"' in statement
    ]
    assert len(task_reads) == 2
    assert sum("COUNT(*)" in statement for statement in task_reads) == 1
    page_reads = [statement for statement in task_reads if "COUNT(*)" not in statement]
    assert len(page_reads) == 1
    assert "LIMIT 5" in page_reads[0]
    assert "OFFSET 15" in page_reads[0]
    eval_prefetches = [
        statement
        for statement in sql
        if 'AS "_prefetch_related_val_evaltask_id"' in statement
        and 'FROM "tracer_custom_eval_config"' in statement
    ]
    assert len(eval_prefetches) == 1


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "path",
    [
        "/tracer/eval-task/list_eval_tasks/",
        "/tracer/eval-task/list_eval_tasks_with_project_name/",
    ],
)
def test_numeric_filter_and_sort_are_applied_before_pagination(
    auth_client, project, custom_eval_config, path
):
    _tasks(
        project,
        custom_eval_config,
        [
            {"name": "Alpha low", "sampling_rate": 10},
            {"name": "alpha high", "sampling_rate": 80},
            {"name": "ALPHABET mid", "sampling_rate": 60},
            {"name": "Beta high", "sampling_rate": 90},
        ],
    )
    filters = [
        {
            "column_id": "sampling_rate",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 50,
            },
        }
    ]
    with CaptureQueriesContext(connection) as captured:
        response = auth_client.get(
            path,
            _list_params(
                path,
                project,
                page_number=0,
                page_size=1,
                filters=json.dumps(filters),
                sort_params=json.dumps(
                    [{"column_id": "sampling_rate", "direction": "desc"}]
                ),
            ),
        )

    assert response.status_code == 200, response.json()
    data = _result(response)
    assert data["metadata"]["total_rows"] == 3
    assert [row["name"] for row in data["table"]] == ["Beta high"]

    task_reads = [
        query["sql"]
        for query in captured.captured_queries
        if 'FROM "tracer_eval_task"' in query["sql"]
    ]
    assert len(task_reads) == 2
    assert sum("COUNT(*)" in statement for statement in task_reads) == 1
    page_reads = [statement for statement in task_reads if "COUNT(*)" not in statement]
    assert len(page_reads) == 1
    assert "LIMIT 1" in page_reads[0]
    assert all('"sampling_rate" >' in statement for statement in task_reads)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "path",
    [
        "/tracer/eval-task/list_eval_tasks/",
        "/tracer/eval-task/list_eval_tasks_with_project_name/",
    ],
)
@pytest.mark.parametrize("filter_type", ["text", "categorical", "thumbs", "annotator"])
def test_unicode_text_filters_keep_filter_engine_semantics(
    auth_client, project, custom_eval_config, path, filter_type
):
    composed = "\u0130stanbul"
    _tasks(
        project,
        custom_eval_config,
        [
            {"name": composed},
            {"name": "istanbul"},
            {"name": "Other"},
        ],
    )
    filters = [
        {
            "column_id": "name",
            "filter_config": {
                "filter_type": filter_type,
                "filter_op": "equals",
                # Python lower() retains the combining dot generated by U+0130.
                "filter_value": "i\u0307stanbul",
            },
        }
    ]

    with CaptureQueriesContext(connection) as captured:
        response = auth_client.get(
            path,
            _list_params(
                path,
                project,
                page_number=0,
                page_size=10,
                filters=json.dumps(filters),
            ),
        )

    assert response.status_code == 200, response.json()
    data = _result(response)
    assert data["metadata"]["total_rows"] == 1
    assert [row["name"] for row in data["table"]] == [composed]

    _assert_bounded_compatibility_task_reads(captured)
    assert all("UPPER(" not in query["sql"] for query in captured.captured_queries)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "path",
    [
        "/tracer/eval-task/list_eval_tasks/",
        "/tracer/eval-task/list_eval_tasks_with_project_name/",
    ],
)
def test_unicode_text_sort_keeps_python_ordering(
    auth_client, project, custom_eval_config, path
):
    names = ["éclair", "Zebra", "alpha", "Äther"]
    _tasks(
        project,
        custom_eval_config,
        [{"name": name} for name in names],
    )

    with CaptureQueriesContext(connection) as captured:
        response = auth_client.get(
            path,
            _list_params(
                path,
                project,
                page_number=0,
                page_size=10,
                sort_params=json.dumps([{"column_id": "name", "direction": "asc"}]),
            ),
        )

    assert response.status_code == 200, response.json()
    data = _result(response)
    assert data["metadata"]["total_rows"] == len(names)
    assert [row["name"] for row in data["table"]] == sorted(names)

    _assert_bounded_compatibility_task_reads(captured)
    hydration_read = next(
        query["sql"]
        for query in captured.captured_queries
        if 'FROM "tracer_eval_task"' in query["sql"]
        and "_compat_filter_chars" not in query["sql"]
    )
    order_by = hydration_read.rpartition("ORDER BY")[2]
    assert '"tracer_eval_task"."name"' not in order_by


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("asc", ["Early", "Late", "Never"]),
        ("desc", ["Never", "Late", "Early"]),
    ],
)
def test_last_run_sort_preserves_python_null_ordering(
    auth_client, project, custom_eval_config, direction, expected
):
    now = timezone.now()
    _tasks(
        project,
        custom_eval_config,
        [
            {"name": "Never", "last_run": None},
            {"name": "Late", "last_run": now},
            {"name": "Early", "last_run": now - timedelta(days=1)},
        ],
    )
    response = auth_client.get(
        "/tracer/eval-task/list_eval_tasks_with_project_name/",
        {
            "page_number": 0,
            "page_size": 10,
            "sort_params": json.dumps(
                [{"column_id": "last_run", "direction": direction}]
            ),
        },
    )

    assert response.status_code == 200, response.json()
    assert [row["name"] for row in _result(response)["table"]] == expected


@pytest.mark.integration
@pytest.mark.api
def test_untranslatable_eval_name_filter_keeps_filter_engine_semantics(
    auth_client, project, custom_eval_config
):
    second_config = CustomEvalConfig.objects.create(
        project=project,
        eval_template=custom_eval_config.eval_template,
        name="Second Eval",
    )
    first = _tasks(project, custom_eval_config, [{"name": "First task"}])[0]
    second = _tasks(project, second_config, [{"name": "Second task"}])[0]
    filters = [
        {
            "column_id": "evals_applied",
            "filter_config": {
                "filter_type": "array",
                "filter_op": "contains",
                "filter_value": ["Second Eval"],
            },
        }
    ]

    response = auth_client.get(
        "/tracer/eval-task/list_eval_tasks/",
        {
            "project_id": str(project.id),
            "page_number": 0,
            "page_size": 10,
            "filters": json.dumps(filters),
        },
    )

    assert response.status_code == 200, response.json()
    data = _result(response)
    assert data["metadata"]["total_rows"] == 1
    assert [row["id"] for row in data["table"]] == [str(second.id)]
    assert str(first.id) not in {row["id"] for row in data["table"]}
