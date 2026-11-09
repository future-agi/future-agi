"""Request and response contracts of the MCP credential vault and the
connection probe (M14). A secret is write-only: it appears in requests and
never in a response."""

from rest_framework import serializers

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.services.mcp_credentials import MAX_SECRET_CHARS, PLACEMENTS

_STATIC_KINDS = [AgentccMCPCredential.Kind.BEARER, AgentccMCPCredential.Kind.API_KEY]


def _secret_field():
    # Not trimmed: a value with surrounding spaces is refused, never altered.
    return serializers.CharField(
        write_only=True, trim_whitespace=False, max_length=MAX_SECRET_CHARS
    )


def _placement_fields(fields):
    """``in`` and ``name``: where an API key goes (AM-C9; CN11 R4). ``in`` is a
    Python keyword, so it cannot be a class attribute."""
    fields["in"] = serializers.ChoiceField(
        choices=list(PLACEMENTS),
        required=False,
        help_text="Where the key goes: header (the default), query or url_variable.",
    )
    fields["name"] = serializers.CharField(
        max_length=128,
        required=False,
        trim_whitespace=False,
        help_text="The header, query parameter or URL variable the key goes in.",
    )
    return fields


class MCPCredentialCreateRequestSerializer(serializers.Serializer):
    server_id = serializers.CharField(max_length=64)
    kind = serializers.ChoiceField(choices=_STATIC_KINDS)
    value = _secret_field()
    header_name = serializers.CharField(
        max_length=128, required=False, allow_blank=True, default=""
    )

    def get_fields(self):
        return _placement_fields(super().get_fields())

    label = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    # Per-user credentials come from each user's own connect flow.
    scope = serializers.ChoiceField(
        choices=[AgentccMCPCredential.Scope.SHARED],
        required=False,
        default=AgentccMCPCredential.Scope.SHARED,
    )


class MCPCredentialRotateRequestSerializer(serializers.Serializer):
    value = _secret_field()
    header_name = serializers.CharField(max_length=128, required=False)

    def get_fields(self):
        return _placement_fields(super().get_fields())


class MCPCredentialListQuerySerializer(serializers.Serializer):
    server_id = serializers.CharField(max_length=64, required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=200, required=False)


class MCPCredentialSerializer(serializers.Serializer):
    """``describe(credential)``: everything but the value."""

    id = serializers.UUIDField()
    server_id = serializers.CharField()
    scope = serializers.CharField()
    kind = serializers.CharField()
    header_name = serializers.CharField(allow_blank=True)
    name = serializers.CharField(
        allow_blank=True,
        help_text="The header, query parameter or URL variable the key goes in.",
    )
    status = serializers.CharField()
    version = serializers.IntegerField()
    label = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    rotated_at = serializers.DateTimeField(allow_null=True)
    last_used_at = serializers.DateTimeField(allow_null=True)
    has_value = serializers.BooleanField()

    def get_fields(self):
        fields = super().get_fields()
        fields["in"] = serializers.CharField(
            help_text="Where the key goes: header, query or url_variable."
        )
        return fields


class MCPCredentialWriteResultSerializer(MCPCredentialSerializer):
    gateway_synced = serializers.BooleanField(allow_null=True)


class MCPCredentialResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPCredentialWriteResultSerializer()


class MCPCredentialListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    results = MCPCredentialSerializer(many=True)


class MCPUseManagedResultSerializer(serializers.Serializer):
    server_id = serializers.CharField()
    version = serializers.IntegerField()
    revoked = serializers.ListField(child=serializers.UUIDField())
    gateway_synced = serializers.BooleanField()


class MCPUseManagedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPUseManagedResultSerializer()


class MCPProbeAuthSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=_STATIC_KINDS)
    header = serializers.CharField(max_length=128, required=False)
    value = _secret_field()


class MCPProbeRequestSerializer(serializers.Serializer):
    """``POST /agentcc/mcp/probe/``: the org comes from the session, never the
    body. A credential is named by ``credential_id`` (resolved from the vault)
    or typed as ``auth`` (used for this probe only), not both."""

    url = serializers.CharField(max_length=2048)
    transport = serializers.ChoiceField(choices=["http", "sse"], required=False)
    credential_id = serializers.UUIDField(required=False)
    auth = MCPProbeAuthSerializer(required=False)

    def validate(self, attrs):
        if "credential_id" in attrs and "auth" in attrs:
            raise serializers.ValidationError("Send credential_id or auth, not both.")
        return attrs


class MCPProbeServerInfoSerializer(serializers.Serializer):
    name = serializers.CharField()
    title = serializers.CharField(required=False)
    version = serializers.CharField(required=False)


class MCPProbeResultSerializer(serializers.Serializer):
    """mcp-probe-v1's result (MCPProbeResult)."""

    ok = serializers.BooleanField()
    era = serializers.CharField(required=False)
    server_info = MCPProbeServerInfoSerializer(required=False)
    tool_count = serializers.IntegerField(min_value=0, required=False)
    tools_hash = serializers.CharField(required=False)
    latency_ms = serializers.IntegerField(min_value=0)
    error = serializers.CharField(allow_null=True)


class MCPProbeResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPProbeResultSerializer()
