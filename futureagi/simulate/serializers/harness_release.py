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


class HarnessReleaseAppSerializer(serializers.Serializer):
    capability = serializers.CharField()
    server = serializers.CharField()
    tools = serializers.ListField(child=serializers.CharField())
    records = serializers.IntegerField()


class HarnessReleaseCatalogueSerializer(serializers.Serializer):
    """What a catalogue card and a ready environment's World tab show, sealed into the release
    from its package (title, first sentence, seeded records per table, attached apps, example
    requests from its first scenarios)."""

    title = serializers.CharField()
    summary = serializers.CharField(allow_blank=True)
    channel = serializers.ChoiceField(choices=("chat", "voice"))
    records = serializers.DictField(child=serializers.IntegerField())
    apps = HarnessReleaseAppSerializer(many=True)
    example_requests = serializers.ListField(child=serializers.CharField())


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
    catalogue = HarnessReleaseCatalogueSerializer(
        allow_null=True,
        help_text="Null for a release sealed before catalogue metadata existed.",
    )


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
    agent = HarnessAgentSerializer(
        required=False,
        help_text=(
            "How the hosted agent is reached. Omitted for an agent connected on the Agents "
            "page: its provider, agent id and stored key are read from the version."
        ),
    )
    name = serializers.CharField(max_length=255)
    idempotency_key = serializers.CharField(max_length=255)
    parallelism = _parallelism_field()
    pairing = serializers.DictField(
        child=serializers.CharField(allow_null=True),
        required=False,
        help_text="World tool to the agent tool it is paired with, as the person reviewed it.",
    )
    mode = serializers.ChoiceField(
        choices=("review", "run"),
        default="review",
        help_text=(
            "review: the builder drafts the binding and the person approves its design. run: "
            "the binding is drafted and checked without a session, then the measurable cases run."
        ),
    )


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


class HarnessReleaseFitRequestSerializer(serializers.Serializer):
    suite_id = serializers.UUIDField()
    agent_version_id = serializers.UUIDField()
    pairing = serializers.DictField(
        child=serializers.CharField(allow_null=True), required=False
    )


class HarnessReleaseFitRowSerializer(serializers.Serializer):
    world = serializers.CharField()
    agent = serializers.CharField(allow_null=True)
    kind = serializers.ChoiceField(choices=("matched", "mapped", "missing"))


class HarnessReleaseFitToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True, required=False)


class HarnessReleaseFitCaseSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()


class HarnessReleaseFitAgentSerializer(serializers.Serializer):
    name = serializers.CharField()
    version = serializers.CharField()


class HarnessReleaseFitResponseSerializer(serializers.Serializer):
    """How the agent fits the release before anything is built."""

    agent = HarnessReleaseFitAgentSerializer()
    agent_tools = HarnessReleaseFitToolSerializer(many=True)
    rows = HarnessReleaseFitRowSerializer(many=True)
    platform_actions = HarnessReleaseFitToolSerializer(many=True)
    total = serializers.IntegerField()
    unmeasured = HarnessReleaseFitCaseSerializer(many=True)


class HarnessReleaseConflictSerializer(serializers.Serializer):
    rule = serializers.CharField()
    policy = serializers.CharField(help_text="The rule as the world states it.")
    agent = serializers.CharField(help_text="The agent's instruction, quoted.")


class HarnessReleaseConflictsResponseSerializer(serializers.Serializer):
    """Where the agent's instructions conflict with the rules the world grades it on."""

    checked = serializers.BooleanField(
        help_text="False when the instructions couldn't be read against the rules yet."
    )
    conflicts = HarnessReleaseConflictSerializer(many=True)


class HarnessAdaptationWorldSerializer(serializers.Serializer):
    name = serializers.CharField()
    title = serializers.CharField()
    summary = serializers.CharField(allow_blank=True)
    channel = serializers.CharField()
    release = serializers.CharField(help_text="The release's digest.")
    records = serializers.DictField(child=serializers.IntegerField())
    tools = HarnessReleaseFitToolSerializer(many=True)
    rules = HarnessReleaseAgentRuleSerializer(many=True)
    people = serializers.ListField(child=serializers.CharField())


class HarnessAdaptationCaseSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()


class HarnessAdaptationDesignSerializer(serializers.Serializer):
    """An adaptation's design, as Review the design shows it before anything is built."""

    state = serializers.ChoiceField(
        choices=("checking", "awaiting_approval", "building", "ready", "failed"),
        help_text=(
            "checking: the mapping is still being checked on every case; awaiting_approval: "
            "Approve and build may be pressed; building: approved; ready: the environment exists."
        ),
    )
    failure = serializers.JSONField(allow_null=True)
    composition_id = serializers.UUIDField(allow_null=True)
    agent = HarnessReleaseFitAgentSerializer()
    world = HarnessAdaptationWorldSerializer()
    suite = serializers.CharField()
    scenarios = HarnessAdaptationCaseSerializer(many=True)
    fit = HarnessReleaseFitResponseSerializer()
    conflicts = HarnessReleaseConflictsResponseSerializer()
    check = serializers.JSONField(
        allow_null=True,
        help_text="What checking the mapping on every case found, once it has: the approval's data.",
    )
