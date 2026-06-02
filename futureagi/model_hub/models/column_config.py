import uuid

from django.db import models
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace


class ColumnConfig(models.Model):
    class TableName(models.TextChoices):
        DATASET = "Dataset", "Dataset"
        DATASET_DETAIL = "DatasetDetail", "Dataset Detail"
        OPTIMIZE_DATASET = "OptimizeDataset", "Optimize Dataset"
        OPTIMIZE_DATASET_RIGHT_ANSWER = (
            "OptimizeDatasetRightAnswer",
            "Optimize Dataset Right Answer",
        )
        OPTIMIZE_DATASET_PROMPT_TEMPLATE_EXPLORE = (
            "OptimizeDatasetPromptTemplateExplore",
            "Optimize Dataset Prompt Template Explore",
        )
        EVAL_USAGE = "EvalUsage", "Eval Usage"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    table_name = models.CharField(max_length=100, choices=TableName.choices)
    identifier = models.CharField(max_length=255)
    columns = models.JSONField(null=True)

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="column_configs"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="column_configs",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["table_name", "organization", "identifier"],
                name="unique_column_config_per_table_org_identifier",
            ),
        ]

    def __str__(self):
        return str(self.id)
