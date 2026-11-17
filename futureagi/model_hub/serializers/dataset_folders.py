"""Strict public contracts for dataset folders."""

from rest_framework import serializers

from model_hub.serializers.develop_dataset_contracts import _StrictSerializer


class FolderPathSerializer(_StrictSerializer):
    path = serializers.CharField(max_length=500, trim_whitespace=False)

    def validate_path(self, path):
        parts = path.split("/")
        if len(parts) > 10 or any(
            not part
            or part != part.strip()
            or part in (".", "..")
            or len(part) > 80
            or "\\" in part
            or any(ord(c) < 32 or ord(c) == 127 for c in part)
            for part in parts
        ):
            raise serializers.ValidationError(
                "Use up to 10 nonempty folder names, at most 80 characters each."
            )
        return path


class FolderMoveDatasetsSerializer(_StrictSerializer):
    dataset_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=100
    )
    folder_id = serializers.UUIDField(allow_null=True)

    def validate_dataset_ids(self, ids):
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Dataset IDs must be unique.")
        return ids


class FolderItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    path = serializers.CharField()
    count = serializers.IntegerField()
    total = serializers.IntegerField()


class FolderResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = FolderItemSerializer()


class FolderListResultSerializer(serializers.Serializer):
    folders = FolderItemSerializer(many=True)
    all_count = serializers.IntegerField()
    no_folder_count = serializers.IntegerField()
    starred_count = serializers.IntegerField()


class FolderListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = FolderListResultSerializer()


class FolderMoveResultSerializer(serializers.Serializer):
    dataset_ids = serializers.ListField(child=serializers.UUIDField())
    folder_id = serializers.UUIDField(allow_null=True)


class FolderMoveResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = FolderMoveResultSerializer()
