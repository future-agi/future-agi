"""Atomic persistence for manual datasets and their ordered column schema."""

from django.db import transaction

from model_hub.models.choices import DatasetSourceChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row


@transaction.atomic
def create_blank_grid(*, name, organization, workspace, user, row_count, columns):
    dataset = Dataset.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
    )
    saved_columns = []
    column_config = {}
    for spec in columns:
        column = Column.objects.create(
            name=spec["name"],
            data_type=spec["type"],
            source=SourceChoices.OTHERS.value,
            dataset=dataset,
        )
        saved_columns.append(column)
        config = {"is_visible": True, "is_frozen": None}
        if spec.get("role"):
            config["role"] = spec["role"]
        column_config[str(column.id)] = config
    dataset.column_order = [str(column.id) for column in saved_columns]
    dataset.column_config = column_config
    dataset.dataset_config = {"eval_recommendations": ["Deterministic Evals"]}
    dataset.save()
    rows = Row.objects.bulk_create(
        [Row(dataset=dataset, order=index) for index in range(row_count)]
    )
    Cell.objects.bulk_create(
        [
            Cell(dataset=dataset, column=column, row=row, value=None)
            for row in rows
            for column in saved_columns
        ],
        batch_size=1000,
    )
    return dataset
