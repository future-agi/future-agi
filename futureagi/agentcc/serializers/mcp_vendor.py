"""Explicit partner API shapes. No read serializer includes a secret or endpoint."""

from rest_framework import serializers

from agentcc.models.mcp_vendor import AgentccMCPVendorConnection


class VendorBindingRequest(serializers.Serializer):
    server_id = serializers.RegexField(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    vendor = serializers.ChoiceField(choices=["pipedream", "composio"])
    credential_id = serializers.UUIDField()
    identity_policy = serializers.ChoiceField(
        choices=["member", "member_or_asserted"], default="member"
    )
    project_id = serializers.RegexField(
        r"^[A-Za-z0-9_-]{1,128}$", required=False, default="", allow_blank=True
    )
    environment = serializers.ChoiceField(
        choices=["development", "production"], default="development"
    )
    app_slug = serializers.RegexField(
        r"^[a-z0-9_-]{1,100}$", required=False, default="", allow_blank=True
    )
    toolkits = serializers.ListField(
        child=serializers.RegexField(r"^[a-z0-9_-]{1,100}$"),
        max_length=1,
        required=False,
        default=list,
    )
    tools = serializers.DictField(
        child=serializers.ListField(
            child=serializers.RegexField(r"^[A-Za-z0-9_-]{1,128}$"),
            min_length=1,
            max_length=1000,
        ),
        required=False,
        default=dict,
    )
    enabled = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if attrs["vendor"] == "pipedream" and (
            not attrs["project_id"] or not attrs["app_slug"]
        ):
            raise serializers.ValidationError(
                "Pipedream requires project_id and app_slug."
            )
        if attrs["vendor"] == "composio":
            if len(attrs["toolkits"]) != 1 or set(attrs["tools"]) != set(
                attrs["toolkits"]
            ):
                raise serializers.ValidationError(
                    "Configure one toolkit and an explicit direct-tool allowlist."
                )
            if any(
                slug.startswith("COMPOSIO_")
                for slugs in attrs["tools"].values()
                for slug in slugs
            ):
                raise serializers.ValidationError("Meta tools are not direct tools.")
        return attrs


class VendorBindingDisableRequest(serializers.Serializer):
    enabled = serializers.ChoiceField(choices=[False])


class VendorBindingResponse(serializers.Serializer):
    id = serializers.UUIDField()
    server_id = serializers.CharField()
    vendor = serializers.ChoiceField(choices=["pipedream", "composio"])
    enabled = serializers.BooleanField()


class VendorBindingDisabledResponse(serializers.Serializer):
    id = serializers.UUIDField()
    enabled = serializers.ChoiceField(choices=[False])


class VendorConnectResponse(serializers.Serializer):
    connect_url = serializers.URLField()


class VendorConnectionSelector(serializers.Serializer):
    server_id = serializers.CharField(max_length=64, required=False)
    connection_id = serializers.UUIDField(required=False)
    user_id = serializers.UUIDField(required=False)


class VendorConnectRequest(serializers.Serializer):
    binding = serializers.CharField(max_length=4096, required=False)
    asserted_id = serializers.CharField(max_length=128, required=False)


class VendorConnectionResponse(serializers.ModelSerializer):
    server_id = serializers.CharField(source="binding.server_id")
    vendor = serializers.CharField(source="binding.vendor")

    class Meta:
        model = AgentccMCPVendorConnection
        fields = [
            "id",
            "server_id",
            "vendor",
            "source",
            "status",
            "gateway_purge_pending",
            "vendor_revoke_pending",
            "vendor_revoke_outcome",
            "created_at",
        ]
