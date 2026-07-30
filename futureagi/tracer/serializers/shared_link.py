from rest_framework import serializers

from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tracer.models.shared_link import (
    AccessType,
    ResourceType,
    SharedLink,
    SharedLinkAccess,
)

SUPPORTED_SHARED_RESOURCE_TYPE_CHOICES = [
    (ResourceType.TRACE.value, ResourceType.TRACE.label),
    (ResourceType.DASHBOARD.value, ResourceType.DASHBOARD.label),
    (ResourceType.PROJECT.value, ResourceType.PROJECT.label),
]


class SharedLinkAccessSerializer(serializers.ModelSerializer):
    class Meta:
        model = SharedLinkAccess
        fields = ["id", "email", "user", "granted_by", "created_at"]
        read_only_fields = ["id", "user", "granted_by", "created_at"]


class SharedLinkListSerializer(serializers.ModelSerializer):
    access_count = serializers.SerializerMethodField()
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = SharedLink
        fields = [
            "id",
            "resource_type",
            "resource_id",
            "token",
            "access_type",
            "is_active",
            "expires_at",
            "created_by",
            "created_at",
            "access_count",
            "share_url",
        ]
        read_only_fields = fields

    def get_access_count(self, obj):
        return obj.access_list.filter(deleted=False).count()

    def get_share_url(self, obj):
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(f"/shared/{obj.token}")
        return f"/shared/{obj.token}"


class SharedLinkCreateSerializer(serializers.Serializer):
    resource_type = serializers.ChoiceField(
        choices=SUPPORTED_SHARED_RESOURCE_TYPE_CHOICES
    )
    resource_id = serializers.CharField(max_length=255)
    access_type = serializers.ChoiceField(
        choices=AccessType.choices, default=AccessType.RESTRICTED
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    emails = serializers.ListField(
        child=serializers.EmailField(),
        required=False,
        default=list,
        help_text="Emails to grant access to (for restricted links).",
    )


class SharedLinkUpdateSerializer(serializers.Serializer):
    access_type = serializers.ChoiceField(choices=AccessType.choices, required=False)
    is_active = serializers.BooleanField(required=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class AddAccessSerializer(serializers.Serializer):
    emails = serializers.ListField(child=serializers.EmailField(), min_length=1)


class SharedLinkDetailSerializer(serializers.ModelSerializer):
    """Full detail including ACL list."""

    access_list = SharedLinkAccessSerializer(many=True, read_only=True)
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = SharedLink
        fields = [
            "id",
            "resource_type",
            "resource_id",
            "token",
            "access_type",
            "is_active",
            "expires_at",
            "created_by",
            "created_at",
            "access_list",
            "share_url",
        ]
        read_only_fields = fields

    def get_share_url(self, obj):
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(f"/shared/{obj.token}")
        return f"/shared/{obj.token}"


class SharedLinkResolvedTraceSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    project_id = serializers.CharField()
    input = serializers.JSONField(required=False, allow_null=True)
    output = serializers.JSONField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, allow_null=True)
    tags = serializers.JSONField(required=False, allow_null=True)
    created_at = serializers.CharField(required=False, allow_null=True)


class SharedLinkResolvedSummarySerializer(serializers.Serializer):
    total_spans = serializers.IntegerField(required=False)


class SharedLinkResolvedDataSerializer(serializers.Serializer):
    trace = SharedLinkResolvedTraceSerializer(required=False)
    observation_spans = serializers.ListField(
        child=serializers.JSONField(), required=False
    )
    summary = SharedLinkResolvedSummarySerializer(required=False)
    id = serializers.CharField(required=False)
    name = serializers.CharField(required=False)
    trace_type = serializers.CharField(required=False)
    model_type = serializers.CharField(required=False)
    metadata = serializers.JSONField(required=False, allow_null=True)
    config = serializers.JSONField(required=False, allow_null=True)
    session_config = serializers.JSONField(required=False, allow_null=True)
    tags = serializers.JSONField(required=False, allow_null=True)
    organization = serializers.CharField(required=False)
    url_path = serializers.CharField(required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    workspace = serializers.CharField(required=False)
    created_by = serializers.JSONField(required=False, allow_null=True)
    updated_by = serializers.JSONField(required=False, allow_null=True)
    created_at = serializers.CharField(required=False, allow_null=True)
    updated_at = serializers.CharField(required=False, allow_null=True)
    widgets = serializers.ListField(child=serializers.JSONField(), required=False)
    widget_count = serializers.IntegerField(required=False)


class SharedLinkResolveResponseSerializer(serializers.Serializer):
    resource_type = serializers.ChoiceField(choices=ResourceType.choices)
    resource_id = serializers.CharField()
    access_type = serializers.ChoiceField(choices=AccessType.choices)
    data = SharedLinkResolvedDataSerializer()


class SharedLinkResolveErrorSerializer(ApiTextErrorResponseSerializer):
    pass
