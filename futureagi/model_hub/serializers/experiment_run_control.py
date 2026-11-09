from rest_framework import serializers


class ExperimentRunSelectionSerializer(serializers.Serializer):
    run_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=False,
        max_length=1000,
        help_text="Variant (experiment dataset) IDs. Omit to select all variants.",
    )


class ExperimentRunControlItemSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    name = serializers.CharField()
    status = serializers.CharField()


class ExperimentRunControlResultSerializer(serializers.Serializer):
    experiment_id = serializers.UUIDField()
    runs = ExperimentRunControlItemSerializer(many=True)


class ExperimentRunControlResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentRunControlResultSerializer()
