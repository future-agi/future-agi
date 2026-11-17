"""Wire contracts for saved dataset grid configurations."""

import json

from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer


class DatasetViewScopeSerializer(StrictInputSerializer):
    surface = serializers.ChoiceField(choices=["grid"])
    scope = serializers.UUIDField()


class DatasetViewQuerySerializer(DatasetViewScopeSerializer):
    page_size = serializers.IntegerField(min_value=1, max_value=100, default=100)
    after = serializers.UUIDField(required=False)


class DatasetViewWriteSerializer(DatasetViewScopeSerializer):
    restore_id = serializers.UUIDField(required=False)
    name = serializers.CharField(max_length=120, required=False)
    state = serializers.JSONField(required=False)
    shared = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if "restore_id" in attrs:
            if any(key in self.initial_data for key in ("name", "state", "shared")):
                raise serializers.ValidationError("Restore cannot change a view.")
        elif "name" not in attrs or "state" not in attrs:
            raise serializers.ValidationError("Name and state are required.")
        return attrs

    def validate_state(self, value):
        if not isinstance(value, dict) or len(json.dumps(value)) > 65536:
            raise serializers.ValidationError(
                "View state must be an object of at most 64 KiB."
            )
        return value


class DatasetViewSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    surface = serializers.CharField()
    scope = serializers.UUIDField()
    name = serializers.CharField()
    state = serializers.JSONField()
    shared = serializers.BooleanField()
    mine = serializers.BooleanField()
    can_delete = serializers.BooleanField()
    created_by = serializers.CharField()
    created_at = serializers.DateTimeField()


class DatasetViewResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetViewSerializer()


class DatasetViewListResultSerializer(serializers.Serializer):
    results = DatasetViewSerializer(many=True)
    next = serializers.UUIDField(
        allow_null=True, help_text="Continuation view ID, or null on the final page."
    )


class DatasetViewListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetViewListResultSerializer()


class DatasetViewDeletedResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class DatasetViewDeletedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetViewDeletedResultSerializer()
