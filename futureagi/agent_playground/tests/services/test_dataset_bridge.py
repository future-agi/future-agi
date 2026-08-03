"""Tests for dataset_bridge service — default row seeding, execution, add-only sync, and draft commit."""

import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone

from agent_playground.models.choices import GraphVersionStatus, PortDirection
from agent_playground.services.dataset_bridge import (
    commit_draft_prompt_versions,
    execute_rows,
    sync_dataset_columns,
)
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Row


@pytest.mark.unit
class TestEnsureMinimumRow:
    """Tests for _ensure_minimum_row called at end of sync_dataset_columns."""

    def _mock_exposed_ports(self, version_id, port_names):
        """Return a mock side-effect for get_exposed_ports_for_versions."""

        def _side_effect(version_ids):
            result = {}
            for vid in version_ids:
                if vid == version_id:
                    result[vid] = [
                        {"display_name": name, "direction": PortDirection.INPUT}
                        for name in port_names
                    ]
            return result

        return _side_effect

    def test_first_activation_creates_default_row(
        self, graph, graph_version, dataset, graph_dataset
    ):
        """On first activation with exposed ports, one row with cells is created."""
        version = graph_version
        version.status = GraphVersionStatus.ACTIVE
        version.save()

        port_names = ["input_a", "input_b"]

        with patch(
            "agent_playground.services.dataset_bridge.get_exposed_ports_for_versions",
            side_effect=self._mock_exposed_ports(version.id, port_names),
        ):
            sync_dataset_columns(
                graph=graph,
                version=version,
            )

        columns = Column.no_workspace_objects.filter(dataset=dataset)
        assert columns.count() == 2

        rows = Row.no_workspace_objects.filter(dataset=dataset)
        assert rows.count() == 1

        cells = Cell.no_workspace_objects.filter(dataset=dataset)
        assert cells.count() == 2  # 1 row × 2 columns

        row = rows.first()
        assert row.order == 1
        for cell in cells:
            assert cell.row_id == row.id
            assert cell.value is None

    def test_activation_no_row_created_when_rows_exist(
        self, graph, graph_version, dataset, graph_dataset, dataset_columns
    ):
        """If rows already exist, no extra row is created on sync."""
        # Pre-create a row
        existing_row = Row.no_workspace_objects.create(dataset=dataset, order=1)
        for col in dataset_columns:
            Cell.no_workspace_objects.create(
                dataset=dataset, column=col, row=existing_row, value="existing"
            )

        version = graph_version
        version.status = GraphVersionStatus.ACTIVE
        version.save()

        port_names = [col.name for col in dataset_columns]

        with patch(
            "agent_playground.services.dataset_bridge.get_exposed_ports_for_versions",
            side_effect=self._mock_exposed_ports(version.id, port_names),
        ):
            sync_dataset_columns(
                graph=graph,
                version=version,
            )

        rows = Row.no_workspace_objects.filter(dataset=dataset)
        assert rows.count() == 1
        assert rows.first().id == existing_row.id

    def test_no_row_created_when_no_columns(
        self, graph, graph_version, dataset, graph_dataset
    ):
        """No row is created if the version has no exposed input ports."""
        version = graph_version
        version.status = GraphVersionStatus.ACTIVE
        version.save()

        with patch(
            "agent_playground.services.dataset_bridge.get_exposed_ports_for_versions",
            side_effect=self._mock_exposed_ports(version.id, []),
        ):
            sync_dataset_columns(
                graph=graph,
                version=version,
            )

        assert Column.no_workspace_objects.filter(dataset=dataset).count() == 0
        assert Row.no_workspace_objects.filter(dataset=dataset).count() == 0


