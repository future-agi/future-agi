import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class GraphDataset(BaseModel):
    """
    Links a Graph to its auto-created Dataset.

    OneToOne relationship: each Graph gets exactly one Dataset
    for storing input variable rows used in batch execution.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    graph = models.OneToOneField(
        "agent_playground.Graph",
        on_delete=models.CASCADE,
        related_name="graph_dataset",
    )

    dataset = models.OneToOneField(
        "model_hub.Dataset",
        on_delete=models.CASCADE,
        related_name="graph_dataset",
    )

    class Meta:
        db_table = "agent_playground_graph_dataset"

    def __str__(self):
        return f"GraphDataset(graph={self.graph_id}, dataset={self.dataset_id})"
