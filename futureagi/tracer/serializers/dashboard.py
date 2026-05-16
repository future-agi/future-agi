from rest_framework import serializers

from accounts.serializers.user import UserSerializer
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.serializers.filters import filter_list_field


class DashboardWidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = DashboardWidget
        fields = [
            "id",
            "name",
            "description",
            "position",
            "width",
            "height",
            "query_config",
            "chart_config",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def validate_width(self, value):
        if value < 1 or value > 12:
            raise serializers.ValidationError("Width must be between 1 and 12.")
        return value

    def validate_height(self, value):
        if value < 1:
            raise serializers.ValidationError("Height must be at least 1.")
        return value

    def validate_query_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("query_config must be a JSON object.")
        return value

    def validate_chart_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("chart_config must be a JSON object.")
        valid_types = (
            "line",
            "stacked_line",
            "column",
            "stacked_column",
            "bar",
            "stacked_bar",
            "pie",
            "table",
            "metric",
        )
        if "chart_type" in value and value["chart_type"] not in valid_types:
            raise serializers.ValidationError(
                f"chart_type must be one of: {', '.join(valid_types)}"
            )
        return value


class DashboardSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    updated_by = UserSerializer(read_only=True)
    widget_count = serializers.SerializerMethodField()

    class Meta:
        model = Dashboard
        fields = [
            "id",
            "name",
            "description",
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "widget_count",
        ]
        read_only_fields = [
            "id",
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]

    def get_widget_count(self, obj):
        return obj.widgets.filter(deleted=False).count()


class DashboardDetailSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    updated_by = UserSerializer(read_only=True)
    widgets = serializers.SerializerMethodField()

    class Meta:
        model = Dashboard
        fields = [
            "id",
            "name",
            "description",
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "widgets",
        ]
        read_only_fields = [
            "id",
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]

    def get_widgets(self, obj):
        widgets = obj.widgets.filter(deleted=False).order_by("position", "created_at")
        return DashboardWidgetSerializer(widgets, many=True).data


class DashboardCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dashboard
        fields = ["name", "description"]

    def validate_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Dashboard name cannot be empty.")
        return value.strip()


class DashboardQuerySerializer(serializers.Serializer):
    workflow = serializers.ChoiceField(
        choices=["observability", "dataset", "simulation"], default="observability"
    )
    project_ids = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    time_range = serializers.DictField(required=True)
    granularity = serializers.ChoiceField(
        choices=["minute", "hour", "day", "week", "month"], default="day"
    )
    metrics = serializers.ListField(
        child=serializers.DictField(), min_length=1, max_length=5
    )
    filters = filter_list_field(required=False, default=list)
    breakdowns = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )
