"""Measured usage reported by the authenticated ALK control process."""

from rest_framework import serializers

from simulate.serializers.hosted_harness import _reject_non_finite


class HarnessUsageRecordSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    action = serializers.ChoiceField(
        choices=("scenario_generation", "text_call", "voice_call", "managed_evaluation")
    )
    scenario_key = serializers.CharField(max_length=255)
    amount = serializers.FloatField(min_value=0, validators=[_reject_non_finite])
    occurred_at = serializers.DateTimeField()
    funding = serializers.ChoiceField(choices=("platform", "customer"))
    infra_failed = serializers.BooleanField()
    model = serializers.CharField(max_length=255, required=False, allow_null=True)

    def validate(self, attrs):
        amount = attrs["amount"]
        if (
            attrs["action"] in {"scenario_generation", "managed_evaluation"}
            and amount != 1
        ):
            raise serializers.ValidationError(
                "Each completed action reports exactly one unit."
            )
        if attrs["action"] == "scenario_generation" and (
            attrs["funding"] != "platform" or attrs["infra_failed"]
        ):
            raise serializers.ValidationError(
                "Generated rows are platform-funded successful actions."
            )
        if attrs["action"] == "managed_evaluation" and not attrs.get("model"):
            raise serializers.ValidationError(
                {"model": "The evaluator model is required."}
            )
        if attrs["action"] == "text_call" and not amount.is_integer():
            raise serializers.ValidationError("Token counts must be integers.")
        return attrs


class HarnessUsageTotalsSerializer(serializers.Serializer):
    text_sim_tokens = serializers.IntegerField(min_value=0)
    voice_sim_minutes = serializers.FloatField(
        min_value=0, validators=[_reject_non_finite]
    )


class HarnessUsageRequestSerializer(serializers.Serializer):
    operation = serializers.ChoiceField(choices=("check", "report"))
    action = serializers.ChoiceField(
        choices=(
            "scenario_generation",
            "text_call",
            "voice_call",
            "managed_evaluation",
        ),
        required=False,
    )
    amount = serializers.FloatField(
        min_value=0, validators=[_reject_non_finite], required=False
    )
    model = serializers.CharField(max_length=255, required=False)
    schema_version = serializers.ChoiceField(
        choices=("futureagi.harness-usage.v1",), required=False
    )
    records = HarnessUsageRecordSerializer(many=True, max_length=10000, required=False)
    totals = HarnessUsageTotalsSerializer(required=False)
    sandbox_seconds = serializers.FloatField(
        min_value=0, validators=[_reject_non_finite], required=False
    )

    def validate(self, attrs):
        required = (
            ("action",)
            if attrs["operation"] == "check"
            else ("schema_version", "records", "totals", "sandbox_seconds")
        )
        missing = [name for name in required if name not in attrs]
        if missing:
            raise serializers.ValidationError(
                {name: "This field is required." for name in missing}
            )
        if attrs.get("action") == "managed_evaluation" and not attrs.get("model"):
            raise serializers.ValidationError(
                {"model": "The evaluator model is required."}
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
