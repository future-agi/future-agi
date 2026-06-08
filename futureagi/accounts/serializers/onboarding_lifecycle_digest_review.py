from rest_framework import serializers

from accounts.models import NotificationDeliveryLog, OnboardingLifecycleSendLog
from accounts.services.onboarding.lifecycle_digest_promotion import (
    DIGEST_PROMOTION_SCOPE_TYPES,
    DIGEST_PROMOTION_SOURCE_TYPES,
    MAX_DIGEST_PROMOTION_SOURCES,
)
from accounts.services.onboarding.lifecycle_digest_review import (
    DIGEST_REVIEW_CAMPAIGNS,
    MAX_DIGEST_REVIEW_LIMIT,
)


class OnboardingLifecycleDigestPreviewQuerySerializer(serializers.Serializer):
    campaign_key = serializers.ChoiceField(
        choices=tuple((key, key) for key in DIGEST_REVIEW_CAMPAIGNS),
        required=False,
    )
    limit = serializers.IntegerField(
        min_value=1,
        max_value=MAX_DIGEST_REVIEW_LIMIT,
        required=False,
        default=25,
    )


class OnboardingLifecycleDigestActionSerializer(serializers.Serializer):
    action_id = serializers.CharField()
    label = serializers.CharField()
    route = serializers.CharField()
    fallback_route = serializers.CharField()
    source_type = serializers.CharField()
    source_id = serializers.CharField(required=False, allow_blank=True)
    primary_path = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    age_minutes = serializers.IntegerField(min_value=0)
    last_event_at = serializers.DateTimeField(required=False, allow_null=True)
    assigned_to_user_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    is_overdue = serializers.BooleanField(default=False)


class OnboardingLifecycleDigestPreviewSerializer(serializers.Serializer):
    kind = serializers.CharField()
    campaign_key = serializers.CharField()
    template_key = serializers.CharField()
    generated_at = serializers.DateTimeField(required=False, allow_null=True)
    workspace_id = serializers.CharField()
    action_count = serializers.IntegerField(min_value=0)
    omitted_action_count = serializers.IntegerField(min_value=0)
    actions = OnboardingLifecycleDigestActionSerializer(many=True)


class OnboardingLifecycleDigestSummarySerializer(serializers.Serializer):
    action_count = serializers.IntegerField(min_value=0)
    visible_action_count = serializers.IntegerField(min_value=0)
    omitted_action_count = serializers.IntegerField(min_value=0)
    overdue_count = serializers.IntegerField(min_value=0)
    assigned_count = serializers.IntegerField(min_value=0)


class OnboardingLifecycleDigestDeliveryLogSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    channel = serializers.CharField()
    status = serializers.ChoiceField(choices=NotificationDeliveryLog.STATUS_CHOICES)
    suppressed_reason = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    sent_at = serializers.DateTimeField(required=False, allow_null=True)
    created_at = serializers.DateTimeField()


class OnboardingLifecycleDigestReviewItemSerializer(serializers.Serializer):
    source_type = serializers.ChoiceField(choices=("evaluation_log", "send_log"))
    source_id = serializers.UUIDField()
    campaign_key = serializers.CharField()
    campaign_group = serializers.CharField(required=False, allow_null=True)
    template_key = serializers.CharField(required=False, allow_null=True)
    template_version = serializers.CharField(required=False, allow_null=True)
    status = serializers.CharField()
    suppression_reason = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    user_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(required=False, allow_null=True)
    evaluated_at = serializers.DateTimeField(required=False, allow_null=True)
    queued_at = serializers.DateTimeField(required=False, allow_null=True)
    sent_at = serializers.DateTimeField(required=False, allow_null=True)
    created_at = serializers.DateTimeField()
    preview = OnboardingLifecycleDigestPreviewSerializer()
    summary = OnboardingLifecycleDigestSummarySerializer()
    delivery_logs = OnboardingLifecycleDigestDeliveryLogSerializer(many=True)

    def validate(self, attrs):
        if (
            attrs["source_type"] == "send_log"
            and attrs["status"] == OnboardingLifecycleSendLog.STATUS_SENT
            and not attrs.get("sent_at")
        ):
            raise serializers.ValidationError("Sent digest review rows need sent_at.")
        return attrs


class OnboardingLifecycleDigestReviewResultSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    limit = serializers.IntegerField(min_value=1, max_value=MAX_DIGEST_REVIEW_LIMIT)
    campaign_key = serializers.CharField(required=False, allow_blank=True)
    count = serializers.IntegerField(min_value=0)
    items = OnboardingLifecycleDigestReviewItemSerializer(many=True)


class OnboardingLifecycleDigestReviewResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OnboardingLifecycleDigestReviewResultSerializer()


class OnboardingLifecycleDigestPromotionSourceSerializer(serializers.Serializer):
    source_type = serializers.ChoiceField(
        choices=tuple((source, source) for source in DIGEST_PROMOTION_SOURCE_TYPES),
    )
    source_id = serializers.UUIDField()


class OnboardingLifecycleDigestPromotionRequestSerializer(serializers.Serializer):
    sources = OnboardingLifecycleDigestPromotionSourceSerializer(many=True)
    scope_type = serializers.ChoiceField(
        choices=tuple((scope, scope) for scope in DIGEST_PROMOTION_SCOPE_TYPES),
        required=False,
        default="user",
    )
    dry_run = serializers.BooleanField(required=False, default=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=180)

    def validate_sources(self, value):
        if not value:
            raise serializers.ValidationError("At least one source is required.")
        if len(value) > MAX_DIGEST_PROMOTION_SOURCES:
            raise serializers.ValidationError(
                f"At most {MAX_DIGEST_PROMOTION_SOURCES} sources are allowed."
            )
        return value


class OnboardingLifecycleDigestPromotionEntrySerializer(serializers.Serializer):
    source_type = serializers.ChoiceField(
        choices=tuple((source, source) for source in DIGEST_PROMOTION_SOURCE_TYPES),
    )
    source_id = serializers.UUIDField()
    allowlist_id = serializers.UUIDField(required=False, allow_null=True)
    operation = serializers.ChoiceField(
        choices=("created", "updated", "would_create", "would_update"),
    )
    scope_type = serializers.ChoiceField(
        choices=tuple((scope, scope) for scope in DIGEST_PROMOTION_SCOPE_TYPES),
    )
    scope_value = serializers.CharField()
    campaign_group = serializers.CharField(required=False, allow_null=True)
    user_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField()


class OnboardingLifecycleDigestPromotionSkippedSerializer(serializers.Serializer):
    source_type = serializers.ChoiceField(
        choices=tuple((source, source) for source in DIGEST_PROMOTION_SOURCE_TYPES),
    )
    source_id = serializers.UUIDField()
    reason = serializers.ChoiceField(
        choices=(
            "duplicate_source",
            "duplicate_target",
            "missing_digest_preview",
            "not_found",
            "unsupported_campaign",
        ),
    )


class OnboardingLifecycleDigestPromotionResultSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    environment = serializers.CharField()
    campaign_key = serializers.CharField()
    scope_type = serializers.ChoiceField(
        choices=tuple((scope, scope) for scope in DIGEST_PROMOTION_SCOPE_TYPES),
    )
    dry_run = serializers.BooleanField()
    promoted_count = serializers.IntegerField(min_value=0)
    skipped_count = serializers.IntegerField(min_value=0)
    created_count = serializers.IntegerField(min_value=0)
    updated_count = serializers.IntegerField(min_value=0)
    entries = OnboardingLifecycleDigestPromotionEntrySerializer(many=True)
    skipped = OnboardingLifecycleDigestPromotionSkippedSerializer(many=True)


class OnboardingLifecycleDigestPromotionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OnboardingLifecycleDigestPromotionResultSerializer()
