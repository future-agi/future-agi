from rest_framework import serializers

from tracer.serializers.filters import StrictInputSerializer
from tracer.services.trace_investigation import (
    InvestigationControlError,
    canonical_wire_result_digest,
)


class TraceAvailableItemSerializer(StrictInputSerializer):
    trace_id = serializers.UUIDField()
    root_span_id = serializers.CharField(max_length=64)
    root_end_time = serializers.DateTimeField()


class TraceAvailableEventSerializer(StrictInputSerializer):
    version = serializers.IntegerField(min_value=1, max_value=1)
    event_id = serializers.UUIDField()
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(allow_null=True)
    project_id = serializers.UUIDField()
    event_kind = serializers.ChoiceField(choices=("root_span_written",))
    traces = TraceAvailableItemSerializer(many=True, min_length=1, max_length=100)
    emitted_at = serializers.DateTimeField()


class TraceAvailableDeliverySerializer(StrictInputSerializer):
    topic = serializers.ChoiceField(choices=("error-feed.trace-available.v1",))
    partition = serializers.IntegerField(min_value=0)
    offset = serializers.IntegerField(min_value=0)
    value = TraceAvailableEventSerializer()


class RecordTraceNotificationsRequestSerializer(StrictInputSerializer):
    deliveries = TraceAvailableDeliverySerializer(
        many=True, min_length=1, max_length=100
    )


class PendingInvestigationSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(allow_null=True)
    project_id = serializers.UUIDField()
    trace_id = serializers.UUIDField()
    job_id = serializers.UUIDField()
    generation = serializers.IntegerField(min_value=1)
    state = serializers.CharField()
    not_before = serializers.DateTimeField()


class RecordTraceNotificationsResponseSerializer(serializers.Serializer):
    accepted_events = serializers.IntegerField(min_value=0)
    duplicate_events = serializers.IntegerField(min_value=0)
    pending = PendingInvestigationSerializer(many=True)


class ClaimInvestigationsRequestSerializer(StrictInputSerializer):
    worker_id = serializers.CharField(max_length=255)
    engine_version = serializers.CharField(max_length=20)
    limit = serializers.IntegerField(min_value=1, max_value=50)


class InvestigationMemoryEntrySerializer(serializers.Serializer):
    id = serializers.CharField(max_length=128)
    text = serializers.CharField(max_length=4000)
    source_feedback_id = serializers.CharField(
        max_length=128, required=False, allow_null=True
    )


class InvestigationMemorySerializer(serializers.Serializer):
    snapshot_id = serializers.CharField(max_length=128)
    digest = serializers.CharField(max_length=71)
    entries = InvestigationMemoryEntrySerializer(many=True)


class InvestigationLimitsSerializer(serializers.Serializer):
    deadline_seconds = serializers.IntegerField(min_value=1)
    max_model_calls = serializers.IntegerField(min_value=1)
    max_children = serializers.IntegerField(min_value=0)
    max_parallel_children = serializers.IntegerField(min_value=0)
    max_input_tokens_total = serializers.IntegerField(min_value=1)
    max_output_tokens_total = serializers.IntegerField(min_value=1)
    max_evidence_bytes = serializers.IntegerField(min_value=1)
    max_tool_result_bytes = serializers.IntegerField(min_value=1)


class InvestigationClaimSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(allow_null=True)
    project_id = serializers.UUIDField()
    job_id = serializers.UUIDField()
    trace_id = serializers.UUIDField()
    generation = serializers.IntegerField(min_value=1)
    attempt_id = serializers.UUIDField()
    lease_token = serializers.CharField()
    lease_expires_at = serializers.DateTimeField()
    read_cutoff = serializers.DateTimeField()
    engine_version = serializers.CharField(max_length=20)
    contract_version = serializers.CharField()
    memory = InvestigationMemorySerializer()
    limits = InvestigationLimitsSerializer()
    verification_capabilities = serializers.ListField(child=serializers.CharField())
    feature_enabled = serializers.BooleanField()


class ClaimInvestigationsResponseSerializer(serializers.Serializer):
    claims = InvestigationClaimSerializer(many=True)


class UpdateInvestigationAttemptRequestSerializer(StrictInputSerializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(allow_null=True)
    project_id = serializers.UUIDField()
    job_id = serializers.UUIDField()
    lease_token = serializers.CharField(max_length=255)
    action = serializers.ChoiceField(choices=("renew", "cancel"))
    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=2000, default=""
    )


class UpdateInvestigationAttemptResponseSerializer(serializers.Serializer):
    attempt_id = serializers.UUIDField()
    status = serializers.CharField()
    lease_expires_at = serializers.DateTimeField()
    cancellation_requested = serializers.BooleanField()
    job_state = serializers.CharField()


