from rest_framework import serializers

from tfc.utils.api_serializers import ApiTextErrorResponseSerializer


class ClickHouseHealthResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=("healthy", "degraded", "unhealthy", "disabled")
    )
    clickhouse_connected = serializers.BooleanField()
    cdc_lag = serializers.DictField(
        child=serializers.FloatField(),
        help_text="Per-table lag in seconds; -1 means unverified, including empty tables.",
    )
    cdc_lag_verified = serializers.DictField(
        child=serializers.BooleanField(),
        required=False,
        help_text=(
            "True only for complete relevant coverage on every destination with an "
            "observed sync timestamp. Incomplete coverage alone does not degrade health."
        ),
    )
    routing = serializers.DictField(child=serializers.JSONField())
    error = serializers.CharField(required=False)


class ClickHouseHealthErrorResponseSerializer(ApiTextErrorResponseSerializer):
    health_status = serializers.ChoiceField(
        choices=("healthy", "degraded", "unhealthy", "disabled"),
        required=False,
    )
    clickhouse_connected = serializers.BooleanField(required=False)
    cdc_lag = serializers.DictField(child=serializers.FloatField(), required=False)
    cdc_lag_verified = serializers.DictField(
        child=serializers.BooleanField(), required=False
    )
    routing = serializers.DictField(child=serializers.JSONField(), required=False)
