import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class PromptFolder(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="prompt_folders",
        null=True,
        blank=True,
    )

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="prompt_folders",
        null=True,
        blank=True,
    )

    parent_folder = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="child_folders",
        null=True,
        blank=True,
    )

    is_sample = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="prompt_folders",
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"Prompt Folder {self.id}"

    class Meta:
        db_table = "model_hub_prompt_folder"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "organization", "workspace"],
                condition=models.Q(deleted=False),
                name="unique_prompt_folder_name_organization_workspace_not_deleted",
            )
        ]

        indexes = [
            models.Index(fields=["organization", "workspace", "created_at"]),
        ]
