"""History paginates eligible runs, not a newest-500 candidate prefix."""

from datetime import UTC, datetime, timedelta

import pytest

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.instant_eval import InstantEvalRun
from tracer.tests.test_instant_eval_scale import (
    instant_eval_enabled as instant_eval_enabled,
)

RUNS = "/tracer/instant-eval/runs/"
pytestmark = pytest.mark.django_db


@pytest.fixture
def history_question(observe_project, eval_template):
    return CustomEvalConfig.objects.create(
        name="History pagination question",
        project=observe_project,
        eval_template=eval_template,
        config={"instant_eval": {"question": "q", "unit": "traces"}},
        mapping={},
        filters={},
    )


@pytest.fixture
def make_history_tasks(observe_project, history_question):
    """Bulk-create tasks oldest first, with deterministic distinct timestamps."""
    ordinal = 0

    def make(count, *, row_type=RowType.TRACES, run_unit=None, with_question=True):
        nonlocal ordinal
        tasks = EvalTask.objects.bulk_create(
            [
                EvalTask(
                    project=observe_project,
                    name=f"Instant eval · history {ordinal + i}",
                    filters={},
                    spans_limit=0,
                    run_type=RunType.HISTORICAL,
                    status=EvalTaskStatus.COMPLETED,
                    row_type=row_type,
                )
                for i in range(count)
            ]
        )
        for i, task in enumerate(tasks):
            task.created_at = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(
                seconds=ordinal + i
            )
        EvalTask.objects.bulk_update(tasks, ["created_at"])
        ordinal += count
        if with_question:
            EvalTask.evals.through.objects.bulk_create(
                [
                    EvalTask.evals.through(
                        evaltask_id=task.id, customevalconfig_id=history_question.id
                    )
                    for task in tasks
                ]
            )
        if run_unit is not None:
            InstantEvalRun.objects.bulk_create(
                [
                    InstantEvalRun(
                        eval_task=task,
                        project=observe_project,
                        organization=observe_project.organization,
                        unit=run_unit,
                        row_type=row_type,
                    )
                    for task in tasks
                ]
            )
        return tasks

    return make


def _page(client, project, **params):
    response = client.get(RUNS, {"project_id": str(project.id), **params})
    assert response.status_code == 200, response.content
    return response.json()["result"]


@pytest.mark.parametrize(
    ("page_number", "has_more"),
    [(24, True), (25, False)],
    ids=["lookahead-past-500", "oldest-page-past-500"],
)
def test_history_browses_past_500_runs(
    auth_client, observe_project, make_history_tasks, page_number, has_more
):
    tasks = make_history_tasks(501)
    expected = list(reversed(tasks))[page_number * 20 : (page_number + 1) * 20]
    page = _page(auth_client, observe_project, page_number=page_number, page_size=20)
    assert [run["task_id"] for run in page["runs"]] == [
        str(task.id) for task in expected
    ]
    assert page["has_more"] is has_more
    assert _page(auth_client, observe_project, page_number=26, page_size=20) == {
        "runs": [],
        "has_more": False,
    }


@pytest.mark.parametrize("unit_source", ["run", "question", "row_type"])
def test_unit_history_reaches_older_matches_before_paginating(
    auth_client,
    observe_project,
    history_question,
    make_history_tasks,
    unit_source,
):
    # Each source must keep its precedence across the old cutoff: a recorded
    # run can override its question; a legacy question can override row_type.
    metadata = history_question.config["instant_eval"]
    if unit_source == "run":
        metadata["unit"] = "spans"
    elif unit_source == "row_type":
        metadata.pop("unit")
    history_question.save(update_fields=["config"])
    targets = make_history_tasks(
        2,
        row_type=RowType.TRACES if unit_source == "row_type" else RowType.SPANS,
        run_unit="traces" if unit_source == "run" else None,
    )
    make_history_tasks(500, row_type=RowType.SPANS, run_unit="spans")

    first = _page(auth_client, observe_project, unit="traces", page_size=1)
    assert [(run["task_id"], run["unit"]) for run in first["runs"]] == [
        (str(targets[1].id), "traces")
    ]
    assert first["has_more"] is True
    last = _page(
        auth_client, observe_project, unit="traces", page_number=1, page_size=1
    )
    assert [run["task_id"] for run in last["runs"]] == [str(targets[0].id)]
    assert last["has_more"] is False


def test_history_lookahead_crosses_batches_without_eligible_questions(
    auth_client, observe_project, make_history_tasks
):
    [oldest] = make_history_tasks(1)
    make_history_tasks(1000, with_question=False)
    [newest] = make_history_tasks(1)

    first = _page(auth_client, observe_project, page_size=1)
    assert [run["task_id"] for run in first["runs"]] == [str(newest.id)]
    assert first["has_more"] is True
    last = _page(auth_client, observe_project, page_number=1, page_size=1)
    assert [run["task_id"] for run in last["runs"]] == [str(oldest.id)]
    assert last["has_more"] is False


def test_history_has_more_requires_an_eligible_extra_result(
    auth_client, observe_project, make_history_tasks
):
    make_history_tasks(500, with_question=False)
    tasks = make_history_tasks(20)
    page = _page(auth_client, observe_project, page_size=20)
    assert [run["task_id"] for run in page["runs"]] == [
        str(task.id) for task in reversed(tasks)
    ]
    assert page["has_more"] is False


def test_history_search_and_task_id_still_find_older_runs(
    auth_client, observe_project, make_history_tasks
):
    [oldest] = make_history_tasks(1)
    oldest.name = "Instant eval · archived-needle"
    oldest.save(update_fields=["name"])
    make_history_tasks(500)

    for filters in ({"search": "archived-needle"}, {"task_id": str(oldest.id)}):
        page = _page(auth_client, observe_project, **filters)
        assert [run["task_id"] for run in page["runs"]] == [str(oldest.id)]
        assert page["has_more"] is False
