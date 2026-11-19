"""
Serializers for Agent Version Diff and Changelog endpoints.
"""

from rest_framework import serializers


class PromptChunkSerializer(serializers.Serializer):
    """Structured line item in prompt textual diff."""

    type = serializers.ChoiceField(
        choices=["unchanged", "added", "removed"],
        help_text="Diff chunk line type",
    )
    line = serializers.CharField(help_text="Content of the line")


class PromptDiffSerializer(serializers.Serializer):
    """Prompt difference representation."""

    has_changes = serializers.BooleanField(help_text="True if any prompt differences exist")
    added_lines = serializers.IntegerField(help_text="Total lines added")
    removed_lines = serializers.IntegerField(help_text="Total lines removed")
    unified_diff = serializers.CharField(help_text="Standard unified diff representation")
    chunks = PromptChunkSerializer(many=True, help_text="Structured line-by-line diff chunks")


class ParameterDiffSerializer(serializers.Serializer):
    """Configuration parameter difference."""

    key = serializers.CharField(help_text="Configuration attribute name")
    change_type = serializers.ChoiceField(
        choices=["added", "removed", "modified"],
        help_text="Type of change",
    )
    base_value = serializers.JSONField(
        allow_null=True, required=False, help_text="Value in base version"
    )
    target_value = serializers.JSONField(
        allow_null=True, required=False, help_text="Value in target version"
    )


class MetricDeltaItemSerializer(serializers.Serializer):
    """Single metric delta comparison."""

    base = serializers.FloatField(allow_null=True, help_text="Value in base version")
    target = serializers.FloatField(allow_null=True, help_text="Value in target version")
    delta = serializers.FloatField(allow_null=True, help_text="Difference (target - base)")


class TemplateDeltaSerializer(serializers.Serializer):
    """Evaluation template metric delta comparison."""

    template_id = serializers.CharField(help_text="Evaluation template ID")
    template_name = serializers.CharField(help_text="Evaluation template name")
    base_average_score = serializers.FloatField(allow_null=True, help_text="Base average score")
    target_average_score = serializers.FloatField(allow_null=True, help_text="Target average score")
    score_delta = serializers.FloatField(allow_null=True, help_text="Score delta")


class PerformanceDeltaSerializer(serializers.Serializer):
    """Overall and per-template performance metric deltas."""

    score = MetricDeltaItemSerializer(help_text="Overall score comparison")
    pass_rate = MetricDeltaItemSerializer(help_text="Pass rate comparison")
    test_count = serializers.DictField(help_text="Test count comparison")
    template_deltas = TemplateDeltaSerializer(
        many=True, required=False, help_text="Per-template metric score deltas"
    )


class VersionMetaSerializer(serializers.Serializer):
    """Version metadata in diff comparison."""

    id = serializers.CharField(help_text="Version UUID")
    version_number = serializers.IntegerField(help_text="Version number")
    version_name = serializers.CharField(help_text="Display version name")
    status = serializers.CharField(help_text="Version status (active, draft, etc.)")
    score = serializers.FloatField(allow_null=True, help_text="Overall score")
    pass_rate = serializers.FloatField(allow_null=True, help_text="Pass rate percentage")
    test_count = serializers.IntegerField(help_text="Number of tests run")
    commit_message = serializers.CharField(allow_null=True, help_text="Commit message")
    created_at = serializers.CharField(allow_null=True, help_text="ISO timestamp")


class AgentVersionDiffResponseSerializer(serializers.Serializer):
    """Full response serializer for agent version diff comparison."""

    base_version = VersionMetaSerializer(help_text="Base version metadata")
    target_version = VersionMetaSerializer(help_text="Target version metadata")
    prompt_diff = PromptDiffSerializer(help_text="Prompt text difference")
    parameter_diffs = ParameterDiffSerializer(
        many=True, help_text="Configuration parameter changes"
    )
    performance_delta = PerformanceDeltaSerializer(help_text="Performance metric deltas")
    summary = serializers.ListField(
        child=serializers.CharField(),
        help_text="High-level human-readable change bullet points",
    )


class AgentChangelogItemSerializer(serializers.Serializer):
    """Single item in the chronological changelog timeline."""

    id = serializers.CharField(help_text="Version UUID")
    version_number = serializers.IntegerField(help_text="Version number")
    version_name = serializers.CharField(help_text="Display version name")
    status = serializers.CharField(help_text="Version status")
    commit_message = serializers.CharField(allow_null=True, help_text="Commit message")
    release_notes = serializers.CharField(allow_null=True, help_text="Release notes")
    score = serializers.FloatField(allow_null=True, help_text="Performance score")
    pass_rate = serializers.FloatField(allow_null=True, help_text="Test pass rate")
    test_count = serializers.IntegerField(help_text="Test count")
    changes_summary = serializers.ListField(
        child=serializers.CharField(), help_text="Summary of changes from prior version"
    )
    created_at = serializers.CharField(allow_null=True, help_text="ISO timestamp")
    is_active = serializers.BooleanField(help_text="True if currently active version")
    is_latest = serializers.BooleanField(help_text="True if latest version")
