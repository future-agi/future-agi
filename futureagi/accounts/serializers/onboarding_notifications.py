from rest_framework import serializers

from accounts.models import (
    NotificationChannel,
    NotificationDeliveryLog,
)
from accounts.services.onboarding.notification_registry import (
    NOTIFICATION_CHANNELS,
    NOTIFICATION_FAMILIES,
)


class NotificationFamilySerializer(serializers.Serializer):
    id = serializers.ChoiceField(choices=tuple(NOTIFICATION_FAMILIES.keys()))
    label = serializers.CharField()
    description = serializers.CharField()
    default_channels = serializers.ListField(
        child=serializers.ChoiceField(choices=NOTIFICATION_CHANNELS)
    )
    non_critical = serializers.BooleanField()
    user_controllable = serializers.BooleanField()
    workspace_controllable = serializers.BooleanField()


class NotificationPreferenceSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    scope = serializers.ChoiceField(
        choices=("organization", "workspace", "user", "user_workspace")
    )
    family = serializers.ChoiceField(choices=tuple(NOTIFICATION_FAMILIES.keys()))
    channel = serializers.ChoiceField(choices=NOTIFICATION_CHANNELS)
    enabled = serializers.BooleanField()
    mute_until = serializers.DateTimeField(required=False, allow_null=True)
    frequency_cap_minutes = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
    )
    settings = serializers.JSONField(required=False)
    source = serializers.CharField(required=False)


class NotificationDecisionSerializer(serializers.Serializer):
    allowed = serializers.BooleanField()
    family = serializers.ChoiceField(choices=tuple(NOTIFICATION_FAMILIES.keys()))
    channel = serializers.ChoiceField(choices=NOTIFICATION_CHANNELS)
    reason = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    source = serializers.CharField()
    preference_id = serializers.UUIDField(required=False, allow_null=True)


class NotificationChannelSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    scope = serializers.ChoiceField(
        choices=("organization", "workspace"),
        required=False,
    )
    type = serializers.ChoiceField(
        choices=(
            NotificationChannel.TYPE_EMAIL_LIST,
            NotificationChannel.TYPE_SLACK_WEBHOOK,
            NotificationChannel.TYPE_WEBHOOK,
        )
    )
    display_name = serializers.CharField()
    target_identifier = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    config = serializers.JSONField(required=False)
    is_active = serializers.BooleanField(default=True)
    last_tested_at = serializers.DateTimeField(required=False, allow_null=True)
    last_test_status = serializers.ChoiceField(
        choices=(
            NotificationChannel.STATUS_UNTESTED,
            NotificationChannel.STATUS_READY,
            NotificationChannel.STATUS_FAILED,
        ),
        required=False,
    )
    metadata = serializers.JSONField(required=False)


class NotificationDeliveryLogSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    family = serializers.ChoiceField(choices=tuple(NOTIFICATION_FAMILIES.keys()))
    source_type = serializers.CharField()
    source_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    channel = serializers.ChoiceField(choices=NOTIFICATION_CHANNELS)
    recipient_type = serializers.CharField(required=False, allow_blank=True)
    recipient_identifier_masked = serializers.CharField(
        required=False,
        allow_blank=True,
    )
    notification_key = serializers.CharField(required=False, allow_blank=True)
    stage = serializers.CharField(required=False, allow_blank=True)
    severity = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(
        choices=(
            NotificationDeliveryLog.STATUS_ELIGIBLE,
            NotificationDeliveryLog.STATUS_SUPPRESSED,
            NotificationDeliveryLog.STATUS_SENT,
            NotificationDeliveryLog.STATUS_FAILED,
            NotificationDeliveryLog.STATUS_CLICKED,
            NotificationDeliveryLog.STATUS_COMPLETED,
        )
    )
    suppressed_reason = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    route_url = serializers.CharField(required=False, allow_blank=True)
    sent_at = serializers.DateTimeField(required=False, allow_null=True)
    created_at = serializers.DateTimeField(required=False)
    metadata = serializers.JSONField(required=False)


class NotificationSettingsResultSerializer(serializers.Serializer):
    families = NotificationFamilySerializer(many=True)
    channels = NotificationChannelSerializer(many=True)
    preferences = NotificationPreferenceSerializer(many=True)
    decisions = NotificationDecisionSerializer(many=True)
    delivery_logs = NotificationDeliveryLogSerializer(many=True)
    can_manage_workspace = serializers.BooleanField()


class NotificationSettingsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = NotificationSettingsResultSerializer()


class NotificationPreferencePatchItemSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(
        choices=("organization", "workspace", "user", "user_workspace"),
        default="user",
    )
    family = serializers.ChoiceField(choices=tuple(NOTIFICATION_FAMILIES.keys()))
    channel = serializers.ChoiceField(choices=NOTIFICATION_CHANNELS)
    enabled = serializers.BooleanField()
    mute_until = serializers.DateTimeField(required=False, allow_null=True)
    frequency_cap_minutes = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
    )
    settings = serializers.JSONField(required=False)


class NotificationChannelPatchItemSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    scope = serializers.ChoiceField(
        choices=("organization", "workspace"),
        default="workspace",
    )
    type = serializers.ChoiceField(
        choices=(
            NotificationChannel.TYPE_EMAIL_LIST,
            NotificationChannel.TYPE_SLACK_WEBHOOK,
            NotificationChannel.TYPE_WEBHOOK,
        )
    )
    display_name = serializers.CharField(max_length=120)
    config = serializers.JSONField(required=False)
    is_active = serializers.BooleanField(default=True)
    metadata = serializers.JSONField(required=False)


class NotificationSettingsPatchRequestSerializer(serializers.Serializer):
    preferences = NotificationPreferencePatchItemSerializer(
        many=True,
        required=False,
    )
    channels = NotificationChannelPatchItemSerializer(many=True, required=False)


class NotificationChannelTestResultSerializer(serializers.Serializer):
    channel = NotificationChannelSerializer()


class NotificationChannelTestResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = NotificationChannelTestResultSerializer()
