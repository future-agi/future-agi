from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from simulate.models import AgentFixCandidate, AgentFixRun
from simulate.pydantic_schemas.agent_fix import (
    BUDGET_CREDITS_MAX,
    MAX_MINUTES_MAX,
    SCENARIOS_MAX,
    CandidateOutcome,
    Ending,
    FailureOwner,
    FixStep,
    Surface,
)
from simulate.selectors.agent_fix import (
    agent_identity,
    base_check_runs,
    is_retryable,
)
from simulate.services.harness_environment import environment_name


class CreditsField(serializers.FloatField):
    """US dollars billed, read as credits: 1 credit is $0.01."""

    def to_representation(self, value):
        return round(float(value) * 100, 2)


class AgentFixLaunchSerializer(serializers.Serializer):
    # A run is known to the user by its test execution, as every run page names it.
    source_run_id = serializers.UUIDField()
    target_scenario_keys = serializers.ListField(
        child=serializers.CharField(max_length=255),
        min_length=1,
        max_length=SCENARIOS_MAX,
    )
    surfaces = serializers.ListField(
        child=serializers.ChoiceField(choices=[one.value for one in Surface]),
        min_length=1,
    )
    locked_paths = serializers.ListField(
        child=serializers.CharField(max_length=512), required=False, default=list
    )
    # The fix's limit in credits; it covers the engine's model calls and the check runs.
    budget_credits = serializers.IntegerField(min_value=1, max_value=BUDGET_CREDITS_MAX)
    max_minutes = serializers.IntegerField(min_value=1, max_value=MAX_MINUTES_MAX)


class AgentFixListQuerySerializer(serializers.Serializer):
    environment_id = serializers.UUIDField()
    # Named as ExtendedPageNumberPagination reads them.
    page = serializers.IntegerField(required=False, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100)


class AgentFixRequestSerializer(serializers.Serializer):
    target_scenario_keys = serializers.ListField(child=serializers.CharField())
    # The targets the worker never sees; a fix started before the split has none.
    held_back_scenario_keys = serializers.ListField(
        child=serializers.CharField(), default=list
    )
    failing_sub_goals = serializers.ListField(child=serializers.CharField())
    surfaces = serializers.ListField(child=serializers.CharField())
    locked_paths = serializers.ListField(child=serializers.CharField())
    budget_credits = CreditsField(source="budget_usd")
    max_minutes = serializers.IntegerField()


class AgentFixVerdictTierSerializer(serializers.Serializer):
    name = serializers.CharField()
    status = serializers.ChoiceField(choices=["passed", "failed", "pending", "skipped"])
    detail = serializers.CharField(required=False, allow_blank=True, default="")


class AgentFixPlanSerializer(serializers.Serializer):
    target = serializers.IntegerField(min_value=0)
    held_back = serializers.IntegerField(min_value=0)
    must_pass = serializers.IntegerField(min_value=0)
    protected = serializers.IntegerField(min_value=0, required=False, default=0)
    max_candidates = serializers.IntegerField(min_value=0)
    repeats = serializers.IntegerField(min_value=0)
    baseline_spread_pts = serializers.IntegerField(min_value=0)


class AgentFixDiagnosisSerializer(serializers.Serializer):
    causes = serializers.ListField(child=serializers.CharField(), default=list)


class AgentFixAgentSerializer(serializers.Serializer):
    name = serializers.CharField()
    kind = serializers.CharField()
    version_id = serializers.UUIDField()
    version_number = serializers.IntegerField(allow_null=True)
    ref = serializers.CharField(allow_null=True)


class AgentFixEnvironmentSerializer(serializers.Serializer):
    name = serializers.CharField()
    version = serializers.IntegerField(allow_null=True)
    default_agent_version_id = serializers.UUIDField(allow_null=True)


class AgentFixVerdictSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    reason = serializers.CharField()
    target_pass_rate = serializers.ListField(
        child=serializers.FloatField(), allow_null=True, required=False
    )
    held_back_pass_rate = serializers.ListField(
        child=serializers.FloatField(), allow_null=True, required=False
    )
    must_pass_regressions = serializers.ListField(child=serializers.CharField())
    must_pass_within_noise = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    # The verdict's checks in order; a verdict from an older engine has none.
    tiers = AgentFixVerdictTierSerializer(many=True, required=False, default=list)


class AgentFixReportSerializer(serializers.Serializer):
    ending = serializers.CharField(allow_null=True)
    summary = serializers.CharField(allow_blank=True)
    winner_candidate_id = serializers.CharField(allow_null=True)


class AgentFixFailureSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class AgentFixCheckRunSerializer(serializers.Serializer):
    """A Run the fix started to check a candidate or the base version. It stays out of
    the environment's run list; its results open by ``test_execution_id``."""

    run_id = serializers.UUIDField(source="id")
    test_execution_id = serializers.UUIDField(allow_null=True)
    # The engine's name for the run ("baseline", "c2-target"), for display.
    label = serializers.CharField(source="fix_label")
    state = serializers.CharField()


