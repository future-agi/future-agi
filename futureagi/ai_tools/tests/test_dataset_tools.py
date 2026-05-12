import uuid

import pytest

from ai_tools.registry import registry
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import (
    make_dataset,
    make_dataset_with_rows,
    make_eval_template,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def test_dataset_eval_required_keys_prefers_template_config():
    from ai_tools.tools.datasets.add_dataset_eval import _get_template_required_keys

    class Template:
        config = {"required_keys": ["output", "expected"]}
        required_fields = ["legacy_only"]

    assert _get_template_required_keys(Template()) == ["output", "expected"]


@pytest.fixture
def dataset(tool_context):
    from model_hub.models.develop_dataset import Column, Dataset

    ds = Dataset(
        name="Test Dataset",
        source="sdk",
        organization=tool_context.organization,
        workspace=tool_context.workspace,
        user=tool_context.user,
        column_order=[],
        model_type="GenerativeLLM",
    )
    # Save without validation to bypass column_order UUID check with empty list
    ds.save()

    # Create columns and update column_order with their UUIDs
    cols = []
    for name, dtype in [("input", "text"), ("output", "text"), ("expected", "text")]:
        col = Column.objects.create(
            name=name,
            data_type=dtype,
            dataset=ds,
            source="evaluation",
        )
        cols.append(col)

    ds.column_order = [str(c.id) for c in cols]
    ds.save()

    return ds


@pytest.fixture
def dataset_with_columns(dataset):
    from model_hub.models.develop_dataset import Column

    cols = list(Column.objects.filter(dataset=dataset, deleted=False))
    return dataset, cols


@pytest.fixture
def writable_dataset(tool_context, mock_resource_limit):
    """Dataset created via the service layer with 'others' source columns."""
    return make_dataset(
        tool_context,
        name="Writable Dataset",
        columns=[("input", "text"), ("output", "text"), ("context", "text")],
    )


@pytest.fixture
def populated_dataset(tool_context, mock_resource_limit):
    """Dataset with rows already populated."""
    ds, cols, rows = make_dataset_with_rows(
        tool_context,
        name="Populated Dataset",
        row_data=[
            {"input": "hello", "output": "world"},
            {"input": "foo", "output": "bar"},
        ],
    )
    return ds, cols, rows


# ===================================================================
# READ TOOLS
# ===================================================================


class TestListDatasetsTool:
    def test_list_empty(self, tool_context):
        tool = registry.get("list_datasets")
        result = tool.run({}, tool_context)

        assert not result.is_error
        assert "Datasets (0)" in result.content
        assert result.data["total"] == 0

    def test_list_with_data(self, tool_context, dataset):
        tool = registry.get("list_datasets")
        result = tool.run({}, tool_context)

        assert not result.is_error
        assert "Datasets (1)" in result.content
        assert "Test Dataset" in result.content
        assert result.data["total"] == 1

    def test_list_filter_by_source(self, tool_context, dataset):
        tool = registry.get("list_datasets")

        result = tool.run({"source": "sdk"}, tool_context)
        assert result.data["total"] == 1

        result = tool.run({"source": "build"}, tool_context)
        assert result.data["total"] == 0

    def test_list_pagination(self, tool_context, dataset):
        tool = registry.get("list_datasets")
        result = tool.run({"limit": 1, "offset": 0}, tool_context)

        assert not result.is_error
        assert len(result.data["datasets"]) <= 1


class TestGetDatasetTool:
    def test_get_existing(self, tool_context, dataset_with_columns):
        ds, cols = dataset_with_columns
        tool = registry.get("get_dataset")
        result = tool.run({"dataset_id": str(ds.id)}, tool_context)

        assert not result.is_error
        assert "Test Dataset" in result.content
        assert "input" in result.content
        assert "output" in result.content
        assert result.data["id"] == str(ds.id)
        assert result.data["row_count"] == 0
        assert len(result.data["columns"]) == 3

    def test_get_nonexistent(self, tool_context):
        tool = registry.get("get_dataset")
        fake_id = str(uuid.uuid4())
        result = tool.run({"dataset_id": fake_id}, tool_context)

        assert result.is_error
        assert "Not Found" in result.content

    def test_get_shows_schema(self, tool_context, dataset_with_columns):
        ds, cols = dataset_with_columns
        tool = registry.get("get_dataset")
        result = tool.run({"dataset_id": str(ds.id)}, tool_context)

        assert "Schema" in result.content
        assert "Column" in result.content
        assert "Type" in result.content

    def test_get_invalid_uuid(self, tool_context):
        tool = registry.get("get_dataset")
        result = tool.run({"dataset_id": "not-a-uuid"}, tool_context)

        assert result.is_error


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestCreateDatasetTool:
    def test_create_schema_discourages_blank_rows_when_values_known(self):
        tool = registry.get("create_dataset")
        schema = tool.input_schema

        rows_description = schema["properties"]["number_of_rows"]["description"]
        blank_rows_description = schema["properties"]["create_blank_rows"][
            "description"
        ]

        assert "planning only" in rows_description
        assert "blank placeholder rows" in blank_rows_description
        assert "create_blank_rows=true" in tool.description

    def test_create_basic(self, tool_context, mock_resource_limit):
        result = run_tool(
            "create_dataset",
            {"name": "New DS", "columns": ["col_a", "col_b"]},
            tool_context,
        )

        assert not result.is_error
        assert "Dataset Created" in result.content
        assert "New DS" in result.content
        assert result.data["dataset_id"]
        assert len(result.data["columns"]) == 2
        assert result.data["columns"][0]["type"] == "text"

    def test_create_with_types(self, tool_context, mock_resource_limit):
        result = run_tool(
            "create_dataset",
            {
                "name": "Typed DS",
                "columns": ["score", "flag", "data"],
                "column_types": ["float", "boolean", "json"],
            },
            tool_context,
        )

        assert not result.is_error
        types = [c["type"] for c in result.data["columns"]]
        assert types == ["float", "boolean", "json"]

    def test_create_number_of_rows_does_not_create_blanks_by_default(
        self, tool_context, mock_resource_limit
    ):
        result = run_tool(
            "create_dataset",
            {"name": "Planned Rows DS", "columns": ["input"], "number_of_rows": 20},
            tool_context,
        )

        assert not result.is_error
        assert result.data["rows_created"] == 0
        assert result.data["requested_row_count"] == 20
        assert result.data["requires_add_dataset_rows"] is True
        assert "add_dataset_rows" in result.content

    def test_create_blank_rows_requires_explicit_flag(
        self, tool_context, mock_resource_limit
    ):
        result = run_tool(
            "create_dataset",
            {
                "name": "Blank Rows DS",
                "columns": ["input"],
                "number_of_rows": 3,
                "create_blank_rows": True,
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["rows_created"] == 3
        assert result.data["requires_add_dataset_rows"] is False

    def test_create_duplicate_name(self, tool_context, mock_resource_limit):
        run_tool("create_dataset", {"name": "Dup", "columns": ["a"]}, tool_context)
        result = run_tool(
            "create_dataset", {"name": "Dup", "columns": ["a"]}, tool_context
        )

        assert not result.is_error
        assert result.data["already_exists"] is True
        assert "Dataset Already Exists" in result.content

    def test_create_mismatched_types_length(self, tool_context, mock_resource_limit):
        result = run_tool(
            "create_dataset",
            {"name": "Bad", "columns": ["a", "b"], "column_types": ["text"]},
            tool_context,
        )

        assert result.is_error
        assert "VALIDATION_ERROR" == result.error_code

    def test_create_invalid_type(self, tool_context, mock_resource_limit):
        result = run_tool(
            "create_dataset",
            {"name": "Bad2", "columns": ["a"], "column_types": ["invalid_type"]},
            tool_context,
        )

        assert result.is_error
        assert "Invalid column type" in result.content

    def test_create_empty_columns_rejected(self, tool_context, mock_resource_limit):
        result = run_tool(
            "create_dataset",
            {"name": "Empty", "columns": []},
            tool_context,
        )

        assert not result.is_error
        assert result.status == "success"
        assert result.data["requires_columns"] is True


class TestAddDatasetRowsTool:
    def test_add_rows_schema_requires_rows_in_same_call(self):
        tool = registry.get("add_dataset_rows")
        schema = tool.input_schema

        rows_description = schema["properties"]["rows"]["description"]

        assert "Do not call this tool without rows" in rows_description
        assert "include the rows parameter in the same call" in tool.description

    def test_add_rows(self, tool_context, writable_dataset, mock_resource_limit):
        result = run_tool(
            "add_dataset_rows",
            {
                "dataset_id": str(writable_dataset.id),
                "rows": [
                    {"input": "q1", "output": "a1"},
                    {"input": "q2", "output": "a2"},
                ],
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["rows_added"] == 2
        assert result.data["total_rows"] == 2

    def test_add_rows_partial_columns(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        """Rows can have subset of columns — missing get empty string."""
        result = run_tool(
            "add_dataset_rows",
            {
                "dataset_id": str(writable_dataset.id),
                "rows": [{"input": "only input"}],
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["rows_added"] == 1
        # Cells created for all writable columns, not just provided ones
        assert result.data["cells_created"] == 3

    def test_add_rows_unknown_column(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        result = run_tool(
            "add_dataset_rows",
            {
                "dataset_id": str(writable_dataset.id),
                "rows": [{"nonexistent_col": "value"}],
            },
            tool_context,
        )

        # Service silently ignores unknown columns and creates rows with defaults
        assert not result.is_error
        assert result.data["rows_added"] == 1

    def test_add_rows_nonexistent_dataset(self, tool_context, mock_resource_limit):
        result = run_tool(
            "add_dataset_rows",
            {"dataset_id": str(uuid.uuid4()), "rows": [{"a": "b"}]},
            tool_context,
        )

        assert result.is_error

    def test_add_rows_empty_list_rejected(self, tool_context, mock_resource_limit):
        result = run_tool(
            "add_dataset_rows",
            {"dataset_id": str(uuid.uuid4()), "rows": []},
            tool_context,
        )

        assert result.is_error  # Pydantic min_length=1


class TestAddDatasetEvalTool:
    def test_missing_template_returns_candidates(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        make_eval_template(tool_context, name="Candidate Eval")

        result = run_tool(
            "add_dataset_eval",
            {
                "dataset_id": str(writable_dataset.id),
                "template_id": str(uuid.uuid4()),
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_template_id"] is True
        assert result.data["templates"]
        assert "Eval Template Not Found" in result.content


class TestDeleteDatasetTool:
    def test_delete_single(self, tool_context, writable_dataset, mock_resource_limit):
        ds_id = str(writable_dataset.id)
        result = run_tool(
            "delete_dataset",
            {"dataset_ids": [ds_id]},
            tool_context,
        )

        assert not result.is_error
        assert result.data["deleted"] == 1
        assert "Writable Dataset" in result.data["names"]

        # Verify soft-deleted — shouldn't appear in list
        list_result = run_tool("list_datasets", {}, tool_context)
        assert list_result.data["total"] == 0

    def test_delete_nonexistent(self, tool_context, mock_resource_limit):
        result = run_tool(
            "delete_dataset",
            {"dataset_ids": [str(uuid.uuid4())]},
            tool_context,
        )

        assert result.is_error

    def test_delete_already_deleted(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        ds_id = str(writable_dataset.id)
        run_tool("delete_dataset", {"dataset_ids": [ds_id]}, tool_context)
        result = run_tool("delete_dataset", {"dataset_ids": [ds_id]}, tool_context)

        assert result.is_error


class TestCloneDatasetTool:
    def test_clone_basic(self, tool_context, writable_dataset, mock_resource_limit):
        result = run_tool(
            "clone_dataset",
            {"dataset_id": str(writable_dataset.id), "new_name": "Cloned DS"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "Cloned DS"
        assert result.data["columns"] > 0

    def test_clone_default_name(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        result = run_tool(
            "clone_dataset",
            {"dataset_id": str(writable_dataset.id)},
            tool_context,
        )

        assert not result.is_error
        assert "Copy of" in result.data["name"]

    def test_clone_with_rows(
        self, tool_context, populated_dataset, mock_resource_limit
    ):
        ds, cols, rows = populated_dataset
        result = run_tool(
            "clone_dataset",
            {"dataset_id": str(ds.id), "new_name": "Clone With Rows"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["rows"] == 2

    def test_clone_duplicate_name(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        run_tool(
            "clone_dataset",
            {"dataset_id": str(writable_dataset.id), "new_name": "Clone1"},
            tool_context,
        )
        result = run_tool(
            "clone_dataset",
            {"dataset_id": str(writable_dataset.id), "new_name": "Clone1"},
            tool_context,
        )

        assert not result.is_error
        assert "already exist" in result.content
        assert result.data["already_exists"] is True

    def test_clone_nonexistent(self, tool_context, mock_resource_limit):
        result = run_tool(
            "clone_dataset",
            {"dataset_id": str(uuid.uuid4()), "new_name": "Clone"},
            tool_context,
        )

        assert result.is_error


class TestAddColumnsTool:
    def test_add_single_column(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        result = run_tool(
            "add_columns",
            {
                "dataset_id": str(writable_dataset.id),
                "columns": [{"name": "new_col", "data_type": "text"}],
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["columns"][0]["name"] == "new_col"

    def test_add_multiple_columns(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        result = run_tool(
            "add_columns",
            {
                "dataset_id": str(writable_dataset.id),
                "columns": [
                    {"name": "score", "data_type": "float"},
                    {"name": "flag", "data_type": "boolean"},
                ],
            },
            tool_context,
        )

        assert not result.is_error
        assert len(result.data["columns"]) == 2

    def test_accepts_column_names_with_parallel_types(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        result = run_tool(
            "add_columns",
            {
                "dataset_id": str(writable_dataset.id),
                "columns": '["score_from_agent"]',
                "column_types": '["float"]',
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["columns"][0]["name"] == "score_from_agent"
        assert result.data["columns"][0]["data_type"] == "float"

    def test_add_duplicate_column_name(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        """Cannot add a column with same name as existing one."""
        result = run_tool(
            "add_columns",
            {
                "dataset_id": str(writable_dataset.id),
                "columns": [
                    {"name": "input", "data_type": "text"}
                ],  # 'input' already exists
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["already_exists"] is True
        assert "already exist" in result.content

    def test_add_invalid_type(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        result = run_tool(
            "add_columns",
            {
                "dataset_id": str(writable_dataset.id),
                "columns": [{"name": "bad", "data_type": "invalid_type"}],
            },
            tool_context,
        )

        assert result.is_error
        assert "Invalid" in result.content

    def test_add_columns_nonexistent_dataset(self, tool_context, mock_resource_limit):
        result = run_tool(
            "add_columns",
            {
                "dataset_id": str(uuid.uuid4()),
                "columns": [{"name": "a"}],
            },
            tool_context,
        )

        assert result.is_error


class TestDeleteColumnTool:
    def test_delete_column(self, tool_context, writable_dataset, mock_resource_limit):
        from model_hub.models.develop_dataset import Column

        col = Column.objects.filter(dataset=writable_dataset, deleted=False).first()
        result = run_tool(
            "delete_column",
            {
                "dataset_id": str(writable_dataset.id),
                "column_id": str(col.id),
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["column_name"] == col.name

        # Verify soft-deleted
        col.refresh_from_db()
        assert col.deleted is True

    def test_delete_nonexistent_column(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        result = run_tool(
            "delete_column",
            {
                "dataset_id": str(writable_dataset.id),
                "column_id": str(uuid.uuid4()),
            },
            tool_context,
        )

        assert result.is_error


class TestDeleteRowsTool:
    def test_delete_rows(self, tool_context, populated_dataset, mock_resource_limit):
        ds, cols, rows = populated_dataset
        row_id = str(rows[0].id)

        result = run_tool(
            "delete_rows",
            {"dataset_id": str(ds.id), "row_ids": [row_id]},
            tool_context,
        )

        assert not result.is_error
        assert result.data["deleted"] == 1
        assert result.data["remaining"] == 1

    def test_delete_all_rows(
        self, tool_context, populated_dataset, mock_resource_limit
    ):
        ds, cols, rows = populated_dataset
        all_ids = [str(r.id) for r in rows]

        result = run_tool(
            "delete_rows",
            {"dataset_id": str(ds.id), "row_ids": all_ids},
            tool_context,
        )

        assert not result.is_error
        assert result.data["deleted"] == 2
        assert result.data["remaining"] == 0

    def test_delete_nonexistent_rows(
        self, tool_context, populated_dataset, mock_resource_limit
    ):
        ds, _, _ = populated_dataset
        result = run_tool(
            "delete_rows",
            {"dataset_id": str(ds.id), "row_ids": [str(uuid.uuid4())]},
            tool_context,
        )

        # Service returns success with deleted=0 for nonexistent row IDs
        assert not result.is_error
        assert result.data["deleted"] == 0
        assert result.data["remaining"] == 2


class TestDatasetEvalRecovery:
    def test_get_eval_stats_missing_dataset_returns_candidates(self, tool_context):
        make_dataset(tool_context, name="Candidate Eval Stats Dataset")

        result = run_tool(
            "get_dataset_eval_stats",
            {"dataset_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_dataset_id"] is True
        assert "Dataset Not Found" in result.content

    def test_list_dataset_evals_missing_dataset_returns_candidates(self, tool_context):
        make_dataset(tool_context, name="Candidate Dataset Evals Dataset")

        result = run_tool(
            "list_dataset_evals",
            {"dataset_id": "missing-dataset-name"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_dataset_id"] is True
        assert "Dataset Not Found" in result.content


class TestUpdateDatasetTool:
    def test_rename(self, tool_context, writable_dataset, mock_resource_limit):
        result = run_tool(
            "update_dataset",
            {"dataset_id": str(writable_dataset.id), "name": "Renamed DS"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "Renamed DS"

    def test_rename_duplicate(self, tool_context, mock_resource_limit):
        ds1 = make_dataset(tool_context, name="DS One")
        ds2 = make_dataset(tool_context, name="DS Two")

        result = run_tool(
            "update_dataset",
            {"dataset_id": str(ds2.id), "name": "DS One"},
            tool_context,
        )

        assert result.is_error
        assert "already exists" in result.content

    def test_update_model_type(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        result = run_tool(
            "update_dataset",
            {
                "dataset_id": str(writable_dataset.id),
                "model_type": "BinaryClassification",
            },
            tool_context,
        )

        assert not result.is_error

    def test_update_nothing_fails(
        self, tool_context, writable_dataset, mock_resource_limit
    ):
        result = run_tool(
            "update_dataset",
            {"dataset_id": str(writable_dataset.id)},
            tool_context,
        )

        assert result.is_error
        assert "at least one" in result.content

    def test_update_nonexistent(self, tool_context, mock_resource_limit):
        result = run_tool(
            "update_dataset",
            {"dataset_id": str(uuid.uuid4()), "name": "New Name"},
            tool_context,
        )

        assert result.is_error


class TestUpdateCellValueTool:
    def test_update_cell(self, tool_context, populated_dataset, mock_resource_limit):
        from model_hub.models.develop_dataset import Cell

        ds, cols, rows = populated_dataset
        cell = Cell.objects.filter(dataset=ds, row=rows[0]).first()

        result = run_tool(
            "update_cell_value",
            {
                "dataset_id": str(ds.id),
                "updates": {str(cell.id): "updated value"},
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["updated"] == 1

        cell.refresh_from_db()
        assert cell.value == "updated value"

    def test_update_nonexistent_cell(
        self, tool_context, populated_dataset, mock_resource_limit
    ):
        ds, _, _ = populated_dataset
        result = run_tool(
            "update_cell_value",
            {
                "dataset_id": str(ds.id),
                "updates": {str(uuid.uuid4()): "nope"},
            },
            tool_context,
        )

        # All cells missing → is_error=True
        assert result.is_error
        assert result.data["updated"] == 0
        assert len(result.data["errors"]) == 1

    def test_update_nonexistent_dataset(self, tool_context, mock_resource_limit):
        result = run_tool(
            "update_cell_value",
            {
                "dataset_id": str(uuid.uuid4()),
                "updates": {"fake": "val"},
            },
            tool_context,
        )

        assert result.is_error

    def test_update_multiple_cells(
        self, tool_context, populated_dataset, mock_resource_limit
    ):
        from model_hub.models.develop_dataset import Cell

        ds, cols, rows = populated_dataset
        cells = Cell.objects.filter(dataset=ds, row=rows[0])[:2]

        updates = {str(c.id): f"new_{i}" for i, c in enumerate(cells)}
        result = run_tool(
            "update_cell_value",
            {"dataset_id": str(ds.id), "updates": updates},
            tool_context,
        )

        assert not result.is_error
        assert result.data["updated"] == 2
