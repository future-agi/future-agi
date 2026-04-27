import uuid

from django.db import models

from accounts.models.organization import Organization
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.base_model import BaseModel


class Workspace(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="workspaces"
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="created_workspaces"
    )

    class Meta:
        unique_together = [["organization", "name"]]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "is_default"],
                condition=models.Q(is_default=True),
                name="unique_default_workspace_per_org",
            )
        ]

    def __str__(self):
        return f"{self.organization.name} - {self.name}"

    def save(self, *args, **kwargs):
        # If display_name is not provided, default to the value of name
        if not self.display_name:
            self.display_name = self.name
        super().save(*args, **kwargs)


class WorkspaceMembership(BaseModel):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="workspace_memberships"
    )
    role = models.CharField(
        max_length=20,
        choices=OrganizationRoles.choices,
        default=OrganizationRoles.WORKSPACE_MEMBER,
    )

    # Integer level (new RBAC) — Workspace Admin=8, Member=3, Viewer=1
    level = models.PositiveSmallIntegerField(
        choices=Level.WORKSPACE_CHOICES,
        null=True,
        blank=True,
        help_text="Integer RBAC level. Null means legacy role string is authoritative.",
    )

    # Link to the org membership — CASCADE ensures no orphan ws memberships
    organization_membership = models.ForeignKey(
        "accounts.OrganizationMembership",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="workspace_memberships",
        help_text="Nullable during transition; backfill migration will populate.",
    )

    # Who granted this workspace access
    granted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_workspace_memberships",
    )
    granted_at = models.DateTimeField(auto_now_add=True, null=True)

    invited_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="invited_workspace_members",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [["workspace", "user"]]

    def __str__(self):
        return f"{self.user.email} - {self.workspace.name} ({self.role})"

    @property
    def level_or_legacy(self):
        """
        Return the integer level. If `level` is not yet backfilled,
        derive it from the legacy `role` string.
        """
        if self.level is not None:
            return self.level
        return Level.STRING_TO_LEVEL.get(self.role, Level.WORKSPACE_VIEWER)
