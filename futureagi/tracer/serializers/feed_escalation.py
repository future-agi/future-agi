"""Explicit request/response contracts for workspace escalation settings."""

from decimal import Decimal

from rest_framework import serializers

from tracer.services.feed_escalation_policy import POLICY_FIELDS


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        return super().to_internal_value(data)


class FeedEscalationValuesSerializer(serializers.Serializer):
    min_findings = StrictIntegerField(min_value=2, max_value=1_000_000)
    entry_multiplier = serializers.DecimalField(
        max_digits=3,
        decimal_places=1,
        min_value=Decimal("1.1"),
        max_value=Decimal("20"),
    )
    entry_delta = StrictIntegerField(min_value=1, max_value=1_000_000)
    entry_confirmation_minutes = StrictIntegerField(min_value=5, max_value=60)
    recovery_min = StrictIntegerField(min_value=0)
    recovery_multiplier = serializers.DecimalField(
        max_digits=3, decimal_places=1, min_value=1, max_value=20
    )
    recovery_delta = StrictIntegerField(min_value=0)
    recovery_confirmation_minutes = StrictIntegerField(min_value=5, max_value=60)


class FeedEscalationSettingsBodySerializer(FeedEscalationValuesSerializer):
    revision = StrictIntegerField(min_value=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in POLICY_FIELDS:
            self.fields[name].required = False

    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                dict.fromkeys(sorted(unknown), "Unknown setting")
            )
        return super().to_internal_value(data)


class FeedEscalationSettingsSerializer(serializers.Serializer):
    values = FeedEscalationValuesSerializer()
    defaults = FeedEscalationValuesSerializer()
    ranges = serializers.DictField(
        child=serializers.DictField(child=serializers.FloatField())
    )
    revision = serializers.IntegerField()
    algorithm_version = serializers.CharField()
    defaults_version = serializers.IntegerField()
    workspace_id = serializers.UUIDField()
    workspace_name = serializers.CharField()
    can_edit = serializers.BooleanField()
    window_minutes = serializers.IntegerField()
    baseline_hours = serializers.IntegerField()
    cadence_minutes = serializers.IntegerField()
    processing_allowance_minutes = serializers.IntegerField()


class FeedEscalationSettingsApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = FeedEscalationSettingsSerializer()
