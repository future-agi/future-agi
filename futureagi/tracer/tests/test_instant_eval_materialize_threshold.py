"""A run that stores enough rows to refresh the planner's statistics keeps them
even when that refresh is refused."""

import uuid

import pytest
from django.db import connection
from django.utils import timezone

from accounts.services.hard_erase import HardEraseRefused
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.instant_eval import InstantEvalRow, InstantEvalRun
from tracer.models.project import Project
from tracer.services import instant_eval_rows as rows


@pytest.mark.django_db
def test_rows_past_the_analyze_threshold_survive_a_refused_analyze(
    observe_project, monkeypatch, django_capture_on_commit_callbacks
):
    n = rows.ANALYZE_AFTER + 5
    task = EvalTask.objects.create(
        project=observe_project,
        name="Instant eval · question",
        filters={},
        sampling_rate=100,
        run_type=RunType.HISTORICAL,
        row_type="sessions",
        status=EvalTaskStatus.RUNNING,
    )
    run = InstantEvalRun.objects.create(
        eval_task=task,
        project=observe_project,
        organization=observe_project.organization,
        unit="sessions",
        row_type="sessions",
        rows_requested=n,
        population=n,
        questions=1,
    )
    picked = [(str(uuid.UUID(int=1 + i)), 1 + i) for i in range(n)]
    generation = Project._base_manager.get(
        pk=observe_project.pk
    ).analysis_source_generation

    def read_facets(project_id, row_type, ids, **kwargs):
        out = rows.FacetRows(source_generation=generation)
        for rid in ids:
            out[rid] = {
                "time": timezone.now(),
                "end_user_id": None,
                "user_label": "",
                "model": "",
                "turns": 1,
                "duration_ms": 10,
                "error": False,
                "excerpt": "",
                "trace_id": None,
            }
        return out

    monkeypatch.setattr(rows, "pick_rows", lambda *args, **kwargs: picked)
    monkeypatch.setattr(rows, "read_facets", read_facets)
    monkeypatch.setattr(
        "tfc.ee_gates.trace_retention_cutoff", lambda *args, **kwargs: None
    )

    # Refuse every ANALYZE the way the hard-erase SQL guard refuses one on a
    # census-protected table, whether or not that guard is installed here
    # (an installed guard refuses it first).
    def refuse_analyze(execute, sql, params, many, context):
        if str(sql).lstrip().upper().startswith("ANALYZE"):
            raise HardEraseRefused(
                "Hard erase refused: raw SQL cannot mutate protected application tables"
            )
        return execute(sql, params, many, context)

    # Each attempt, with the rows the task held when it ran.
    analyzed = []
    real_analyze = rows.analyze

    def spy_analyze(table):
        analyzed.append((table, InstantEvalRow.objects.filter(task_id=task.id).count()))
        real_analyze(table)

    monkeypatch.setattr(rows, "analyze", spy_analyze)

    with connection.execute_wrapper(refuse_analyze):
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            added = rows.extend_run(run)
            # The hint waits for the write to commit; it never runs inside it.
            assert analyzed == []

    assert added == n
    assert [c for c in callbacks if getattr(c, "func", None) is spy_analyze]
    # It was attempted once, after the write, and its refusal undid nothing.
    assert analyzed == [("tracer_instant_eval_row", n)]
    assert InstantEvalRow.objects.filter(task_id=task.id).count() == n
    assert list(
        InstantEvalRow.objects.filter(task_id=task.id)
        .order_by("sample_rank")
        .values_list("row_id", flat=True)[:3]
    ) == [rid for rid, _ in picked[:3]]
    run.refresh_from_db()
    assert run.hash_cursor == str(n)
