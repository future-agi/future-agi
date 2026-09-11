from rest_framework import serializers

from tracer.models.saved_view import SavedView
from tracer.serializers.filters import StrictInputSerializer, filter_list_field


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


class SavedViewDefaultTabSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    tab_type = serializers.CharField()


class SavedViewListResultSerializer(serializers.Serializer):
    default_tabs = SavedViewDefaultTabSerializer(many=True)
    custom_views = SavedViewListSerializer(many=True)


class SavedViewListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = SavedViewListResultSerializer()


class SavedViewDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = SavedViewDetailSerializer()


class SavedViewMessageResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class SavedViewMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = SavedViewMessageResultSerializer()


FILTER_CONFIG_KEYS = (
    "filters",
    "compare_filters",
    "extra_filters",
    "compare_extra_filters",
)
SAVED_VIEW_CONFIG_KEYS = {
    "filters",
    "columns",
    "sort",
    "display",
    "widgets",
    "conversation_id",
    "sub_tab",
    "compare_filters",
    "compare_date_filter",
    "extra_filters",
    "compare_extra_filters",
}


def validate_saved_view_config(value):
    if not isinstance(value, dict):
        raise serializers.ValidationError("config must be a JSON object.")
    invalid_keys = set(value.keys()) - SAVED_VIEW_CONFIG_KEYS
    if invalid_keys:
        raise serializers.ValidationError(
            f"Invalid config keys: {', '.join(invalid_keys)}. "
            f"Allowed: {', '.join(SAVED_VIEW_CONFIG_KEYS)}"
        )

    validated = dict(value)
    for key in FILTER_CONFIG_KEYS:
        if key in validated and validated[key] is not None:
            validated[key] = filter_list_field().run_validation(validated[key])
    return validated


class SavedViewCreateSerializer(StrictInputSerializer):
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
        return validate_saved_view_config(value)


class SavedViewUpdateSerializer(StrictInputSerializer):
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
        return validate_saved_view_config(value)


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
