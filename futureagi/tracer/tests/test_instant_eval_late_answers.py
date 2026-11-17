"""Late retained answers invalidate stopped-run snapshots in the same transaction."""

import uuid

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.db.models import F, QuerySet
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.instant_eval import InstantEvalAnswer, InstantEvalRow, InstantEvalRun
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.services import instant_eval_answers as answers
from tracer.services import instant_eval_run as run_service
from tracer.tests.test_instant_eval_scale import _answer, _question, _summary
from tracer.tests.test_instant_eval_scale import (
    instant_eval_enabled as instant_eval_enabled,
)
from tracer.views import instant_eval_scale

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


@pytest.fixture
def yes_no(observe_project, eval_template):
    return _question(observe_project, eval_template, "Did the agent resolve it?")


@pytest.fixture
def late_run(observe_project, yes_no):
    task = EvalTask.objects.create(
        project=observe_project,
        name="Late answer snapshot",
        run_type=RunType.HISTORICAL,
        row_type=RowType.SESSIONS,
        status=EvalTaskStatus.PAUSED,
    )
    task.evals.add(yes_no)
    stamp = timezone.now()
    run = InstantEvalRun.objects.create(
        eval_task=task,
        project=observe_project,
        organization=observe_project.organization,
        unit="sessions",
        row_type="sessions",
        rows_requested=2,
        population=2,
        questions=1,
        cancelled_at=stamp,
        answers_synced=True,
        answers_version=7,
    )
    rows = InstantEvalRow.objects.bulk_create(
        [
            InstantEvalRow(
                task=task,
                row_id=str(uuid.uuid4()),
                unit="sessions",
                sample_rank=rank,
                time=stamp,
            )
            for rank in range(2)
        ]
    )
    entries = EvalLogger.objects.bulk_create(
        [_answer(task, yes_no, row.row_id, "Yes") for row in rows]
    )
    return task, run, entries


@pytest.fixture(params=["single", "batch", "sync"])
def write_answers(request):
    def write(task, entries):
        if request.param == "single":
            return sum(answers.record_answer(entry.id) for entry in entries)
        if request.param == "batch":
            return answers.record_answers(task.id, [entry.id for entry in entries])
        return answers.sync_answers(task.id, "sessions")

    return write


@pytest.mark.parametrize("stopped", ["cancelled", "paused", "cancelled-running"])
def test_late_insert_and_update_advance_stopped_run_version(
    late_run, write_answers, stopped
):
    task, run, entries = late_run
    if stopped == "paused":
        InstantEvalRun.objects.filter(id=run.id).update(cancelled_at=None)
    elif stopped == "cancelled-running":
        EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.RUNNING)

    before = run.answers_version
    assert write_answers(task, entries) == 2
    run.refresh_from_db()
    assert run.answers_version > before
    assert list(
        InstantEvalAnswer.objects.filter(task=task).values_list("label", flat=True)
    ) == ["Yes", "Yes"]

    # ON CONFLICT updates must invalidate the old labels as well as inserts.
    before = run.answers_version
    EvalLogger.objects.filter(id__in=[entry.id for entry in entries]).update(
        output_str_list=["No"]
    )
    assert write_answers(task, entries) == 2
    run.refresh_from_db()
    assert run.answers_version > before
    assert list(
        InstantEvalAnswer.objects.filter(task=task).values_list("label", flat=True)
    ) == ["No", "No"]


def test_reopened_cancelled_summary_includes_late_answers(
    auth_client, observe_project, yes_no, late_run, write_answers, monkeypatch
):
    task, run, entries = late_run
    kept = LocMemCache(f"late-answer-summary-{run.id}", {})
    monkeypatch.setattr(run_service, "cache", kept)
    monkeypatch.setattr(instant_eval_scale, "cache", kept)
    monkeypatch.setattr(instant_eval_scale, "shown_credits_per_answer", lambda: 0.06)

    # Reproduce the cancellation-time snapshot while the requests are in flight.
    EvalLogger.objects.filter(id__in=[entry.id for entry in entries]).update(
        status=EvalEntryStatus.RUNNING
    )
    before = _summary(auth_client, observe_project, task)
    assert before["status"] == "cancelled"
    assert (before["answered"], before["not_read"], before["credits_used"]) == (0, 2, 0)
    view, _ = run_service._view(task, run, {"tz": "UTC"})
    old_key = run_service._summary_key(task, run, "cancelled", view)
    assert kept.get(old_key)["payload"]["answered"] == 0

    EvalLogger.objects.filter(id__in=[entry.id for entry in entries]).update(
        status=EvalEntryStatus.COMPLETED
    )
    assert write_answers(task, entries) == 2
    after = _summary(auth_client, observe_project, task)
    assert after["status"] == "cancelled"
    assert (after["answered"], after["not_read"], after["credits_used"]) == (2, 0, 0.12)
    assert after["counts"][str(yes_no.id)] == {"Yes": 2, "No": 0, "__error__": 0}
    # The old cache entry still exists: a new version, not cache eviction,
    # makes the reopen use a fresh snapshot.
    assert kept.get(old_key)["payload"]["answered"] == 0


@pytest.mark.parametrize("status", [EvalTaskStatus.PENDING, EvalTaskStatus.RUNNING])
def test_live_answers_keep_the_current_version(late_run, write_answers, status):
    task, run, entries = late_run
    InstantEvalRun.objects.filter(id=run.id).update(cancelled_at=None)
    EvalTask.objects.filter(id=task.id).update(status=status)
    before = run.answers_version
    assert write_answers(task, entries) == 2
    run.refresh_from_db()
    assert run.answers_version == before
    assert InstantEvalAnswer.objects.filter(task=task).count() == 2


