"""One shared trace projection, with bounded identity reads and typed results."""
from datetime import timedelta

from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer
from tracer.serializers.filters import BoundedFilterListField
from tracer.services.clickhouse.query_builders.filters import is_skill_filter


class TraceSkillsRequest(StrictInputSerializer):
    project_id = serializers.UUIDField()
    projection = serializers.ChoiceField(choices=("page", "detail", "cohort"))
    trace_ids = serializers.ListField(child=serializers.CharField(max_length=256), max_length=100, min_length=1, required=False)
    trace_id = serializers.CharField(max_length=256, required=False)
    filters = BoundedFilterListField(required=False, default=list)
    to = serializers.DateTimeField(required=False)
    cursor = serializers.CharField(max_length=2048, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=100)

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = serializers.DateTimeField(required=False)
        return fields

    def validate(self, attrs):
        mode = attrs["projection"]
        if mode == "page" and (not attrs.get("trace_ids") or attrs.get("trace_id")):
            raise serializers.ValidationError("Supply trace_ids for a page projection.")
        if mode == "detail" and (not attrs.get("trace_id") or attrs.get("trace_ids")):
            raise serializers.ValidationError("Supply trace_id for a detail projection.")
        if mode == "cohort":
            start, end = attrs.get("from"), attrs.get("to")
            # Observe's date picker includes the remainder of today and supports
            # 90-day windows. A future end-of-day bound is not invalid telemetry.
            if not start or not end or start >= end or end - start > timedelta(days=90):
                raise serializers.ValidationError("Choose a nonempty window of at most 90 days.")
            if not any(is_skill_filter(item) for item in attrs["filters"]):
                raise serializers.ValidationError("A cohort requires a SKILL filter.")
            if attrs.get("trace_id") or attrs.get("trace_ids") or attrs.get("cursor"):
                raise serializers.ValidationError("Cohort reads do not accept trace ids or cursors.")
        elif attrs.get("from") or attrs.get("to") or attrs["filters"]:
            raise serializers.ValidationError("Identity reads cover the whole trace, without a window or filters.")
        return attrs


class TraceSkill(serializers.Serializer):
    skill_id = serializers.UUIDField(allow_null=True)
    name = serializers.CharField()
    version_id = serializers.UUIDField(allow_null=True)
    version = serializers.IntegerField(allow_null=True)
    requested_label = serializers.CharField(allow_blank=True)
    requested_version = serializers.IntegerField(allow_null=True, required=False)
    agent = serializers.CharField(allow_blank=True)
    agent_key = serializers.ListField(child=serializers.CharField(allow_blank=True))
    evidence = serializers.CharField(allow_blank=True)
    digest_status = serializers.ChoiceField(choices=("verified", "mismatch"))
    loads = serializers.IntegerField()
    span_ids = serializers.ListField(child=serializers.CharField())


class FailedSkillLoad(serializers.Serializer):
    skill_name = serializers.CharField(allow_blank=True)
    error_type = serializers.CharField(allow_blank=True)
    span_id = serializers.CharField()


class TraceContext(serializers.Serializer):
    context_id = serializers.UUIDField()
    name = serializers.CharField()
    version = serializers.IntegerField()


class TraceScopeCoverage(serializers.Serializer):
    spans = serializers.IntegerField()
    stamped_spans = serializers.IntegerField()
    rate = serializers.FloatField(allow_null=True)
    span_ids_before_first_load = serializers.ListField(child=serializers.CharField())


class TraceSkillEntry(serializers.Serializer):
    skills = TraceSkill(many=True)
    failed = FailedSkillLoad(many=True)
    more = serializers.IntegerField()


class FailedRequests(serializers.Serializer):
    count = serializers.IntegerField()
    rate = serializers.FloatField(allow_null=True)


class CohortLatency(serializers.Serializer):
    p95 = serializers.FloatField(allow_null=True)


class CohortCost(serializers.Serializer):
    per_trace_mean = serializers.FloatField(allow_null=True)


class TraceCohortSummary(serializers.Serializer):
    traces = serializers.IntegerField()
    incomplete = serializers.IntegerField()
    failed_requests = FailedRequests()
    latency_ms = CohortLatency()
    cost = CohortCost()


class TraceSkillsResult(serializers.Serializer):
    traces = serializers.DictField(child=TraceSkillEntry(), required=False)
    skills = TraceSkill(many=True, required=False)
    failed = FailedSkillLoad(many=True, required=False)
    contexts = TraceContext(many=True, required=False)
    linked_not_loaded = serializers.ListField(child=serializers.CharField(), required=False)
    coverage = TraceScopeCoverage(required=False)
    summary = TraceCohortSummary(required=False)
    next_cursor = serializers.CharField(allow_null=True)
    truncated = serializers.BooleanField()
