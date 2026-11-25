"""Which datasets mark_eval_cells_stopped updates when an evaluation stops."""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from accounts.models import Organization
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.eval_group import apply_eval_group_to_experiment
from model_hub.utils.eval_cell_status import mark_eval_cells_stopped
from model_hub.views.experiments import _create_eval_metrics_inline

RUNNING = CellStatus.RUNNING.value
ERROR = CellStatus.ERROR.value


def _ds(name, organization, workspace, source=DatasetSourceChoices.BUILD.value):
    return Dataset.objects.create(
        name=name, organization=organization, workspace=workspace, source=source
    )


def _snap(name, organization, workspace):
    return _ds(
        name, organization, workspace, DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value
    )


def _template(organization, workspace):
    return EvalTemplate.objects.create(
        name="Stop scope template",
        organization=organization,
        workspace=workspace,
        criteria="Evaluate the following: {{output}}",
        model="gpt-4",
    )


def _apply_group(experiment, organization, workspace, user):
    template = _template(organization, workspace)
    group = EvalGroup.objects.create(
        name="Stop scope group", organization=organization, workspace=workspace
    )
    group.eval_templates.add(template)
    apply_eval_group_to_experiment(
        group, {"experiment_id": experiment.id}, {}, user, [], workspace, {}
    )
    return experiment.user_eval_template_ids.get()


