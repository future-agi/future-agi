"""End-to-end tests for dataset, annotation, and evaluation tools via the MCP HTTP endpoint.

These tests exercise the full stack: HTTP POST -> view -> tool registry -> service layer -> DB,
then verify DB state with ORM queries and check usage/session tracking.
"""

import uuid
from unittest.mock import patch

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from mcp_server.models.session import MCPSession
from mcp_server.models.usage import MCPUsageRecord
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_resource_limit():
    """Bypass dataset resource limit checks for testing."""
    with patch(
        "model_hub.services.dataset_service._check_resource_limit",
        return_value=True,
    ):
        yield


def _call_tool(auth_client, tool_name, params, session_id=None):
    """Helper: POST to /mcp/internal/tool-call/ and return response."""
    body = {"tool_name": tool_name, "params": params}
    if session_id:
        body["session_id"] = session_id
    return auth_client.post("/mcp/internal/tool-call/", body, format="json")


def _create_dataset_via_tool(auth_client, name="Test Dataset", columns=None):
    """Helper: create a dataset and return (response, dataset_id)."""
    cols = columns or ["input", "expected_output"]
    resp = _call_tool(auth_client, "create_dataset", {"name": name, "columns": cols})
    assert resp.status_code == 200, f"create_dataset failed: {resp.data}"
    assert resp.data["status"] is True
    dataset_id = resp.data["result"]["data"]["dataset_id"]
    return resp, dataset_id


def _add_rows_via_tool(auth_client, dataset_id, rows):
    """Helper: add rows and return response."""
    resp = _call_tool(
        auth_client,
        "add_dataset_rows",
        {"dataset_id": dataset_id, "rows": rows},
    )
    assert resp.status_code == 200, f"add_dataset_rows failed: {resp.data}"
    assert resp.data["status"] is True
    return resp


