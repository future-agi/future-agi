import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class AuditAction:
    """Known audit actions. CharField is not DB-enforced, so new actions can be
    added without a migration — but always define them here first."""

    MEMBER_INVITED = "member.invited"
    MEMBER_REMOVED = "member.removed"
    MEMBER_ROLE_UPDATED = "member.role_updated"
    MEMBER_REACTIVATED = "member.reactivated"
    MEMBER_DEACTIVATED = "member.deactivated"
    INVITE_RESENT = "invite.resent"
    INVITE_CANCELLED = "invite.cancelled"
    WS_MEMBER_ADDED = "workspace_member.added"
    WS_MEMBER_REMOVED = "workspace_member.removed"
    WS_MEMBER_ROLE_UPDATED = "workspace_member.role_updated"

    # Omega product rollout changes (omega_rollout/audit.py, READINESS 3.13), written under the
    # operator's org and the target org. Not in CHOICES: adding them there changes the field's
    # choices and needs an accounts migration, which three lanes would otherwise number 0026 at once.
    # `action` is a CharField with no database constraint, so these values store as they are.
    ROLLOUT_STAGE_CHANGED = "omega_rollout.stage_changed"
    ROLLOUT_PERCENTAGE_CHANGED = "omega_rollout.percentage_changed"
    ROLLOUT_KILLED = "omega_rollout.killed"
    ROLLOUT_RESTORED = "omega_rollout.restored"
    ROLLOUT_ALLOW_SET = "omega_rollout.allow_set"
    ROLLOUT_DENY_SET = "omega_rollout.deny_set"
    ROLLOUT_ACCESS_REMOVED = "omega_rollout.access_removed"
    ROLLOUT_ACCESS_REQUESTED = "omega_rollout.access_requested"
    ROLLOUT_INVITED = "omega_rollout.invited"
    ROLLOUT_REQUEST_DECLINED = "omega_rollout.request_declined"
    ROLLOUT_ACTIONS = frozenset(
        {
            ROLLOUT_STAGE_CHANGED,
            ROLLOUT_PERCENTAGE_CHANGED,
            ROLLOUT_KILLED,
            ROLLOUT_RESTORED,
            ROLLOUT_ALLOW_SET,
            ROLLOUT_DENY_SET,
            ROLLOUT_ACCESS_REMOVED,
            ROLLOUT_ACCESS_REQUESTED,
            ROLLOUT_INVITED,
            ROLLOUT_REQUEST_DECLINED,
        }
    )

    # Like rollout actions, these do not change field choices or storage.
    GATEWAY_KEY_CREATED = "gateway_key.created"
    GATEWAY_KEY_REVOKED = "gateway_key.revoked"
    GATEWAY_KEY_ROTATED = "gateway_key.rotated"

    CHOICES = [
        (MEMBER_INVITED, "Member Invited"),
        (MEMBER_REMOVED, "Member Removed"),
        (MEMBER_ROLE_UPDATED, "Member Role Updated"),
        (MEMBER_REACTIVATED, "Member Reactivated"),
        (MEMBER_DEACTIVATED, "Member Deactivated"),
        (INVITE_RESENT, "Invite Resent"),
        (INVITE_CANCELLED, "Invite Cancelled"),
        (WS_MEMBER_ADDED, "Workspace Member Added"),
        (WS_MEMBER_REMOVED, "Workspace Member Removed"),
        (WS_MEMBER_ROLE_UPDATED, "Workspace Member Role Updated"),
    ]


class AuditScope:
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"

    CHOICES = [
        (ORGANIZATION, "Organization"),
        (WORKSPACE, "Workspace"),
    ]


class AuditLog(BaseModel):
    """Immutable audit trail for RBAC changes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # What happened
    action = models.CharField(max_length=64, choices=AuditAction.CHOICES)
    scope = models.CharField(max_length=32, choices=AuditScope.CHOICES)

    # Target entity ID — can reference User, OrganizationInvite, Workspace,
    # etc. depending on the action. Not a ForeignKey because it spans tables.
    target_id = models.UUIDField(db_index=True)

    # Before/after snapshot
    changes = models.JSONField(default=dict, blank=True)

    # Extra context (IP, user-agent, invite token, etc.)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_audit_log"
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=["organization", "scope", "-created_at"],
                name="audit_org_scope_ts",
            ),
        ]

    def __str__(self):
        return f"{self.action} by {self.actor_id} at {self.created_at}"