@pytest.mark.parametrize("missing", ["unsettled", "config", "row", "retired"])
def test_unaccepted_answers_do_not_advance_version(late_run, write_answers, missing):
    task, run, entries = late_run
    if missing == "unsettled":
        EvalLogger.objects.filter(id__in=[entry.id for entry in entries]).update(
            status=EvalEntryStatus.RUNNING
        )
    elif missing == "config":
        EvalLogger.objects.filter(id__in=[entry.id for entry in entries]).update(
            custom_eval_config=None
        )
    elif missing == "row":
        InstantEvalRow.objects.filter(task=task).delete()
    else:
        EvalTask.objects.filter(id=task.id).update(deleted=True)
    before = run.answers_version
    assert write_answers(task, entries) == 0
    run.refresh_from_db()
    assert run.answers_version == before
    assert not InstantEvalAnswer.objects.filter(task=task).exists()


def test_failed_version_update_rolls_back_the_answer(
    late_run, write_answers, monkeypatch
):
    task, run, entries = late_run
    before = run.answers_version
    update = QuerySet.update

    def fail_version_update(queryset, **fields):
        if queryset.model is InstantEvalRun and "answers_version" in fields:
            raise RuntimeError("version update failed")
        return update(queryset, **fields)

    monkeypatch.setattr(QuerySet, "update", fail_version_update)
    with pytest.raises(RuntimeError, match="version update failed"):
        write_answers(task, entries)
    run.refresh_from_db()
    assert run.answers_version == before
    assert not InstantEvalAnswer.objects.filter(task=task).exists()


def test_resync_does_not_overwrite_newer_answer_versions(late_run, monkeypatch):
    task, run, _ = late_run
    monkeypatch.setattr(answers, "BATCH", 1)
    run.answers_synced = False
    # A stale in-memory run must not reset increments from other accepted writes.
    InstantEvalRun.objects.filter(id=run.id).update(
        answers_synced=False, answers_version=F("answers_version") + 5
    )
    before = InstantEvalRun.objects.get(id=run.id).answers_version
    assert answers.ensure_synced(run, check=False)
    persisted = InstantEvalRun.objects.get(id=run.id)
    assert persisted.answers_version > before
    assert run.answers_version == persisted.answers_version
    assert run.answers_synced and persisted.answers_synced
    assert InstantEvalAnswer.objects.filter(task=task).count() == 2


def test_answer_upsert_preserves_projection_and_row_time(late_run, write_answers):
    task, _, entries = late_run
    rows = {row.id: row for row in InstantEvalRow.objects.filter(task=task)}
    assert write_answers(task, entries) == 2
    saved = list(InstantEvalAnswer.objects.filter(task=task))
    assert {answer.row_id for answer in saved} == set(rows)
    for answer in saved:
        assert answer.config_id == entries[0].custom_eval_config_id
        assert (answer.label, answer.confidence) == ("Yes", 0.9)
        assert answer.time == rows[answer.row_id].time
        assert answer.answered_at is not None

    # Conflict updates keep the original row time and answer identity.
    InstantEvalRow.objects.filter(task=task).update(time=None)
    EvalLogger.objects.filter(id__in=[entry.id for entry in entries]).update(
        output_str_list=["No"], output_metadata={"jev": {"confidence": 0.25}}
    )
    assert write_answers(task, entries) == 2
    assert set(
        InstantEvalAnswer.objects.filter(task=task).values_list("id", flat=True)
    ) == {answer.id for answer in saved}
    for answer in saved:
        answer.refresh_from_db()
        assert (answer.label, answer.confidence) == ("No", 0.25)
        assert answer.time == rows[answer.row_id].time
        assert answer.answered_at is not None


@pytest.mark.parametrize("cleanup", ["forget", "sync"])
def test_retry_cleanup_preserves_other_rows_and_questions(
    late_run, monkeypatch, cleanup
):
    task, _, entries = late_run
    monkeypatch.setattr(answers, "BATCH", 1)
    rows = list(InstantEvalRow.objects.filter(task=task).order_by("sample_rank"))
    # Seed with the ORM so the direct retry case reaches the cleanup even on base.
    saved = InstantEvalAnswer.objects.bulk_create(
        [
            InstantEvalAnswer(
                task=task,
                row=row,
                config_id=entries[0].custom_eval_config_id,
                label="Yes",
                confidence=0.9,
                time=row.time,
            )
            for row in rows
        ]
    )
    other_question = InstantEvalAnswer.objects.create(
        task=task, row=rows[0], config_id=uuid.uuid4(), label="Other"
    )
    assert answers.forget_answers(task.id, []) == 0
    if cleanup == "forget":
        assert answers.forget_answers(task.id, [entries[0].id, uuid.uuid4()]) == 1
        assert answers.forget_answers(task.id, [entries[0].id]) == 0
    else:
        EvalLogger.objects.filter(id=entries[0].id).update(
            status=EvalEntryStatus.RUNNING
        )
        assert answers.sync_answers(task.id, "sessions") == 1

    assert not InstantEvalAnswer.objects.filter(id=saved[0].id).exists()
    assert set(
        InstantEvalAnswer.objects.filter(task=task).values_list("id", flat=True)
    ) == {
        saved[1].id,
        other_question.id,
    }
    assert InstantEvalRow.objects.filter(task=task).count() == 2
    assert (
        EvalLogger.objects.filter(id__in=[entry.id for entry in entries]).count() == 2
    )
