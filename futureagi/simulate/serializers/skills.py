from rest_framework import serializers

from simulate.models import AgentVersionSkillScanStatus, ContextRuntime, SkillKind

USAGE_STATES = ("instrumented", "not_instrumented", "no_calls", "unavailable")
SKILLS_STATUSES = (
    *AgentVersionSkillScanStatus.values,
    "no_version",
    "unsupported_source",
    "not_scanned",
)


class AgentSkillsQuerySerializer(serializers.Serializer):
    agent_definition_id = serializers.UUIDField()
    agent_version_id = serializers.UUIDField(required=False)


class AgentSkillVersionRefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version_number = serializers.IntegerField()


class AgentSkillUsageSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=USAGE_STATES)
    calls = serializers.IntegerField()


class AgentSkillItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=SkillKind.choices)
    name = serializers.CharField()
    path = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    version_number = serializers.IntegerField()
    readable = serializers.BooleanField()
    problem = serializers.CharField(allow_blank=True)
    observed_calls = serializers.IntegerField(allow_null=True)


class AgentContextFileSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    path = serializers.CharField()
    version_number = serializers.IntegerField()
    readable = serializers.BooleanField()
    problem = serializers.CharField(allow_blank=True)
    position = serializers.IntegerField()
    rule = serializers.CharField()
    on_demand = serializers.BooleanField()
    folder = serializers.CharField(allow_blank=True)
    imported_from = serializers.CharField(allow_blank=True)


class AgentContextSerializer(serializers.Serializer):
    runtime = serializers.ChoiceField(choices=ContextRuntime.choices)
    capped = serializers.BooleanField()
    files = AgentContextFileSerializer(many=True)


class AgentSkillsSerializer(serializers.Serializer):
    agent_version = AgentSkillVersionRefSerializer(allow_null=True)
    source_kind = serializers.CharField(allow_null=True, required=False)
    status = serializers.ChoiceField(choices=SKILLS_STATUSES)
    usage = AgentSkillUsageSerializer(required=False)
    context = AgentContextSerializer(required=False)
    items = AgentSkillItemSerializer(many=True)


class AgentSkillFileSerializer(serializers.Serializer):
    path = serializers.CharField()
    content = serializers.CharField(allow_blank=True, trim_whitespace=False)


class AgentSkillFilesSerializer(serializers.Serializer):
    files = AgentSkillFileSerializer(many=True)


class CallSkillItemSerializer(serializers.Serializer):
    name = serializers.CharField()
    path = serializers.CharField()
    kind = serializers.ChoiceField(choices=SkillKind.choices)
    version_number = serializers.IntegerField()
    observed = serializers.BooleanField(allow_null=True)


class CallSkillsSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=(*USAGE_STATES, "not_scanned"))
    items = CallSkillItemSerializer(many=True)
