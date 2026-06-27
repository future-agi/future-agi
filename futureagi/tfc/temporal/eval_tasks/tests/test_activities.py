"""Unit tests for the eval-task activity sync helpers — the testable core that
each ``@activity.defn`` wrapper delegates to. They reload by id and call the
PR4/5 services; CH-backed reconcile is mocked (materialize is covered in PR4)."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestReconcileActivitySync:
    def test_delegates_to_service_and_maps_counts(self, monkeypatch, eval_task):
        from tracer.services.eval_tasks.reconciler import ReconcileResult

        captured = {}

        def _fake(task):
            captured["task_id"] = str(task.id)
            return ReconcileResult(created=3, requeued=1, dropped=2)

        monkeypatch.setattr("tracer.services.eval_tasks.reconciler.reconcile", _fake)
        from tfc.temporal.eval_tasks.activities import _reconcile_sync

        out = _reconcile_sync(str(eval_task.id))
        assert captured["task_id"] == str(eval_task.id)
        assert out == {
            "task_id": str(eval_task.id),
            "created": 3,
            "requeued": 1,
            "dropped": 2,
        }


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestClaimBatchActivitySync:
    def test_returns_ids_and_marks_running(self, eval_task, make_pending_entries):
        make_pending_entries(eval_task, 4)
        from tfc.temporal.eval_tasks.activities import _claim_batch_sync

        out = _claim_batch_sync(str(eval_task.id), 3)
        assert len(out["entry_ids"]) == 3
        assert (
            EvalLogger.objects.filter(
                eval_task_id=str(eval_task.id), status=EvalEntryStatus.RUNNING
            ).count()
            == 3
        )

    def test_empty_when_no_pending(self, eval_task):
        from tfc.temporal.eval_tasks.activities import _claim_batch_sync

        assert _claim_batch_sync(str(eval_task.id), 5) == {"entry_ids": []}


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestRunEntryActivitySync:
    def test_delegates_and_returns_status(
        self, monkeypatch, eval_task, make_pending_entries
    ):
        [entry] = make_pending_entries(eval_task, 1)
        seen = {}

        def _fake(e):
            seen["id"] = str(e.id)
            return EvalEntryStatus.COMPLETED

        monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _fake)
        from tfc.temporal.eval_tasks.activities import _run_entry_sync

        out = _run_entry_sync(str(entry.id))
        assert seen["id"] == str(entry.id)
        assert out == {"entry_id": str(entry.id), "status": EvalEntryStatus.COMPLETED}

    def test_deleted_when_entry_gone(self, eval_task, make_pending_entries):
        [entry] = make_pending_entries(eval_task, 1)
        entry.delete()  # soft-delete
        from tfc.temporal.eval_tasks.activities import _run_entry_sync

        assert _run_entry_sync(str(entry.id))["status"] == "deleted"


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestReapActivitySync:
    def test_requeues_stale_running(self, eval_task, make_pending_entries):
        entries = make_pending_entries(eval_task, 2, status=EvalEntryStatus.RUNNING)
        old = timezone.now() - timedelta(seconds=3600)
        EvalLogger.objects.filter(id__in=[e.id for e in entries]).update(
            updated_at=old, attempts=0
        )
        from tfc.temporal.eval_tasks.activities import _reap_sync

        out = _reap_sync(str(eval_task.id), 600, 3)
        assert out == {"requeued": 2, "failed": 0}
        assert (
            EvalLogger.objects.filter(
                eval_task_id=str(eval_task.id), status=EvalEntryStatus.PENDING
            ).count()
            == 2
        )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestTaskStateActivitySync:
    def test_active_with_undrained_work(self, eval_task, make_pending_entries):
        make_pending_entries(eval_task, 1)
        from tfc.temporal.eval_tasks.activities import _get_task_state_sync

        out = _get_task_state_sync(str(eval_task.id))
        assert out["active"] is True
        assert out["has_undrained_work"] is True

    def test_paused_is_inactive(self, eval_task):
        eval_task.status = EvalTaskStatus.PAUSED
        eval_task.save(update_fields=["status"])
        from tfc.temporal.eval_tasks.activities import _get_task_state_sync

        assert _get_task_state_sync(str(eval_task.id))["active"] is False


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestWorkflowLabelsActivitySync:
    def test_returns_sa_values_and_memo_context(self, eval_task):
        from tfc.temporal.eval_tasks.activities import _get_workflow_labels_sync

        out = _get_workflow_labels_sync(str(eval_task.id))
        assert out["project_id"] == str(eval_task.project_id)
        assert out["org_id"] == str(eval_task.project.organization_id)
        assert out["run_type"] == "historical"
        assert out["task_name"] == "WF Task"
        assert out["project_name"] == "WF Test Project"
        assert out["org_name"]  # org has a name
        assert "evals=1" in out["config_summary"]
        assert "row_type=spans" in out["config_summary"]


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestFinalizeActivitySync:
    def test_finalizes_when_drained(self, eval_task, make_pending_entries):
        make_pending_entries(eval_task, 2, status=EvalEntryStatus.COMPLETED)
        from tfc.temporal.eval_tasks.activities import _finalize_task_sync

        out = _finalize_task_sync(str(eval_task.id))
        assert out["finalized"] is True
        assert EvalTask.objects.get(id=eval_task.id).status == EvalTaskStatus.COMPLETED

    def test_not_finalized_while_pending(self, eval_task, make_pending_entries):
        make_pending_entries(eval_task, 1)  # still pending
        from tfc.temporal.eval_tasks.activities import _finalize_task_sync

        out = _finalize_task_sync(str(eval_task.id))
        assert out["finalized"] is False
        assert EvalTask.objects.get(id=eval_task.id).status != EvalTaskStatus.COMPLETED

    def test_missing_task_id_is_handled(self):
        from tfc.temporal.eval_tasks.activities import _get_task_state_sync

        with pytest.raises(EvalTask.DoesNotExist):
            _get_task_state_sync(str(uuid.uuid4()))
