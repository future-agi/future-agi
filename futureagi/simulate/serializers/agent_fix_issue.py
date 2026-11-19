"""The HTTP contract of Run Epoch from an Error Feed issue, for the issue page and the
`fi` CLI alike."""

from rest_framework import serializers

from simulate.pydantic_schemas.agent_fix import FixStep
from simulate.pydantic_schemas.agent_fix_issue import IssueJobPhase
from simulate.selectors.evidence_import import MAX_SOURCES
from simulate.serializers.agent_fix import (
    AgentFixFailureSerializer,
    AgentFixLaunchSerializer,
    AgentFixLaunchSurfaceSerializer,
)
from simulate.serializers.evidence_import import EvidenceImportSerializer

_UNITS = ["call", "session", "trace"]
_REASONS = ["example", "cited", "recent"]
# Issues a status read takes at once: one page of the issue list.
STATUS_ISSUES_MAX = 100


class AgentFixIssueRefSerializer(serializers.Serializer):
    """An Error Feed issue as its URL names it: its id in its project."""

    cluster_id = serializers.CharField(max_length=20)
    project_id = serializers.UUIDField()


class AgentFixIssuePlanQuerySerializer(AgentFixIssueRefSerializer):
    # Left out, the environment the project's last Run Epoch from an issue used.
    environment_id = serializers.UUIDField(required=False)


class AgentFixIssueStatusQuerySerializer(serializers.Serializer):
    # Left out, the ids are read in every project the caller may read.
    project_id = serializers.UUIDField(required=False)
    cluster_ids = serializers.CharField(
        help_text="The issues' ids, comma-separated: one page of the issue list."
    )

    def validate_cluster_ids(self, value):
        ids = [one.strip() for one in value.split(",") if one.strip()]
        if not ids or len(ids) > STATUS_ISSUES_MAX:
            raise serializers.ValidationError(
                f"Name between 1 and {STATUS_ISSUES_MAX} issues."
            )
        return list(dict.fromkeys(ids))


class AgentFixIssueCreateSerializer(AgentFixIssueRefSerializer):
    environment_id = serializers.UUIDField()
    member_ids = serializers.ListField(
        child=serializers.CharField(max_length=64),
        min_length=1,
        max_length=MAX_SOURCES,
        help_text="The plan's picks after any swap: each pick's member_id.",
    )


class AgentFixIssueRunSerializer(AgentFixLaunchSerializer):
    """Run Epoch after the review: the drafted scenarios kept, and the launch form's
    settings. The baseline takes the place of the launch form's run and scenarios."""

    REPLACED_BY_BASELINE = (
        "source_run_id",
        "target_scenario_keys",
        "protected_scenario_keys",
    )

    case_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=MAX_SOURCES,
        help_text="The import's cases to keep: drafted ones, and repeats of scenarios"
        " the suite already holds.",
    )

    def get_fields(self):
        fields = super().get_fields()
        for name in self.REPLACED_BY_BASELINE:
            del fields[name]
        return fields


class AgentFixIssueSamplePickSerializer(serializers.Serializer):
    member_id = serializers.CharField()
    unit = serializers.ChoiceField(choices=_UNITS)
    source_id = serializers.CharField()
    reason = serializers.ChoiceField(choices=_REASONS)
    started_at = serializers.CharField(allow_null=True)


class AgentFixIssueSampleSerializer(serializers.Serializer):
    unit = serializers.ChoiceField(choices=_UNITS, allow_null=True)
    picks = AgentFixIssueSamplePickSerializer(many=True)
    alternates = AgentFixIssueSamplePickSerializer(many=True)


class AgentFixIssueSerializer(serializers.Serializer):
    cluster_id = serializers.CharField()
    project_id = serializers.UUIDField()
    title = serializers.CharField(allow_null=True)


class AgentFixIssueEnvironmentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class AgentFixIssueScenariosSerializer(serializers.Serializer):
    """The environment's scenarios from Error Feed issues, against its limit."""

    held = serializers.IntegerField()
    limit = serializers.IntegerField()


class AgentFixIssueDefaultsSerializer(serializers.Serializer):
    surfaces = AgentFixLaunchSurfaceSerializer(many=True)
    budget_credits = serializers.IntegerField()
    budget_credits_max = serializers.IntegerField()
    max_minutes_max = serializers.IntegerField()
    reply_latency_margin_pct = serializers.IntegerField()
    cost_margin_pct = serializers.IntegerField()


class AgentFixIssuePlanSerializer(serializers.Serializer):
    """What Run Epoch on an issue shows before anything is drafted. No model is called
    to make it."""

    issue = AgentFixIssueSerializer()
    environment = AgentFixIssueEnvironmentSerializer(allow_null=True)
    # Why the environment cannot take an epoch from an issue now.
    blocked = AgentFixFailureSerializer(allow_null=True)
    sample = AgentFixIssueSampleSerializer()
    # The issue's epoch in progress, to open instead of starting another.
    open_job_id = serializers.UUIDField(allow_null=True)
    issue_scenarios = AgentFixIssueScenariosSerializer(allow_null=True)
    defaults = AgentFixIssueDefaultsSerializer()


class AgentFixIssueBaselineSerializer(serializers.Serializer):
    # The run's test execution, as every run page names it.
    run_id = serializers.UUIDField(source="test_execution_id")
    state = serializers.CharField()
    scenarios = serializers.IntegerField()
    completed_count = serializers.IntegerField()
    failed_count = serializers.IntegerField()


class AgentFixIssueEpochSerializer(serializers.Serializer):
    """The job's newest epoch, as the issue shows it; its page says the rest."""

    id = serializers.UUIDField()
    number = serializers.IntegerField(allow_null=True)
    environment_id = serializers.UUIDField()
    state = serializers.CharField()
    step = serializers.ChoiceField(
        choices=[one.value for one in FixStep], allow_null=True
    )
    failure_code = serializers.CharField(allow_null=True)
    retryable = serializers.BooleanField()
    limited_validation = serializers.BooleanField()
    # The winner's pull request, once it was opened.
    pull_request_url = serializers.CharField(allow_null=True)


class AgentFixIssueJobSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    phase = serializers.ChoiceField(choices=[one.value for one in IssueJobPhase])
    # The issue as the feed shows it now; null once no live issue is left.
    issue = AgentFixIssueSerializer(allow_null=True)
    environment = AgentFixIssueEnvironmentSerializer()
    sample = AgentFixIssueSamplePickSerializer(many=True)
    # Why the job ended before an epoch started.
    ending = AgentFixFailureSerializer(allow_null=True)
    baseline = AgentFixIssueBaselineSerializer(allow_null=True)
    epoch = AgentFixIssueEpochSerializer(allow_null=True)
    created_at = serializers.DateTimeField()


class AgentFixIssueJobDetailSerializer(AgentFixIssueJobSerializer):
    # The drafts to review, as an environment's import review shows them.
    evidence_import = EvidenceImportSerializer()
    # What Run Epoch's form starts from, as the plan gave it.
    defaults = AgentFixIssueDefaultsSerializer()


class AgentFixIssueStatusSerializer(serializers.Serializer):
    cluster_id = serializers.CharField()
    project_id = serializers.UUIDField()
    job = AgentFixIssueJobSerializer()


class AgentFixIssueStatusListSerializer(serializers.Serializer):
    """Each named issue's newest job; an issue with none is left out."""

    results = AgentFixIssueStatusSerializer(many=True)
