"""Experiment evaluation columns are selected inside the experiment's own data.

Deleting or editing an experiment evaluation selects its per-table columns by
the ``-sourceid-<metric id>`` suffix. That selection must stay inside the
caller's experiment: its snapshot dataset (its own dataset for an experiment
without a snapshot) and its experiment dataset tables. A column elsewhere that
happens to carry the same suffix belongs to someone else.
"""

import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import DataTypeChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db

PASSWORD = "testpassword123"
LAYOUTS = ["snapshot", "no-snapshot"]


def _login(user, organization, workspace):
    clear_workspace_context()
    client = APIClient()
    login = client.post(
        "/accounts/token/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200, login.content
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


@pytest.fixture
def owner_client(user, organization, workspace):
    return _login(user, organization, workspace)


@pytest.fixture
def tenant_b():
    clear_workspace_context()
    organization = Organization.objects.create(name="Evaluation scope tenant B")
    user = User.objects.create_user(
        email=f"eval-scope-{uuid.uuid4().hex}@futureagi.com",
        password=PASSWORD,
        name="Evaluation scope owner B",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.no_workspace_objects.create(
        name="Evaluation scope workspace B",
        organization=organization,
        created_by=user,
        is_default=True,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return SimpleNamespace(
        organization=organization,
        workspace=workspace,
        client=lambda: _login(user, organization, workspace),
    )


def _column(dataset, name, source, source_id=None):
    return Column.no_workspace_objects.create(
        name=name,
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=source,
        source_id=source_id,
    )


def _experiment(organization, workspace, *, layout="snapshot", reason=True):
    """An experiment with one evaluated output table, as the runner writes it."""
    base = Dataset.no_workspace_objects.create(
        name="Experiment source", organization=organization, workspace=workspace
    )
    data = base
    if layout == "snapshot":
        data = Dataset.no_workspace_objects.create(
            name="Experiment snapshot", organization=organization, workspace=workspace
        )
    template = EvalTemplate.no_workspace_objects.create(
        name=f"scope-template-{uuid.uuid4().hex[:12]}",
        organization=organization,
        workspace=workspace,
        criteria="Check {{output}}",
        model="fixture-model",
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="Scoped evaluation",
        dataset=base,
        snapshot_dataset=data if layout == "snapshot" else None,
    )
    metrics = [
        UserEvalMetric.no_workspace_objects.create(
            name=f"Evaluation {index}",
            dataset=data,
            organization=organization,
            workspace=workspace,
            template=template,
            source_id=str(experiment.id),
            status=StatusType.COMPLETED.value,
            config={"mapping": {"output": "Output"}, "reason_column": True},
        )
        for index in (1, 2)
    ]
    experiment.user_eval_template_ids.add(*metrics)
    table = ExperimentDatasetTable.no_workspace_objects.create(
        name="Output table", experiment=experiment
    )
    output = _column(data, "Output", SourceChoices.EXPERIMENT.value, str(table.id))
    evaluation = _column(
        data,
        "Evaluation 1-Output",
        SourceChoices.EXPERIMENT_EVALUATION.value,
        f"{table.id}-{output.id}-sourceid-{metrics[0].id}",
    )
    table.columns.add(output, evaluation)
    reason_column = None
    if reason:
        reason_column = _column(
            data,
            "Evaluation 1-Output-reason",
            SourceChoices.EVALUATION_REASON.value,
            f"{evaluation.id}-sourceid-{metrics[0].id}",
        )
    sibling = _column(data, "Untouched", SourceChoices.OTHERS.value)
    row = Row.no_workspace_objects.create(dataset=data, order=1)
    cells = [
        Cell.no_workspace_objects.create(
            dataset=data, column=column, row=row, value=value
        )
        for column, value in (
            (evaluation, "Pass"),
            (reason_column, "Because"),
            (sibling, "Keep"),
        )
        if column is not None
    ]
    kept = [output, sibling]
    removed = [evaluation] + ([reason_column] if reason_column else [])
    data.column_order = [str(column.id) for column in [output, *removed, sibling]]
    data.column_config = {
        str(column.id): {"width": 100 + index}
        for index, column in enumerate([output, *removed, sibling])
    }
    data.save(update_fields=["column_order", "column_config"])
    return SimpleNamespace(
        base=base,
        data=data,
        experiment=experiment,
        table=table,
        metrics=metrics,
        output=output,
        evaluation=evaluation,
        reason=reason_column,
        sibling=sibling,
        row=row,
        cells=cells,
        kept=kept,
        removed=removed,
        config=deepcopy(data.column_config),
    )


def _stray_dataset(organization, workspace, metric, *, reason=True, prefix=None):
    """A dataset outside the experiment whose columns carry the metric's suffix."""
    dataset = Dataset.no_workspace_objects.create(
        name="Unrelated dataset", organization=organization, workspace=workspace
    )
    columns = [
        _column(
            dataset,
            "Stray evaluation",
            SourceChoices.EXPERIMENT_EVALUATION.value,
            f"{prefix or uuid.uuid4()}-{uuid.uuid4()}-sourceid-{metric.id}",
        )
    ]
    if reason:
        columns.append(
            _column(
                dataset,
                "Stray reason",
                SourceChoices.EVALUATION_REASON.value,
                f"{uuid.uuid4()}-sourceid-{metric.id}",
            )
        )
    keep = _column(dataset, "Keep", SourceChoices.OTHERS.value)
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    cells = [
        Cell.no_workspace_objects.create(
            dataset=dataset, column=column, row=row, value="Other"
        )
        for column in columns
    ]
    dataset.column_order = [str(column.id) for column in [*columns, keep]]
    dataset.column_config = {
        str(column.id): {"width": 300 + index}
        for index, column in enumerate([*columns, keep])
    }
    dataset.save(update_fields=["column_order", "column_config"])
    return SimpleNamespace(
        dataset=dataset,
        columns=columns,
        keep=keep,
        cells=cells,
        order=list(dataset.column_order),
        config=deepcopy(dataset.column_config),
    )


def _delete(client, seeded, experiment_id=None, metric=None):
    metric = metric or seeded.metrics[0]
    return client.delete(
        f"/model-hub/develops/{metric.dataset_id}/delete_user_eval/{metric.id}/",
        {
            "delete_column": True,
            "experiment_id": str(experiment_id or seeded.experiment.id),
        },
        format="json",
    )


def _edit(client, seeded, experiment_id=None, metric=None):
    metric = metric or seeded.metrics[0]
    return client.post(
        f"/model-hub/develops/{metric.dataset_id}/edit_and_run_user_eval/{metric.id}/",
        {
            "experiment_id": str(experiment_id or seeded.experiment.id),
            "config": {
                "mapping": {"output": str(seeded.output.id)},
                "reason_column": True,
            },
            "run": False,
        },
        format="json",
    )


def _refresh(*records):
    for record in records:
        record.refresh_from_db()


def _assert_stray_untouched(stray):
    _refresh(stray.dataset, stray.keep, *stray.columns, *stray.cells)
    assert not any(column.deleted for column in stray.columns), stray.columns
    assert not any(cell.deleted for cell in stray.cells), stray.cells
    assert not stray.keep.deleted
    assert stray.dataset.column_order == stray.order
    assert stray.dataset.column_config == stray.config


def _assert_evaluation_deleted(seeded):
    _refresh(seeded.data, *seeded.metrics, *seeded.kept, *seeded.removed)
    _refresh(*seeded.cells)
    assert all(column.deleted for column in seeded.removed), seeded.removed
    assert not any(column.deleted for column in seeded.kept), seeded.kept
    removed_ids = {column.id for column in seeded.removed}
    for cell in seeded.cells:
        assert cell.deleted is (cell.column_id in removed_ids), cell
    assert seeded.metrics[0].deleted and not seeded.metrics[1].deleted
    kept_ids = [str(column.id) for column in seeded.kept]
    assert seeded.data.column_order == kept_ids
    assert seeded.data.column_config == {
        key: value for key, value in seeded.config.items() if key in kept_ids
    }


@pytest.mark.parametrize("layout", LAYOUTS)
def test_delete_leaves_other_tenant_columns_with_the_same_suffix(
    owner_client, organization, workspace, tenant_b, layout
):
    seeded = _experiment(organization, workspace, layout=layout)
    stray = _stray_dataset(tenant_b.organization, tenant_b.workspace, seeded.metrics[0])

    response = _delete(owner_client, seeded)

    assert response.status_code == 200, response.content
    _assert_stray_untouched(stray)
    _assert_evaluation_deleted(seeded)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_delete_leaves_an_unrelated_dataset_of_the_same_tenant_unchanged(
    owner_client, organization, workspace, layout
):
    seeded = _experiment(organization, workspace, layout=layout)
    stray = _stray_dataset(organization, workspace, seeded.metrics[0])

    response = _delete(owner_client, seeded)

    assert response.status_code == 200, response.content
    _assert_stray_untouched(stray)
    _assert_evaluation_deleted(seeded)


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("owner", ["same-tenant", "other-tenant"])
def test_delete_prunes_the_experiment_snapshot(
    owner_client, organization, workspace, tenant_b, layout, owner
):
    seeded = _experiment(organization, workspace, layout=layout)
    # Created last, so it is the newest column carrying the suffix.
    if owner == "same-tenant":
        _stray_dataset(organization, workspace, seeded.metrics[0])
    else:
        _stray_dataset(tenant_b.organization, tenant_b.workspace, seeded.metrics[0])

    response = _delete(owner_client, seeded)

    assert response.status_code == 200, response.content
    _refresh(seeded.data)
    kept_ids = [str(column.id) for column in seeded.kept]
    assert seeded.data.column_order == kept_ids
    assert set(seeded.data.column_config) == set(kept_ids)


def test_delete_keeps_columns_of_another_experiment_table(
    owner_client, organization, workspace
):
    seeded = _experiment(organization, workspace)
    other_experiment = ExperimentsTable.no_workspace_objects.create(
        name="Other experiment", dataset=seeded.base
    )
    other_table = ExperimentDatasetTable.no_workspace_objects.create(
        name="Other output table", experiment=other_experiment
    )
    foreign_table_column = _column(
        seeded.data,
        "Other table evaluation",
        SourceChoices.EXPERIMENT_EVALUATION.value,
        f"{other_table.id}-{seeded.output.id}-sourceid-{seeded.metrics[0].id}",
    )
    seeded.data.column_order = [*seeded.data.column_order, str(foreign_table_column.id)]
    seeded.data.save(update_fields=["column_order"])

    response = _delete(owner_client, seeded)

    assert response.status_code == 200, response.content
    _refresh(foreign_table_column, seeded.data, seeded.evaluation)
    assert seeded.evaluation.deleted
    assert not foreign_table_column.deleted
    assert str(foreign_table_column.id) in seeded.data.column_order


@pytest.mark.parametrize("owner", ["same-tenant", "other-tenant"])
def test_delete_leaves_a_column_with_the_experiment_table_prefix_in_another_dataset(
    owner_client, organization, workspace, tenant_b, owner
):
    seeded = _experiment(organization, workspace)
    if owner == "same-tenant":
        organization_b, workspace_b = organization, workspace
    else:
        organization_b, workspace_b = tenant_b.organization, tenant_b.workspace
    # Both the experiment table prefix and the metric suffix match.
    stray = _stray_dataset(
        organization_b,
        workspace_b,
        seeded.metrics[0],
        reason=False,
        prefix=seeded.table.id,
    )

    response = _delete(owner_client, seeded)

    assert response.status_code == 200, response.content
    _assert_stray_untouched(stray)
    _assert_evaluation_deleted(seeded)


def test_delete_removes_every_column_the_experiment_wrote(
    owner_client, organization, workspace
):
    seeded = _experiment(organization, workspace)
    metric = seeded.metrics[0]
    legacy_table = ExperimentDatasetTable.no_workspace_objects.create(
        name="Legacy output table"
    )
    seeded.experiment.experiments_datasets.add(legacy_table)
    retired_table = ExperimentDatasetTable.no_workspace_objects.create(
        name="Retired output table", experiment=seeded.experiment, deleted=True
    )
    written = [
        _column(
            seeded.data,
            "Legacy table evaluation",
            SourceChoices.EXPERIMENT_EVALUATION.value,
            f"{legacy_table.id}-{seeded.output.id}-sourceid-{metric.id}",
        ),
        _column(
            seeded.data,
            "Retired table evaluation",
            SourceChoices.EXPERIMENT_EVALUATION.value,
            f"{retired_table.id}-{seeded.output.id}-sourceid-{metric.id}",
        ),
        _column(
            seeded.data,
            "Evaluation tags",
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
            f"{seeded.table.id}-{seeded.output.id}-sourceid-{metric.id}",
        ),
        _column(
            seeded.data,
            "Output-keyed reason",
            SourceChoices.EVALUATION_REASON.value,
            f"{seeded.output.id}-sourceid-{metric.id}",
        ),
    ]
    seeded.data.column_order = [
        *seeded.data.column_order,
        *(str(column.id) for column in written),
    ]
    seeded.data.save(update_fields=["column_order"])
    seeded.removed.extend(written)

    response = _delete(owner_client, seeded)

    assert response.status_code == 200, response.content
    _assert_evaluation_deleted(seeded)


def test_delete_refuses_an_experiment_outside_the_caller_scope(
    owner_client, organization, workspace, tenant_b
):
    seeded = _experiment(organization, workspace)
    foreign = _experiment(tenant_b.organization, tenant_b.workspace)
    # The caller's metric names the other tenant's experiment.
    UserEvalMetric.no_workspace_objects.filter(id=seeded.metrics[0].id).update(
        source_id=str(foreign.experiment.id)
    )
    seeded.metrics[0].refresh_from_db()
    stray = _stray_dataset(tenant_b.organization, tenant_b.workspace, seeded.metrics[0])

    response = _delete(owner_client, seeded, experiment_id=foreign.experiment.id)

    assert response.status_code == 404, response.content
    _assert_stray_untouched(stray)
    _refresh(*seeded.metrics, *seeded.removed, *seeded.cells, seeded.data)
    assert not any(metric.deleted for metric in seeded.metrics)
    assert not any(column.deleted for column in seeded.removed)
    assert not any(cell.deleted for cell in seeded.cells)
    assert seeded.data.column_config == seeded.config


@pytest.mark.parametrize("layout", LAYOUTS)
def test_edit_creates_reason_columns_only_in_the_experiment_data(
    owner_client, organization, workspace, tenant_b, layout
):
    seeded = _experiment(organization, workspace, layout=layout, reason=False)
    strays = [
        _stray_dataset(organization, workspace, seeded.metrics[0], reason=False),
        _stray_dataset(
            tenant_b.organization,
            tenant_b.workspace,
            seeded.metrics[0],
            reason=False,
        ),
    ]

    response = _edit(owner_client, seeded)

    assert response.status_code == 200, response.content
    for stray in strays:
        assert not Column.all_objects.filter(
            dataset=stray.dataset, source=SourceChoices.EVALUATION_REASON.value
        ).exists()
        _assert_stray_untouched(stray)
    created = Column.no_workspace_objects.filter(
        source=SourceChoices.EVALUATION_REASON.value,
        source_id__endswith=f"-sourceid-{seeded.metrics[0].id}",
    )
    assert [(column.dataset_id, column.source_id) for column in created] == [
        (seeded.data.id, f"{seeded.evaluation.id}-sourceid-{seeded.metrics[0].id}")
    ]


@pytest.mark.parametrize("owner", ["same-tenant", "other-tenant"])
def test_edit_skips_a_column_with_the_experiment_table_prefix_in_another_dataset(
    owner_client, organization, workspace, tenant_b, owner
):
    seeded = _experiment(organization, workspace, reason=False)
    metric = seeded.metrics[0]
    if owner == "same-tenant":
        organization_b, workspace_b = organization, workspace
    else:
        organization_b, workspace_b = tenant_b.organization, tenant_b.workspace
    # Both the experiment table prefix and the metric suffix match.
    stray = _stray_dataset(
        organization_b, workspace_b, metric, reason=False, prefix=seeded.table.id
    )

    response = _edit(owner_client, seeded)

    assert response.status_code == 200, response.content
    assert not Column.all_objects.filter(
        dataset=stray.dataset, source=SourceChoices.EVALUATION_REASON.value
    ).exists()
    _assert_stray_untouched(stray)
    created = Column.no_workspace_objects.filter(
        source=SourceChoices.EVALUATION_REASON.value,
        source_id__endswith=f"-sourceid-{metric.id}",
    )
    assert [(column.dataset_id, column.source_id) for column in created] == [
        (seeded.data.id, f"{seeded.evaluation.id}-sourceid-{metric.id}")
    ]


def test_edit_skips_columns_of_another_experiment_table(
    owner_client, organization, workspace
):
    seeded = _experiment(organization, workspace, reason=False)
    metric = seeded.metrics[0]
    other_experiment = ExperimentsTable.no_workspace_objects.create(
        name="Other experiment", dataset=seeded.base
    )
    other_table = ExperimentDatasetTable.no_workspace_objects.create(
        name="Other output table", experiment=other_experiment
    )
    _column(
        seeded.data,
        "Other table evaluation",
        SourceChoices.EXPERIMENT_EVALUATION.value,
        f"{other_table.id}-{seeded.output.id}-sourceid-{metric.id}",
    )

    response = _edit(owner_client, seeded)

    assert response.status_code == 200, response.content
    created = Column.no_workspace_objects.filter(
        source=SourceChoices.EVALUATION_REASON.value,
        source_id__endswith=f"-sourceid-{metric.id}",
    )
    assert [column.source_id for column in created] == [
        f"{seeded.evaluation.id}-sourceid-{metric.id}"
    ]


def test_edit_creates_reason_columns_for_legacy_experiment_tables(
    owner_client, organization, workspace
):
    seeded = _experiment(organization, workspace, reason=False)
    metric = seeded.metrics[0]
    legacy_table = ExperimentDatasetTable.no_workspace_objects.create(
        name="Legacy output table"
    )
    seeded.experiment.experiments_datasets.add(legacy_table)
    legacy = _column(
        seeded.data,
        "Legacy table evaluation",
        SourceChoices.EXPERIMENT_EVALUATION.value,
        f"{legacy_table.id}-{seeded.output.id}-sourceid-{metric.id}",
    )

    response = _edit(owner_client, seeded)

    assert response.status_code == 200, response.content
    created = Column.no_workspace_objects.filter(
        source=SourceChoices.EVALUATION_REASON.value,
        source_id__endswith=f"-sourceid-{metric.id}",
    )
    assert sorted((str(c.dataset_id), c.source_id) for c in created) == sorted(
        [
            (str(seeded.data.id), f"{seeded.evaluation.id}-sourceid-{metric.id}"),
            (str(seeded.data.id), f"{legacy.id}-sourceid-{metric.id}"),
        ]
    )


def test_edit_with_an_experiment_outside_the_caller_scope_creates_no_reason_column(
    owner_client, organization, workspace, tenant_b
):
    seeded = _experiment(organization, workspace, reason=False)
    foreign = _experiment(tenant_b.organization, tenant_b.workspace, reason=False)
    UserEvalMetric.no_workspace_objects.filter(id=seeded.metrics[0].id).update(
        source_id=str(foreign.experiment.id)
    )
    seeded.metrics[0].refresh_from_db()
    config = seeded.metrics[0].config
    stray = _stray_dataset(
        tenant_b.organization, tenant_b.workspace, seeded.metrics[0], reason=False
    )

    response = _edit(owner_client, seeded, experiment_id=foreign.experiment.id)

    # An experiment the caller cannot reach names no evaluation of theirs:
    # the edit is refused and changes nothing.
    assert response.status_code == 404, response.content
    seeded.metrics[0].refresh_from_db()
    assert seeded.metrics[0].config == config
    assert not Column.all_objects.filter(
        source=SourceChoices.EVALUATION_REASON.value,
        source_id__endswith=f"-sourceid-{seeded.metrics[0].id}",
    ).exists()
    _assert_stray_untouched(stray)


def test_other_tenant_cannot_delete_or_edit_the_experiment_evaluation(
    organization, workspace, tenant_b
):
    seeded = _experiment(organization, workspace)
    foreign_client = tenant_b.client()

    delete = _delete(foreign_client, seeded)
    edit = _edit(foreign_client, seeded)

    assert delete.status_code == 404, delete.content
    assert edit.status_code == 404, edit.content
    _refresh(*seeded.metrics, *seeded.removed, *seeded.cells, seeded.data)
    assert not any(metric.deleted for metric in seeded.metrics)
    assert seeded.metrics[0].config == {
        "mapping": {"output": "Output"},
        "reason_column": True,
    }
    assert not any(column.deleted for column in seeded.removed)
    assert not any(cell.deleted for cell in seeded.cells)
    assert seeded.data.column_config == seeded.config
