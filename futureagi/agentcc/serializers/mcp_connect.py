"""Request and response contracts of the per-user upstream OAuth routes
(M25x): the connect start, the disconnect and the personal-key toggle. No
token ever appears in one."""

from rest_framework import serializers

MAX_BINDING_CHARS = 4096


class MCPConnectStartRequestSerializer(serializers.Serializer):
    binding = serializers.CharField(
        max_length=MAX_BINDING_CHARS,
        required=False,
        trim_whitespace=False,
        help_text="The sealed binding of a gateway connect link, when the "
        "connect comes from one.",
    )
    mode = serializers.ChoiceField(
        choices=["user", "shared"],
        required=False,
        default="user",
        help_text="user: the caller's own account. shared: the organization's "
        "one account on a shared server whose credential kind is oauth, signed "
        "in by an Owner or Admin; it takes no binding.",
    )


class MCPConnectStartResultSerializer(serializers.Serializer):
    authorize_url = serializers.CharField(
        help_text="Open in this browser to sign in to the server's account."
    )
    expires_in = serializers.IntegerField(
        help_text="Seconds the sign-in may take before it must start again."
    )


class MCPConnectStartResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPConnectStartResultSerializer()


class MCPDisconnectQuerySerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=["user", "shared"],
        required=False,
        default="user",
        help_text="user: your own account; shared: the organization's account (Owner/Admin only).",
    )

    def to_internal_value(self, data):
        if hasattr(data, "getlist") and len(data.getlist("mode")) > 1:
            raise serializers.ValidationError({"mode": "Specify mode only once."})
        if "mode" in data and data.get("mode") == "":
            raise serializers.ValidationError({"mode": "Specify user or shared."})
        return super().to_internal_value(data)


class MCPRevocationField(serializers.ChoiceField):
    def __init__(self, **kwargs):
        super().__init__(
            choices=["pending", "confirmed", "unconfirmed"],
            allow_null=True,
            help_text="Vendor revocation: pending can be retried; unconfirmed is terminal "
            "and the old tokens are erased; confirmed means the vendor acknowledged. "
            "Null for an account without a revocation attempt.",
            **kwargs,
        )


class MCPDisconnectResultSerializer(serializers.Serializer):
    server_id = serializers.CharField()
    disconnected = serializers.BooleanField()
    gateway_synced = serializers.BooleanField()
    upstream_revocation = MCPRevocationField()
    cleanup_pending = serializers.BooleanField(
        help_text="Local revocation is recorded, but upstream or cache cleanup still needs retry."
    )


class MCPDisconnectResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPDisconnectResultSerializer()


class MCPOwnConnectionListQuerySerializer(serializers.Serializer):
    server_id = serializers.CharField(
        max_length=64,
        required=False,
        help_text="Only this MCP server's row.",
    )


class MCPOwnConnectionStartSerializer(serializers.Serializer):
    state = serializers.ChoiceField(
        choices=["ready", "needs_admin", "unknown", "unavailable"],
        help_text="ready: you can start a sign-in now. needs_admin: an Owner or "
        "Admin must register the organization's own OAuth app for this server "
        "(org_oauth_app_required), or replace an unreadable one, first. "
        "unknown: the organization stored no OAuth app for this server, and "
        "whether the server accepts a sign-in without one is known only once a "
        "sign-in starts. unavailable: no sign-in can start here.",
    )
    reason = serializers.ChoiceField(
        choices=[
            "org_oauth_app",
            "org_oauth_app_required",
            "oauth_client_unreadable",
            "client_not_confirmed",
            "mcp_off",
            "per_user_channel_off",
            "server_changed",
            "sign_in_not_served",
            "server_disabled",
            "cleanup_pending",
        ],
        help_text="Why the state is what it is.",
    )


class MCPOwnConnectionSerializer(serializers.Serializer):
    server_id = serializers.CharField()
    connected = serializers.BooleanField(
        help_text="Whether the gateway uses your account on this server for your calls."
    )
    status = serializers.ChoiceField(
        choices=["active", "expired", "needs_reauth", "revoked"],
        allow_null=True,
        help_text="Your connection's state; null when you never connected one.",
    )
    cleanup_pending = serializers.BooleanField(
        help_text="Retry Disconnect to finish upstream revocation or gateway cache eviction."
    )
    upstream_revocation = MCPRevocationField()
    connected_at = serializers.DateTimeField(allow_null=True)
    rotated_at = serializers.DateTimeField(allow_null=True)
    start = MCPOwnConnectionStartSerializer()
    start_url = serializers.CharField(
        allow_null=True,
        help_text="Where the console POSTs to start your sign-in; only when "
        "start.state is ready.",
    )
    disconnect_url = serializers.CharField(
        allow_null=True,
        help_text="Where you DELETE to disconnect your own account; whenever "
        "you hold one that is not revoked or has pending cleanup.",
    )


class MCPOwnConnectionListResultSerializer(serializers.Serializer):
    connections = MCPOwnConnectionSerializer(many=True)


class MCPOwnConnectionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPOwnConnectionListResultSerializer()


class MCPPersonalKeyRequestSerializer(serializers.Serializer):
    personal = serializers.BooleanField(
        help_text="Whether the key acts as you on MCP servers that connect each "
        "member's own account."
    )


class MCPPersonalKeyResultSerializer(serializers.Serializer):
    key_id = serializers.CharField()
    personal = serializers.BooleanField()


class MCPPersonalKeyResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPPersonalKeyResultSerializer()
