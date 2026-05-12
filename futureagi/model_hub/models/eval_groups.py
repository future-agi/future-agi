import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class History(BaseModel):
    """
    Generic history table to track actions on various entities
    """

    SOURCE_TYPE_CHOICES = [
        ("EVAL_GROUP", "Eval Group"),
        # Add other source types as needed
    ]

    ACTION_CHOICES = [
        ("ADD", "Add"),
        ("DELETE", "Delete"),
        # Add other actions as needed
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_id = models.UUIDField(help_text="ID of the source entity")
    source_type = models.CharField(max_length=50, choices=SOURCE_TYPE_CHOICES)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    action_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="history_actions",
        help_text="User who performed the action",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="history_records",
        null=True,
        blank=True,
        help_text="Organization where the action was performed",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="history_records",
        null=True,
        blank=True,
        help_text="Workspace where the action was performed",
    )
    reference_id = models.UUIDField(
        help_text="ID of the referenced entity (e.g., eval_template_id)"
    )

    def __str__(self):
        return f"History {self.id}"

    class Meta:
        db_table = "model_hub_history"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_id", "source_type", "created_at"]),
            models.Index(fields=["reference_id", "action"]),
            models.Index(fields=["action_by", "created_at"]),
            models.Index(fields=["organization", "workspace", "created_at"]),
        ]


class EvalGroup(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="eval_groups",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="eval_groups",
        null=True,
        blank=True,
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="eval_groups",
        null=True,
        blank=True,
    )

    description = models.TextField(null=True, blank=True)

    # Many-to-many relationship with EvalTemplate
    eval_templates = models.ManyToManyField(
        "model_hub.EvalTemplate",  # Use string reference to avoid circular import
        related_name="eval_groups",
        blank=True,
    )

    is_sample = models.BooleanField(default=False)

    def __str__(self):
        return f"Eval Group {self.id}"

    class Meta:
        db_table = "model_hub_eval_group"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "workspace"],
                condition=models.Q(deleted=False),
                name="unique_eval_group_name_workspace_not_deleted",
            )
        ]

        indexes = [
            models.Index(fields=["organization", "workspace", "created_at"]),
        ]