class AgentFixCandidateSerializer(serializers.ModelSerializer):
    agent_version_id = serializers.UUIDField()
    version_number = serializers.IntegerField(source="agent_version.version_number")
    verdict = AgentFixVerdictSerializer(allow_null=True)
    check_runs = serializers.SerializerMethodField()

    @swagger_serializer_method(
        serializer_or_field=AgentFixCheckRunSerializer(many=True)
    )
    def get_check_runs(self, candidate: AgentFixCandidate) -> list[dict]:
        runs = candidate.fix_run.check_run_list
        mine = [
            run for run in runs if run.agent_version_id == candidate.agent_version_id
        ]
        return AgentFixCheckRunSerializer(mine, many=True).data

    class Meta:
        model = AgentFixCandidate
        fields = [
            "id",
            "attempt",
            "agent_version_id",
            "version_number",
            "summary",
            "outcome",
            "verdict",
            "check_runs",
            "pull_request_url",
            "created_at",
        ]


class AgentFixRunSerializer(serializers.ModelSerializer):
    environment_id = serializers.UUIDField()
    # The run's test execution, as for launch.
    source_run_id = serializers.UUIDField(
        source="source_run.test_execution_id", allow_null=True
    )
    base_agent_version_id = serializers.UUIDField()
    request = AgentFixRequestSerializer()
    report = AgentFixReportSerializer(allow_null=True)
    failure = AgentFixFailureSerializer(allow_null=True)
    # The engine's model calls and the sealed check runs, as billed.
    spent_credits = CreditsField(source="fix_spent_usd")
    # What the limit is held against: the spend plus a worst case for each unsealed check run.
    committed_credits = CreditsField(source="committed_usd")
    candidates = AgentFixCandidateSerializer(many=True)
    check_runs = serializers.SerializerMethodField()
    # The fix's place among the environment's fixes, and its run's among its runs.
    number = serializers.IntegerField(allow_null=True)
    source_run_number = serializers.IntegerField(allow_null=True)
    step = serializers.SerializerMethodField()
    plan = AgentFixPlanSerializer(allow_null=True)
    diagnosis = AgentFixDiagnosisSerializer(allow_null=True)
    agent = serializers.SerializerMethodField()
    environment = serializers.SerializerMethodField()
    # The failed fix this one starts again, and whether this one may be started again.
    retried_from_id = serializers.UUIDField(allow_null=True)
    retryable = serializers.SerializerMethodField()

    @swagger_serializer_method(serializer_or_field=serializers.BooleanField())
    def get_retryable(self, fix_run: AgentFixRun) -> bool:
        return is_retryable(fix_run)

    @swagger_serializer_method(
        serializer_or_field=serializers.ChoiceField(
            choices=[one.value for one in FixStep], allow_null=True
        )
    )
    def get_step(self, fix_run: AgentFixRun) -> str | None:
        """A finished fix is done, whatever the engine last said."""
        if fix_run.state not in AgentFixRun.ACTIVE_STATES:
            return FixStep.DONE.value
        return fix_run.step or None

    @swagger_serializer_method(serializer_or_field=AgentFixAgentSerializer)
    def get_agent(self, fix_run: AgentFixRun) -> dict:
        return agent_identity(fix_run.base_agent_version).model_dump(mode="json")

    @swagger_serializer_method(serializer_or_field=AgentFixEnvironmentSerializer)
    def get_environment(self, fix_run: AgentFixRun) -> dict:
        environment = fix_run.environment
        version = environment.environment_version
        return {
            "name": environment_name(environment),
            "version": version.version_number if version else None,
            "default_agent_version_id": environment.agent_version_id,
        }

    @swagger_serializer_method(
        serializer_or_field=AgentFixCheckRunSerializer(many=True)
    )
    def get_check_runs(self, fix_run: AgentFixRun) -> list[dict]:
        """The base version's runs; each candidate lists its own."""
        return AgentFixCheckRunSerializer(base_check_runs(fix_run), many=True).data

    class Meta:
        model = AgentFixRun
        fields = [
            "id",
            "number",
            "environment_id",
            "environment",
            "source_run_id",
            "source_run_number",
            "base_agent_version_id",
            "agent",
            "state",
            "stage",
            "step",
            "plan",
            "diagnosis",
            "request",
            "spent_credits",
            "committed_credits",
            "report",
            "failure",
            "retried_from_id",
            "retryable",
            "check_runs",
            "candidates",
            "started_at",
            "finished_at",
            "created_at",
        ]


class AgentFixRunListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    results = AgentFixRunSerializer(many=True)


class AgentFixMakeDefaultSerializer(serializers.Serializer):
    outcome = serializers.CharField()


