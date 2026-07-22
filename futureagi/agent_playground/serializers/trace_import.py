from rest_framework import serializers


class TraceToGraphRequestSerializer(serializers.Serializer):
    trace_id = serializers.UUIDField()


class TraceToGraphResultSerializer(serializers.Serializer):
    graph_id = serializers.UUIDField()
    version_id = serializers.UUIDField()


class TraceToGraphResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = TraceToGraphResultSerializer()
