import uuid

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from tfc.constants.roles import OrganizationRoles, RoleMapping
from tfc.utils.base_model import BaseModel


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    invited_by = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, default=None
    )

    organization_role = models.CharField(
        max_length=20,
        choices=OrganizationRoles.choices,
        default=OrganizationRoles.OWNER,
        null=True,
        blank=True,
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="members",
    )

    # Additional organizations where they are invited members
    invited_organizations = models.ManyToManyField(
        Organization,
        through="OrganizationMembership",
        through_fields=("user", "organization"),
        related_name="invited_members",
        blank=True,
    )

    objects = CustomUserManager()

    config = models.JSONField(default=dict, blank=True)

    # Onboarding fields
    role = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="User's job role (e.g., Data Scientist, ML Engineer, or custom role)",
    )
    goals = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text="List of user's goals for using the platform",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = [
        "name"
    ]  # For the createsuperuser management command. You can add fields here, but remember, they will be prompted for when creating a superuser. Email is not included here because it's already the USERNAME_FIELD.

    def __str__(self):
        return self.email

    # --- Multi-org helpers ---

    def get_membership(self, organization):
        """Get the active OrganizationMembership for a specific org, or None."""
        try:
            return OrganizationMembership.no_workspace_objects.get(
                user=self, organization=organization, is_active=True
            )
        except OrganizationMembership.DoesNotExist:
            return None

    def get_membership_level(self, organization):
        """Get integer RBAC level for a specific org."""
        membership = self.get_membership(organization)
        if membership:
            return membership.level_or_legacy
        # Fallback: legacy FK (only for users with no membership records for this org)
        # This covers truly legacy accounts created before the membership table existed.
        # Users with deactivated memberships should NOT get this fallback.
        if self.organization_id and self.organization_id == organization.id:
            # Only use fallback if user has no membership records at all for this org
            has_any_membership = OrganizationMembership.no_workspace_objects.filter(
                user=self, organization=organization
            ).exists()
            if not has_any_membership:
                from tfc.constants.levels import Level

                return Level.STRING_TO_LEVEL.get(self.organization_role, Level.VIEWER)
        return None

    # --- Organization access ---

    def can_access_organization(self, organization):
        """Check if user has access to a specific organization.

        Uses OrganizationMembership as source of truth.
        """
        return OrganizationMembership.no_workspace_objects.filter(
            user=self, organization=organization, is_active=True
        ).exists()

    def get_organization_role(self, organization=None):
        """Get user's role in the given organization.

        Queries OrganizationMembership for the target org.
        Falls back to legacy user.organization_role only for truly legacy accounts
        (users with no membership records at all for this org).
        """
        target_org = organization or self.organization
        if not target_org:
            return None

        membership = self.get_membership(target_org)
        if membership:
            return membership.role

        # Fallback: legacy field (only for truly legacy accounts)
        # Users with deactivated memberships should NOT get this fallback.
        if self.organization_id and self.organization_id == target_org.id:
            # Only use fallback if user has no membership records at all for this org
            has_any_membership = OrganizationMembership.no_workspace_objects.filter(
                user=self, organization=target_org
            ).exists()
            if not has_any_membership:
                return self.organization_role
        return None

    def has_global_workspace_access(self, organization=None):
        """Check if user's role in the given org grants global workspace access."""
        target_org = organization or self.organization
        if not target_org:
            return False

        membership = self.get_membership(target_org)
        if membership:
            from tfc.constants.levels import Level

            return membership.level_or_legacy >= Level.ADMIN

        # Legacy fallback: no membership record exists for this org
        role = self.get_organization_role(target_org)
        if not role:
            return False

        from tfc.constants.roles import RolePermissions

        return role in RolePermissions.GLOBAL_ACCESS_ROLES

    def is_workspace_only_user(self, organization=None):
        """Check if user only has workspace-specific access."""
        return not self.has_global_workspace_access(organization)

    # --- Workspace access ---

    def can_access_workspace(self, workspace):
        """Check if user can access a specific workspace."""
        from accounts.models.workspace import WorkspaceMembership

        org = workspace.organization

        if not self.can_access_organization(org):
            return False

        if self.has_global_workspace_access(org):
            return True

        # Use no_workspace_objects to avoid workspace-scoped filtering,
        # which would prevent cross-workspace access checks.
        return WorkspaceMembership.no_workspace_objects.filter(
            user=self, workspace=workspace, is_active=True
        ).exists()

    def get_workspace_role(self, workspace):
        """Get user's role for a specific workspace."""
        org = workspace.organization
        org_role = self.get_organization_role(org)

        if org_role and self.has_global_workspace_access(org):
            return RoleMapping.get_workspace_role(org_role)

        # Check specific workspace membership (use no_workspace_objects to
        # avoid workspace-scoped filtering during cross-workspace checks)
        try:
            from accounts.models.workspace import WorkspaceMembership

            membership = WorkspaceMembership.no_workspace_objects.get(
                user=self, workspace=workspace, is_active=True
            )
            # Prefer level-based role (new RBAC) over legacy role string
            if membership.level is not None:
                from tfc.constants.levels import Level

                _level_to_role = {
                    Level.WORKSPACE_ADMIN: OrganizationRoles.WORKSPACE_ADMIN,
                    Level.WORKSPACE_MEMBER: OrganizationRoles.WORKSPACE_MEMBER,
                    Level.WORKSPACE_VIEWER: OrganizationRoles.WORKSPACE_VIEWER,
                }
                return _level_to_role.get(membership.level, membership.role)
            # Coerce the raw role string to an OrganizationRoles enum member
            # so callers always get a consistent type (enum, not str).
            try:
                return OrganizationRoles(membership.role)
            except ValueError:
                return membership.role
        except Exception:
            return None

    def can_write_to_workspace(self, workspace):
        """Check if user can write to a specific workspace."""
        if not self.can_access_workspace(workspace):
            return False

        role = self.get_workspace_role(workspace)
        if not role:
            return False

        from tfc.constants.roles import RolePermissions

        return role in RolePermissions.WRITE_ACCESS_ROLES

    def can_read_from_workspace(self, workspace):
        """Check if user can read from a specific workspace."""
        if not self.can_access_workspace(workspace):
            return False

        role = self.get_workspace_role(workspace)
        if not role:
            return False

        from tfc.constants.roles import RolePermissions

        return role in RolePermissions.READ_ACCESS_ROLES

    @property
    def has_2fa_enabled(self):
        """True if user has at least one confirmed 2FA method."""
        try:
            has_totp = self.totp_device.confirmed
        except Exception:
            has_totp = False
        has_passkey = self.webauthn_credentials.exists()
        return has_totp or has_passkey


class OrgApiKey(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, default="system_org_key")
    api_key = models.CharField(max_length=255, unique=True)
    secret_key = models.CharField(max_length=255)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="api_keys"
    )

    type = models.CharField(
        max_length=50,
        default="system",
        choices=[("system", "System"), ("user", "User"), ("mcp", "MCP")],
    )
    enabled = models.BooleanField(default=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="user_secret_key",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="ws_api_keys",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "type"],
                condition=models.Q(type="system", deleted=False),
                name="unique_system_api_key_per_org_not_deleted",
                violation_error_message="Only one system API key is allowed per organization.",
            )
        ]

    def __str__(self):
        if self.organization.display_name:
            return f"{self.organization.display_name} API Key"
        return f"{self.organization.name} API Key"

    def save(self, *args, **kwargs):
        if not self.api_key:
            self.api_key = self.generate_api_key()
        if not self.secret_key:
            self.secret_key = self.generate_secret_key()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_api_key():
        # Implement your logic for generating a unique API key
        return uuid.uuid4().hex

    @staticmethod
    def generate_secret_key():
        # Implement your logic for generating a secret key
        return uuid.uuid4().hex
