"""
Integer-based role levels for RBAC.

Replaces string-based OrganizationRoles for permission checks.
Integer levels allow >= comparison, max() aggregation, and leave gaps
for future roles without migration.
"""


class Level:
    """
    Role levels with gaps for future expansion.
    Gaps (2, 4-7, 9-14) are reserved for roles like "Billing Admin" (10).
    """

    # Organization-level roles
    VIEWER = 1
    MEMBER = 3
    ADMIN = 8
    OWNER = 15

    # Workspace-level roles (same scale, different scope)
    WORKSPACE_VIEWER = 1
    WORKSPACE_MEMBER = 3
    WORKSPACE_ADMIN = 8

    CHOICES = [
        (OWNER, "Owner"),
        (ADMIN, "Admin"),
        (MEMBER, "Member"),
        (VIEWER, "Viewer"),
    ]

    WORKSPACE_CHOICES = [
        (WORKSPACE_ADMIN, "Workspace Admin"),
        (WORKSPACE_MEMBER, "Workspace Member"),
        (WORKSPACE_VIEWER, "Workspace Viewer"),
    ]

    # Mapping from legacy string roles to integer levels
    STRING_TO_LEVEL = {
        "Owner": OWNER,
        "Admin": ADMIN,
        "Member": MEMBER,
        "Viewer": VIEWER,
        "workspace_admin": WORKSPACE_ADMIN,
        "workspace_member": WORKSPACE_MEMBER,
        "workspace_viewer": WORKSPACE_VIEWER,
        # Legacy display-label values (stored by older code)
        "Workspace Admin": WORKSPACE_ADMIN,
        "Workspace Member": WORKSPACE_MEMBER,
        "Workspace Viewer": WORKSPACE_VIEWER,
    }

    LEVEL_TO_ORG_STRING = {
        OWNER: "Owner",
        ADMIN: "Admin",
        MEMBER: "Member",
        VIEWER: "Viewer",
    }

    LEVEL_TO_WS_STRING = {
        OWNER: "Workspace Admin",
        WORKSPACE_ADMIN: "Workspace Admin",
        WORKSPACE_MEMBER: "Workspace Member",
        WORKSPACE_VIEWER: "Workspace Viewer",
    }

    # DB-safe values matching OrganizationRoles choices for CharField storage
    LEVEL_TO_WS_ROLE = {
        OWNER: "workspace_admin",
        WORKSPACE_ADMIN: "workspace_admin",
        WORKSPACE_MEMBER: "workspace_member",
        WORKSPACE_VIEWER: "workspace_viewer",
    }

    @classmethod
    def from_string(cls, role_string):
        """Convert a legacy string role to an integer level."""
        level = cls.STRING_TO_LEVEL.get(role_string)
        if level is None:
            raise ValueError(f"Unknown role string: {role_string}")
        return level

    @classmethod
    def to_org_string(cls, level):
        """Convert an integer level to an org role display string."""
        return cls.LEVEL_TO_ORG_STRING.get(level, "Unknown")

    @classmethod
    def to_ws_string(cls, level):
        """Convert an integer level to a workspace role display string."""
        return cls.LEVEL_TO_WS_STRING.get(level, "Unknown")

    @classmethod
    def to_ws_role(cls, level):
        """Convert an integer level to a workspace role DB value (OrganizationRoles-compatible)."""
        return cls.LEVEL_TO_WS_ROLE.get(level, "workspace_member")

    # Normalize legacy display-label workspace roles to DB values
    _WS_ROLE_NORMALIZE = {
        "Workspace Admin": "workspace_admin",
        "Workspace Member": "workspace_member",
        "Workspace Viewer": "workspace_viewer",
    }

    @classmethod
    def normalize_ws_role(cls, role):
        """Normalize a workspace role string to its DB value.

        Handles legacy display labels ("Workspace Admin") and passes through
        already-correct DB values ("workspace_admin").
        """
        return cls._WS_ROLE_NORMALIZE.get(role, role)

    @classmethod
    def get_default_ws_level(cls, org_level):
        """Get the default workspace level for an org level."""
        if org_level >= cls.ADMIN:
            return cls.WORKSPACE_ADMIN
        if org_level >= cls.MEMBER:
            return cls.WORKSPACE_MEMBER
        return cls.WORKSPACE_VIEWER


INVITE_VALIDITY_DAYS = 7
