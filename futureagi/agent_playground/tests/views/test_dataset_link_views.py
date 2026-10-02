"""Tests for GraphDatasetViewSet."""

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status

from agent_playground.models.choices import GraphVersionStatus
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from model_hub.models.develop_dataset import Cell, Dataset, Row

# =============================================================================
# Dataset retrieve
# =============================================================================


@pytest.mark.unit
class TestDatasetRetrieve:
    """Tests for GET /graphs/<graph_id>/dataset/."""

    def test_success(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """Returns columns, rows with cells, and metadata."""
        url = reverse("graph-dataset-detail", kwargs={"graph_id": graph.id})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert str(result["dataset_id"]) == str(dataset.id)
        assert len(result["columns"]) == 2
        assert len(result["rows"]) == 1
        assert len(result["rows"][0]["cells"]) == 2
        assert "metadata" in result

    def test_not_found_no_graph_dataset(self, authenticated_client, graph):
        """404 when graph has no linked dataset."""
        url = reverse("graph-dataset-detail", kwargs={"graph_id": graph.id})
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, graph):
        """403/401 when not authenticated."""
        url = reverse("graph-dataset-detail", kwargs={"graph_id": graph.id})
        response = api_client.get(url)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_retrieve_with_version_id_filters_columns(
        self,
        authenticated_client,
        graph,
        graph_version,
        graph_dataset,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """Pass ?version_id=X, mock exposed ports to ["input_text"] → only 1 column returned."""
        url = reverse("graph-dataset-detail", kwargs={"graph_id": graph.id})
        with patch(
            "agent_playground.views.dataset_link._get_exposed_input_display_names",
            return_value={"input_text"},
        ):
            response = authenticated_client.get(
                url, {"version_id": str(graph_version.id)}
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert len(result["columns"]) == 1
        assert result["columns"][0]["name"] == "input_text"
        # Cells should also be filtered to the visible column only
        assert len(result["rows"]) == 1
        assert len(result["rows"][0]["cells"]) == 1

    def test_retrieve_without_version_id_uses_latest_version(
        self,
        authenticated_client,
        graph,
        graph_version,
        graph_dataset,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """No query param → mock is called with the latest version (by version_number)."""
        # Create a second version with higher version_number
        v2 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=99,
            status=GraphVersionStatus.DRAFT,
            tags=[],
        )

        url = reverse("graph-dataset-detail", kwargs={"graph_id": graph.id})
        with patch(
            "agent_playground.views.dataset_link._get_exposed_input_display_names",
            return_value={"input_text", "context"},
        ) as mock_fn:
            response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # The mock should have been called with the latest version's ID (v2)
        mock_fn.assert_called_once_with(v2.id)

    def test_retrieve_with_invalid_version_id_returns_404(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset,
    ):
        """Pass a non-existent UUID → 404."""
        url = reverse("graph-dataset-detail", kwargs={"graph_id": graph.id})
        fake_version_id = uuid.uuid4()
        response = authenticated_client.get(url, {"version_id": str(fake_version_id)})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_with_version_id_wrong_graph_returns_404(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset,
        organization,
        workspace,
        user,
    ):
        """Pass a version_id belonging to a different graph → 404."""
        other_graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Other Graph",
            created_by=user,
        )
        other_version = GraphVersion.no_workspace_objects.create(
            graph=other_graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
            tags=[],
        )

        url = reverse("graph-dataset-detail", kwargs={"graph_id": graph.id})
        response = authenticated_client.get(url, {"version_id": str(other_version.id)})

        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Dataset create row
# =============================================================================


@pytest.mark.unit
class TestDatasetCreateRow:
    """Tests for POST /graphs/<graph_id>/dataset/rows/."""

    def test_success(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset_columns,
    ):
        """Creates a row with one cell per column."""
        url = reverse("graph-dataset-row-create", kwargs={"graph_id": graph.id})
        response = authenticated_client.post(url, data={}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        result = response.data["result"]
        assert "id" in result
        assert result["order"] == 1
        assert len(result["cells"]) == len(dataset_columns)

    def test_increments_order(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """New row gets order = max_order + 1."""
        url = reverse("graph-dataset-row-create", kwargs={"graph_id": graph.id})
        response = authenticated_client.post(url, data={}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["order"] == 2

    def test_bad_request_no_columns(self, authenticated_client, graph, graph_dataset):
        """400 when no columns exist in the dataset."""
        url = reverse("graph-dataset-row-create", kwargs={"graph_id": graph.id})
        response = authenticated_client.post(url, data={}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_not_found_no_graph_dataset(self, authenticated_client, graph):
        """404 when graph has no linked dataset."""
        url = reverse("graph-dataset-row-create", kwargs={"graph_id": graph.id})
        response = authenticated_client.post(url, data={}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Dataset delete rows
# =============================================================================


@pytest.mark.unit
class TestDatasetDeleteRows:
    """Tests for DELETE /graphs/<graph_id>/dataset/rows/delete/."""

    def test_success_with_row_ids(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """Soft-deletes rows and their cells by row_ids (keeps at least one)."""
        row, cells = dataset_row_with_cells
        # Create a second row so we can delete the first without hitting the minimum
        row2 = Row.no_workspace_objects.create(dataset=dataset, order=2)
        for col in dataset_columns:
            Cell.no_workspace_objects.create(
                dataset=dataset, column=col, row=row2, value="v"
            )

        url = reverse("graph-dataset-rows-delete", kwargs={"graph_id": graph.id})
        response = authenticated_client.delete(
            url,
            data={"row_ids": [str(row.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        row.refresh_from_db()
        assert row.deleted is True
        for cell in cells:
            cell.refresh_from_db()
            assert cell.deleted is True

    def test_success_with_select_all_and_exclude(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """Soft-deletes rows via select_all while excluding one (keeps minimum)."""
        row, _ = dataset_row_with_cells
        # Create a second row
        row2 = Row.no_workspace_objects.create(dataset=dataset, order=2)
        for col in dataset_columns:
            Cell.no_workspace_objects.create(
                dataset=dataset, column=col, row=row2, value="v"
            )

        url = reverse("graph-dataset-rows-delete", kwargs={"graph_id": graph.id})
        response = authenticated_client.delete(
            url,
            data={"select_all": True, "exclude_ids": [str(row.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        row.refresh_from_db()
        assert row.deleted is False
        row2.refresh_from_db()
        assert row2.deleted is True

    def test_select_all_with_exclude_ids(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """Excluded rows are preserved when select_all with exclude_ids."""
        row, _ = dataset_row_with_cells
        # Create a second row
        row2 = Row.no_workspace_objects.create(dataset=dataset, order=2)
        for col in dataset_columns:
            Cell.no_workspace_objects.create(
                dataset=dataset, column=col, row=row2, value="v"
            )

        url = reverse("graph-dataset-rows-delete", kwargs={"graph_id": graph.id})
        response = authenticated_client.delete(
            url,
            data={"select_all": True, "exclude_ids": [str(row.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        # Excluded row should NOT be deleted
        row.refresh_from_db()
        assert row.deleted is False
        # The other row should be deleted
        row2.refresh_from_db()
        assert row2.deleted is True

    def test_cannot_delete_all_rows_with_row_ids(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """400 when trying to delete the only row by ID."""
        row, _ = dataset_row_with_cells
        url = reverse("graph-dataset-rows-delete", kwargs={"graph_id": graph.id})
        response = authenticated_client.delete(
            url,
            data={"row_ids": [str(row.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        row.refresh_from_db()
        assert row.deleted is False

    def test_cannot_delete_all_rows_with_select_all(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """400 when select_all=True would delete every row."""
        row, _ = dataset_row_with_cells
        url = reverse("graph-dataset-rows-delete", kwargs={"graph_id": graph.id})
        response = authenticated_client.delete(
            url,
            data={"select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        row.refresh_from_db()
        assert row.deleted is False

    def test_can_delete_some_rows(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """200 when deleting one of two rows — one remains."""
        row, _ = dataset_row_with_cells
        row2 = Row.no_workspace_objects.create(dataset=dataset, order=2)
        for col in dataset_columns:
            Cell.no_workspace_objects.create(
                dataset=dataset, column=col, row=row2, value="v"
            )

        url = reverse("graph-dataset-rows-delete", kwargs={"graph_id": graph.id})
        response = authenticated_client.delete(
            url,
            data={"row_ids": [str(row.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        row.refresh_from_db()
        assert row.deleted is True
        row2.refresh_from_db()
        assert row2.deleted is False

    def test_not_found_missing_row_ids(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset_columns,
    ):
        """404 when some row_ids don't exist, response includes missing_ids."""
        missing = uuid.uuid4()
        url = reverse("graph-dataset-rows-delete", kwargs={"graph_id": graph.id})
        response = authenticated_client.delete(
            url,
            data={"row_ids": [str(missing)]},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "missing_ids" in str(response.data)

    def test_bad_request_both_select_all_and_row_ids(
        self,
        authenticated_client,
        graph,
        graph_dataset,
    ):
        """400 when both select_all and row_ids are provided."""
        url = reverse("graph-dataset-rows-delete", kwargs={"graph_id": graph.id})
        response = authenticated_client.delete(
            url,
            data={"select_all": True, "row_ids": [str(uuid.uuid4())]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_not_found_no_graph_dataset(self, authenticated_client, graph):
        """404 when graph has no linked dataset."""
        url = reverse("graph-dataset-rows-delete", kwargs={"graph_id": graph.id})
        response = authenticated_client.delete(
            url,
            data={"select_all": True},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Dataset update cell
# =============================================================================


@pytest.mark.unit
class TestDatasetUpdateCell:
    """Tests for PUT /graphs/<graph_id>/dataset/cells/<cell_id>/."""

    def test_success(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """Successfully updates cell value."""
        _, cells = dataset_row_with_cells
        cell = cells[0]
        url = reverse(
            "graph-dataset-cell-update",
            kwargs={"graph_id": graph.id, "cell_id": cell.id},
        )
        response = authenticated_client.put(
            url, data={"value": "updated"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert result["value"] == "updated"
        assert str(result["column_id"]) == str(cell.column_id)

    def test_not_found_cell(
        self,
        authenticated_client,
        graph,
        graph_dataset,
    ):
        """404 when cell_id doesn't exist."""
        url = reverse(
            "graph-dataset-cell-update",
            kwargs={"graph_id": graph.id, "cell_id": uuid.uuid4()},
        )
        response = authenticated_client.put(url, data={"value": "x"}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_not_found_no_graph_dataset(self, authenticated_client, graph):
        """404 when graph has no linked dataset."""
        url = reverse(
            "graph-dataset-cell-update",
            kwargs={"graph_id": graph.id, "cell_id": uuid.uuid4()},
        )
        response = authenticated_client.put(url, data={"value": "x"}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Dataset execute
# =============================================================================


@pytest.mark.unit
class TestDatasetExecute:
    """Tests for POST /graphs/<graph_id>/dataset/execute/."""

    def test_success(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        active_graph_version,
    ):
        """Calls execute_rows and returns execution_ids."""
        fake_ids = [str(uuid.uuid4())]
        with patch(
            "agent_playground.views.dataset_link.execute_rows",
            return_value=fake_ids,
        ) as mock_execute_rows:
            url = reverse("graph-dataset-execute", kwargs={"graph_id": graph.id})
            response = authenticated_client.post(
                url, data={"task_queue": "agent-playground-test"}, format="json"
            )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["execution_ids"] == fake_ids
        assert (
            mock_execute_rows.call_args.kwargs["task_queue"] == "agent-playground-test"
        )
        assert mock_execute_rows.call_args.kwargs["row_ids"] is None

    def test_success_with_row_ids(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        active_graph_version,
        dataset_row_with_cells,
    ):
        """Calls execute_rows with validated row_ids from the graph dataset."""
        row, _ = dataset_row_with_cells
        fake_ids = [str(uuid.uuid4())]
        with patch(
            "agent_playground.views.dataset_link.execute_rows",
            return_value=fake_ids,
        ) as mock_execute_rows:
            url = reverse("graph-dataset-execute", kwargs={"graph_id": graph.id})
            response = authenticated_client.post(
                url, data={"row_ids": [str(row.id)]}, format="json"
            )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["execution_ids"] == fake_ids
        assert mock_execute_rows.call_args.kwargs["row_ids"] == [row.id]

    def test_not_found_no_active_version(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        graph_version,  # draft, not active
    ):
        """404 when no active graph version exists."""
        url = reverse("graph-dataset-execute", kwargs={"graph_id": graph.id})
        response = authenticated_client.post(url, data={}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_not_found_no_graph_dataset(self, authenticated_client, graph):
        """404 when graph has no linked dataset."""
        url = reverse("graph-dataset-execute", kwargs={"graph_id": graph.id})
        response = authenticated_client.post(url, data={}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_bad_request_value_error(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        active_graph_version,
    ):
        """400 when execute_rows raises ValueError."""
        with patch(
            "agent_playground.views.dataset_link.execute_rows",
            side_effect=ValueError("No rows to execute"),
        ):
            url = reverse("graph-dataset-execute", kwargs={"graph_id": graph.id})
            response = authenticated_client.post(url, data={}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_bad_request_empty_row_ids_does_not_dispatch(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        active_graph_version,
    ):
        """400 when row_ids is provided but empty, without dispatching work."""
        with patch(
            "agent_playground.views.dataset_link.execute_rows",
        ) as mock_execute_rows:
            url = reverse("graph-dataset-execute", kwargs={"graph_id": graph.id})
            response = authenticated_client.post(
                url, data={"row_ids": []}, format="json"
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_execute_rows.assert_not_called()

    def test_not_found_missing_row_ids_does_not_dispatch(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        active_graph_version,
    ):
        """404 when requested row_ids do not exist in the graph dataset."""
        missing = uuid.uuid4()
        with patch(
            "agent_playground.views.dataset_link.execute_rows",
        ) as mock_execute_rows:
            url = reverse("graph-dataset-execute", kwargs={"graph_id": graph.id})
            response = authenticated_client.post(
                url, data={"row_ids": [str(missing)]}, format="json"
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert str(missing) in str(response.data)
        mock_execute_rows.assert_not_called()

    def test_not_found_foreign_dataset_row_id_does_not_dispatch(
        self,
        authenticated_client,
        graph,
        graph_dataset,
        active_graph_version,
        organization,
        workspace,
        user,
    ):
        """404 when row_ids exist but belong to another dataset."""
        other_dataset = Dataset.no_workspace_objects.create(
            name="Other Dataset",
            organization=organization,
            workspace=workspace,
            user=user,
            source="graph",
            model_type="GenerativeLLM",
        )
        foreign_row = Row.no_workspace_objects.create(
            dataset=other_dataset,
            order=1,
        )

        with patch(
            "agent_playground.views.dataset_link.execute_rows",
        ) as mock_execute_rows:
            url = reverse("graph-dataset-execute", kwargs={"graph_id": graph.id})
            response = authenticated_client.post(
                url, data={"row_ids": [str(foreign_row.id)]}, format="json"
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert str(foreign_row.id) in str(response.data)
        mock_execute_rows.assert_not_called()
