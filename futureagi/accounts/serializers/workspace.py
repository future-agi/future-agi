import json
import re

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
        # Use the prefetched cache when the view supplies it (avoids the
        # per-workspace N+1); fall back to a direct query otherwise.
        admin_memberships = getattr(obj, "admin_memberships_cache", None)
        if admin_memberships is None:
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

    old_workspace_id = serializers.UUIDField(required=False)
    new_workspace_id = serializers.UUIDField()


class PaginationSerializer(serializers.Serializer):
    """Base serializer for paginated requests"""

    page = serializers.IntegerField(min_value=1, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=10)
    search = serializers.CharField(required=False, allow_blank=True, default="")
    sort = serializers.CharField(required=False, allow_blank=True, default="")


class WorkspaceListRequestSerializer(PaginationSerializer):
    """Serializer for workspace list request"""

    pass


class UserListSortField(serializers.Field):
    """Normalize supported user-list sort payload shapes to Django order_by fields."""

    default_error_messages = {
        "invalid": "Sort must be a field name, JSON object, or JSON list."
    }
    _DESC_VALUES = {"desc", "descending", "down", "false"}
    _COLUMN_MAP = {
        "name": "name",
        "email": "email",
        "role": "computed_role_rank",
        "status": "computed_status",
        "startdate": "created_at",
        "start_date": "created_at",
        "lastupdateddate": "created_at",
        "last_updated_date": "created_at",
    }

    def to_internal_value(self, data):
        if data in (None, ""):
            return []
        if isinstance(data, str):
            data = self._parse_string(data)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            self.fail("invalid")

        ordering = []
        for item in data:
            order_by = self._item_to_ordering(item)
            if order_by:
                ordering.append(order_by)
        return ordering

    def to_representation(self, value):
        return value or []

    def _parse_string(self, value):
        value = value.strip()
        if not value:
            return []
        if value[0] in "[{":
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                self.fail("invalid")
        return [value]

    def _item_to_ordering(self, item):
        if isinstance(item, str):
            descending = item.startswith("-")
            field_name = self._map_column(item[1:] if descending else item)
            if not field_name:
                return None
            return f"-{field_name}" if descending else field_name

        if not isinstance(item, dict):
            return None

        column_id = item.get("columnId") or item.get("id") or item.get("column")
        field_name = self._map_column(column_id)
        if not field_name:
            return None
        sort_type = item.get("type") or item.get("order") or item.get("dir")
        if str(sort_type).lower() in self._DESC_VALUES:
            return f"-{field_name}"
        return field_name

    def _map_column(self, column_id):
        if not column_id:
            return None
        key = str(column_id).strip()
        return self._COLUMN_MAP.get(key.lower())


class UserListRequestSerializer(PaginationSerializer):
    """Serializer for user list request with additional filters"""

    sort = UserListSortField(required=False, default=list)
    workspace_id = serializers.UUIDField(required=False)
    filter_status = serializers.ListField(
        child=serializers.ChoiceField(
            choices=[
                "All status",
                "Active",
                "Inactive",
                "Pending",
                "Expired",
                "Request Pending",
                "Request Expired",
            ]
        ),
        required=False,
        default=list,
    )

    _SORT_BRACKET_KEY_RE = re.compile(r"^sort\[(\d+)\]\[(columnId|type)\]$")

    def allows_unknown_field(self, field_name):
        return bool(self._SORT_BRACKET_KEY_RE.match(field_name))

    def to_internal_value(self, data):
        normalized = self._normalized_query_data(data)
        bracket_sort = self._sort_from_bracket_query(data)
        if bracket_sort and not normalized.get("sort"):
            normalized["sort"] = bracket_sort
        return super().to_internal_value(normalized)

    def _normalized_query_data(self, data):
        if not hasattr(data, "keys"):
            return data

        normalized = {}
        multi_value_fields = {"filter_status", "filter_role"}
        for key in data.keys():
            if self._SORT_BRACKET_KEY_RE.match(key):
                continue
            if hasattr(data, "getlist") and key in multi_value_fields:
                normalized[key] = data.getlist(key)
            else:
                normalized[key] = data.get(key)
        return normalized

    def _sort_from_bracket_query(self, data):
        if not hasattr(data, "keys"):
            return []

        sort_items = {}
        for key in data.keys():
            match = self._SORT_BRACKET_KEY_RE.match(key)
            if not match:
                continue
            index = int(match.group(1))
            sort_key = match.group(2)
            sort_items.setdefault(index, {})[sort_key] = data.get(key)

        return [sort_items[index] for index in sorted(sort_items.keys())]
    filter_role = serializers.ListField(
        child=serializers.ChoiceField(choices=OrganizationRoles.choices),
        required=False,
        default=list,
    )