class FindingAttributionRoleSerializer(StrictInputSerializer):
    status = serializers.ChoiceField(choices=("supported", "unsupported", "unknown"))
    span_id = serializers.CharField(
        max_length=64, required=False, allow_null=True, allow_blank=False
    )
    evidence_ids = serializers.ListField(
        child=serializers.CharField(max_length=128), max_length=100
    )


class FindingAttributionSerializer(StrictInputSerializer):
    origin = FindingAttributionRoleSerializer()
    decisive = FindingAttributionRoleSerializer()
    symptom = FindingAttributionRoleSerializer()


class InvestigationFindingSerializer(StrictInputSerializer):
    finding_id = serializers.CharField(max_length=128)
    kind = serializers.CharField(max_length=64)
    statement = serializers.CharField(max_length=8000)
    requirement_id = serializers.CharField(
        max_length=128, required=False, allow_null=True
    )
    evidence_ids = serializers.ListField(
        child=serializers.CharField(max_length=128), max_length=100
    )
    recovery = serializers.CharField(max_length=64)
    attribution = FindingAttributionSerializer()


class InvestigationRequirementCheckSerializer(StrictInputSerializer):
    requirement_id = serializers.CharField(max_length=128)
    requirement = serializers.CharField(max_length=8000)
    status = serializers.CharField(max_length=64)
    expected = serializers.JSONField(required=False, allow_null=True)
    observed = serializers.JSONField(required=False, allow_null=True)
    evidence_ids = serializers.ListField(
        child=serializers.CharField(max_length=128), max_length=100
    )


class InvestigationEvidenceReceiptSerializer(StrictInputSerializer):
    evidence_id = serializers.CharField(max_length=128)
    span_id = serializers.CharField(max_length=64)
    parent_span_id = serializers.CharField(
        max_length=64, required=False, allow_null=True
    )
    excerpt = serializers.CharField(max_length=8000)
    input = serializers.JSONField(required=False, allow_null=True)
    output = serializers.JSONField(required=False, allow_null=True)
    end_time = serializers.DateTimeField(required=False, allow_null=True)


class InvestigationVerificationReceiptSerializer(StrictInputSerializer):
    receipt_id = serializers.CharField(max_length=128)
    executed = serializers.BooleanField()


class InvestigationCoverageSerializer(StrictInputSerializer):
    scope = serializers.CharField(max_length=255)
    observed_span_count = serializers.IntegerField(min_value=0)
    read_complete = serializers.BooleanField()
    future_arrivals_known = serializers.BooleanField()


class InvestigationUsageSerializer(StrictInputSerializer):
    model_calls = serializers.IntegerField(min_value=0)
    input_tokens = serializers.IntegerField(min_value=0)
    output_tokens = serializers.IntegerField(min_value=0)
    cost_usd = serializers.FloatField(required=False, allow_null=True, min_value=0)
    cost_status = serializers.CharField(max_length=64)


class GatewayAccountingSerializer(StrictInputSerializer):
    request_id = serializers.CharField(max_length=255, required=False, allow_null=True)
    model_used = serializers.CharField(max_length=255)
    cost = serializers.FloatField(min_value=0, allow_null=True)
    input_tokens = serializers.IntegerField(required=False, min_value=0)
    output_tokens = serializers.IntegerField(required=False, min_value=0)
    raw = serializers.JSONField(required=False, allow_null=True)


class InvestigationResultSerializer(StrictInputSerializer):
    contract_version = serializers.ChoiceField(choices=("omega-investigation/v1",))
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(allow_null=True)
    project_id = serializers.UUIDField()
    job_id = serializers.UUIDField()
    generation = serializers.IntegerField(min_value=1)
    attempt_id = serializers.UUIDField()
    trace_id = serializers.UUIDField()
    engine_version = serializers.CharField(max_length=20)
    read_cutoff = serializers.DateTimeField()
    memory_snapshot_id = serializers.CharField(max_length=128)
    memory_digest = serializers.CharField(max_length=71)
    evidence_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    execution_status = serializers.ChoiceField(choices=("completed", "failed"))
    outcome = serializers.ChoiceField(choices=("success", "failure", "unknown"))
    findings = InvestigationFindingSerializer(many=True, max_length=100)
    requirement_checks = InvestigationRequirementCheckSerializer(
        many=True, max_length=100
    )
    evidence_receipts = InvestigationEvidenceReceiptSerializer(
        many=True, max_length=200
    )
    verification_receipts = InvestigationVerificationReceiptSerializer(
        many=True, max_length=100
    )
    coverage = InvestigationCoverageSerializer()
    usage = InvestigationUsageSerializer()
    gateway_accounting = GatewayAccountingSerializer(many=True, max_length=100)
    result_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")


