"""Public declared-schema request and response contracts."""

from rest_framework import serializers

from model_hub.services.dataset_schema import validate_definition
from tfc.utils.api_serializers import StrictInputSerializer


class DatasetSchemaWriteSerializer(StrictInputSerializer):
    declared = serializers.JSONField()
    enforce = serializers.ChoiceField(choices=("off", "check", "reject"))
    preset = serializers.CharField(max_length=100, allow_blank=True, default="")

    def validate_declared(self, value):
        return validate_definition(value)


class DatasetSchemaResultSerializer(serializers.Serializer):
    declared = serializers.JSONField(help_text="Empty until a row schema is declared.")
    enforce = serializers.ChoiceField(choices=("off", "check", "reject"))
    preset = serializers.CharField(allow_blank=True)


class DatasetSchemaResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetSchemaResultSerializer()