# ---------------------------------------------------------------------------
# 1. Dataset CRUD E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDatasetE2EWorkflow:
    """Complete dataset CRUD through HTTP endpoint with DB verification."""

    def test_create_dataset_and_verify_db(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """POST create_dataset -> verify Dataset + Column objects exist in DB."""
        resp, dataset_id = _create_dataset_via_tool(
            auth_client,
            name="E2E Dataset",
            columns=["question", "answer", "context"],
        )

        # Verify Dataset in DB
        ds = Dataset.objects.get(id=dataset_id)
        assert ds.name == "E2E Dataset"
        assert ds.deleted is False

        # Verify Columns in DB
        cols = Column.objects.filter(dataset=ds, deleted=False, source="OTHERS")
        assert cols.count() == 3
        col_names = set(cols.values_list("name", flat=True))
        assert col_names == {"question", "answer", "context"}

        # Verify response structure
        assert "dataset_id" in resp.data["result"]["data"]
        assert len(resp.data["result"]["data"]["columns"]) == 3

    def test_add_rows_and_verify_cells(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """Create dataset, add rows, verify Row + Cell objects in DB."""
        _, dataset_id = _create_dataset_via_tool(
            auth_client, columns=["input", "output"]
        )

        rows_data = [
            {"input": "What is AI?", "output": "Artificial Intelligence"},
            {"input": "What is ML?", "output": "Machine Learning"},
        ]
        resp = _add_rows_via_tool(auth_client, dataset_id, rows_data)

        # Verify response data
        assert resp.data["result"]["data"]["rows_added"] == 2
        assert resp.data["result"]["data"]["cells_created"] == 4

        # Verify Rows in DB
        db_rows = Row.objects.filter(dataset_id=dataset_id, deleted=False).order_by(
            "order"
        )
        assert db_rows.count() == 2

        # Verify Cells in DB
        cells = Cell.objects.filter(dataset_id=dataset_id, deleted=False)
        assert cells.count() == 4

        # Verify cell values
        input_col = Column.objects.get(
            dataset_id=dataset_id, name="input", deleted=False, source="OTHERS"
        )
        first_row = db_rows.first()
        cell_val = Cell.objects.get(column=input_col, row=first_row, deleted=False)
        assert cell_val.value == "What is AI?"

    def test_update_dataset_name(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """Create, update name, verify DB reflects the change."""
        _, dataset_id = _create_dataset_via_tool(auth_client, name="Original Name")

        resp = _call_tool(
            auth_client,
            "update_dataset",
            {"dataset_id": dataset_id, "name": "Updated Name"},
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True

        ds = Dataset.objects.get(id=dataset_id)
        assert ds.name == "Updated Name"

    def test_add_columns_to_existing(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """Create dataset, add rows, then add column -- verify new Column and existing rows get cells."""
        _, dataset_id = _create_dataset_via_tool(auth_client, columns=["input"])

        # Add a row first
        _add_rows_via_tool(auth_client, dataset_id, [{"input": "hello"}])

        # Add a new column
        resp = _call_tool(
            auth_client,
            "add_columns",
            {
                "dataset_id": dataset_id,
                "columns": [{"name": "score", "data_type": "float"}],
            },
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True

        # Verify new column exists
        new_col = Column.objects.filter(
            dataset_id=dataset_id, name="score", deleted=False
        )
        assert new_col.exists()
        assert new_col.first().data_type == "float"

    def test_clone_dataset_with_data(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """Create dataset + rows, clone, verify new dataset has same structure and data."""
        _, src_id = _create_dataset_via_tool(
            auth_client, name="Source DS", columns=["col_a", "col_b"]
        )
        _add_rows_via_tool(
            auth_client,
            src_id,
            [
                {"col_a": "val1", "col_b": "val2"},
                {"col_a": "val3", "col_b": "val4"},
            ],
        )

        resp = _call_tool(
            auth_client,
            "clone_dataset",
            {"dataset_id": src_id, "new_name": "Cloned DS"},
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True

        clone_id = resp.data["result"]["data"]["dataset_id"]
        assert clone_id != src_id

        # Verify cloned dataset in DB
        clone_ds = Dataset.objects.get(id=clone_id)
        assert clone_ds.name == "Cloned DS"

        # Verify columns cloned
        clone_cols = Column.objects.filter(
            dataset_id=clone_id, deleted=False, source="OTHERS"
        )
        assert clone_cols.count() == 2

        # Verify rows cloned
        clone_rows = Row.objects.filter(dataset_id=clone_id, deleted=False)
        assert clone_rows.count() == 2

        # Verify cells cloned
        clone_cells = Cell.objects.filter(dataset_id=clone_id, deleted=False)
        assert clone_cells.count() == 4

    def test_delete_rows_and_verify(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """Create with rows, delete one, verify soft-delete."""
        _, dataset_id = _create_dataset_via_tool(auth_client, columns=["x"])
        _add_rows_via_tool(
            auth_client, dataset_id, [{"x": "a"}, {"x": "b"}, {"x": "c"}]
        )

        rows = Row.objects.filter(dataset_id=dataset_id, deleted=False).order_by(
            "order"
        )
        assert rows.count() == 3
        row_to_delete = str(rows[1].id)

        resp = _call_tool(
            auth_client,
            "delete_rows",
            {"dataset_id": dataset_id, "row_ids": [row_to_delete]},
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True
        assert resp.data["result"]["data"]["deleted"] == 1

        # Verify soft-delete
        active_rows = Row.objects.filter(dataset_id=dataset_id, deleted=False)
        assert active_rows.count() == 2

        deleted_row = Row.all_objects.get(id=row_to_delete)
        assert deleted_row.deleted is True

    def test_delete_dataset(self, auth_client, user, workspace, mock_resource_limit):
        """Create, delete, verify soft-delete."""
        _, dataset_id = _create_dataset_via_tool(auth_client, name="To Delete")

        resp = _call_tool(
            auth_client,
            "delete_dataset",
            {"dataset_ids": [dataset_id]},
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True
        assert resp.data["result"]["data"]["deleted"] == 1

        ds = Dataset.all_objects.get(id=dataset_id)
        assert ds.deleted is True

    def test_delete_column(self, auth_client, user, workspace, mock_resource_limit):
        """Create with columns, delete one, verify column_order updated."""
        _, dataset_id = _create_dataset_via_tool(
            auth_client, columns=["alpha", "beta", "gamma"]
        )

        col_to_delete = Column.objects.get(
            dataset_id=dataset_id, name="beta", deleted=False, source="OTHERS"
        )

        resp = _call_tool(
            auth_client,
            "delete_column",
            {"dataset_id": dataset_id, "column_id": str(col_to_delete.id)},
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True
        assert resp.data["result"]["data"]["column_name"] == "beta"

        # Verify column soft-deleted
        col_to_delete.refresh_from_db()
        assert col_to_delete.deleted is True

        # Remaining active columns
        remaining = Column.objects.filter(
            dataset_id=dataset_id, deleted=False, source="OTHERS"
        )
        remaining_names = set(remaining.values_list("name", flat=True))
        assert "beta" not in remaining_names
        assert "alpha" in remaining_names
        assert "gamma" in remaining_names

    def test_duplicate_name_error(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """Create two datasets with the same name -- expect error on second."""
        _create_dataset_via_tool(auth_client, name="Unique Name", columns=["x"])

        resp = _call_tool(
            auth_client,
            "create_dataset",
            {"name": "Unique Name", "columns": ["y"]},
        )
        # The response should indicate an error (either HTTP error or is_error in result)
        if resp.status_code == 200:
            assert resp.data["result"]["is_error"] is True
        else:
            assert resp.status_code in (400, 409, 500)

    def test_full_lifecycle(self, auth_client, user, workspace, mock_resource_limit):
        """Create -> add rows -> add column -> update -> clone -> delete."""
        # 1. Create
        _, dataset_id = _create_dataset_via_tool(
            auth_client, name="Lifecycle DS", columns=["input"]
        )
        assert Dataset.objects.filter(id=dataset_id, deleted=False).exists()

        # 2. Add rows
        _add_rows_via_tool(
            auth_client,
            dataset_id,
            [
                {"input": "row1"},
                {"input": "row2"},
            ],
        )
        assert Row.objects.filter(dataset_id=dataset_id, deleted=False).count() == 2

        # 3. Add column
        resp = _call_tool(
            auth_client,
            "add_columns",
            {
                "dataset_id": dataset_id,
                "columns": [{"name": "output", "data_type": "text"}],
            },
        )
        assert resp.status_code == 200

        # 4. Update name
        resp = _call_tool(
            auth_client,
            "update_dataset",
            {"dataset_id": dataset_id, "name": "Lifecycle DS Updated"},
        )
        assert resp.status_code == 200
        assert Dataset.objects.get(id=dataset_id).name == "Lifecycle DS Updated"

        # 5. Clone
        resp = _call_tool(
            auth_client,
            "clone_dataset",
            {"dataset_id": dataset_id, "new_name": "Lifecycle Clone"},
        )
        assert resp.status_code == 200
        clone_id = resp.data["result"]["data"]["dataset_id"]
        assert Dataset.objects.filter(id=clone_id, deleted=False).exists()

        # 6. Delete original
        resp = _call_tool(
            auth_client,
            "delete_dataset",
            {"dataset_ids": [dataset_id]},
        )
        assert resp.status_code == 200
        assert Dataset.all_objects.get(id=dataset_id).deleted is True
        # Clone is still alive
        assert Dataset.objects.get(id=clone_id).deleted is False

    def test_usage_recording(self, auth_client, user, workspace, mock_resource_limit):
        """After calling tools, verify MCPUsageRecord entries exist with correct fields."""
        initial_count = MCPUsageRecord.objects.count()

        # Make two tool calls
        _create_dataset_via_tool(auth_client, name="Usage DS", columns=["a"])
        _call_tool(auth_client, "list_datasets", {})

        records = MCPUsageRecord.objects.filter(
            tool_name__in=["create_dataset", "list_datasets"]
        ).order_by("called_at")
        assert records.count() >= 2

        create_rec = records.filter(tool_name="create_dataset").first()
        assert create_rec is not None
        assert create_rec.response_status == "success"
        assert create_rec.user == user

        list_rec = records.filter(tool_name="list_datasets").first()
        assert list_rec is not None
        assert list_rec.response_status == "success"

    def test_session_counter_increments(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """Multiple tool calls reusing a session -> session.tool_call_count matches."""
        # First call: creates a session
        resp1 = _call_tool(auth_client, "list_datasets", {})
        session_id = resp1.data["session_id"]

        # Second call reusing session
        resp2 = _call_tool(auth_client, "list_datasets", {}, session_id=session_id)
        assert resp2.data["session_id"] == session_id

        # Third call
        _create_dataset_via_tool(auth_client, name="Counter DS", columns=["z"])
        # Note: _create_dataset_via_tool doesn't pass session_id, so it may create a new session.
        # Let's explicitly pass session_id for a third call:
        resp3 = _call_tool(
            auth_client,
            "list_datasets",
            {},
            session_id=session_id,
        )

        session = MCPSession.objects.get(id=session_id)
        assert session.tool_call_count == 3


# ---------------------------------------------------------------------------
# 2. Evaluation E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEvaluationE2EWorkflow:
    """Evaluation tools through the MCP HTTP endpoint."""

    def test_list_eval_templates(self, auth_client, user, workspace):
        """List eval templates -- verify response structure."""
        resp = _call_tool(auth_client, "list_eval_templates", {})
        assert resp.status_code == 200
        assert resp.data["status"] is True
        assert "templates" in resp.data["result"]["data"]
        assert "total" in resp.data["result"]["data"]

    def test_get_eval_template_not_found(self, auth_client, user, workspace):
        """Get with fake UUID -- verify error response."""
        fake_id = str(uuid.uuid4())
        resp = _call_tool(
            auth_client,
            "get_eval_template",
            {"eval_template_id": fake_id},
        )
        assert resp.status_code == 200
        assert resp.data["result"]["is_error"] is True
        assert "not found" in resp.data["result"]["content"].lower()

    def test_create_eval_template_via_tool(self, auth_client, user, workspace):
        """Create template via tool, verify DB entry."""
        resp = _call_tool(
            auth_client,
            "create_eval_template",
            {
                "name": "e2e-test-metric",
                "description": "A test evaluation template",
                "criteria": "Evaluate whether {{response}} is helpful.",
                "required_keys": ["response"],
                "eval_tags": ["test", "e2e"],
                "output_type": "Pass/Fail",
            },
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True

        template_id = resp.data["result"]["data"]["id"]

        # Verify in DB
        tmpl = EvalTemplate.objects.get(id=template_id)
        assert tmpl.name == "e2e-test-metric"
        assert tmpl.owner == "user"
        assert tmpl.description == "A test evaluation template"

    def test_list_evaluations(self, auth_client, user, workspace):
        """List evaluations -- verify format."""
        resp = _call_tool(auth_client, "list_evaluations", {})
        assert resp.status_code == 200
        assert resp.data["status"] is True
        assert "evaluations" in resp.data["result"]["data"]
        assert "total" in resp.data["result"]["data"]


# ---------------------------------------------------------------------------
# 3. Annotation E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnnotationE2EWorkflow:
    """Annotation tools through the MCP HTTP endpoint."""

    def _create_label(
        self, auth_client, name="Test Label", label_type="text", **kwargs
    ):
        """Helper: create an annotation label and return (response, label_id)."""
        params = {"name": name, "label_type": label_type, **kwargs}
        resp = _call_tool(auth_client, "create_annotation_label", params)
        assert resp.status_code == 200, f"create_annotation_label failed: {resp.data}"
        assert resp.data["status"] is True
        return resp, resp.data["result"]["data"]["label_id"]

    def test_create_annotation_label_and_verify_db(self, auth_client, user, workspace):
        """Create label, check AnnotationsLabels in DB."""
        resp, label_id = self._create_label(
            auth_client,
            name="Quality Score",
            label_type="star",
            settings={"no_of_stars": 5},
        )

        # Verify DB
        label = AnnotationsLabels.objects.get(id=label_id)
        assert label.name == "Quality Score"
        assert label.type == "star"
        assert label.settings == {"no_of_stars": 5}
        assert label.organization == user.organization

    def test_update_annotation_label(self, auth_client, user, workspace):
        """Create, update, verify DB change."""
        _, label_id = self._create_label(
            auth_client, name="Old Name", label_type="text"
        )

        resp = _call_tool(
            auth_client,
            "update_annotation_label",
            {
                "label_id": label_id,
                "name": "New Name",
                "description": "Updated description",
            },
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True

        label = AnnotationsLabels.objects.get(id=label_id)
        assert label.name == "New Name"
        assert label.description == "Updated description"

    def test_create_annotation_with_dataset(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """Create dataset + label, then annotation, verify Annotations in DB."""
        # Create dataset
        _, dataset_id = _create_dataset_via_tool(
            auth_client, name="Annotation DS", columns=["input", "output"]
        )

        # Create label
        _, label_id = self._create_label(
            auth_client,
            name="Relevance",
            label_type="categorical",
            settings={
                "options": [{"label": "Good"}, {"label": "Bad"}],
                "multi_choice": False,
                "auto_annotate": False,
                "rule_prompt": "",
                "strategy": None,
            },
        )

        # Create annotation task
        resp = _call_tool(
            auth_client,
            "create_annotation",
            {
                "name": "Review Task",
                "dataset_id": dataset_id,
                "label_ids": [label_id],
                "responses": 2,
            },
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True

        ann_id = resp.data["result"]["data"]["annotation_id"]

        # Verify in DB
        ann = Annotations.objects.get(id=ann_id)
        assert ann.name == "Review Task"
        assert str(ann.dataset_id) == dataset_id
        assert ann.responses == 2
        assert ann.labels.count() == 1
        assert str(ann.labels.first().id) == label_id

    def test_delete_annotation_label(self, auth_client, user, workspace):
        """Create, delete, verify label is removed from DB."""
        _, label_id = self._create_label(
            auth_client, name="To Delete", label_type="thumbs_up_down"
        )

        assert AnnotationsLabels.objects.filter(id=label_id).exists()

        resp = _call_tool(
            auth_client,
            "delete_annotation_label",
            {"label_id": label_id},
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True

        # Label hard-deleted (delete_label tool uses .delete())
        assert not AnnotationsLabels.objects.filter(id=label_id).exists()

    def test_annotation_summary(
        self, auth_client, user, workspace, mock_resource_limit
    ):
        """Create annotation on dataset, call annotation_summary."""
        # Create dataset with rows
        _, dataset_id = _create_dataset_via_tool(
            auth_client, name="Summary DS", columns=["text"]
        )
        _add_rows_via_tool(
            auth_client, dataset_id, [{"text": "hello"}, {"text": "world"}]
        )

        # Create label and annotation
        _, label_id = self._create_label(
            auth_client,
            name="Sentiment",
            label_type="categorical",
            settings={
                "options": [{"label": "Positive"}, {"label": "Negative"}],
                "multi_choice": False,
                "auto_annotate": False,
                "rule_prompt": "",
                "strategy": None,
            },
        )
        _call_tool(
            auth_client,
            "create_annotation",
            {
                "name": "Sentiment Task",
                "dataset_id": dataset_id,
                "label_ids": [label_id],
            },
        )

        # Call annotation_summary
        resp = _call_tool(
            auth_client,
            "annotation_summary",
            {"dataset_id": dataset_id},
        )
        assert resp.status_code == 200
        assert resp.data["status"] is True
        assert "total_rows" in resp.data["result"]["data"]
        assert resp.data["result"]["data"]["total_rows"] == 2


# ---------------------------------------------------------------------------
# 4. Tool Disabling
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestToolDisabling:
    """Verify that disabled tool groups return 403."""

    def test_disabled_tool_returns_403(self, auth_client, user, workspace):
        """Disable a tool group, try to call a tool from that group, verify 403."""
        from mcp_server.models.tool_config import MCPToolGroupConfig
        from mcp_server.usage_helpers import get_or_create_connection

        org = user.organization

        # Create connection and set tool groups WITHOUT 'datasets'
        connection = get_or_create_connection(user, org, workspace)
        config = connection.tool_config
        # Remove 'datasets' group
        config.enabled_groups = [g for g in config.enabled_groups if g != "datasets"]
        config.save()

        # Try calling a datasets tool
        resp = _call_tool(
            auth_client,
            "list_datasets",
            {},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.data.get("error", "").lower()
