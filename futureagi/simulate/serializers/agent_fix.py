from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from simulate.models import AgentFixCandidate, AgentFixRun
from simulate.pydantic_schemas.agent_fix import CandidateOutcome, Ending, Surface


class CreditsField(serializers.FloatField):
    """US dollars billed, read as credits: 1 credit is $0.01."""

    def to_representation(self, value):
        return round(float(value) * 100, 2)


class AgentFixLaunchSerializer(serializers.Serializer):
    source_run_id = serializers.UUIDField()
    target_scenario_keys = serializers.ListField(
        child=serializers.CharField(max_length=255), min_length=1, max_length=200
    )
    surfaces = serializers.ListField(
        child=serializers.ChoiceField(choices=[one.value for one in Surface]),
        min_length=1,
    )
    locked_paths = serializers.ListField(
        child=serializers.CharField(max_length=512), required=False, default=list
    )
    # The fix's limit in credits; it covers the engine's model calls and the check runs.
    budget_credits = serializers.IntegerField(min_value=1, max_value=50_000)
    max_minutes = serializers.IntegerField(min_value=1, max_value=24 * 60)


class AgentFixListQuerySerializer(serializers.Serializer):
    environment_id = serializers.UUIDField()


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
            "created_at",
        ]


class AgentFixRunSerializer(serializers.ModelSerializer):
    environment_id = serializers.UUIDField()
    source_run_id = serializers.UUIDField(allow_null=True)
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

    @swagger_serializer_method(
        serializer_or_field=AgentFixCheckRunSerializer(many=True)
    )
    def get_check_runs(self, fix_run: AgentFixRun) -> list[dict]:
        """The base version's runs; each candidate lists its own."""
        runs = fix_run.check_run_list
        base = [
            run for run in runs if run.agent_version_id == fix_run.base_agent_version_id
        ]
        return AgentFixCheckRunSerializer(base, many=True).data

    class Meta:
        model = AgentFixRun
        fields = [
            "id",
            "environment_id",
            "source_run_id",
            "base_agent_version_id",
            "state",
            "stage",
            "request",
            "spent_credits",
            "committed_credits",
            "report",
            "failure",
            "check_runs",
            "candidates",
            "started_at",
            "finished_at",
            "created_at",
        ]


class AgentFixMakeDefaultSerializer(serializers.Serializer):
    outcome = serializers.CharField()


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


class AgentFixCheckRunCreatedSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()


class AgentFixCandidateUpdateSerializer(serializers.Serializer):
    candidate_id = serializers.UUIDField()
    outcome = serializers.ChoiceField(choices=[one.value for one in CandidateOutcome])
    verdict = AgentFixVerdictSerializer(required=False, allow_null=True)


class AgentFixEngineReportSerializer(serializers.Serializer):
    stage = serializers.CharField(max_length=32)
    candidates = AgentFixCandidateUpdateSerializer(
        many=True, required=False, default=list
    )
    ending = serializers.ChoiceField(
        choices=[one.value for one in Ending], required=False, allow_null=True
    )
    summary = serializers.CharField(required=False, allow_blank=True, default="")
    winner_candidate_id = serializers.UUIDField(required=False, allow_null=True)


class AgentFixEngineStateSerializer(serializers.Serializer):
    state = serializers.CharField()
