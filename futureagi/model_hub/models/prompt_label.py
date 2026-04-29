import uuid
from enum import Enum

from django.db import models
from django.db.models import Q

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class LabelTypeChoices(Enum):
    SYSTEM = "system"
    CUSTOM = "custom"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class PromptLabel(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="prompt_labels",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="prompt_labels",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=2000)
    type = models.CharField(max_length=20, choices=LabelTypeChoices.get_choices())
    metadata = models.JSONField(default=dict, null=True, blank=True)

    def __str__(self):
        return self.name

    @classmethod
    def create_default_system_labels(cls):
        """Create default system labels: Production, Staging, Development for the given organization."""
        default_labels = ["Production", "Staging", "Development"]
        created_labels = []
        for label_name in default_labels:
            label, created = cls.objects.get_or_create(
                name=label_name,
                organization=None,
                defaults={
                    "type": LabelTypeChoices.SYSTEM.value,
                    "metadata": {
                        "description": f"Default {label_name.lower()} environment label"
                    },
                },
            )
            if created:
                created_labels.append(label)
        return created_labels

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "workspace"],
                condition=Q(deleted=False),
                name="unique_label_name_per_org_active",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(
                    organization__isnull=True,
                    type=LabelTypeChoices.SYSTEM.value,
                    deleted=False,
                ),
                name="unique_global_system_label_name",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "type"]),
            models.Index(fields=["name"]),
        ]
