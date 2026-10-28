"""Request and response shapes of the release API (`environment-releases.md` §7.6)."""

from rest_framework import serializers

from simulate.serializers.harness_job import HOSTED_AGENT_MODELS, HarnessAgentSerializer


class HarnessReleaseParentSerializer(serializers.Serializer):
    digest = serializers.CharField()
    name = serializers.CharField()


class HarnessReleaseToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    mutates_state = serializers.BooleanField()


class HarnessReleasePolicyParameterSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.ChoiceField(choices=("string", "integer", "number", "boolean"))
    default = serializers.JSONField(allow_null=True)
    allowed = serializers.ListField(child=serializers.JSONField(), allow_null=True)


class HarnessReleaseAgentRuleSerializer(serializers.Serializer):
    id = serializers.CharField()
    text = serializers.CharField(allow_blank=True)


class HarnessReleasePolicySerializer(serializers.Serializer):
    parameters = HarnessReleasePolicyParameterSerializer(many=True)
    agent_rules = HarnessReleaseAgentRuleSerializer(many=True)


class HarnessSuiteOriginsSerializer(serializers.Serializer):
    base = serializers.IntegerField()
    specialized = serializers.IntegerField()
    custom = serializers.IntegerField()


class HarnessSuiteSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    digest = serializers.CharField()
    name = serializers.CharField()
    scenario_count = serializers.IntegerField()
    origins = HarnessSuiteOriginsSerializer()
    policy_profile = serializers.JSONField()


class HarnessReleaseSerializer(serializers.Serializer):
    """An environment release an organization may compose: a system release or its own."""

    id = serializers.UUIDField()
    digest = serializers.CharField()
    name = serializers.CharField()
    system = serializers.BooleanField()
    parent = HarnessReleaseParentSerializer(allow_null=True)
    tools = HarnessReleaseToolSerializer(many=True)
    policy = HarnessReleasePolicySerializer()
    suites = HarnessSuiteSummarySerializer(many=True)


class HarnessReleaseListResponseSerializer(serializers.Serializer):
    results = HarnessReleaseSerializer(many=True)


def _parallelism_field() -> serializers.IntegerField:
    return serializers.IntegerField(
        min_value=1,
        max_value=8,
        required=False,
        help_text=(
            "Worlds the composition's runs use side by side; admission may lower it. "
            "Omitted: as many as this deployment admits."
        ),
    )


class HarnessReleaseAgentRequestSerializer(serializers.Serializer):
    """Which agent version is bound, and how its hosted agent is reached (as in a hosted job)."""

    suite_id = serializers.UUIDField()
    agent_version_id = serializers.UUIDField()
    agent = HarnessAgentSerializer()
    name = serializers.CharField(max_length=255)
    idempotency_key = serializers.CharField(max_length=255)
    parallelism = _parallelism_field()


class HarnessHostedAgentRequestSerializer(serializers.Serializer):
    """Our own agent on a release suite (contract §6.4): its spec and the suite it runs on."""

    suite_id = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    instructions = serializers.CharField(max_length=65536)
    model = serializers.ChoiceField(choices=HOSTED_AGENT_MODELS)
    idempotency_key = serializers.CharField(max_length=255)
    parallelism = _parallelism_field()


class HarnessHostedAgentResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The composition, an environment id.")
    agent_version_id = serializers.UUIDField()
