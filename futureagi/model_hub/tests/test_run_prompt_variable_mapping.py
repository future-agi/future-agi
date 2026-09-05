"""Variable-to-column mapping coverage for prompt rendering.

``populate_placeholders`` used to key its render context by column name only, so
a prompt variable resolved only when a column happened to carry the same name.
These tests pin the mapping path that lets a column feed a variable it does not
share a name with, and pin that the name-keyed behaviour still works untouched.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from model_hub.views.run_prompt import populate_placeholders


class _Column:
    def __init__(self, col_id, name, data_type="text"):
        self.id = col_id
        self.name = name
        self.data_type = data_type


class _Cell:
    def __init__(self, value):
        self.value = value


def _render(messages, columns, cells, variable_mapping=None):
    """Run populate_placeholders against an in-memory dataset.

    ``columns`` maps column id to _Column, ``cells`` maps column id to its value
    for the single row under test.
    """
    dataset = mock.Mock()
    dataset.column_order = list(columns.keys())
    output_col_id = str(uuid.uuid4())

    def _get_column(id):  # noqa: A002 - mirrors Column.objects.get(id=...)
        return columns[str(id)]

    def _filter_cells(dataset, column, row__id):
        value = cells.get(str(column.id))
        result = mock.Mock()
        result.first.return_value = _Cell(value) if value is not None else None
        return result

    with (
        mock.patch(
            "model_hub.views.run_prompt.Dataset.objects.get", return_value=dataset
        ),
        mock.patch(
            "model_hub.views.run_prompt.Column.objects.get", side_effect=_get_column
        ),
        mock.patch(
            "model_hub.views.run_prompt.Cell.objects.filter", side_effect=_filter_cells
        ),
    ):
        return populate_placeholders(
            messages,
            dataset_id=str(uuid.uuid4()),
            row_id=str(uuid.uuid4()),
            col_id=output_col_id,
            model_name="gpt-4o",
            variable_mapping=variable_mapping,
        )


def _text_of(messages):
    content = messages[0]["content"]
    if isinstance(content, str):
        return content
    return " ".join(part.get("text", "") for part in content if isinstance(part, dict))


COLUMN_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def columns():
    return {COLUMN_ID: _Column(COLUMN_ID, "Question")}


@pytest.fixture
def cells():
    return {COLUMN_ID: "why is the sky blue"}


def test_mapped_column_feeds_a_variable_with_a_different_name(columns, cells):
    """The whole point of the mapping: `Question` can feed `{{question}}`."""
    messages = [{"role": "user", "content": "Answer {{question}}"}]

    out = _render(messages, columns, cells, variable_mapping={"question": COLUMN_ID})

    assert "why is the sky blue" in _text_of(out)


def test_without_a_mapping_the_mismatched_name_does_not_resolve(columns, cells):
    """Guards the bug this feature fixes: name equality alone leaves it unfilled."""
    messages = [{"role": "user", "content": "Answer {{question}}"}]

    out = _render(messages, columns, cells, variable_mapping=None)

    assert "why is the sky blue" not in _text_of(out)


def test_matching_column_names_still_resolve_without_a_mapping(columns, cells):
    """Backwards compatibility: prompts that already line up are untouched."""
    messages = [{"role": "user", "content": "Answer {{Question}}"}]

    out = _render(messages, columns, cells, variable_mapping=None)

    assert "why is the sky blue" in _text_of(out)


def test_mapping_falls_back_to_the_column_name(columns, cells):
    """A mapping stored as a column name still resolves."""
    messages = [{"role": "user", "content": "Answer {{question}}"}]

    out = _render(messages, columns, cells, variable_mapping={"question": "Question"})

    assert "why is the sky blue" in _text_of(out)
