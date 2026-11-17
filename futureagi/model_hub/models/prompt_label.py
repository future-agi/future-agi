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
    def create_default_system_labels(cls, _organization=None):
        """Create the global Production, Staging, and Development system labels."""
        from tfc.middleware.workspace_context import (
            clear_workspace_context,
            get_current_organization,
            get_current_user,
            get_current_workspace,
            set_workspace_context,
        )

        default_labels = ["Production", "Staging", "Development"]
        created_labels = []
        current_workspace = get_current_workspace()
        current_organization = get_current_organization()
        current_user = get_current_user()
        clear_workspace_context()
        try:
            for label_name in default_labels:
                label, created = cls.no_workspace_objects.get_or_create(
                    name=label_name,
                    organization=None,
                    type=LabelTypeChoices.SYSTEM.value,
                    defaults={
                        "workspace": None,
                        "metadata": {
                            "description": f"Default {label_name.lower()} environment label"
                        },
                    },
                )
                if created:
                    created_labels.append(label)
        finally:
            set_workspace_context(
                workspace=current_workspace,
                organization=current_organization,
                user=current_user,
            )
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


class PromptLabelHead(models.Model):
    """Where one label sits within one prompt, and how many times that has changed (webhooks DESIGN.md section 14.3).

    One row per (prompt, label). It is the serialization point of the one label writer
    (``model_hub.services.prompt_label``): every change locks the affected heads ``FOR UPDATE`` in ``label_id`` order,
    after the version rows it writes to (``model_hub.services.prompt_locks``), reads from and to under the lock,
    writes the attachment, bumps ``revision`` and moves ``version``. A label is on at most one version of a prompt;
    ``version`` is null when it is on none. ``revision`` orders the label's ``prompt.label.changed`` events for
    receivers.

    Rolling deploy: a pod still on code from before migration 0123 writes attachments without moving heads, and
    publishes nothing. The writer does not trust ``version`` for where the label is: under the lock it reads the real
    holder from the attachment table and repairs a stale head first (``prompt_locks.real_holders``), so the next
    change names the right ``from_version``. A label edit an old pod makes during the rollout publishes no event and
    takes no head lock; only the writer's own changes are ordered and published.
    """

    template = models.ForeignKey(
        "model_hub.PromptTemplate",
        on_delete=models.CASCADE,
        related_name="label_heads",
    )
    label = models.ForeignKey(
        PromptLabel, on_delete=models.CASCADE, related_name="heads"
    )
    version = models.ForeignKey(
        "model_hub.PromptVersion",
        on_delete=models.SET_NULL,
        related_name="label_heads",
        null=True,
        blank=True,
    )
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "model_hub_promptlabelhead"
        constraints = [
            models.UniqueConstraint(
                fields=["template", "label"], name="prompt_label_head_unique"
            )
        ]