class PublishInvestigationRequestSerializer(StrictInputSerializer):
    idempotency_key = serializers.CharField(max_length=255)
    lease_token = serializers.CharField(max_length=255)
    result = InvestigationResultSerializer()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # Digest the JSON value as received, before UUID and DateTime fields are
        # normalized by DRF. This is the representation the Node spool retains.
        try:
            attrs["wire_result_digest"] = canonical_wire_result_digest(
                self.initial_data["result"]
            )
        except InvestigationControlError as error:
            raise serializers.ValidationError({"result": str(error)}) from error
        return attrs


class PublishInvestigationResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("accepted", "duplicate"))
    report_id = serializers.UUIDField()
    occurrence_ids = serializers.ListField(child=serializers.UUIDField())
    grouping_status = serializers.CharField()
    active_projection_updated = serializers.BooleanField()


class InvestigationControlErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()


class SubmitInvestigationFeedbackRequestSerializer(StrictInputSerializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(allow_null=True)
    project_id = serializers.UUIDField()
    report_id = serializers.UUIDField()
    occurrence_id = serializers.UUIDField()
    finding_id = serializers.CharField(max_length=128)
    idempotency_key = serializers.CharField(max_length=255)
    feedback_type = serializers.ChoiceField(
        choices=(
            "confirm_finding",
            "false_positive",
            "attribution_correction",
            "grouping_merge",
            "grouping_split",
        )
    )
    comment = serializers.CharField(max_length=8000, allow_blank=True)


class SubmitInvestigationFeedbackResponseSerializer(serializers.Serializer):
    feedback_id = serializers.UUIDField()
    review_state = serializers.ChoiceField(choices=("reviewed",))
    learning_event_id = serializers.UUIDField()
    active_memory_unchanged = serializers.BooleanField()


class MemoryCandidateEntrySerializer(StrictInputSerializer):
    id = serializers.CharField(max_length=128)
    text = serializers.CharField(max_length=4000)
    source_feedback_id = serializers.UUIDField(required=False)


class CreateMemoryCandidateRequestSerializer(StrictInputSerializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(allow_null=True)
    project_id = serializers.UUIDField()
    expected_parent_snapshot_id = serializers.UUIDField()
    idempotency_key = serializers.CharField(max_length=255)
    source_feedback_ids = serializers.ListField(
        child=serializers.UUIDField(), max_length=100
    )
    entries = MemoryCandidateEntrySerializer(many=True, max_length=20)


class CreateMemoryCandidateResponseSerializer(serializers.Serializer):
    candidate_id = serializers.UUIDField()
    candidate_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    parent_snapshot_id = serializers.UUIDField()
    status = serializers.ChoiceField(
        choices=("candidate", "active", "superseded", "rejected")
    )
    active_memory_unchanged = serializers.BooleanField()


class MemoryEvaluationMetricsSerializer(StrictInputSerializer):
    sample_count = serializers.IntegerField(min_value=1, max_value=100000)
    precision = serializers.FloatField(min_value=0, max_value=1, allow_null=True)
    recall = serializers.FloatField(min_value=0, max_value=1, allow_null=True)
    unknown_rate = serializers.FloatField(min_value=0, max_value=1, allow_null=True)
    cost_usd = serializers.FloatField(min_value=0, allow_null=True)
    latency_ms = serializers.FloatField(min_value=0, allow_null=True)


class RecordMemoryEvaluationRequestSerializer(StrictInputSerializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(allow_null=True)
    project_id = serializers.UUIDField()
    candidate_id = serializers.UUIDField()
    candidate_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    idempotency_key = serializers.CharField(max_length=255)
    cohort_id = serializers.CharField(max_length=255)
    metrics = MemoryEvaluationMetricsSerializer()
    passed = serializers.BooleanField()
    holdout_disjoint = serializers.BooleanField()


class RecordMemoryEvaluationResponseSerializer(serializers.Serializer):
    evaluation_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=("passed", "failed"))
    candidate_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    promotion_allowed = serializers.BooleanField()


class ChangeActiveMemoryRequestSerializer(StrictInputSerializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField(allow_null=True)
    project_id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=("promote", "rollback"))
    idempotency_key = serializers.CharField(max_length=255)
    expected_current_snapshot_id = serializers.UUIDField()
    target_snapshot_id = serializers.UUIDField()
    target_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    evaluation_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["action"] == "promote" and not attrs.get("evaluation_id"):
            raise serializers.ValidationError(
                {"evaluation_id": "This field is required for promotion."}
            )
        if attrs["action"] == "rollback" and attrs.get("evaluation_id"):
            raise serializers.ValidationError(
                {"evaluation_id": "This field is not allowed for rollback."}
            )
        return attrs


class ChangeActiveMemoryResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("promote", "rollback"))
    change_id = serializers.UUIDField()
    active_snapshot_id = serializers.UUIDField()
    active_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    previous_snapshot_id = serializers.UUIDField()
    next_attempt_uses = serializers.UUIDField()
