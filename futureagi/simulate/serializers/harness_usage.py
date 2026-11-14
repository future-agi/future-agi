"""Measured call usage reported by the authenticated ALK control process."""

from rest_framework import serializers

from simulate.serializers.hosted_harness import _reject_non_finite

_FAILURE_DOMAINS = (
    "agent",
    "simulator",
    "environment",
    "connectivity",
    "infrastructure",
    "grading",
    "artifact",
    "platform_sync",
)


class HarnessUsageRecordSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=("text_call", "voice_call"))
    scenario_key = serializers.CharField(max_length=255)
    amount = serializers.FloatField(min_value=0, validators=[_reject_non_finite])
    occurred_at = serializers.DateTimeField()
    funding = serializers.ChoiceField(choices=("platform", "customer"))
    outcome = serializers.ChoiceField(
        choices=("completed", "failed"), default="completed"
    )
    failure_domain = serializers.ChoiceField(
        choices=_FAILURE_DOMAINS, required=False, allow_null=True
    )

    def validate(self, attrs):
        if attrs["action"] == "text_call" and not attrs["amount"].is_integer():
            raise serializers.ValidationError("Token counts must be integers.")
        if attrs["outcome"] == "failed" and not attrs.get("failure_domain"):
            raise serializers.ValidationError(
                {"failure_domain": "Failed records must include a failure domain."}
            )
        if attrs["outcome"] == "completed" and attrs.get("failure_domain"):
            raise serializers.ValidationError(
                {"failure_domain": "Completed records cannot include a failure domain."}
            )
        return attrs


class HarnessUsageRequestSerializer(serializers.Serializer):
    operation = serializers.ChoiceField(choices=("check", "report"))
    action = serializers.ChoiceField(
        choices=("text_call", "voice_call"), required=False
    )
    schema_version = serializers.ChoiceField(
        choices=("futureagi.harness-usage.v1",), required=False
    )
    records = HarnessUsageRecordSerializer(many=True, max_length=10000, required=False)

    def validate(self, attrs):
        required = (
            ("action",)
            if attrs["operation"] == "check"
            else ("schema_version", "records")
        )
        missing = [name for name in required if name not in attrs]
        if missing:
            raise serializers.ValidationError(
                dict.fromkeys(missing, "This field is required.")
            )
        return attrs


class HarnessUsageResponseSerializer(serializers.Serializer):
    allowed = serializers.BooleanField(required=False)
    accepted = serializers.BooleanField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True)
    error_code = serializers.CharField(required=False, allow_blank=True)
    dimension = serializers.CharField(required=False, allow_blank=True)
    current_usage = serializers.FloatField(required=False)
    limit = serializers.FloatField(required=False)
    upgrade_cta = serializers.JSONField(required=False, allow_null=True)
