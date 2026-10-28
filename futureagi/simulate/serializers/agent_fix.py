from rest_framework import serializers

from simulate.models import AgentFixCandidate, AgentFixRun
from simulate.pydantic_schemas.agent_fix import Surface


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
    budget_usd = serializers.FloatField(min_value=0.01, max_value=500)
    max_minutes = serializers.IntegerField(min_value=1, max_value=24 * 60)


class AgentFixListQuerySerializer(serializers.Serializer):
    environment_id = serializers.UUIDField()


class AgentFixRequestSerializer(serializers.Serializer):
    target_scenario_keys = serializers.ListField(child=serializers.CharField())
    failing_sub_goals = serializers.ListField(child=serializers.CharField())
    surfaces = serializers.ListField(child=serializers.CharField())
    locked_paths = serializers.ListField(child=serializers.CharField())
    budget_usd = serializers.FloatField()
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


class AgentFixReportSerializer(serializers.Serializer):
    ending = serializers.CharField(allow_null=True)
    summary = serializers.CharField(allow_blank=True)
    winner_candidate_id = serializers.CharField(allow_null=True)


class AgentFixFailureSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class AgentFixCandidateSerializer(serializers.ModelSerializer):
    agent_version_id = serializers.UUIDField()
    version_number = serializers.IntegerField(source="agent_version.version_number")
    verdict = AgentFixVerdictSerializer(allow_null=True)

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
            "created_at",
        ]


class AgentFixRunSerializer(serializers.ModelSerializer):
    environment_id = serializers.UUIDField()
    source_run_id = serializers.UUIDField(allow_null=True)
    base_agent_version_id = serializers.UUIDField()
    request = AgentFixRequestSerializer()
    report = AgentFixReportSerializer(allow_null=True)
    failure = AgentFixFailureSerializer(allow_null=True)
    spent_usd = serializers.DecimalField(max_digits=10, decimal_places=4)
    candidates = AgentFixCandidateSerializer(many=True)

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
            "spent_usd",
            "report",
            "failure",
            "candidates",
            "started_at",
            "finished_at",
            "created_at",
        ]


class AgentFixMakeDefaultSerializer(serializers.Serializer):
    outcome = serializers.CharField()
