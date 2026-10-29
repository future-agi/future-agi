from rest_framework import serializers


class StoredProviderRefSerializer(serializers.Serializer):
    provider = serializers.CharField()
    id = serializers.CharField()
    version = serializers.CharField(allow_null=True)


class StoredBehaviourUnitSerializer(serializers.Serializer):
    id = serializers.CharField()
    shape = serializers.CharField()
    instructions = serializers.CharField(allow_null=True)
    tools = serializers.ListField(child=serializers.CharField())
    transitions = serializers.ListField(child=serializers.CharField())


class StoredToolEndpointSerializer(serializers.Serializer):
    url = serializers.CharField()


class StoredToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    kind = serializers.CharField()
    parameters = serializers.JSONField(allow_null=True)
    endpoint = StoredToolEndpointSerializer(allow_null=True)


class StoredModelSerializer(serializers.Serializer):
    provider = serializers.CharField(allow_null=True)
    name = serializers.CharField()
    temperature = serializers.FloatField(allow_null=True)
    max_tokens = serializers.IntegerField(allow_null=True)


class StoredVoiceSerializer(serializers.Serializer):
    provider = serializers.CharField(allow_null=True)
    voice_id = serializers.CharField()
    language = serializers.CharField(allow_null=True)
    speed = serializers.FloatField(allow_null=True)


class StoredTelephonySerializer(serializers.Serializer):
    number = serializers.CharField()
    provider = serializers.CharField(allow_null=True)
    direction_supported = serializers.ListField(child=serializers.CharField())


class StoredAgentSerializer(serializers.Serializer):
    schema_version = serializers.CharField()
    type = serializers.CharField()
    kind = serializers.CharField()
    name = serializers.CharField()
    provider_ref = StoredProviderRefSerializer(allow_null=True)
    behaviour = StoredBehaviourUnitSerializer(many=True)
    tools = StoredToolSerializer(many=True)
    model = StoredModelSerializer(allow_null=True)
    voice = StoredVoiceSerializer(allow_null=True)
    telephony = StoredTelephonySerializer(many=True)
    supports = serializers.ListField(child=serializers.CharField())
    direction_answer = serializers.CharField(allow_null=True)
    gaps = serializers.ListField(child=serializers.CharField())
    instrumentation = serializers.CharField()
    setup_hash = serializers.CharField()


class StoredAgentResponseSerializer(serializers.Serializer):
    agent = StoredAgentSerializer()
    sdk_version = serializers.CharField()
    stored_at = serializers.DateTimeField()
    setup_stale = serializers.BooleanField()
