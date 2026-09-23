from rest_framework import serializers

from simulate.serializers.harness_job import (
    HarnessRunCreateResponseSerializer,
    HarnessRunCreateSerializer,
)


class HarnessEnvironmentListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True, allow_null=True)
    domain = serializers.CharField(allow_blank=True, allow_null=True)
    status = serializers.ChoiceField(choices=("building", "ready", "failed"))
    agent_type = serializers.ChoiceField(choices=("voice", "chat"))
    tools_count = serializers.IntegerField(min_value=0)
    scenario_count = serializers.IntegerField(min_value=0)
    sub_goals_count = serializers.IntegerField(min_value=0)
    runs_count = serializers.IntegerField(min_value=0)
    created_at = serializers.DateTimeField()
    last_updated = serializers.DateTimeField()


class HarnessEnvironmentListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    total_pages = serializers.IntegerField(min_value=0)
    current_page = serializers.IntegerField(min_value=1)
    results = HarnessEnvironmentListItemSerializer(many=True)


class HarnessEnvironmentDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    overview = serializers.JSONField()
    contract = serializers.JSONField(allow_null=True)
    world = serializers.JSONField(allow_null=True)
    scenarios = serializers.ListField(child=serializers.JSONField())
    evaluations = serializers.JSONField()
    settings = serializers.JSONField()


class HarnessEnvironmentRenameSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=255, trim_whitespace=True)


__all__ = [
    "HarnessEnvironmentDetailSerializer",
    "HarnessEnvironmentListResponseSerializer",
    "HarnessEnvironmentRenameSerializer",
    "HarnessRunCreateResponseSerializer",
    "HarnessRunCreateSerializer",
]
