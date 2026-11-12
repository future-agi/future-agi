from rest_framework import serializers

from tfc.utils.api_serializers import ManagementAPIErrorResponseSerializer


class MCPApprovalMessageSerializer(serializers.Serializer):
    """Message-only responses returned directly by the approval bridge."""

    message = serializers.CharField()


class MCPApprovalNotFoundSerializer(ManagementAPIErrorResponseSerializer):
    """Gateway 404s have only message; overlong IDs use the DRF error envelope."""

    message = serializers.CharField()


class MCPApprovalDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approve", "deny"])
    note = serializers.CharField(max_length=1024, allow_blank=True, default="")

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - {"decision", "note"}:
            raise serializers.ValidationError(
                {"non_field_errors": ["Only decision and note are accepted."]}
            )
        return super().to_internal_value(data)

    def validate_note(self, value):
        if len(value.encode()) > 1024:
            raise serializers.ValidationError("The note must fit in 1024 UTF-8 bytes.")
        return value


class MCPApprovalPrincipalSerializer(serializers.Serializer):
    kind = serializers.CharField()
    key_id = serializers.CharField(required=False)
    user_id = serializers.CharField(required=False)
    client_id = serializers.CharField(required=False)


class MCPApprovalSerializer(serializers.Serializer):
    approval_id = serializers.CharField(max_length=4096)
    org_id = serializers.CharField()
    virtual_server_id = serializers.CharField()
    tool = serializers.CharField()
    args_hash = serializers.CharField()
    agent_run_id = serializers.CharField(required=False)
    principal = MCPApprovalPrincipalSerializer()
    status = serializers.ChoiceField(
        choices=["pending", "approved", "denied", "used", "expired"]
    )
    requested_at = serializers.DateTimeField()
    expires_at = serializers.DateTimeField()
    decided_by = serializers.CharField(required=False)
    decided_at = serializers.DateTimeField(required=False)
    note = serializers.CharField(required=False)


class MCPApprovalResultSerializer(serializers.Serializer):
    result = MCPApprovalSerializer()


class MCPApprovalListResultSerializer(serializers.Serializer):
    result = MCPApprovalSerializer(many=True)
