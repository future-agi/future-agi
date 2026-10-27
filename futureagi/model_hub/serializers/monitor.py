from rest_framework import serializers

from model_hub.models.monitors import Monitor


class MonitorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Monitor
        exclude = ("ai_model",)
