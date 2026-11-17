"""Shared runtime load projections for skills and agent contexts."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers


class RuntimeWindowQuery(serializers.Serializer):
    to = serializers.DateTimeField()
    cursor = serializers.CharField(required=False, max_length=2048)
    limit = serializers.IntegerField(default=100, min_value=1, max_value=100)

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = serializers.DateTimeField()
        return fields

    def validate(self, attrs):
        start, end = attrs["from"], attrs["to"]
        if start >= end or end - start > timedelta(days=30) or end > timezone.now():
            raise serializers.ValidationError("Choose a nonempty window of at most 30 days ending now or earlier.")
        return attrs


class RuntimeFilterValuesQuery(RuntimeWindowQuery):
    project_id = serializers.UUIDField()
    field = serializers.ChoiceField(choices=(
        "skill.name", "skill.version_id", "skill.agent", "skill.service",
        "skill.requested_label", "skill.context_id",
    ))
    skill_id = serializers.UUIDField(required=False)
    load_result = serializers.ChoiceField(choices=("loaded", "failed"), default="loaded")
    q = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["load_result"] == "failed" and attrs["field"] == "skill.version_id":
            raise serializers.ValidationError("Failed loads have no resolved version.")
        return attrs


class RuntimeFilterValue(serializers.Serializer):
    value = serializers.CharField(allow_blank=True)
    label = serializers.CharField(allow_blank=True)
    skill_id = serializers.UUIDField(required=False)
    name = serializers.CharField(required=False)
    version_id = serializers.UUIDField(required=False)
    version = serializers.IntegerField(required=False)
    skill_name = serializers.CharField(required=False)
    labels_now = serializers.ListField(child=serializers.CharField(), required=False)
    production_version = serializers.IntegerField(required=False)


class RuntimeFilterValuesResult(serializers.Serializer):
    values = RuntimeFilterValue(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    truncated = serializers.BooleanField()


class LatestLoadQuery(serializers.Serializer):
    resource_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=("skill", "agent_context"))
    since = serializers.DateTimeField()


class LoadAgent(serializers.Serializer):
    key = serializers.ListField(child=serializers.CharField(allow_blank=True))
    name = serializers.CharField()
    service = serializers.CharField(allow_blank=True)
    project_id = serializers.UUIDField()
    project_name = serializers.CharField()
    name_source = serializers.ChoiceField(choices=("agent", "service", "project"))


class LoadProject(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class LatestLoad(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    agent = LoadAgent()
    project = LoadProject()
    received_at = serializers.DateTimeField()


class LatestFetch(serializers.Serializer):
    sdk_version = serializers.CharField(allow_null=True)
    tracing_mode = serializers.CharField(allow_null=True)
    can_record = serializers.BooleanField()
    client = serializers.CharField(allow_blank=True)


class LatestLoadResult(serializers.Serializer):
    load = LatestLoad(allow_null=True)
    fetch = LatestFetch(allow_null=True)
