"""Strict public contracts for copying or moving a dataset between workspaces."""

from rest_framework import serializers

from model_hub.serializers.develop_dataset_contracts import _StrictSerializer

TRANSFER_MODES = ("copy", "move")


class DatasetTransferRequestSerializer(_StrictSerializer):
    to_workspace_id = serializers.UUIDField()
    mode = serializers.ChoiceField(choices=TRANSFER_MODES)
    new_dataset_name = serializers.CharField(
        required=False, max_length=2000, trim_whitespace=True
    )
    dry_run = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs["mode"] == "move" and "new_dataset_name" in attrs:
            raise serializers.ValidationError(
                {"new_dataset_name": "A move keeps the dataset name."}
            )
        return attrs


class DatasetTransferWorkspaceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class DatasetTransferPlanSerializer(serializers.Serializer):
    rows = serializers.IntegerField()
    columns = serializers.IntegerField()
    columns_left_out = serializers.IntegerField()
    leaves_folder = serializers.BooleanField()


class DatasetTransferResultSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=TRANSFER_MODES)
    dry_run = serializers.BooleanField()
    source_dataset_id = serializers.UUIDField()
    dataset_id = serializers.UUIDField(allow_null=True)
    dataset_name = serializers.CharField()
    from_workspace = DatasetTransferWorkspaceSerializer(allow_null=True)
    to_workspace = DatasetTransferWorkspaceSerializer()
    plan = DatasetTransferPlanSerializer()


class DatasetTransferResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetTransferResultSerializer()
