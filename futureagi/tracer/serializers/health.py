from rest_framework import serializers


class ClickHouseHealthResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=("healthy", "degraded", "unhealthy", "disabled")
    )
    clickhouse_connected = serializers.BooleanField()
    cdc_lag = serializers.DictField(child=serializers.FloatField())
    routing = serializers.DictField(child=serializers.JSONField())
    error = serializers.CharField(required=False)
