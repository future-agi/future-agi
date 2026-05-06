from rest_framework import serializers

from accounts.models import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.roles import OrganizationRoles


class WorkspaceListSerializer(serializers.ModelSerializer):
    """Serializer for listing workspaces with pagination"""

    admin_names = serializers.SerializerMethodField()
    start_data = serializers.SerializerMethodField()
    last_update_date = serializers.SerializerMethodField()
    invite_link = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = [
            "id",
            "name",
            "display_name",
            "admin_names",
            "start_data",
            "last_update_date",
            "invite_link",
        ]

    def get_admin_names(self, obj):
        """Get admin names for the workspace"""
        admin_memberships = WorkspaceMembership.no_workspace_objects.filter(
            workspace=obj,
            role__in=[
                OrganizationRoles.WORKSPACE_ADMIN,
                OrganizationRoles.OWNER,
                OrganizationRoles.ADMIN,
            ],
            is_active=True,
        ).select_related("user")

        return [
            {"name": membership.user.name, "id": str(membership.user.id)}
            for membership in admin_memberships
        ]

    def get_start_data(self, obj):
        """Get start date in required format"""
        return obj.created_at.strftime("%Y-%m-%d") if obj.created_at else ""

    def get_last_update_date(self, obj):
        """Get last update date in required format"""
        return obj.updated_at.strftime("%Y-%m-%d") if obj.updated_at else ""

    def get_invite_link(self, obj):
        """Get invite link (placeholder for v1)"""
        return "url"  # Not implemented in v1 as per contract


class WorkspaceInviteSerializer(serializers.Serializer):
    """Serializer for inviting users to workspaces with select_all functionality"""

    emails = serializers.ListField(child=serializers.EmailField(), min_length=1)
    role = serializers.ChoiceField(
        choices=[
            OrganizationRoles.WORKSPACE_MEMBER,
            OrganizationRoles.WORKSPACE_ADMIN,
            OrganizationRoles.WORKSPACE_VIEWER,
            OrganizationRoles.MEMBER,
            OrganizationRoles.MEMBER_VIEW_ONLY,
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ],
        default=OrganizationRoles.WORKSPACE_MEMBER,
    )
    select_all = serializers.BooleanField(default=False)
    workspace_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,  # Made optional when select_all is True
    )

    def validate(self, data):
        """
        Custom validation for select_all logic:
        - If select_all is True: workspace_ids should be excluded workspaces (optional)
        - If select_all is False: workspace_ids should be included workspaces (required)
        """
        select_all = data.get("select_all", False)
        workspace_ids = data.get("workspace_ids", [])

        if select_all:
            # When select_all is True, workspace_ids are excluded workspaces (optional)
            # No validation needed for workspace_ids
            pass
        else:
            # When select_all is False, workspace_ids are included workspaces (required)
            if not workspace_ids:
                raise serializers.ValidationError(
                    "workspace_ids is required when select_all is False"
                )

        return data


class DeactivateUserSerializer(serializers.Serializer):
    """Serializer for deactivating users"""

    user_id = serializers.UUIDField()


class UserListSerializer(serializers.ModelSerializer):
    """Serializer for user list with pagination and filtering"""

    role = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    start_date = serializers.SerializerMethodField()
    last_updated_date = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "role",
            "status",
            "start_date",
            "last_updated_date",
        ]

    def get_role(self, obj):
        """Return the effective workspace role if available, otherwise organization role."""
        # Check if computed_workspace_role annotation is available (from UserListAPIView)
        # Fallback to organization role (may be None for workspace-only users)
        role = obj.organization_role if obj.organization_role else None
        if not role:
            role = (
                WorkspaceMembership.objects.filter(user=obj, is_active=True)
                .first()
                .role
            )
        return role

    def get_status(self, obj):
        """Get user status with proper invitation state handling"""

        # Check if user has been invited but not yet activated
        if obj.invited_by and not obj.is_active:
            return "Request Pending"

        # Check if user is a primary member (has organization)
        if obj.organization:
            return "Active"

        # Check if user is active
        if not obj.is_active:
            return "Inactive"

        # Check if user is an invited member
        if obj.invited_organizations.exists():
            # Check if any invitation is still active
            active_invitations = obj.organization_memberships.filter(is_active=True)
            if active_invitations.exists():
                return "Request Pending"
            else:
                return "Request Expired"

        return "Active"

    def get_start_date(self, obj):
        """Get start date in required format"""
        return obj.created_at.strftime("%Y-%m-%d") if obj.created_at else ""

    def get_last_updated_date(self, obj):
        """Get last updated date in required format - using created_at since User doesn't have updated_at"""
        return obj.created_at.strftime("%Y-%m-%d") if obj.created_at else ""


class UserRoleUpdateSerializer(serializers.Serializer):
    """Serializer for updating user role"""

    user_id = serializers.UUIDField()
    new_role = serializers.ChoiceField(choices=OrganizationRoles.choices)
    workspace_id = serializers.UUIDField(required=False, allow_null=True)


class ResendInviteSerializer(serializers.Serializer):
    """Serializer for resending invites"""

    user_id = serializers.UUIDField()


class DeleteUserSerializer(serializers.Serializer):
    """Serializer for deleting users/removing invites"""

    user_id = serializers.UUIDField()


class SwitchWorkspaceSerializer(serializers.Serializer):
    """Serializer for switching workspaces"""

    new_workspace_id = serializers.UUIDField()


class PaginationSerializer(serializers.Serializer):
    """Base serializer for paginated requests"""

    page = serializers.IntegerField(min_value=1, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=10)
    search = serializers.CharField(required=False, allow_blank=True, default="")
    sort = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )


class WorkspaceListRequestSerializer(PaginationSerializer):
    """Serializer for workspace list request"""

    pass


class UserListRequestSerializer(PaginationSerializer):
    """Serializer for user list request with additional filters"""

    filter_status = serializers.ListField(
        child=serializers.ChoiceField(
            choices=["Active", "Inactive", "Request Pending", "Request Expired"]
        ),
        required=False,
        default=list,
    )
    filter_role = serializers.ListField(
        child=serializers.ChoiceField(choices=OrganizationRoles.choices),
        required=False,
        default=list,
    )
