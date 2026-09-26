from rest_framework import serializers

from ee.falcon_ai.models import Skill
from tracer.serializers.filters import StrictInputSerializer


class SkillListSerializer(serializers.ModelSerializer):
    created_by_display = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "icon",
            "is_builtin",
            "is_active",
            "tool_names",
            "trigger_phrases",
            "created_at",
            "created_by_display",
        ]

    def get_created_by_display(self, obj):
        if obj.is_builtin:
            return "System"
        if obj.created_by:
            return obj.created_by.name or obj.created_by.email
        return "Unknown"


class SkillDetailSerializer(serializers.ModelSerializer):
    created_by_display = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "icon",
            "is_builtin",
            "is_active",
            "instructions",
            "tool_names",
            "example_trajectories",
            "trigger_phrases",
            "created_at",
            "updated_at",
            "created_by_display",
        ]

    def get_created_by_display(self, obj):
        if obj.is_builtin:
            return "System"
        if obj.created_by:
            return obj.created_by.name or obj.created_by.email
        return "Unknown"


class SkillCreateSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(allow_blank=True, default="")
    icon = serializers.CharField(max_length=50, default="mdi:star")
    instructions = serializers.CharField()
    tool_names = serializers.ListField(child=serializers.CharField(), default=list)
    trigger_phrases = serializers.ListField(child=serializers.CharField(), min_length=1)

    def validate_trigger_phrases(self, value):
        if not value:
            raise serializers.ValidationError(
                "At least one trigger phrase is required."
            )
        return value
