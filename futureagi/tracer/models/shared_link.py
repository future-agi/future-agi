import secrets
import uuid

from django.db import models
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class ResourceType(models.TextChoices):
    TRACE = "trace", "Trace"
    DASHBOARD = "dashboard", "Dashboard"
    EVAL_RUN = "eval_run", "Eval Run"
    DATASET = "dataset", "Dataset"
    PROJECT = "project", "Project"


class AccessType(models.TextChoices):
    PUBLIC = "public", "Anyone with the link"
    RESTRICTED = "restricted", "Only specific people"


def generate_share_token():
    return secrets.token_urlsafe(32)


class SharedLink(BaseModel):
    """
    A shareable link to any platform resource.
    Supports public (no auth) and restricted (email ACL) access.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # What is being shared
    resource_type = models.CharField(max_length=20, choices=ResourceType.choices)
    resource_id = models.CharField(max_length=255, db_index=True)

    # Unguessable token for the share URL
    token = models.CharField(
        max_length=64, unique=True, db_index=True, default=generate_share_token
    )

    # Access mode
    access_type = models.CharField(
        max_length=16, choices=AccessType.choices, default=AccessType.RESTRICTED
    )

    # Creator and ownership
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="created_shared_links",
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="shared_links"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="shared_links",
    )

    # Expiry (null = never)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Revocation flag
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["resource_type", "resource_id"]),
        ]

    def __str__(self):
        return f"SharedLink({self.resource_type}:{self.resource_id[:8]}, {self.access_type})"

    @property
    def is_expired(self):
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    @property
    def is_accessible(self):
        return self.is_active and not self.is_expired and not self.deleted

    @property
    def share_url_path(self):
        return f"/shared/{self.token}"


class SharedLinkAccess(BaseModel):
    """
    ACL entry for restricted shared links.
    Each row grants one email/user access to view the shared resource.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    shared_link = models.ForeignKey(
        SharedLink, on_delete=models.CASCADE, related_name="access_list"
    )

    # The person who has access
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="shared_link_access",
    )
    email = models.EmailField(db_index=True)

    # Who granted this access
    granted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="granted_shared_access",
    )

    class Meta:
        unique_together = [["shared_link", "email"]]
        ordering = ("-created_at",)

    def __str__(self):
        return f"Access({self.email} → {self.shared_link_id})"
