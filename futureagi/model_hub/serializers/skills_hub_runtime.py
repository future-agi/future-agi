"""Shared runtime load projections for skills and agent contexts."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers


class RuntimeWindowQuery(serializers.Serializer):
    to = serializers.DateTimeField()
    cursor = serializers.CharField(required=False, max_length=2048)
    limit = serializers.IntegerField(default=100, min_value=1, max_value=100)

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = serializers.DateTimeField()
        return fields

    def validate(self, attrs):
        start, end = attrs["from"], attrs["to"]
        if start >= end or end - start > timedelta(days=30) or end > timezone.now():
            raise serializers.ValidationError(
                "Choose a nonempty window of at most 30 days ending now or earlier."
            )
        return attrs


class RuntimeFilterValuesQuery(RuntimeWindowQuery):
    project_id = serializers.UUIDField()
    field = serializers.ChoiceField(
        choices=(
            "skill.name",
            "skill.version_id",
            "skill.agent",
            "skill.service",
            "skill.requested_label",
            "skill.context_id",
        )
    )
    skill_id = serializers.UUIDField(required=False)
    load_result = serializers.ChoiceField(
        choices=("loaded", "failed"), default="loaded"
    )
    q = serializers.CharField(
        required=False, allow_blank=True, max_length=200, default=""
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["load_result"] == "failed" and attrs["field"] == "skill.version_id":
            raise serializers.ValidationError("Failed loads have no resolved version.")
        return attrs


class RuntimeFilterValue(serializers.Serializer):
    value = serializers.CharField(allow_blank=True)
    label = serializers.CharField(allow_blank=True)
    skill_id = serializers.UUIDField(required=False)
    name = serializers.CharField(required=False)
    version_id = serializers.UUIDField(required=False)
    version = serializers.IntegerField(required=False)
    skill_name = serializers.CharField(required=False)
    labels_now = serializers.ListField(child=serializers.CharField(), required=False)
    production_version = serializers.IntegerField(required=False)


class RuntimeFilterValuesResult(serializers.Serializer):
    values = RuntimeFilterValue(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    truncated = serializers.BooleanField()


class RuntimeUsageQuery(RuntimeWindowQuery):
    resource_id = serializers.UUIDField(required=False)
    kind = serializers.ChoiceField(choices=("skill", "agent_context"), default="skill")
    group_by = serializers.ChoiceField(
        choices=("skill", "version", "agent", "context"), default="version"
    )
    project_ids = serializers.CharField(required=False, max_length=1900)
    project_id = serializers.UUIDField(required=False)
    version_ids = serializers.CharField(required=False, max_length=7400)
    skill_ids = serializers.CharField(required=False, max_length=7400)
    context_version_id = serializers.UUIDField(required=False)
    label = serializers.CharField(required=False, max_length=40)
    service = serializers.CharField(required=False, allow_blank=True, max_length=256)
    agent = serializers.CharField(required=False, allow_blank=True, max_length=256)
    projection = serializers.ChoiceField(choices=("table", "series"), default="table")
    metric = serializers.RegexField(
        r"^(?:traces|failed_requests|latency_p95|cost_per_trace|eval:[0-9a-f-]{36})$",
        default="failed_requests",
        max_length=50,
    )
    bucket = serializers.ChoiceField(choices=("hour", "day"), default="day")

    def validate(self, attrs):
        from uuid import UUID

        attrs = super().validate(attrs)
        for name in ("project_ids", "version_ids", "skill_ids"):
            if name in attrs:
                try:
                    attrs[name] = sorted(
                        {str(UUID(value)) for value in attrs[name].split(",")}
                    )
                except ValueError as exc:
                    raise serializers.ValidationError(
                        {name: "Supply comma-separated UUIDs."}
                    ) from exc
        if (
            any(key in attrs for key in ("service", "agent"))
            and "project_id" not in attrs
        ):
            raise serializers.ValidationError("An agent requires one project.")
        if attrs["kind"] == "agent_context" and "resource_id" not in attrs:
            raise serializers.ValidationError("Choose a context.")
        if "context_version_id" in attrs and attrs["kind"] != "agent_context":
            raise serializers.ValidationError(
                "A context version requires a context resource."
            )
        return attrs


class RuntimeComparisonQuery(serializers.Serializer):
    resource_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=("skill", "agent_context"), default="skill")
    event_id = serializers.UUIDField(required=False)
    label = serializers.CharField(default="production", max_length=40)
    window = serializers.RegexField(
        r"^(?:[1-9][0-9]*)(?:h|d)$", default="24h", max_length=5
    )
    project_ids = serializers.CharField(required=False, max_length=1900)
    as_of = serializers.DateTimeField(required=False)
    before_from = serializers.DateTimeField(required=False)
    before_to = serializers.DateTimeField(required=False)
    after_from = serializers.DateTimeField(required=False)
    after_to = serializers.DateTimeField(required=False)
    cursor = serializers.CharField(required=False, max_length=2048)
    limit = serializers.IntegerField(default=100, min_value=1, max_value=100)

    def validate(self, attrs):
        from uuid import UUID

        if attrs.get("as_of", timezone.now()) > timezone.now():
            raise serializers.ValidationError("The comparison must end now or earlier.")
        window = attrs["window"]
        hours = int(window[:-1]) * (24 if window[-1] == "d" else 1)
        if not 1 <= hours <= 168:
            raise serializers.ValidationError("Choose one hour to seven days.")
        fields = ("before_from", "before_to", "after_from", "after_to")
        if any(key in attrs for key in fields):
            if not all(key in attrs for key in fields) or "event_id" in attrs:
                raise serializers.ValidationError(
                    "Supply a label event OR both complete windows."
                )
            for prefix in ("before", "after"):
                start, end = attrs[f"{prefix}_from"], attrs[f"{prefix}_to"]
                if (
                    start >= end
                    or end - start > timedelta(days=7)
                    or end > timezone.now()
                ):
                    raise serializers.ValidationError(
                        "Each window must be nonempty, at most seven days, and in the past."
                    )
            if attrs["before_to"] > attrs["after_from"]:
                raise serializers.ValidationError(
                    "Comparison windows must not overlap."
                )
        if "project_ids" in attrs:
            try:
                attrs["project_ids"] = sorted(
                    {str(UUID(value)) for value in attrs["project_ids"].split(",")}
                )
            except ValueError as exc:
                raise serializers.ValidationError("Supply project UUIDs.") from exc
        return attrs


class RuntimeUsageResult(serializers.Serializer):
    rows = serializers.ListField(child=serializers.DictField())
    summary = serializers.DictField()
    coverage = serializers.DictField()
    diagnosis = serializers.DictField()
    failed_loads = serializers.DictField()
    mismatch = serializers.DictField(allow_null=True)
    move = serializers.DictField(allow_null=True)
    next_cursor = serializers.CharField(allow_null=True)
    totals_note = serializers.CharField()
    truncated = serializers.BooleanField()
    agent = serializers.DictField(required=False, allow_null=True)
    contexts = serializers.ListField(child=serializers.DictField(), required=False)
    links_summary = serializers.ListField(child=serializers.DictField(), required=False)
    linked_not_loaded = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    loaded_not_linked = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    series = serializers.ListField(child=serializers.DictField(), required=False)
    markers = serializers.ListField(child=serializers.DictField(), required=False)


class RuntimeComparisonResult(serializers.Serializer):
    event_id = serializers.UUIDField(allow_null=True)
    label = serializers.CharField()
    from_version = serializers.IntegerField(allow_null=True)
    to_version = serializers.IntegerField(allow_null=True)
    from_version_id = serializers.UUIDField(allow_null=True)
    to_version_id = serializers.UUIDField(allow_null=True)
    moved_at = serializers.DateTimeField(allow_null=True)
    window = serializers.CharField()
    before_cut_at = serializers.DateTimeField(allow_null=True)
    ends_at = serializers.DateTimeField(allow_null=True)
    complete = serializers.BooleanField()
    verdict = serializers.ChoiceField(
        choices=(
            "watching",
            "worse_so_far",
            "worse",
            "better",
            "no_change",
            "not_enough_data",
        )
    )
    trigger = serializers.DictField(allow_null=True)
    so_far = serializers.BooleanField()
    collecting = serializers.DictField(allow_null=True)
    same_agents = serializers.DictField()
    all_agents = serializers.DictField()
    control = serializers.DictField(allow_null=True)
    by_agent = serializers.ListField(child=serializers.DictField())
    adoption = serializers.ListField(child=serializers.DictField())
    changed_skills = serializers.ListField(child=serializers.DictField())
    links = serializers.DictField()
    primary_action = serializers.CharField(allow_null=True)
    next_cursor = serializers.CharField(allow_null=True)
    truncated = serializers.BooleanField()
    totals_note = serializers.CharField()


class LatestLoadQuery(serializers.Serializer):
    resource_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=("skill", "agent_context"))
    since = serializers.DateTimeField()


class LoadAgent(serializers.Serializer):
    key = serializers.ListField(child=serializers.CharField(allow_blank=True))
    name = serializers.CharField()
    service = serializers.CharField(allow_blank=True)
    project_id = serializers.UUIDField()
    project_name = serializers.CharField()
    name_source = serializers.ChoiceField(choices=("agent", "service", "project"))


class LoadProject(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class LatestLoad(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    agent = LoadAgent()
    project = LoadProject()
    received_at = serializers.DateTimeField()


class LatestFetch(serializers.Serializer):
    sdk_version = serializers.CharField(allow_null=True)
    tracing_mode = serializers.CharField(allow_null=True)
    can_record = serializers.BooleanField()
    client = serializers.CharField(allow_blank=True)


class LatestLoadResult(serializers.Serializer):
    load = LatestLoad(allow_null=True)
    fetch = LatestFetch(allow_null=True)