def _seed(target, prefix, metric):
    """Running eval (experiment + base) and reason cells for metric in target."""
    eval_col = Column.objects.create(
        name=f"Eval {target.name}",
        dataset=target,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{prefix}-sourceid-{metric.id}",
    )
    reason_col = Column.objects.create(
        name=f"Reason {target.name}",
        dataset=target,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{eval_col.id}-sourceid-{metric.id}",
    )
    base_col = Column.objects.create(
        name=f"Base {target.name}",
        dataset=target,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    row = Row.objects.create(dataset=target, order=0)
    return [
        Cell.objects.create(dataset=target, row=row, column=c, status=RUNNING)
        for c in (eval_col, reason_col, base_col)
    ]


def _statuses(cells):
    out = []
    for c in cells:
        c.refresh_from_db()
        out.append(c.status)
    return out


def _stop(auth_client, dataset_id, metric_id, experiment_id):
    return auth_client.post(
        f"/model-hub/develops/{dataset_id}/stop_user_eval/{metric_id}/",
        {"experiment_id": str(experiment_id)},
        format="json",
    )


@pytest.mark.django_db
class TestOtherSnapshotsUntouched:
    def test_linked_other_experiment_same_source_direct(
        self, organization, workspace, user
    ):
        d = _ds("D", organization, workspace)
        s1 = _snap("S1", organization, workspace)
        s2 = _snap("S2", organization, workspace)
        u = _ds("U", organization, workspace)
        e1 = ExperimentsTable.objects.create(name="E1", dataset=d, snapshot_dataset=s1)
        e2 = ExperimentsTable.objects.create(name="E2", dataset=d, snapshot_dataset=s2)
        m = _apply_group(e1, organization, workspace, user)
        assert m.dataset_id == d.id
        e2.user_eval_template_ids.add(m)  # worst case: other experiment links it
        c_s1 = _seed(s1, e1.id, m)
        c_s2 = _seed(s2, e2.id, m)
        c_u = _seed(u, e1.id, m)
        c_d = _seed(d, e1.id, m)
        n = mark_eval_cells_stopped(m, reason="Evaluation failed")
        assert _statuses(c_s1) == [ERROR] * 3  # positive control
        assert _statuses(c_d) == [ERROR] * 3  # metric's own dataset
        assert _statuses(c_s2) == [RUNNING] * 3
        assert _statuses(c_u) == [RUNNING] * 3
        assert n == 6

    def test_unlinked_other_experiment_same_source_api(
        self, auth_client, organization, workspace, user
    ):
        d = _ds("D", organization, workspace)
        s1 = _snap("S1", organization, workspace)
        s2 = _snap("S2", organization, workspace)
        e1 = ExperimentsTable.objects.create(name="E1", dataset=d, snapshot_dataset=s1)
        e2 = ExperimentsTable.objects.create(name="E2", dataset=d, snapshot_dataset=s2)
        m = _apply_group(e1, organization, workspace, user)
        c_s1 = _seed(s1, e1.id, m)
        c_s2 = _seed(s2, e2.id, m)
        r = _stop(auth_client, d.id, m.id, e1.id)
        assert r.status_code == status.HTTP_200_OK
        assert _statuses(c_s1) == [ERROR] * 3
        assert _statuses(c_s2) == [RUNNING] * 3

    def test_linked_other_experiment_different_source_api(
        self, auth_client, organization, workspace, user
    ):
        d = _ds("D", organization, workspace)
        d3 = _ds("D3", organization, workspace)
        s1 = _snap("S1", organization, workspace)
        s3 = _snap("S3", organization, workspace)
        e1 = ExperimentsTable.objects.create(name="E1", dataset=d, snapshot_dataset=s1)
        e3 = ExperimentsTable.objects.create(name="E3", dataset=d3, snapshot_dataset=s3)
        m = _apply_group(e1, organization, workspace, user)
        e3.user_eval_template_ids.add(m)
        c_s1 = _seed(s1, e1.id, m)
        c_s3 = _seed(s3, e3.id, m)
        r = _stop(auth_client, d.id, m.id, e1.id)
        assert r.status_code == status.HTTP_200_OK
        assert _statuses(c_s1) == [ERROR] * 3
        assert _statuses(c_s3) == [RUNNING] * 3

    def test_snapshot_in_other_org_not_included(self, organization, workspace, user):
        other_org = Organization.objects.create(name="Other Org")
        d = _ds("D", organization, workspace)
        foreign = _ds(
            "Foreign Snap",
            other_org,
            None,
            DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
        )
        e1 = ExperimentsTable.objects.create(
            name="E1", dataset=d, snapshot_dataset=foreign
        )
        m = _apply_group(e1, organization, workspace, user)
        c_f = _seed(foreign, e1.id, m)
        c_d = _seed(d, e1.id, m)
        mark_eval_cells_stopped(m, reason="Evaluation failed")
        assert _statuses(c_d) == [ERROR] * 3
        assert _statuses(c_f) == [RUNNING] * 3

    def test_deleted_experiment_snapshot_not_included(
        self, organization, workspace, user
    ):
        d = _ds("D", organization, workspace)
        s1 = _snap("S1", organization, workspace)
        e1 = ExperimentsTable.objects.create(name="E1", dataset=d, snapshot_dataset=s1)
        m = _apply_group(e1, organization, workspace, user)
        c_s1 = _seed(s1, e1.id, m)
        assert ExperimentsTable.objects.filter(id=e1.id).update(deleted=True) == 1
        mark_eval_cells_stopped(m, reason="Evaluation failed")
        assert _statuses(c_s1) == [RUNNING] * 3


@pytest.mark.django_db
class TestSnapshotBoundPaths:
    def test_add_eval_snapshot_bound_stop_api(
        self, auth_client, organization, workspace, user
    ):
        d = _ds("D", organization, workspace)
        s = _snap("S", organization, workspace)
        e = ExperimentsTable.objects.create(
            name="E", dataset=d, snapshot_dataset=s, user=user
        )
        template = _template(organization, workspace)
        with patch("model_hub.views.experiments.ExperimentRunner") as runner:
            runner.return_value = MagicMock()
            r = auth_client.post(
                f"/model-hub/experiments/{e.id}/add-eval/",
                {
                    "name": "review-add-eval",
                    "template_id": str(template.id),
                    "config": {"mapping": {"output": "output_column"}},
                },
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK, r.content
        m = UserEvalMetric.objects.get(name="review-add-eval", source_id=str(e.id))
        assert m.dataset_id == s.id
        assert e.user_eval_template_ids.filter(id=m.id).exists()
        c_s = _seed(s, e.id, m)
        c_d = _seed(d, e.id, m)
        r = _stop(auth_client, s.id, m.id, e.id)
        assert r.status_code == status.HTTP_200_OK, r.content
        m.refresh_from_db()
        assert m.status == StatusType.ERROR.value
        assert _statuses(c_s) == [ERROR] * 3
        assert _statuses(c_d) == [RUNNING] * 3

    def test_inline_create_update_snapshot_bound_direct(
        self, organization, workspace, user
    ):
        d = _ds("D", organization, workspace)
        s = _snap("S", organization, workspace)
        e = ExperimentsTable.objects.create(name="E", dataset=d, snapshot_dataset=s)
        template = _template(organization, workspace)
        (m,) = _create_eval_metrics_inline(
            [
                {
                    "name": "review-inline",
                    "template_id": template.id,
                    "config": {"mapping": {"output": "output_column"}},
                }
            ],
            e,
            s,
            organization,
            user,
            workspace=workspace,
        )
        e.user_eval_template_ids.add(m)
        assert m.dataset_id == s.id
        c_s = _seed(s, e.id, m)
        c_d = _seed(d, e.id, m)
        n = mark_eval_cells_stopped(m, reason="Evaluation failed")
        assert n == 3
        assert _statuses(c_s) == [ERROR] * 3
        assert _statuses(c_d) == [RUNNING] * 3

    def test_v1_experiment_without_snapshot(
        self, auth_client, organization, workspace, user
    ):
        d = _ds("D", organization, workspace)
        e = ExperimentsTable.objects.create(name="E", dataset=d)
        m = _apply_group(e, organization, workspace, user)
        assert m.dataset_id == d.id
        c_d = _seed(d, e.id, m)
        r = _stop(auth_client, d.id, m.id, e.id)
        assert r.status_code == status.HTTP_200_OK
        assert _statuses(c_d) == [ERROR] * 3

    def test_plain_dataset_eval_no_source(self, organization, workspace, user):
        d = _ds("D", organization, workspace)
        template = _template(organization, workspace)
        m = UserEvalMetric.objects.create(
            name="plain",
            organization=organization,
            workspace=workspace,
            dataset=d,
            template=template,
            config={},
            status=StatusType.RUNNING.value,
            user=user,
        )
        c_d = _seed(d, "x", m)
        assert mark_eval_cells_stopped(m, reason="Evaluation failed") == 3
        assert _statuses(c_d) == [ERROR] * 3
