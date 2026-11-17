"""Shared runtime load projections for skills and agent contexts."""

from rest_framework import serializers


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
