from rest_framework import serializers


class LangfuseIngestionEventSerializer(serializers.Serializer):
    id = serializers.CharField(required=False, allow_blank=True)
    type = serializers.CharField()
    body = serializers.JSONField(required=False, allow_null=True)
    timestamp = serializers.CharField(required=False, allow_blank=True)


class LangfuseIngestionRequestSerializer(serializers.Serializer):
    batch = LangfuseIngestionEventSerializer(many=True)


class LangfuseIngestionSuccessSerializer(serializers.Serializer):
    id = serializers.CharField()
    status = serializers.IntegerField()


class LangfuseIngestionErrorSerializer(serializers.Serializer):
    id = serializers.CharField()
    status = serializers.IntegerField()
    message = serializers.CharField()


class LangfuseIngestionResponseSerializer(serializers.Serializer):
    successes = LangfuseIngestionSuccessSerializer(many=True)
    errors = LangfuseIngestionErrorSerializer(many=True)
