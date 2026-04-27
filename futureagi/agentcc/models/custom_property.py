import uuid

from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class AgentccCustomPropertySchema(BaseModel):
    """Custom property schema definition for request log metadata validation."""

    TYPE_STRING = "string"
    TYPE_NUMBER = "number"
    TYPE_BOOLEAN = "boolean"
    TYPE_ENUM = "enum"

    TYPE_CHOICES = [
        (TYPE_STRING, "String"),
        (TYPE_NUMBER, "Number"),
        (TYPE_BOOLEAN, "Boolean"),
        (TYPE_ENUM, "Enum"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_custom_property_schemas",
    )
    project = models.ForeignKey(
        "prism.AgentccProject",
        on_delete=models.CASCADE,
        related_name="custom_property_schemas",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    property_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_STRING,
    )
    required = models.BooleanField(default=False)
    allowed_values = models.JSONField(default=list, blank=True)
    default_value = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_custom_property_schema"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_custom_property_name",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.property_type})"
