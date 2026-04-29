"""Tests for GraphDataset model."""

import pytest
from django.db import IntegrityError

from agent_playground.models.graph import Graph
from agent_playground.models.graph_dataset import GraphDataset
from model_hub.models.develop_dataset import Dataset


@pytest.mark.unit
class TestGraphDatasetModel:
    """Tests for the GraphDataset model."""

    def test_create_graph_dataset(self, graph_dataset, graph, dataset):
        """Creating a GraphDataset links a graph and dataset."""
        assert graph_dataset.graph_id == graph.id
        assert graph_dataset.dataset_id == dataset.id

    def test_str_representation(self, graph_dataset, graph, dataset):
        """__str__ includes both graph and dataset UUIDs."""
        expected = f"GraphDataset(graph={graph.id}, dataset={dataset.id})"
        assert str(graph_dataset) == expected

    def test_unique_graph_constraint(
        self, graph_dataset, graph, organization, workspace, user
    ):
        """Cannot link a second dataset to the same graph."""
        other_dataset = Dataset.no_workspace_objects.create(
            name="Other Dataset",
            organization=organization,
            workspace=workspace,
            user=user,
            source="graph",
            model_type="GenerativeLLM",
        )
        with pytest.raises(IntegrityError):
            GraphDataset.no_workspace_objects.create(
                graph=graph,
                dataset=other_dataset,
            )

    def test_unique_dataset_constraint(
        self, graph_dataset, dataset, organization, workspace, user
    ):
        """Cannot link a second graph to the same dataset."""
        other_graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Other Graph",
            created_by=user,
        )
        with pytest.raises(IntegrityError):
            GraphDataset.no_workspace_objects.create(
                graph=other_graph,
                dataset=dataset,
            )

    def test_reverse_accessor_from_graph(self, graph_dataset, graph):
        """Graph has a graph_dataset reverse accessor."""
        assert graph.graph_dataset == graph_dataset

    def test_reverse_accessor_from_dataset(self, graph_dataset, dataset):
        """Dataset has a graph_dataset reverse accessor."""
        assert dataset.graph_dataset == graph_dataset

    def test_soft_delete_hides_from_no_workspace_objects(self, graph_dataset):
        """Soft-deleted GraphDataset is hidden by no_workspace_objects."""
        gd_id = graph_dataset.id
        graph_dataset.delete()  # soft-delete
        assert not GraphDataset.no_workspace_objects.filter(id=gd_id).exists()
