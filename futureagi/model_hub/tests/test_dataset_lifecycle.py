from datetime import timedelta

import pytest

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    AnnotationTypeChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.run_prompt import RunPrompter
from model_hub.services.dataset_service import delete_column, delete_rows
from model_hub.services.lifecycle import bulk_restore, bulk_soft_delete


@pytest.fixture
def lifecycle_context(db):
    organization = Organization.objects.create(name="Dataset Lifecycle Org")
    user = User.objects.create_user(
        email="dataset-lifecycle@example.com",
        password="testpassword123",
        name="Dataset Lifecycle User",
        organization=organization,
    )
    workspace = Workspace.objects.create(
        name="Dataset Lifecycle Workspace",
        organization=organization,
        created_by=user,
    )
    dataset = Dataset.objects.create(
        name="Dataset Lifecycle",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    return organization, user, workspace, dataset


def _assert_soft_deleted(instance, previous_updated_at):
    instance.refresh_from_db()
    assert instance.deleted is True
    assert instance.deleted_at is not None
    assert instance.updated_at == instance.deleted_at
    assert instance.updated_at > previous_updated_at


@pytest.mark.django_db
def test_bulk_lifecycle_helpers_set_exact_delete_and_restore_timestamps(
    lifecycle_context,
):
    organization, _, _, dataset = lifecycle_context
    deleted_at = dataset.updated_at + timedelta(seconds=1)
    restored_at = deleted_at + timedelta(seconds=1)

    assert (
        bulk_soft_delete(
            Dataset.objects.filter(id=dataset.id, organization=organization),
            now=deleted_at,
        )
        == 1
    )
    dataset.refresh_from_db()
    assert dataset.deleted is True
    assert dataset.deleted_at == deleted_at
    assert dataset.updated_at == deleted_at

    assert (
        bulk_restore(
            Dataset.all_objects.filter(id=dataset.id, organization=organization),
            now=restored_at,
        )
        == 1
    )
    dataset.refresh_from_db()
    assert dataset.deleted is False
    assert dataset.deleted_at is None
    assert dataset.updated_at == restored_at


@pytest.mark.django_db
def test_delete_run_prompt_column_stamps_source_and_column(lifecycle_context):
    organization, _, workspace, dataset = lifecycle_context
    run_prompter = RunPrompter.objects.create(
        name="Lifecycle Prompt",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
        model="gpt-4",
        messages=[{"role": "user", "content": "Test"}],
        run_prompt_config={},
    )
    column = Column.objects.create(
        name="Prompt Output",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=str(run_prompter.id),
    )
    dependent_column = Column.objects.create(
        name="Prompt Output Detail",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
        source_id=f"{column.id}-detail",
    )
    row = Row.objects.create(dataset=dataset, order=0)
    dependent_cell = Cell.objects.create(
        dataset=dataset,
        column=dependent_column,
        row=row,
        value="detail",
    )
    source_updated_at = run_prompter.updated_at
    column_updated_at = column.updated_at
    dependent_column_updated_at = dependent_column.updated_at
    dependent_cell_updated_at = dependent_cell.updated_at

    result = delete_column(
        dataset_id=str(dataset.id),
        column_id=str(column.id),
        organization=organization,
    )

    assert result["column_id"] == str(column.id)
    _assert_soft_deleted(run_prompter, source_updated_at)
    _assert_soft_deleted(column, column_updated_at)
    _assert_soft_deleted(dependent_column, dependent_column_updated_at)
    _assert_soft_deleted(dependent_cell, dependent_cell_updated_at)
    assert run_prompter.updated_at == column.updated_at
    assert len(
        {
            run_prompter.updated_at,
            column.updated_at,
            dependent_column.updated_at,
            dependent_cell.updated_at,
        }
    ) == 1


@pytest.mark.django_db
def test_delete_eval_column_stamps_source_and_column(lifecycle_context):
    organization, user, workspace, dataset = lifecycle_context
    template = EvalTemplate.objects.create(
        name="dataset-lifecycle-eval",
        organization=organization,
        workspace=workspace,
        criteria="Evaluate {{output}}",
        model="gpt-4",
    )
    metric = UserEvalMetric.objects.create(
        name="Dataset Lifecycle Eval",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        status=StatusType.COMPLETED.value,
        config={},
        user=user,
    )
    column = Column.objects.create(
        name="Eval Output",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    metric_updated_at = metric.updated_at
    column_updated_at = column.updated_at

    result = delete_column(
        dataset_id=str(dataset.id),
        column_id=str(column.id),
        organization=organization,
    )

    assert result["column_id"] == str(column.id)
    _assert_soft_deleted(metric, metric_updated_at)
    _assert_soft_deleted(column, column_updated_at)
    assert metric.updated_at == column.updated_at


@pytest.mark.django_db
def test_delete_annotation_column_stamps_column_and_cells(lifecycle_context):
    organization, _, workspace, dataset = lifecycle_context
    label = AnnotationsLabels.objects.create(
        name="Lifecycle Label",
        type=AnnotationTypeChoices.TEXT.value,
        organization=organization,
        workspace=workspace,
        settings={"placeholder": "", "min_length": 0, "max_length": 100},
    )
    annotation = Annotations.objects.create(
        name="Lifecycle Annotation",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
    )
    column = Column.objects.create(
        name="Annotation Output",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.ANNOTATION_LABEL.value,
        source_id=f"{annotation.id}-sourceid-{label.id}",
    )
    row = Row.objects.create(dataset=dataset, order=0)
    cell = Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value="annotation",
    )
    annotation.labels.add(label)
    annotation.columns.add(column)
    dataset.column_order = [str(column.id)]
    dataset.save(update_fields=["column_order"])
    column_updated_at = column.updated_at
    cell_updated_at = cell.updated_at

    result = delete_column(
        dataset_id=str(dataset.id),
        column_id=str(column.id),
        organization=organization,
    )

    assert result["column_id"] == str(column.id)
    _assert_soft_deleted(column, column_updated_at)
    _assert_soft_deleted(cell, cell_updated_at)
    assert column.updated_at == cell.updated_at
    assert annotation.labels.filter(id=label.id).exists() is False
    assert annotation.columns.filter(id=column.id).exists() is False


@pytest.mark.django_db
def test_delete_rows_stamps_rows_and_cells(lifecycle_context):
    organization, _, _, dataset = lifecycle_context
    column = Column.objects.create(
        name="Row Value",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    cell = Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value="value",
    )
    row_updated_at = row.updated_at
    cell_updated_at = cell.updated_at

    result = delete_rows(
        dataset_id=str(dataset.id),
        row_ids=[str(row.id)],
        organization=organization,
    )

    assert result["deleted"] == 1
    _assert_soft_deleted(row, row_updated_at)
    _assert_soft_deleted(cell, cell_updated_at)
    assert row.updated_at == cell.updated_at
