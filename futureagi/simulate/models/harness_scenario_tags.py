import uuid

from django.contrib.postgres.indexes import GinIndex
from django.db import models

from tfc.utils.base_model import BaseModel


class HarnessScenarioTags(BaseModel):
    """The free-text tags a person puts on one of an environment's scenarios; a label, never versioned."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.CASCADE,
        related_name="scenario_tags",
    )
    scenario_key = models.CharField(max_length=255)
    tags = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "simulate_harness_scenario_tags"
        constraints = [
            models.UniqueConstraint(
                fields=["environment", "scenario_key"],
                name="uniq_harness_scenario_tags_key",
            ),
        ]
        indexes = [
            GinIndex(fields=["tags"], name="idx_harness_scenario_tags_gin"),
        ]
