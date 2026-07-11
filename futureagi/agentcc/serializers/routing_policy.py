from rest_framework import serializers

from agentcc.models.routing_policy import AgentccRoutingPolicy


class AgentccRoutingPolicySerializer(serializers.ModelSerializer):
    """A standalone, versioned routing policy whose JSON config controls how the
    gateway routes requests across models/providers (fallbacks, load balancing,
    etc.). Use it to define and manage routing rules. Listed/read via
    list_agentcc_routing_policies / get_agentcc_routing_policy; version is
    server-managed and increments on change."""

    class Meta:
        model = AgentccRoutingPolicy
        fields = [
            "id",
            "organization",
            "name",
            "description",
            "version",
            "config",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "version",
            "created_by",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "name": {"help_text": "Name for this routing policy."},
            "description": {"help_text": "Optional description of the policy."},
            "version": {
                "help_text": "Monotonic version number, server-managed (read-only)."
            },
            "config": {
                "help_text": "JSON object holding the routing rules/configuration."
            },
            "is_active": {"help_text": "Whether this policy version is active."},
            "created_by": {
                "help_text": "UUID of the user who created this version (read-only)."
            },
        }

    def validate_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("config must be a JSON object")
        return value
