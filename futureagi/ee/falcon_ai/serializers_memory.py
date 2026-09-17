from rest_framework import serializers

from ee.falcon_ai.models import FalconMemory
from tracer.serializers.filters import StrictInputSerializer


class FalconMemorySerializer(serializers.ModelSerializer):
    class Meta:
        model = FalconMemory
        fields = ["id", "key", "value", "source", "created_at"]


class FalconMemoryCreateSerializer(StrictInputSerializer):
    key = serializers.CharField(max_length=200)
    value = serializers.CharField()
