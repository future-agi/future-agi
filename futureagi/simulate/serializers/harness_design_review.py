from rest_framework import serializers

from simulate.services.harness_design_contract import KINDS
from simulate.services.harness_design_review import OPS, STATUSES


class DesignEditRequestSerializer(serializers.Serializer):
    op = serializers.ChoiceField(choices=OPS)
    kind = serializers.ChoiceField(choices=KINDS)
    id = serializers.CharField(max_length=255)
    text = serializers.CharField(required=False, max_length=2000)

    def validate(self, attrs):
        if attrs["op"] == "edit" and not attrs.get("text", "").strip():
            raise serializers.ValidationError({"text": "The rule needs some text"})
        return attrs


class DesignImpactQuerySerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("table", "tool"))
    id = serializers.CharField(max_length=255)


class DesignTableSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    columns = serializers.ListField(child=serializers.CharField())
    source = serializers.CharField()  # type: ignore[assignment]  # the API field is named source, as Field's own
    removed = serializers.BooleanField()


class DesignToolArgSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField(allow_blank=True)


class DesignToolSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    args = DesignToolArgSerializer(many=True)
    requires = serializers.ListField(child=serializers.CharField())
    inputs_from = serializers.DictField(child=serializers.CharField())
    effects = serializers.CharField(allow_blank=True)
    refuses = serializers.CharField(allow_blank=True)
    tables_read = serializers.ListField(child=serializers.CharField(), required=False)
    tables_changed = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    status = serializers.ChoiceField(
        choices=("simulated", "in_process", "unsupported", "unknown"), required=False
    )
    source = serializers.CharField()  # type: ignore[assignment]  # the API field is named source, as Field's own
    removed = serializers.BooleanField()


class DesignRuleSerializer(serializers.Serializer):
    id = serializers.CharField()
    text = serializers.CharField()
    source = serializers.CharField()  # type: ignore[assignment]  # the API field is named source, as Field's own
    removed = serializers.BooleanField()
    edited = serializers.BooleanField()
    original_text = serializers.CharField(allow_null=True)


class DesignUseCaseSerializer(serializers.Serializer):
    id = serializers.CharField()
    text = serializers.CharField()
    source = serializers.CharField()  # type: ignore[assignment]  # the API field is named source, as Field's own
    removed = serializers.BooleanField()
    planned_scenarios = serializers.IntegerField(min_value=0)


class DesignAgentSerializer(serializers.Serializer):
    name = serializers.CharField()
    version = serializers.CharField()


class DesignBriefSerializer(serializers.Serializer):
    description = serializers.CharField(allow_blank=True)
    channel = serializers.ChoiceField(choices=("chat", "voice"))
    agent = DesignAgentSerializer(allow_null=True)


class DesignActorSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    role = serializers.CharField(allow_blank=True)


class DesignUnsupportedSerializer(serializers.Serializer):
    tool = serializers.CharField()
    reaches = serializers.ListField(child=serializers.CharField())
    removed = serializers.BooleanField()


class DesignEditSerializer(serializers.Serializer):
    edit_id = serializers.CharField()
    op = serializers.ChoiceField(choices=OPS)
    kind = serializers.ChoiceField(choices=KINDS)
    id = serializers.CharField()
    text = serializers.CharField(required=False)
    by = serializers.CharField(allow_blank=True)
    at = serializers.CharField()


class DesignReviewSerializer(serializers.Serializer):
    environment_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=STATUSES)
    agent_version_id = serializers.UUIDField(allow_null=True)
    agent_changed = serializers.BooleanField()
    scenario_count = serializers.IntegerField(min_value=0)
    tables = DesignTableSerializer(many=True)
    tools = DesignToolSerializer(many=True)
    rules = DesignRuleSerializer(many=True)
    use_cases = DesignUseCaseSerializer(many=True)
    brief = DesignBriefSerializer()
    actors = DesignActorSerializer(many=True)
    unsupported = DesignUnsupportedSerializer(many=True)
    edits = DesignEditSerializer(many=True)
    created_at = serializers.DateTimeField(allow_null=True)
    approved_at = serializers.DateTimeField(allow_null=True)


class DesignImpactToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    reason = serializers.CharField()


class DesignImpactRuleSerializer(serializers.Serializer):
    id = serializers.CharField()
    text = serializers.CharField()


class DesignImpactSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("table", "tool"))
    id = serializers.CharField()
    tools = DesignImpactToolSerializer(many=True)
    rules = DesignImpactRuleSerializer(many=True)
