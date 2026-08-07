"""API tests for the cutover: create/edit now start a per-task workflow instead
of relying on the cron, and the workflow (not the view) reconciles. Soft-delete
and workflow start are spied (their internals are covered by the engine suites);
these assert the view's orchestration and the option-table guard."""

import pytest
from rest_framework import status

from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType

_UPDATE = "/tracer/eval-task/update_eval_task/"


@pytest.fixture
def spy(monkeypatch):
    # The view no longer reconciles synchronously — the workflow does. The view
    # only records the Delete-&-rerun wipe and (re)starts the workflow.
    calls = {"soft_delete": [], "start": []}
    monkeypatch.setattr(
        "tracer.views.eval_task.soft_delete_live",
        lambda task: (calls["soft_delete"].append(str(task.id)), 0)[1],
    )
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync",
        lambda task, **kw: (calls["start"].append(str(task.id)), "wf")[1],
    )
    return calls


@pytest.fixture
def historical_task(db, project, custom_eval_config):
    task = EvalTask.objects.create(
        project=project,
        name="hist",
        filters={},
        sampling_rate=100.0,
        spans_limit=100,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SPANS,
    )
    task.evals.add(custom_eval_config)
    return task


@pytest.fixture
def second_eval_config(db, project):
    template = EvalTemplate.objects.create(
        name="T2",
        description="t",
        organization=project.organization,
        workspace=project.workspace,
        config={"type": "pass_fail", "criteria": "c"},
    )
    return CustomEvalConfig.objects.create(
        name="Second Eval",
        project=project,
        eval_template=template,
        config={"threshold": 0.5},
        mapping={"input": "input"},
        filters={},
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestCutoverCreate:
    def test_create_starts_the_workflow(
        self, auth_client, project, custom_eval_config, spy
    ):
        resp = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "evals": [str(custom_eval_config.id)],
                "name": "new task",
                "run_type": "historical",
                "spans_limit": 100,
                "sampling_rate": 100.0,
                "filters": {},
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert len(spy["start"]) == 1


@pytest.mark.integration
@pytest.mark.django_db
class TestCutoverEdit:
    def test_edit_rerun_starts_workflow_without_wipe(
        self, auth_client, historical_task, spy
    ):
        resp = auth_client.patch(
            _UPDATE,
            {
                "eval_task_id": str(historical_task.id),
                "sampling_rate": 50.0,
                "edit_type": "edit_rerun",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert spy["start"]
        assert not spy["soft_delete"]

    def test_fresh_run_wipes_then_starts(self, auth_client, historical_task, spy):
        resp = auth_client.patch(
            _UPDATE,
            {
                "eval_task_id": str(historical_task.id),
                "name": "renamed",
                "edit_type": "fresh_run",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert spy["soft_delete"] and spy["start"]

    def test_case3_both_axes_rejects_edit_rerun_offers_delete(
        self, auth_client, historical_task, second_eval_config, custom_eval_config, spy
    ):
        both = {
            "eval_task_id": str(historical_task.id),
            "evals": [str(custom_eval_config.id), str(second_eval_config.id)],
            "sampling_rate": 50.0,
        }
        rejected = auth_client.patch(
            _UPDATE, {**both, "edit_type": "edit_rerun"}, format="json"
        )
        assert rejected.status_code == status.HTTP_400_BAD_REQUEST
        assert not spy["start"]  # nothing ran

        allowed = auth_client.patch(
            _UPDATE, {**both, "edit_type": "fresh_run"}, format="json"
        )
        assert allowed.status_code == status.HTTP_200_OK
        assert spy["soft_delete"] and spy["start"]

    def test_historical_to_continuous_rejects_fresh_run(
        self, auth_client, historical_task, spy
    ):
        resp = auth_client.patch(
            _UPDATE,
            {
                "eval_task_id": str(historical_task.id),
                "run_type": "continuous",
                "edit_type": "fresh_run",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_continuous_to_historical_requires_limit(
        self, auth_client, project, custom_eval_config, spy
    ):
        task = EvalTask.objects.create(
            project=project,
            name="cont",
            filters={},
            sampling_rate=100.0,
            spans_limit=None,
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.PENDING,
            row_type=RowType.SPANS,
        )
        task.evals.add(custom_eval_config)
        resp = auth_client.patch(
            _UPDATE,
            {
                "eval_task_id": str(task.id),
                "run_type": "historical",
                "edit_type": "edit_rerun",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_row_type_is_immutable(self, auth_client, historical_task, spy):
        resp = auth_client.patch(
            _UPDATE,
            {
                "eval_task_id": str(historical_task.id),
                "row_type": "traces",
                "edit_type": "edit_rerun",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_running_task_rejected(self, auth_client, historical_task, spy):
        historical_task.status = EvalTaskStatus.RUNNING
        historical_task.save(update_fields=["status"])
        resp = auth_client.patch(
            _UPDATE,
            {
                "eval_task_id": str(historical_task.id),
                "name": "x",
                "edit_type": "fresh_run",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not spy["start"]


@pytest.mark.integration
@pytest.mark.django_db
class TestCutoverUnpause:
    def test_unpause_restarts_workflow(self, auth_client, historical_task, spy):
        historical_task.status = EvalTaskStatus.PAUSED
        historical_task.save(update_fields=["status"])
        resp = auth_client.post(
            f"/tracer/eval-task/unpause_eval_task/?eval_task_id={historical_task.id}",
            {},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert spy["start"]
        historical_task.refresh_from_db()
        assert historical_task.status == EvalTaskStatus.PENDING
