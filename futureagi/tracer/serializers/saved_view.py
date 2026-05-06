from rest_framework import serializers

from tracer.models.saved_view import SavedView


class SavedViewCreatorSerializer(serializers.Serializer):
    """Lightweight user serializer for saved view responses."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)


class SavedViewListSerializer(serializers.ModelSerializer):
    created_by = SavedViewCreatorSerializer(read_only=True)

    class Meta:
        model = SavedView
        fields = [
            "id",
            "name",
            "tab_type",
            "visibility",
            "position",
            "icon",
            "config",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "created_at",
            "updated_at",
        ]


class SavedViewDetailSerializer(serializers.ModelSerializer):
    created_by = SavedViewCreatorSerializer(read_only=True)
    updated_by = SavedViewCreatorSerializer(read_only=True)

    class Meta:
        model = SavedView
        fields = [
            "id",
            "name",
            "tab_type",
            "visibility",
            "position",
            "icon",
            "config",
            "project",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "project",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]


class SavedViewCreateSerializer(serializers.Serializer):
    project_id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(max_length=255)
    tab_type = serializers.ChoiceField(
        choices=[
            "traces",
            "spans",
            "voice",
            "imagine",
            "users",
            "user_detail",
            "sessions",
        ]
    )
    visibility = serializers.ChoiceField(
        choices=["personal", "project"], default="personal"
    )
    icon = serializers.CharField(max_length=50, required=False, allow_blank=True)
    config = serializers.JSONField(default=dict, required=False)

    def validate_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("View name cannot be empty.")
        return value.strip()

    def validate_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("config must be a JSON object.")
        allowed_keys = {
            "filters",
            "columns",
            "sort",
            "display",
            "widgets",
            "conversation_id",
            "sub_tab",
            "compareFilters",
            "compareDateFilter",
            "extraFilters",
            "compareExtraFilters",
        }
        invalid_keys = set(value.keys()) - allowed_keys
        if invalid_keys:
            raise serializers.ValidationError(
                f"Invalid config keys: {', '.join(invalid_keys)}. "
                f"Allowed: {', '.join(allowed_keys)}"
            )
        return value


class SavedViewUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    visibility = serializers.ChoiceField(
        choices=["personal", "project"], required=False
    )
    icon = serializers.CharField(
        max_length=50, required=False, allow_blank=True, allow_null=True
    )
    config = serializers.JSONField(required=False)

    def validate_name(self, value):
        if value is not None and not value.strip():
            raise serializers.ValidationError("View name cannot be empty.")
        return value.strip() if value else value

    def validate_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("config must be a JSON object.")
        allowed_keys = {
            "filters",
            "columns",
            "sort",
            "display",
            "widgets",
            "conversation_id",
            "sub_tab",
            "compareFilters",
            "compareDateFilter",
            "extraFilters",
            "compareExtraFilters",
        }
        invalid_keys = set(value.keys()) - allowed_keys
        if invalid_keys:
            raise serializers.ValidationError(
                f"Invalid config keys: {', '.join(invalid_keys)}. "
                f"Allowed: {', '.join(allowed_keys)}"
            )
        return value


class ReorderItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    position = serializers.IntegerField(min_value=0)


class SavedViewReorderSerializer(serializers.Serializer):
    project_id = serializers.UUIDField(required=False, allow_null=True)
    tab_type = serializers.ChoiceField(
        choices=[
            "traces",
            "spans",
            "voice",
            "imagine",
            "users",
            "user_detail",
            "sessions",
        ],
        required=False,
    )
    order = ReorderItemSerializer(many=True)
