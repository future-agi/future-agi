"""The TypeScript dataset snippets use the field names the TypeScript SDK reads.

The "Add data using SDK" dialog shows these snippets. @future-agi/sdk builds a
cell from ``column_name`` and a column from ``data_type``; camelCase keys are
ignored, so every cell would be sent without a column name and every column
would be created as text.
"""

import re
import uuid

import pytest

from model_hub.constants import get_curl_ts_code


@pytest.fixture
def typescript_snippets():
    _, _, add_columns, add_rows = get_curl_ts_code(
        dataset_id=uuid.uuid4(),
        api_key="YOUR_API_KEY",
        secret_key="YOUR_SECRET_KEY",
        dataset_name="snippet-dataset",
    )
    return {"add_columns": add_columns, "add_rows": add_rows}


@pytest.mark.parametrize("snippet", ["add_columns", "add_rows"])
def test_typescript_cells_name_their_column_with_column_name(
    typescript_snippets, snippet
):
    cells = re.findall(r"createCell\(\{(.*?)\}\)", typescript_snippets[snippet])

    assert len(cells) == 6
    for cell in cells:
        assert re.search(r"\bcolumn_name: \"[a-z_]+\"", cell), cell
        assert "columnName" not in cell


def test_typescript_columns_set_their_type_with_data_type(typescript_snippets):
    columns = re.findall(r"\{ name: [^}]*\}", typescript_snippets["add_columns"])

    assert len(columns) == 3
    for column in columns:
        assert re.search(r"\bdata_type: DataTypeChoices\.[A-Z]+\b", column), column
        assert "dataType" not in column
