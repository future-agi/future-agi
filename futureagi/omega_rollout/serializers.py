"""Request and response contracts for the rollout APIs (staff admin and Settings -> Early access)."""

from rest_framework import serializers

from accounts.serializers.contracts import AccountsErrorResponseSerializer
from omega_rollout import products as p

PRODUCT_FIELD = {"choices": list(p.HOSTED_PRODUCTS)}
ERROR_RESPONSES = dict.fromkeys(
    (400, 401, 403, 404, 409, 503), AccountsErrorResponseSerializer
)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)


class ProductChangeSerializer(ReasonSerializer):
    stage = serializers.ChoiceField(choices=list(p.STAGES), required=False)
    percentage = serializers.IntegerField(min_value=0, max_value=100, required=False)

    def validate(self, attrs):
        if "stage" not in attrs and "percentage" not in attrs:
            raise serializers.ValidationError(
                "Change the stage, the percentage, or both."
            )
        return attrs


class KillSerializer(ReasonSerializer):
    pause_installs = serializers.BooleanField(required=False, default=False)


class AccessChangeSerializer(ReasonSerializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(required=False, allow_null=True)
    effect = serializers.ChoiceField(choices=[p.EFFECT_ALLOW, p.EFFECT_DENY])
    cohort = serializers.ChoiceField(
        choices=list(p.COHORTS), required=False, default=p.COHORT_PARTNER
    )


class AccessRemoveSerializer(ReasonSerializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(required=False, allow_null=True)


class WaitlistDecisionSerializer(ReasonSerializer):
    invite = serializers.BooleanField()


class ProductStateSerializer(serializers.Serializer):
    product = serializers.ChoiceField(**PRODUCT_FIELD)
    label = serializers.CharField()
    stage = serializers.ChoiceField(choices=list(p.STAGES))
    percentage = serializers.IntegerField()
    paused = serializers.BooleanField()
    installs_paused = serializers.BooleanField()
    epoch = serializers.IntegerField()
    allowed_orgs = serializers.IntegerField()
    denied_entries = serializers.IntegerField()
    open_requests = serializers.IntegerField()


class ProductListSerializer(serializers.Serializer):
    epoch = serializers.IntegerField()
    products = ProductStateSerializer(many=True)


class AccessEntrySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    organization_id = serializers.UUIDField()
    organization_name = serializers.CharField(allow_blank=True)
    workspace_id = serializers.UUIDField(allow_null=True)
    effect = serializers.CharField()
    cohort = serializers.CharField()
    reason = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()


class AccessListSerializer(serializers.Serializer):
    product = serializers.ChoiceField(**PRODUCT_FIELD)
    entries = AccessEntrySerializer(many=True)


class WaitlistEntrySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    product = serializers.ChoiceField(**PRODUCT_FIELD)
    organization_id = serializers.UUIDField()
    organization_name = serializers.CharField(allow_blank=True)
    requested_by = serializers.CharField(allow_blank=True)
    note = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    decided_at = serializers.DateTimeField(allow_null=True)


class WaitlistListSerializer(serializers.Serializer):
    requests = WaitlistEntrySerializer(many=True)


class AuditEventSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    action = serializers.CharField()
    product = serializers.CharField()
    actor = serializers.CharField(allow_blank=True)
    organization_id = serializers.UUIDField(allow_null=True)
    workspace_id = serializers.UUIDField(allow_null=True)
    before = serializers.DictField()
    after = serializers.DictField()
    reason = serializers.CharField(allow_blank=True)
    counts = serializers.DictField()
    epoch = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class AuditListSerializer(serializers.Serializer):
    events = AuditEventSerializer(many=True)


class AdminQuerySerializer(serializers.Serializer):
    product = serializers.ChoiceField(required=False, **PRODUCT_FIELD)
    status = serializers.ChoiceField(
        choices=["requested", "invited", "declined"], required=False
    )


class EarlyAccessRequestSerializer(serializers.Serializer):
    product = serializers.ChoiceField(**PRODUCT_FIELD)
    note = serializers.CharField(
        max_length=1000, required=False, allow_blank=True, default=""
    )


class EarlyAccessProductSerializer(serializers.Serializer):
    product = serializers.ChoiceField(**PRODUCT_FIELD)
    label = serializers.CharField()
    admitted = serializers.BooleanField()
    state = serializers.CharField(allow_null=True)
    request_status = serializers.CharField(allow_null=True)
    requested_at = serializers.DateTimeField(allow_null=True)


class EarlyAccessSerializer(serializers.Serializer):
    can_request = serializers.BooleanField()
    products = EarlyAccessProductSerializer(many=True)
