"""
Serializers for the new RBAC endpoints.
"""

from rest_framework import serializers

from tfc.constants.levels import Level


class _StrictSerializer(serializers.Serializer):
    """Reject unknown keys at nested contract boundaries."""

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            unknown = sorted(set(data.keys()) - set(self.fields.keys()))
            if unknown:
                raise serializers.ValidationError(
                    {key: ["Unknown field."] for key in unknown}
                )
        return super().to_internal_value(data)


class WorkspaceAccessInputSerializer(_StrictSerializer):
    workspace_id = serializers.UUIDField()
    level = serializers.ChoiceField(
        choices=Level.WORKSPACE_CHOICES,
        required=False,
    )

    def validate_workspace_id(self, value):
        return str(value)


class InviteCreateSerializer(serializers.Serializer):
    """
    POST /accounts/organization/invite/
    Payload: { emails, org_level, workspace_access: [{workspace_id, level}] }
    """

    emails = serializers.ListField(
        child=serializers.EmailField(),
        min_length=1,
        max_length=50,
    )
    org_level = serializers.ChoiceField(
        choices=Level.CHOICES,
        help_text="Integer org level to grant (Owner=15, Admin=8, Member=3, Viewer=1).",
    )
    workspace_access = WorkspaceAccessInputSerializer(
        many=True,
        required=False,
        default=list,
        help_text='List of {"workspace_id": "<uuid>", "level": <int>}.',
    )

    def validate_workspace_access(self, value):
        sanitized = []
        for entry in value:
            if "workspace_id" not in entry:
                raise serializers.ValidationError(
                    "Each workspace_access entry must include 'workspace_id'."
                )
            ws_level = entry.get("level", Level.WORKSPACE_VIEWER)
            if ws_level not in dict(Level.WORKSPACE_CHOICES):
                raise serializers.ValidationError(
                    f"Invalid workspace level: {ws_level}. "
                    f"Valid: {[c[0] for c in Level.WORKSPACE_CHOICES]}"
                )
            sanitized.append(
                {
                    "workspace_id": entry["workspace_id"],
                    "level": ws_level,
                }
            )
        return sanitized


class InviteCreateResultSerializer(serializers.Serializer):
    invited = serializers.ListField(child=serializers.EmailField())
    already_members = serializers.ListField(
        child=serializers.EmailField(),
        required=False,
    )


class InviteCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = InviteCreateResultSerializer()


class InviteResendSerializer(serializers.Serializer):
    """POST /accounts/organization/invite/resend/"""

    invite_id = serializers.UUIDField()
    org_level = serializers.ChoiceField(
        choices=Level.CHOICES,
        required=False,
        allow_null=True,
        default=None,
    )


class InviteCancelSerializer(serializers.Serializer):
    """DELETE /accounts/organization/invite/cancel/"""

    invite_id = serializers.UUIDField()


class RBACMessageResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class RBACMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RBACMessageResultSerializer()


class MemberRoleUpdateSerializer(serializers.Serializer):
    """
    POST /accounts/organization/members/role/
    Payload: { user_id, org_level?, ws_level?, workspace_id }
    """

    user_id = serializers.UUIDField()
    org_level = serializers.ChoiceField(
        choices=Level.CHOICES,
        required=False,
        allow_null=True,
    )
    ws_level = serializers.ChoiceField(
        choices=Level.WORKSPACE_CHOICES,
        required=False,
        allow_null=True,
    )
    workspace_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Required when updating ws_level.",
    )
    workspace_access = WorkspaceAccessInputSerializer(
        many=True,
        required=False,
        default=list,
        help_text="List of {workspace_id, level} for explicit workspace grants on demotion.",
    )

    def validate(self, data):
        if data.get("ws_level") and not data.get("workspace_id"):
            raise serializers.ValidationError(
                "workspace_id is required when updating ws_level."
            )
        if not data.get("org_level") and not data.get("ws_level"):
            raise serializers.ValidationError(
                "At least one of org_level or ws_level must be provided."
            )
        return data


class MemberWorkspaceAccessSerializer(serializers.Serializer):
    workspace_id = serializers.UUIDField()
    workspace_name = serializers.CharField()
    ws_level = serializers.IntegerField()
    ws_role = serializers.CharField()
    auto_access = serializers.BooleanField(required=False)


class MemberListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(allow_blank=True)
    email = serializers.EmailField()
    org_level = serializers.IntegerField(required=False, allow_null=True)
    org_role = serializers.CharField(required=False, allow_null=True)
    ws_level = serializers.IntegerField(required=False, allow_null=True)
    ws_role = serializers.CharField(required=False, allow_null=True)
    workspaces = MemberWorkspaceAccessSerializer(many=True, required=False)
    status = serializers.CharField()
    created_at = serializers.CharField(allow_blank=True)
    type = serializers.ChoiceField(choices=["member", "invite"])
    auto_access = serializers.BooleanField(required=False)


class MemberListResultSerializer(serializers.Serializer):
    results = MemberListItemSerializer(many=True)
    total = serializers.IntegerField()
    page = serializers.IntegerField()
    limit = serializers.IntegerField()


class MemberListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MemberListResultSerializer()


class WorkspaceMemberRowSerializer(serializers.Serializer):
    """Contract for a single row of the workspace-members list — the shape built
    by ``services.workspace_members._member_row`` (explicit members, auto-access
    admins, and pending invites all share it)."""

    id = serializers.UUIDField()
    name = serializers.CharField(allow_blank=True)
    email = serializers.EmailField()
    ws_level = serializers.IntegerField(required=False, allow_null=True)
    ws_role = serializers.CharField(required=False, allow_null=True)
    org_level = serializers.IntegerField(required=False, allow_null=True)
    org_role = serializers.CharField(required=False, allow_null=True)
    status = serializers.CharField()
    created_at = serializers.CharField(allow_blank=True)
    type = serializers.ChoiceField(choices=["member", "invite"])
    auto_access = serializers.BooleanField(required=False)


class WorkspaceMemberListResultSerializer(serializers.Serializer):
    results = WorkspaceMemberRowSerializer(many=True)
    total = serializers.IntegerField()
    page = serializers.IntegerField()
    limit = serializers.IntegerField()


class WorkspaceMemberListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WorkspaceMemberListResultSerializer()


class MemberRoleUpdateResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    changes = serializers.JSONField()


class MemberRoleUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MemberRoleUpdateResultSerializer()


class MemberRemoveSerializer(serializers.Serializer):
    """DELETE /accounts/organization/members/remove/"""

    user_id = serializers.UUIDField()


class MemberUserMutationResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    user_id = serializers.UUIDField()


class MemberUserMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MemberUserMutationResultSerializer()


class MemberListRequestSerializer(serializers.Serializer):
    """GET /accounts/organization/members/ query params."""

    SORT_CHOICES = [
        "name",
        "-name",
        "email",
        "-email",
        "status",
        "-status",
        "type",
        "-type",
        "date_joined",
        "-date_joined",
        "created_at",
        "-created_at",
        "org_level",
        "-org_level",
    ]

    page = serializers.IntegerField(min_value=1, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=20)
    search = serializers.CharField(required=False, allow_blank=True, default="")
    filter_status = serializers.ListField(
        child=serializers.ChoiceField(
            choices=["Active", "Pending", "Expired", "Deactivated"]
        ),
        required=False,
        default=list,
    )
    filter_role = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    sort = serializers.ChoiceField(
        choices=SORT_CHOICES,
        required=False,
        default="-created_at",
    )


# ── Workspace-scoped member endpoints ──


class WorkspaceMemberListRequestSerializer(serializers.Serializer):
    """GET /accounts/workspace/<uuid>/members/ query params."""

    SORT_CHOICES = [
        "name",
        "-name",
        "email",
        "-email",
        "status",
        "-status",
        "type",
        "-type",
        "date_joined",
        "-date_joined",
        "created_at",
        "-created_at",
        "ws_level",
        "-ws_level",
    ]

    page = serializers.IntegerField(min_value=1, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=20)
    search = serializers.CharField(required=False, allow_blank=True, default="")
    filter_status = serializers.ListField(
        child=serializers.ChoiceField(choices=["Active", "Pending", "Expired"]),
        required=False,
        default=list,
    )
    filter_role = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    sort = serializers.ChoiceField(
        choices=SORT_CHOICES,
        required=False,
        default="-created_at",
    )


class WorkspaceMemberRoleUpdateSerializer(serializers.Serializer):
    """POST /accounts/workspace/<uuid>/members/role/"""

    user_id = serializers.UUIDField()
    ws_level = serializers.ChoiceField(choices=Level.WORKSPACE_CHOICES)


class WorkspaceMemberRemoveSerializer(serializers.Serializer):
    """DELETE /accounts/workspace/<uuid>/members/remove/"""

    user_id = serializers.UUIDField()


class WorkspaceMemberRoleUpdateResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    user_id = serializers.UUIDField()
    ws_level = serializers.IntegerField()
    ws_role = serializers.CharField()


class WorkspaceMemberRoleUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WorkspaceMemberRoleUpdateResultSerializer()