@pytest.mark.unit
class TestExecuteRows:
    """Tests for execute_rows covering the no-rows / no-input-ports path."""

    EXPOSED_PORTS_PATH = (
        "agent_playground.services.dataset_bridge.get_exposed_ports_for_versions"
    )
    START_EXEC_PATH = "agent_playground.services.dataset_bridge.start_graph_execution"

    def _mock_exposed_ports(self, version_id, port_names):
        """Return a mock side-effect for get_exposed_ports_for_versions."""

        def _side_effect(version_ids):
            result = {}
            for vid in version_ids:
                if vid == version_id:
                    result[vid] = [
                        {"display_name": name, "direction": PortDirection.INPUT}
                        for name in port_names
                    ]
            return result

        return _side_effect

    def test_no_rows_no_input_ports_creates_single_execution(
        self, graph_version, dataset
    ):
        """When there are 0 rows, 0 exposed input ports, and row_ids=None,
        a single execution with empty payload is created."""
        fake_exec_id = str(uuid.uuid4())

        with (
            patch(
                self.EXPOSED_PORTS_PATH,
                side_effect=self._mock_exposed_ports(graph_version.id, []),
            ),
            patch(
                self.START_EXEC_PATH,
                return_value=fake_exec_id,
            ) as mock_start,
        ):
            result = execute_rows(graph_version, dataset, row_ids=None)

        assert result == [fake_exec_id]
        mock_start.assert_called_once_with(
            graph_version_id=str(graph_version.id),
            input_payload={},
            task_queue="tasks_l",
        )

    def test_no_rows_with_input_ports_returns_empty(self, graph_version, dataset):
        """When there are 0 rows but exposed input ports exist,
        execute_rows returns [] without starting any execution."""
        with (
            patch(
                self.EXPOSED_PORTS_PATH,
                side_effect=self._mock_exposed_ports(
                    graph_version.id, ["input_text", "context"]
                ),
            ),
            patch(self.START_EXEC_PATH) as mock_start,
        ):
            result = execute_rows(graph_version, dataset, row_ids=None)

        assert result == []
        mock_start.assert_not_called()

    def test_no_rows_explicit_row_ids_returns_empty(self, graph_version, dataset):
        """When explicit row_ids are given but none match,
        returns [] regardless of port configuration."""
        with patch(self.START_EXEC_PATH) as mock_start:
            result = execute_rows(graph_version, dataset, row_ids=[uuid.uuid4()])

        assert result == []
        mock_start.assert_not_called()

    def test_with_rows_normal_execution(
        self,
        graph_version,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """Normal path: rows exist, payload is built from cells, execution fires."""
        row, cells = dataset_row_with_cells
        port_names = [col.name for col in dataset_columns]
        fake_exec_id = str(uuid.uuid4())

        with (
            patch(
                self.EXPOSED_PORTS_PATH,
                side_effect=self._mock_exposed_ports(graph_version.id, port_names),
            ),
            patch(
                self.START_EXEC_PATH,
                return_value=fake_exec_id,
            ) as mock_start,
        ):
            result = execute_rows(graph_version, dataset, row_ids=None)

        assert result == [fake_exec_id]
        mock_start.assert_called_once()
        call_kwargs = mock_start.call_args[1]
        assert call_kwargs["graph_version_id"] == str(graph_version.id)
        assert call_kwargs["task_queue"] == "tasks_l"
        payload = call_kwargs["input_payload"]
        for col in dataset_columns:
            assert col.name in payload
            assert payload[col.name] == f"value for {col.name}"

    def test_with_rows_passes_requested_task_queue(
        self,
        graph_version,
        dataset,
        dataset_columns,
        dataset_row_with_cells,
    ):
        """The public task_queue option is forwarded to Temporal dispatch."""
        port_names = [col.name for col in dataset_columns]
        fake_exec_id = str(uuid.uuid4())

        with (
            patch(
                self.EXPOSED_PORTS_PATH,
                side_effect=self._mock_exposed_ports(graph_version.id, port_names),
            ),
            patch(
                self.START_EXEC_PATH,
                return_value=fake_exec_id,
            ) as mock_start,
        ):
            result = execute_rows(
                graph_version,
                dataset,
                row_ids=None,
                task_queue="agent-playground-test",
            )

        assert result == [fake_exec_id]
        assert mock_start.call_args[1]["task_queue"] == "agent-playground-test"


@pytest.mark.unit
class TestSyncDatasetColumns:
    """Tests for add-only column sync logic in sync_dataset_columns."""

    EXPOSED_PORTS_PATH = (
        "agent_playground.services.dataset_bridge.get_exposed_ports_for_versions"
    )

    def _mock_exposed_ports(self, version_id, port_names):
        """Return a mock side-effect for get_exposed_ports_for_versions."""

        def _side_effect(version_ids):
            result = {}
            for vid in version_ids:
                if vid == version_id:
                    result[vid] = [
                        {"display_name": name, "direction": PortDirection.INPUT}
                        for name in port_names
                    ]
            return result

        return _side_effect

    def test_creates_columns_for_new_port_names(
        self, graph, graph_version, dataset, graph_dataset
    ):
        """Ports ["a", "b"] → 2 new columns created with correct name, data_type=TEXT, source=OTHERS."""
        with patch(
            self.EXPOSED_PORTS_PATH,
            side_effect=self._mock_exposed_ports(graph_version.id, ["a", "b"]),
        ):
            sync_dataset_columns(graph=graph, version=graph_version)

        columns = Column.no_workspace_objects.filter(dataset=dataset)
        assert columns.count() == 2

        col_names = set(columns.values_list("name", flat=True))
        assert col_names == {"a", "b"}

        for col in columns:
            assert col.data_type == DataTypeChoices.TEXT.value
            assert col.source == SourceChoices.OTHERS.value

    def test_skips_existing_active_columns(
        self, graph, graph_version, dataset, graph_dataset, dataset_columns
    ):
        """If column "input_text" already exists (not deleted), no duplicate created."""
        port_names = [col.name for col in dataset_columns]

        with patch(
            self.EXPOSED_PORTS_PATH,
            side_effect=self._mock_exposed_ports(graph_version.id, port_names),
        ):
            sync_dataset_columns(graph=graph, version=graph_version)

        columns = Column.no_workspace_objects.filter(dataset=dataset)
        assert columns.count() == len(dataset_columns)

    def test_restores_soft_deleted_column(
        self, graph, graph_version, dataset, graph_dataset
    ):
        """Soft-deleted column → restored (deleted=False, deleted_at=None), cells also restored."""
        now = timezone.now()
        col = Column.no_workspace_objects.create(
            name="restored_col",
            data_type=DataTypeChoices.TEXT.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
        )
        # Create a row + cell, then soft-delete both column and cell
        row = Row.no_workspace_objects.create(dataset=dataset, order=1)
        cell = Cell.no_workspace_objects.create(
            dataset=dataset, column=col, row=row, value="hello"
        )

        Column.all_objects.filter(id=col.id).update(deleted=True, deleted_at=now)
        Cell.all_objects.filter(id=cell.id).update(deleted=True, deleted_at=now)

        with patch(
            self.EXPOSED_PORTS_PATH,
            side_effect=self._mock_exposed_ports(graph_version.id, ["restored_col"]),
        ):
            sync_dataset_columns(graph=graph, version=graph_version)

        col.refresh_from_db()
        assert col.deleted is False
        assert col.deleted_at is None

        cell.refresh_from_db()
        assert cell.deleted is False
        assert cell.deleted_at is None

    def test_restored_column_re_added_to_column_order(
        self, graph, graph_version, dataset, graph_dataset
    ):
        """After restore, column ID appears in dataset.column_order."""
        now = timezone.now()
        col = Column.no_workspace_objects.create(
            name="order_col",
            data_type=DataTypeChoices.TEXT.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
        )
        # Remove from column_order and soft-delete
        dataset.column_order = []
        dataset.save()
        Column.all_objects.filter(id=col.id).update(deleted=True, deleted_at=now)

        with patch(
            self.EXPOSED_PORTS_PATH,
            side_effect=self._mock_exposed_ports(graph_version.id, ["order_col"]),
        ):
            sync_dataset_columns(graph=graph, version=graph_version)

        dataset.refresh_from_db()
        assert str(col.id) in dataset.column_order

    def test_backfills_cells_for_new_columns(
        self, graph, graph_version, dataset, graph_dataset
    ):
        """When a new column is created and rows exist, empty cells are backfilled for each row."""
        # Pre-create rows (no columns yet)
        row1 = Row.no_workspace_objects.create(dataset=dataset, order=1)
        row2 = Row.no_workspace_objects.create(dataset=dataset, order=2)

        with patch(
            self.EXPOSED_PORTS_PATH,
            side_effect=self._mock_exposed_ports(graph_version.id, ["new_col"]),
        ):
            sync_dataset_columns(graph=graph, version=graph_version)

        new_col = Column.no_workspace_objects.get(dataset=dataset, name="new_col")
        cells = Cell.no_workspace_objects.filter(dataset=dataset, column=new_col)
        assert cells.count() == 2
        row_ids = set(cells.values_list("row_id", flat=True))
        assert row_ids == {row1.id, row2.id}
        for cell in cells:
            assert cell.value is None

    def test_no_graph_dataset_returns_silently(self, graph, graph_version):
        """No GraphDataset linked → no error, no columns created."""
        # No graph_dataset fixture used — no link exists
        with patch(
            self.EXPOSED_PORTS_PATH,
        ) as mock_ports:
            sync_dataset_columns(graph=graph, version=graph_version)

        mock_ports.assert_not_called()

    def test_no_exposed_ports_returns_silently(
        self, graph, graph_version, dataset, graph_dataset
    ):
        """Empty port list → no columns created, no rows created."""
        with patch(
            self.EXPOSED_PORTS_PATH,
            side_effect=self._mock_exposed_ports(graph_version.id, []),
        ):
            sync_dataset_columns(graph=graph, version=graph_version)

        assert Column.no_workspace_objects.filter(dataset=dataset).count() == 0
        assert Row.no_workspace_objects.filter(dataset=dataset).count() == 0

    def test_never_deletes_columns(
        self, graph, graph_version, dataset, graph_dataset, dataset_columns
    ):
        """Start with columns ["input_text","context"], sync with only ["input_text"] → "context" still exists."""
        with patch(
            self.EXPOSED_PORTS_PATH,
            side_effect=self._mock_exposed_ports(graph_version.id, ["input_text"]),
        ):
            sync_dataset_columns(graph=graph, version=graph_version)

        columns = Column.no_workspace_objects.filter(dataset=dataset)
        assert columns.count() == 2

        col_names = set(columns.values_list("name", flat=True))
        assert "context" in col_names
        assert "input_text" in col_names

        # Verify "context" is NOT soft-deleted either
        context_col = Column.all_objects.get(dataset=dataset, name="context")
        assert context_col.deleted is False


@pytest.mark.unit
class TestCommitDraftPromptVersions:
    """Tests for commit_draft_prompt_versions (TH-3780)."""

    def test_commits_draft_prompt_version(
        self, graph_version, node, prompt_template_node, draft_prompt_version
    ):
        """Draft PromptVersion linked via PromptTemplateNode is committed."""
        # Point the PTN at the draft version
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        assert draft_prompt_version.is_draft is True

        updated = commit_draft_prompt_versions(graph_version)

        assert updated == 1
        draft_prompt_version.refresh_from_db()
        assert draft_prompt_version.is_draft is False

    def test_skips_already_committed_version(
        self, graph_version, node, prompt_template_node, prompt_version
    ):
        """PromptVersion that is already committed (is_draft=False) is not touched."""
        assert prompt_version.is_draft is False

        updated = commit_draft_prompt_versions(graph_version)

        assert updated == 0
        prompt_version.refresh_from_db()
        assert prompt_version.is_draft is False

    def test_skips_deleted_node(
        self, graph_version, node, prompt_template_node, draft_prompt_version
    ):
        """Draft PromptVersion on a soft-deleted node is not committed."""
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        node.deleted = True
        node.save(update_fields=["deleted"])

        updated = commit_draft_prompt_versions(graph_version)

        assert updated == 0
        draft_prompt_version.refresh_from_db()
        assert draft_prompt_version.is_draft is True

    def test_skips_deleted_prompt_template_node(
        self, graph_version, node, prompt_template_node, draft_prompt_version
    ):
        """Draft PromptVersion on a soft-deleted PromptTemplateNode is not committed."""
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        prompt_template_node.deleted = True
        prompt_template_node.save(update_fields=["deleted"])

        updated = commit_draft_prompt_versions(graph_version)

        assert updated == 0
        draft_prompt_version.refresh_from_db()
        assert draft_prompt_version.is_draft is True

    def test_no_prompt_template_nodes_returns_zero(self, graph_version):
        """Graph version with no PromptTemplateNodes → 0 updated."""
        updated = commit_draft_prompt_versions(graph_version)
        assert updated == 0

    def test_commits_multiple_draft_versions(
        self,
        graph_version,
        node,
        second_node,
        prompt_template,
        prompt_template_node,
        draft_prompt_version,
    ):
        """Multiple draft PromptVersions across different nodes are all committed."""
        from agent_playground.models.prompt_template_node import PromptTemplateNode
        from model_hub.models.run_prompt import PromptVersion

        # First node uses draft_prompt_version
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        # Second node gets its own draft PromptVersion
        second_draft_pv = PromptVersion.no_workspace_objects.create(
            original_template=prompt_template,
            template_version="v3",
            prompt_config_snapshot={"messages": [{"role": "user", "content": "test2"}]},
            is_draft=True,
        )
        PromptTemplateNode.no_workspace_objects.create(
            node=second_node,
            prompt_template=prompt_template,
            prompt_version=second_draft_pv,
        )

        updated = commit_draft_prompt_versions(graph_version)

        assert updated == 2
        draft_prompt_version.refresh_from_db()
        second_draft_pv.refresh_from_db()
        assert draft_prompt_version.is_draft is False
        assert second_draft_pv.is_draft is False
