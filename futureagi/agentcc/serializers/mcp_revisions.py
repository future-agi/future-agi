"""Request and response contracts of the MCP tool-revision API (M21):
the tools pinning holds for review, what changed in each, and an admin's
decision."""

from rest_framework import serializers

from agentcc.models.mcp_tool_revision import AgentccMCPToolRevision
from tfc.utils.serializer_fields import JsonValueField

_STATUSES = [choice for choice, _ in AgentccMCPToolRevision.Status.choices]


class MCPRevisionListQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[*_STATUSES, "all"],
        required=False,
        default=AgentccMCPToolRevision.Status.PENDING,
        help_text="Which revisions to list: pending (the default), approved, "
        "rejected, superseded or all.",
    )


class MCPRevisionApproveBaselineRequestSerializer(serializers.Serializer):
    server_id = serializers.CharField(max_length=64)


class MCPRevisionDiffSerializer(serializers.Serializer):
    field = serializers.CharField()
    before = JsonValueField(required=False, allow_null=True)
    after = JsonValueField(required=False, allow_null=True)


class MCPRevisionSerializer(serializers.Serializer):
    """``mcp_revisions.describe(row)``."""

    id = serializers.IntegerField()
    server_id = serializers.CharField()
    tool = serializers.CharField()
    revision = serializers.CharField(
        help_text="The sha256 of the tool's canonical definition."
    )
    previous_revision = serializers.CharField(
        allow_null=True, help_text="The accepted revision this one would replace."
    )
    reason = serializers.ChoiceField(
        choices=["changed", "new"],
        help_text="changed: the definition differs from the accepted one; "
        "new: the server added the tool after its tools were accepted.",
    )
    status = serializers.ChoiceField(choices=_STATUSES)
    definition = serializers.DictField()
    previous_definition = serializers.DictField(allow_null=True)
    diff = MCPRevisionDiffSerializer(many=True)
    first_seen_at = serializers.DateTimeField()
    decided_by = serializers.CharField(allow_null=True)
    decided_at = serializers.DateTimeField(allow_null=True)


class MCPRevisionListResultSerializer(serializers.Serializer):
    revisions = MCPRevisionSerializer(many=True)
    gateway_synced = serializers.BooleanField(
        help_text="Whether the inventory refresh and any saved baseline were "
        "confirmed by the gateway."
    )
    warnings = serializers.ListField(child=serializers.DictField(), required=False)


class MCPRevisionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPRevisionListResultSerializer()


class MCPRevisionDecisionResultSerializer(MCPRevisionSerializer):
    version = serializers.IntegerField(required=False)
    gateway_synced = serializers.BooleanField(required=False, allow_null=True)


class MCPRevisionDecisionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPRevisionDecisionResultSerializer()


class MCPRevisionBaselineResultSerializer(serializers.Serializer):
    server_id = serializers.CharField()
    approved = MCPRevisionSerializer(many=True)
    version = serializers.IntegerField(allow_null=True)
    gateway_synced = serializers.BooleanField(allow_null=True)


class MCPRevisionBaselineResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPRevisionBaselineResultSerializer()
