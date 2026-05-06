import uuid

from django.db import models

from accounts.models.organization import Organization
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.base_model import BaseModel


class OrganizationMembership(BaseModel):
    """
    Model to handle users being members of multiple organizations.
    Each user can have different roles in different organizations.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # User and organization relationship
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="user_memberships"
    )

    # Role in this specific organization (legacy — kept for backward compat)
    role = models.CharField(
        max_length=20,
        choices=OrganizationRoles.choices,
        default=OrganizationRoles.MEMBER,
    )

    # Integer level (new RBAC) — Owner=15, Admin=8, Member=3, Viewer=1
    level = models.PositiveSmallIntegerField(
        choices=Level.CHOICES,
        null=True,
        blank=True,
        help_text="Integer RBAC level. Null means legacy role string is authoritative.",
    )

    # Who invited this user to the organization
    invited_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="invited_organization_members",
    )

    # When they joined
    joined_at = models.DateTimeField(auto_now_add=True)

    # Whether this membership is active
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "accounts_organization_membership"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"],
                condition=models.Q(deleted=False),
                name="unique_active_org_membership",
            ),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.organization.name} ({self.role})"

    @property
    def level_or_legacy(self):
        """
        Return the integer level. If `level` is not yet backfilled,
        derive it from the legacy `role` string.
        """
        if self.level is not None:
            return self.level
        return Level.STRING_TO_LEVEL.get(self.role, 0)

    def save(self, *args, **kwargs):
        # Ensure user is added to invited_organizations ManyToMany field
        if self.is_active and self.pk is None:  # New active membership
            # Add to user's invited_organizations when creating
            self.user.invited_organizations.add(self.organization)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Remove user from invited_organizations ManyToMany field
        self.user.invited_organizations.remove(self.organization)
        super().delete(*args, **kwargs)
