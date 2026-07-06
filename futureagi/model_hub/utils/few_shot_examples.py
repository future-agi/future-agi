from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Case, IntegerField, Value, When

from agentic_eval.core.utils.functions import is_uuid

STATIC_FEW_SHOT_EXAMPLE_LIMIT = 20
STATIC_FEW_SHOT_COLUMNS = ("input", "output", "score")


def expand_static_few_shot_examples(
    few_shot_examples: list[dict] | None,
    organization=None,
    max_examples: int = STATIC_FEW_SHOT_EXAMPLE_LIMIT,
) -> list[dict]:
    """
    Resolve eval config few-shot entries into the literal examples expected by
    CustomPromptEvaluator.
    """
    if not few_shot_examples or max_examples <= 0:
        return []

    literal_examples: list[dict] = []
    dataset_ids: list[str] = []

    for example in few_shot_examples:
        if not isinstance(example, dict):
            continue

        if "input" in example or "output" in example:
            if len(literal_examples) >= max_examples:
                continue
            literal_examples.append(example)
            continue

        dataset_id = example.get("id")
        if is_uuid(str(dataset_id)):
            dataset_ids.append(str(dataset_id))

    remaining = max_examples - len(literal_examples)
    if not dataset_ids or remaining <= 0:
        return literal_examples

    return literal_examples + _examples_from_datasets(
        dataset_ids,
        organization=organization,
        max_examples=remaining,
    )


def _examples_from_datasets(
    dataset_ids: Iterable[str],
    organization=None,
    max_examples: int = STATIC_FEW_SHOT_EXAMPLE_LIMIT,
) -> list[dict]:
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    ordered_ids = list(dict.fromkeys(str(dataset_id) for dataset_id in dataset_ids))
    if not ordered_ids or max_examples <= 0:
        return []

    dataset_filter = {"id__in": ordered_ids, "deleted": False}
    if organization is not None:
        dataset_filter["organization"] = organization

    datasets = Dataset.objects.filter(**dataset_filter).only("id")
    datasets_by_id = {str(dataset.id): dataset for dataset in datasets}
    valid_dataset_ids = [
        dataset_id for dataset_id in ordered_ids if dataset_id in datasets_by_id
    ]
    if not valid_dataset_ids:
        return []

    columns = Column.objects.filter(
        dataset_id__in=valid_dataset_ids,
        deleted=False,
    ).only("id", "dataset_id", "name")
    columns_by_dataset_id: dict[str, dict[str, Column]] = {}
    for column in columns:
        dataset_columns = columns_by_dataset_id.setdefault(str(column.dataset_id), {})
        dataset_columns.setdefault(column.name.strip().lower(), column)

    selected_column_ids: set[str] = set()
    required_columns_by_dataset_id: dict[str, dict[str, Column]] = {}
    for dataset_id in valid_dataset_ids:
        dataset_columns = columns_by_dataset_id.get(dataset_id, {})
        input_column = dataset_columns.get("input")
        output_column = dataset_columns.get("output")
        if input_column is None or output_column is None:
            continue

        selected_columns = {
            "input": input_column,
            "output": output_column,
        }
        score_column = dataset_columns.get("score")
        if score_column is not None:
            selected_columns["score"] = score_column
        required_columns_by_dataset_id[dataset_id] = selected_columns
        selected_column_ids.update(
            str(column.id) for column in selected_columns.values()
        )

    if not required_columns_by_dataset_id:
        return []

    dataset_order = Case(
        *[
            When(dataset_id=dataset_id, then=Value(index))
            for index, dataset_id in enumerate(valid_dataset_ids)
        ],
        output_field=IntegerField(),
    )
    rows = list(
        Row.objects.filter(
            dataset_id__in=required_columns_by_dataset_id.keys(),
            deleted=False,
        )
        .annotate(dataset_order=dataset_order)
        .order_by("dataset_order", "order", "created_at")
        .only("id", "dataset_id", "order", "created_at")[:max_examples]
    )
    if not rows:
        return []

    cells = Cell.objects.filter(
        row_id__in=[str(row.id) for row in rows],
        column_id__in=selected_column_ids,
        deleted=False,
    ).only("row_id", "column_id", "value")
    values_by_row_and_column_id: dict[str, dict[str, str]] = {}
    for cell in cells:
        row_values = values_by_row_and_column_id.setdefault(str(cell.row_id), {})
        row_values[str(cell.column_id)] = (
            "" if cell.value is None else str(cell.value)
        )

    examples: list[dict] = []
    for row in rows:
        selected_columns = required_columns_by_dataset_id.get(str(row.dataset_id))
        if not selected_columns:
            continue
        row_values = values_by_row_and_column_id.get(str(row.id), {})
        input_value = row_values.get(str(selected_columns["input"].id), "")
        output_value = row_values.get(str(selected_columns["output"].id), "")
        if not input_value or not output_value:
            continue

        example = {"input": input_value, "output": output_value}
        score_column = selected_columns.get("score")
        if score_column is not None:
            score_value = row_values.get(str(score_column.id))
            if score_value is not None:
                example["score"] = score_value
        examples.append(example)

    return examples
