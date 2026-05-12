import uuid

from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class ScenarioGraph(BaseModel):
    """Main graph container that groups related cases"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Foreign keys
    scenario = models.ForeignKey(
        "simulate.Scenarios",
        on_delete=models.CASCADE,
        related_name="graphs",
        help_text="Scenario that owns this graph",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="scenario_graphs",
        help_text="Organization this graph belongs to",
    )

    # Graph metadata
    name = models.CharField(max_length=255, help_text="Graph name")
    description = models.TextField(
        blank=True, default="", help_text="Optional description"
    )

    version = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)

    # Graph configuration (not the actual data)
    graph_config = models.JSONField(
        default=dict, help_text="Graph settings, styling, etc."
    )

    class Meta:
        db_table = "simulate_scenario_graph"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["scenario", "is_active"]),
            models.Index(fields=["organization", "is_active"]),
        ]

    def __str__(self):
        return f"{self.name} (v{self.version})"

    @property
    def total_cases(self):
        """Get total number of active cases in this graph"""
        return self.cases.filter(is_active=True).count()

    def get_cases_ordered(self):
        """Get all active cases ordered by their order field"""
        return self.cases.filter(is_active=True).order_by("order")


# Node types constants for reference
class NodeType:
    """Constants for scenario node types"""

    START = "start"
    MESSAGE = "message"
    CONDITION = "condition"
    END = "end"

    # All valid node types
    ALL_TYPES = [START, MESSAGE, CONDITION, END]

    # Terminal node types (nodes that end a conversation path)
    TERMINAL_TYPES = [END]

    # Non-terminal node types
    NON_TERMINAL_TYPES = [START, MESSAGE, CONDITION]

    @classmethod
    def is_valid(cls, node_type: str) -> bool:
        """Check if a node type is valid"""
        return node_type in cls.ALL_TYPES

    @classmethod
    def is_terminal(cls, node_type: str) -> bool:
        """Check if a node type is terminal"""
        return node_type in cls.TERMINAL_TYPES

    @classmethod
    def get_display_name(cls, node_type: str) -> str:
        """Get display name for a node type"""
        display_names = {
            cls.START: "Start",
            cls.MESSAGE: "Message",
            cls.CONDITION: "Condition",
            cls.END: "End",
        }
        return display_names.get(node_type, node_type.title())