class AgentFixPullRequestSerializer(serializers.Serializer):
    pull_request_url = serializers.URLField()


# The engine's calls from its sandbox. The services take the pydantic contract
# (simulate.pydantic_schemas.agent_fix); these serializers are its HTTP shape.


class AgentFixCandidateUploadSerializer(serializers.Serializer):
    attempt = serializers.IntegerField(min_value=1, max_value=32767)
    summary = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=4000
    )
    tree = serializers.FileField()
    patch = serializers.FileField()


class AgentFixCandidateCreatedSerializer(serializers.Serializer):
    candidate_id = serializers.UUIDField()
    agent_version_id = serializers.UUIDField()
    preflight_status = serializers.CharField()


class AgentFixCheckRunRequestSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=64)
    agent_version_id = serializers.UUIDField()
    scenario_keys = serializers.ListField(
        child=serializers.CharField(max_length=255), min_length=1
    )
    trials = serializers.IntegerField(min_value=1, max_value=20)
    repeat = serializers.IntegerField(min_value=1, max_value=20, default=1)


class AgentFixCheckRunCreatedSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()


class AgentFixCandidateUpdateSerializer(serializers.Serializer):
    candidate_id = serializers.UUIDField()
    outcome = serializers.ChoiceField(choices=[one.value for one in CandidateOutcome])
    verdict = AgentFixVerdictSerializer(required=False, allow_null=True)


class AgentFixEngineFailureSerializer(serializers.Serializer):
    owner = serializers.ChoiceField(choices=[one.value for one in FailureOwner])
    code = serializers.CharField(max_length=64)
    message = serializers.CharField(allow_blank=True, max_length=500)


class AgentFixEngineReportSerializer(serializers.Serializer):
    stage = serializers.CharField(max_length=32)
    step = serializers.ChoiceField(
        choices=[one.value for one in FixStep], required=False, allow_null=True
    )
    plan = AgentFixPlanSerializer(required=False, allow_null=True)
    diagnosis = AgentFixDiagnosisSerializer(required=False, allow_null=True)
    candidates = AgentFixCandidateUpdateSerializer(
        many=True, required=False, default=list
    )
    ending = serializers.ChoiceField(
        choices=[one.value for one in Ending], required=False, allow_null=True
    )
    summary = serializers.CharField(required=False, allow_blank=True, default="")
    failure = AgentFixEngineFailureSerializer(required=False, allow_null=True)
    winner_candidate_id = serializers.UUIDField(required=False, allow_null=True)


class AgentFixEngineStateSerializer(serializers.Serializer):
    state = serializers.CharField()


class AgentFixLaunchOptionsQuerySerializer(serializers.Serializer):
    source_run_id = serializers.UUIDField()


class AgentFixLaunchSourceRunSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    number = serializers.IntegerField(allow_null=True)
    state = serializers.CharField()
    finished_at = serializers.CharField(allow_null=True)


class AgentFixLaunchEnvironmentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    version = serializers.IntegerField(allow_null=True)


class AgentFixLaunchScenarioSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    passed = serializers.BooleanField(allow_null=True)
    failing_sub_goals = serializers.ListField(child=serializers.CharField())


class AgentFixLaunchSuggestedSerializer(serializers.Serializer):
    target_scenario_keys = serializers.ListField(child=serializers.CharField())
    protected_scenario_keys = serializers.ListField(child=serializers.CharField())


class AgentFixLaunchSurfaceSerializer(serializers.Serializer):
    id = serializers.ChoiceField(choices=[one.value for one in Surface])
    label = serializers.CharField()
    allowed = serializers.BooleanField()
    reason = serializers.CharField(allow_null=True)


class AgentFixLaunchDefaultsSerializer(serializers.Serializer):
    surfaces = serializers.ListField(
        child=serializers.ChoiceField(choices=[one.value for one in Surface])
    )
    budget_credits = serializers.IntegerField()
    max_minutes = serializers.IntegerField()


class AgentFixLaunchLimitsSerializer(serializers.Serializer):
    budget_credits_max = serializers.IntegerField()
    max_minutes_max = serializers.IntegerField()
    scenarios_max = serializers.IntegerField()


class AgentFixLaunchOptionsSerializer(serializers.Serializer):
    source_run = AgentFixLaunchSourceRunSerializer()
    environment = AgentFixLaunchEnvironmentSerializer()
    agent = AgentFixAgentSerializer()
    scenarios = AgentFixLaunchScenarioSerializer(many=True)
    suggested = AgentFixLaunchSuggestedSerializer()
    surfaces = AgentFixLaunchSurfaceSerializer(many=True)
    defaults = AgentFixLaunchDefaultsSerializer()
    limits = AgentFixLaunchLimitsSerializer()
    # Set when Start must be disabled, with the reason launch would give.
    blocked = AgentFixFailureSerializer(allow_null=True)
