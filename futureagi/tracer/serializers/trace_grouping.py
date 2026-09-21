"""Private grouping worker boundary; source evidence remains server-owned."""

from rest_framework import serializers

from tracer.serializers.filters import StrictInputSerializer


class ClaimGroupingRequestSerializer(StrictInputSerializer):
    worker_id = serializers.CharField(max_length=255)
    limit = serializers.IntegerField(min_value=1, max_value=10)


class GroupingLeaseSerializer(StrictInputSerializer):
    lease_token = serializers.CharField(max_length=255, trim_whitespace=False)


class PublishSeveritySerializer(GroupingLeaseSerializer):
    receipt_id = serializers.UUIDField()


class RenewGroupingFeatureSerializer(GroupingLeaseSerializer):
    action = serializers.ChoiceField(choices=("renew",))


class UpdateGroupingAttemptSerializer(GroupingLeaseSerializer):
    action = serializers.ChoiceField(choices=("renew", "cancel"))


class CompleteGroupingFeatureSerializer(GroupingLeaseSerializer):
    status = serializers.ChoiceField(choices=("ready", "failed"))
    # Feature service validates exact view/vector/bucket keys and source digests.
    features = serializers.ListField(child=serializers.JSONField(), max_length=200)
    error_code = serializers.CharField(
        max_length=100, required=False, default="", allow_blank=True
    )


class GroupingCheckpointSerializer(GroupingLeaseSerializer):
    expected_revision = serializers.IntegerField(min_value=0)
    checkpoint = serializers.JSONField()


class GroupingRepairIntentSerializer(StrictInputSerializer):
    primary_receipt_id = serializers.RegexField(
        r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
    )
    group_index = serializers.IntegerField(min_value=0, max_value=99)
    missing_own_report_ids = serializers.ListField(
        child=serializers.RegexField(
            r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
        ),
        min_length=1,
        max_length=100,
    )


class ReserveGroupingCallSerializer(GroupingLeaseSerializer):
    request_key = serializers.CharField(max_length=255, trim_whitespace=False)
    request_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    max_cost_usd = serializers.DecimalField(
        max_digits=20, decimal_places=9, min_value=0
    )
    repair_intent = GroupingRepairIntentSerializer(
        required=False, allow_null=True, default=None
    )


class SettleGroupingCallSerializer(GroupingLeaseSerializer):
    request_key = serializers.CharField(max_length=255, trim_whitespace=False)
    request_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    status = serializers.ChoiceField(choices=("settled", "unknown"))
    result = serializers.JSONField(required=False, allow_null=True, default=None)
    model_used = serializers.CharField(
        max_length=255, required=False, allow_null=True, default=None
    )
    input_tokens = serializers.IntegerField(
        min_value=0, required=False, allow_null=True, default=None
    )
    output_tokens = serializers.IntegerField(
        min_value=0, required=False, allow_null=True, default=None
    )
    cost_usd = serializers.DecimalField(
        max_digits=20,
        decimal_places=9,
        min_value=0,
        required=False,
        allow_null=True,
        default=None,
    )
    failure_code = serializers.CharField(
        max_length=100, required=False, default="", allow_blank=True
    )


class PublishGroupingSerializer(GroupingLeaseSerializer):
    idempotency_key = serializers.CharField(max_length=255, trim_whitespace=False)
    snapshot_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    registry_revision = serializers.IntegerField(min_value=0)
    # Discriminated commands receive exact-key, member and citation validation
    # in the transaction-owning publisher, including temporary issue references.
    commands = serializers.ListField(child=serializers.JSONField(), max_length=1000)
    receipt_ids = serializers.ListField(child=serializers.UUIDField(), max_length=100)


class GroupingClaimsResponseSerializer(serializers.Serializer):
    claims = serializers.ListField(child=serializers.JSONField())


class GroupingControlResponseSerializer(serializers.Serializer):
    """Variable typed operation result; operation-specific service owns shape."""

    state = serializers.CharField(required=False)
    status = serializers.CharField(required=False)
    checkpoint_revision = serializers.IntegerField(required=False)
    receipt_id = serializers.UUIDField(required=False)


class GroupingErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()


class GroupingOutboxRequestSerializer(StrictInputSerializer):
    limit = serializers.IntegerField(min_value=1, max_value=100)


class GroupingOutboxAckSerializer(StrictInputSerializer):
    pass


class GroupingOutboxResponseSerializer(serializers.Serializer):
    events = serializers.ListField(child=serializers.JSONField())
