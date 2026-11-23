from rest_framework import serializers

from simulate.services.harness_design_review import KINDS, OPS, STATUSES


class DesignEditRequestSerializer(serializers.Serializer):
    op = serializers.ChoiceField(choices=OPS)
    kind = serializers.ChoiceField(choices=KINDS)
    id = serializers.CharField(required=False, max_length=255)
    text = serializers.CharField(required=False, max_length=2000)

    def validate(self, attrs):
        if not attrs.get("id"):
            raise serializers.ValidationError({"id": "Name the item to change"})
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
    source = serializers.CharField()
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
    source = serializers.CharField()
    removed = serializers.BooleanField()


class DesignRuleSerializer(serializers.Serializer):
    id = serializers.CharField()
    text = serializers.CharField()
    source = serializers.CharField()
    removed = serializers.BooleanField()
    edited = serializers.BooleanField()
    original_text = serializers.CharField(allow_null=True)


class DesignUseCaseSerializer(serializers.Serializer):
    id = serializers.CharField()
    text = serializers.CharField()
    source = serializers.CharField()
    removed = serializers.BooleanField()
    planned_scenarios = serializers.IntegerField(min_value=0)


class DesignEditSerializer(serializers.Serializer):
    index = serializers.IntegerField(min_value=0)
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
    edits = DesignEditSerializer(many=True)
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
