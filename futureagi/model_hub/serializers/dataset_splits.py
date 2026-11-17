"""Strict public contracts for live dataset splits."""

from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer

COLORS = ("info", "secondary", "error", "warning", "success", "primary", "grey", "dark")


class DatasetSplitWriteSerializer(StrictInputSerializer):
    name = serializers.RegexField(
        r"^(?!none$)[a-z0-9][a-z0-9._-]{0,29}$", max_length=30
    )
    color = serializers.ChoiceField(choices=COLORS, default="info")
    description = serializers.CharField(max_length=500, allow_blank=True, default="")


class DatasetSplitPatchSerializer(StrictInputSerializer):
    name = serializers.RegexField(
        r"^(?!none$)[a-z0-9][a-z0-9._-]{0,29}$", max_length=30, required=False
    )
    color = serializers.ChoiceField(choices=COLORS, required=False)
    description = serializers.CharField(
        max_length=500, allow_blank=True, required=False
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one field is required.")
        return attrs


class DatasetSplitMembershipSerializer(StrictInputSerializer):
    row_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=1000
    )
    add = serializers.ListField(
        child=serializers.UUIDField(), max_length=100, default=list
    )
    remove = serializers.ListField(
        child=serializers.UUIDField(), max_length=100, default=list
    )

    def validate(self, attrs):
        if set(attrs["add"]) & set(attrs["remove"]):
            raise serializers.ValidationError(
                "A split cannot be added and removed together."
            )
        if not attrs["add"] and not attrs["remove"]:
            raise serializers.ValidationError("Choose splits to add or remove.")
        return attrs


class DatasetSplitRowQuerySerializer(StrictInputSerializer):
    row_id = serializers.UUIDField()


class DatasetSplitExportQuerySerializer(StrictInputSerializer):
    split_id = serializers.UUIDField()


class DatasetSplitSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    color = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    row_count = serializers.IntegerField()


class DatasetSplitResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetSplitSerializer()


class DatasetSplitListResultSerializer(serializers.Serializer):
    splits = DatasetSplitSerializer(many=True)
    no_split_count = serializers.IntegerField()


class DatasetSplitListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetSplitListResultSerializer()


class DatasetSplitRowResultSerializer(serializers.Serializer):
    row_id = serializers.UUIDField()
    split_ids = serializers.ListField(child=serializers.UUIDField())


class DatasetSplitRowResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetSplitRowResultSerializer()


class DatasetSplitExportRowSerializer(serializers.Serializer):
    row_id = serializers.UUIDField()
    fields = serializers.DictField(
        child=serializers.CharField(allow_null=True, allow_blank=True)
    )


class DatasetSplitExportResultSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    split = DatasetSplitSerializer()
    rows = DatasetSplitExportRowSerializer(many=True)


class DatasetSplitExportResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetSplitExportResultSerializer()


class DatasetSplitDeleteResultSerializer(serializers.Serializer):
    deleted = serializers.UUIDField()


class DatasetSplitDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetSplitDeleteResultSerializer()


class DatasetSplitUpdateResultSerializer(serializers.Serializer):
    updated = serializers.IntegerField()


class DatasetSplitUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetSplitUpdateResultSerializer()
