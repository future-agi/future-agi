import uuid
from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

from tfc.constants.levels import INVITE_VALIDITY_DAYS, Level
from tfc.utils.base_model import BaseModel


class InviteStatus:
    PENDING = "Pending"
    ACCEPTED = "Accepted"
    CANCELLED = "Cancelled"
    EXPIRED = "Expired"


class OrganizationInvite(BaseModel):
    """
    Invite to an organization.

    Status field tracks the invite lifecycle:
    - Pending: invite is active and awaiting acceptance
    - Accepted: user accepted the invite
    - Cancelled: admin cancelled the invite
    - Expired: derived — pending invite past INVITE_VALIDITY_DAYS
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="invites",
    )

    target_email = models.EmailField(db_index=True)

    # Integer org-level being offered (Owner=15, Admin=8, Member=3, Viewer=1)
    level = models.PositiveSmallIntegerField(choices=Level.CHOICES)

    # Workspace access to grant on accept: [{workspace_id: <uuid>, level: <int>}]
    workspace_access = models.JSONField(default=list, blank=True)

    invited_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_invites",
    )

    status = models.CharField(
        max_length=16,
        default=InviteStatus.PENDING,
        db_index=True,
    )

    message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "accounts_organization_invite"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "target_email"],
                condition=models.Q(status="Pending"),
                name="unique_pending_invite_per_org_email",
            ),
        ]

    def __str__(self):
        return f"Invite {self.target_email} → {self.organization.name} (level={self.level})"

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    @property
    def is_expired(self):
        if self.status != InviteStatus.PENDING:
            return False
        return timezone.now() > self.created_at + timedelta(days=INVITE_VALIDITY_DAYS)

    @property
    def effective_status(self):
        """Return status, upgrading Pending → Expired if past validity window."""
        if self.status == InviteStatus.PENDING and self.is_expired:
            return InviteStatus.EXPIRED
        return self.status

    # ------------------------------------------------------------------
    # Accept flow
    # ------------------------------------------------------------------

    def accept(self, user):
        """
        Atomically:
        1. Create OrganizationMembership (with integer level).
        2. Create WorkspaceMembership for each entry in workspace_access.
        3. Mark this invite as accepted.

        Raises ValueError if the invite is expired or not pending.
        """
        if self.status != InviteStatus.PENDING:
            raise ValueError("This invite is no longer pending.")
        if self.is_expired:
            raise ValueError("This invite has expired.")

        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.workspace import Workspace, WorkspaceMembership

        with transaction.atomic():
            # 1. Create or update org membership
            # Use all_objects to include soft-deleted rows — BaseModel.delete()
            # only sets deleted=True, leaving the DB row and unique constraint
            # intact, which would cause IntegrityError with objects manager.
            org_membership, created = (
                OrganizationMembership.all_objects.update_or_create(
                    user=user,
                    organization=self.organization,
                    defaults={
                        "level": self.level,
                        "role": Level.to_org_string(self.level),
                        "invited_by": self.invited_by,
                        "is_active": True,
                        "deleted": False,
                        "deleted_at": None,
                    },
                )
            )

            # 2. Create workspace memberships
            for ws_entry in self.workspace_access:
                ws_id = ws_entry.get("workspace_id")
                ws_level = ws_entry.get("level", Level.WORKSPACE_VIEWER)
                try:
                    workspace = Workspace.objects.get(
                        id=ws_id, organization=self.organization
                    )
                except Workspace.DoesNotExist:
                    continue  # workspace deleted between invite and accept

                WorkspaceMembership.all_objects.update_or_create(
                    workspace=workspace,
                    user=user,
                    defaults={
                        "level": ws_level,
                        "role": Level.to_ws_role(ws_level),
                        "organization_membership": org_membership,
                        "invited_by": self.invited_by,
                        "is_active": True,
                        "deleted": False,
                        "deleted_at": None,
                    },
                )

            # 3. Add to user's invited_organizations M2M
            user.invited_organizations.add(self.organization)

            # 4. Mark invite as accepted
            self.status = InviteStatus.ACCEPTED
            self.save(update_fields=["status"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def refresh_expiration(self):
        """Reset expiration by updating created_at to now."""
        self.created_at = timezone.now()
        self.save(update_fields=["created_at"])
