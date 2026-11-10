"""HTTP contracts for retained intake, trace capture and human evidence review."""

from rest_framework import serializers as s

from model_hub.serializers import skills_hub as legacy
from model_hub.serializers import skills_hub_v11 as v11
from model_hub.serializers import skills_hub_v12 as v12
from model_hub.serializers.skills_hub_r3 import HumanAnnotation, RetainedPlanAcceptance


class IntakePreviewInput(legacy.ImportPreviewRequest):
    source_identities = s.JSONField(required=False)

    def validate_source_identities(self, value):
        if not isinstance(value, dict) or len(value) > 50:
            raise s.ValidationError(
                "Supply at most 50 source identities keyed by package."
            )
        for source in value.values():
            serializer = IntakeSource(data=source)
            serializer.is_valid(raise_exception=True)
        return value


class IntakeSource(v12.ContextSerializer):
    origin = s.CharField(max_length=255)
    project = s.CharField(max_length=200)
    resource = s.CharField(max_length=200)
    revision = s.CharField(max_length=200)
    digest = v12.Digest()


class IntakeFile(v12.ContextSerializer):
    path = s.CharField()
    sha256 = s.RegexField(r"^[a-f0-9]{64}$")
    size_bytes = v11.Integer(min_value=0)


class IntakeFinding(legacy.Rejected):
    def get_fields(self):
        return {**super().get_fields(), "source": s.CharField(required=False)}


class IntakeComparisonFile(v12.ContextSerializer):
    path = s.CharField()
    base_sha256 = s.CharField(allow_null=True)
    source_sha256 = s.CharField(allow_null=True)
    hub_sha256 = s.CharField(allow_null=True)


class IntakeComparison(v12.ContextSerializer):
    basis_receipt_id = s.UUIDField(allow_null=True)
    basis_revision = s.CharField(allow_null=True)
    source_changed = v11.Boolean()
    hub_diverged = v11.Boolean()
    files = IntakeComparisonFile(many=True)


class IntakeItem(v12.ContextSerializer):
    key = s.CharField()
    root = s.CharField(allow_blank=True)  # type: ignore[assignment]
    filename = s.CharField()
    name = s.CharField()
    kind = s.ChoiceField(choices=["skill"])
    files = IntakeFile(many=True)
    checks = legacy.ChecksResult()
    omissions = legacy.ImportOmission(many=True)
    upload_source = s.CharField()
    blockers = IntakeFinding(many=True)
    destination_id = s.UUIDField(allow_null=True)
    expected_head_version = v11.Integer(allow_null=True)
    expected_head_digest = v12.Digest(allow_null=True)
    metadata_revision = v11.Integer()
    security_revision = v11.Integer()
    tags_digest = v12.Digest()

    def get_fields(self):
        return {**super().get_fields(), "source": IntakeSource(allow_null=True)}

    source_manifest = IntakeFile(many=True)
    migration = IntakeComparison()
    manual_reconciliation = v11.Boolean(required=False)
    parent_plan_id = s.UUIDField(required=False)


class IntakeInventory(v12.ContextSerializer):
    schema = v11.Integer(min_value=1, max_value=1)
    items = IntakeItem(many=True)
    rejected = IntakeFinding(many=True)
    skipped = s.ListField(child=s.CharField())


class IntakePlan(v12.ContextSerializer):
    id = s.UUIDField()
    kind = s.ChoiceField(choices=["skill_import"])
    fingerprint = v12.Digest()
    inventory = IntakeInventory()
    expires_at = s.DateTimeField()


class IntakeDecision(RetainedPlanAcceptance):
    def get_fields(self):
        fields = super().get_fields()
        fields.pop("plan_id")
        return fields


class IntakeReconcileInput(v12.ContextSerializer):
    item_key = s.CharField(max_length=255)
    expected_fingerprint = v12.Digest()
    files: s.ListSerializer = s.ListSerializer(child=legacy.FileInput(), max_length=100)


class IntakeResult(v12.ContextSerializer):
    resource_id = s.UUIDField()
    kind = s.ChoiceField(choices=["skill", "agent_context"])
    decision = s.ChoiceField(choices=["keep", "replace", "manual"])
    published = v11.Boolean()
    draft_revision = v11.Integer(allow_null=True)
    digest = v12.Digest(allow_null=True)


class IntakeReceipt(v12.ContextSerializer):
    item_key = s.CharField()
    result = IntakeResult()


class IntakeReceiptPage(legacy.Page):
    results = IntakeReceipt(many=True)


class TraceReference(v12.ContextSerializer):
    project_id = s.UUIDField()
    trace_id = s.CharField(max_length=200, trim_whitespace=False)
    span_id = s.CharField(max_length=200, trim_whitespace=False)
    start_time = s.DateTimeField()


class CaptureSourceInput(v12.ContextSerializer):
    trace_ref = TraceReference()


class CapturePreviewInput(CaptureSourceInput):
    selected_segment_ids = s.ListField(
        child=s.CharField(max_length=255), min_length=1, max_length=200
    )


class CaptureSegment(v12.ContextSerializer):
    id = s.CharField()
    kind = s.ChoiceField(choices=["request", "tool_result"])
    text = s.CharField(trim_whitespace=False)


class CaptureSource(v12.ContextSerializer):
    trace_ref = TraceReference()
    source_fingerprint = v12.Digest()
    segments = CaptureSegment(many=True)


class RecordedContext(v12.ContextSerializer):
    version_id = s.UUIDField()
    digest = v12.Digest()


class CaptureOmission(v12.ContextSerializer):
    reason = s.CharField()


class CapturePreview(v12.ContextSerializer):
    capture_id = s.UUIDField()
    expires_at = s.DateTimeField()
    source_fingerprint = v12.Digest()
    recorded_context = RecordedContext(allow_null=True)
    segments = CaptureSegment(many=True)
    omitted = CaptureOmission(many=True)
    outbound_findings = v11.OutboundFinding(many=True)
    replay_scope = s.ChoiceField(choices=["text_only"])


class CaptureAcceptance(v12.ContextSerializer):
    source_fingerprint = v12.Digest()
    suite_revision = v11.Integer(min_value=0)
    case = v12.ContextCaseInput()
    acknowledge_workspace_sharing = v11.Boolean()


class CaptureSuite(v11.SuiteOut):
    cases = v12.ContextCaseOut(many=True)


class AnnotationInput(HumanAnnotation):
    def get_fields(self):
        fields = super().get_fields()
        fields.pop("source")
        fields.pop("source_id")
        return fields


class Annotation(v12.ContextSerializer):
    id = s.UUIDField()
    revision = v11.Integer(min_value=1)
    actor_id = s.UUIDField()
    check_id = s.UUIDField(allow_null=True)
    attempt_id = s.UUIDField(allow_null=True)
    verdict = s.ChoiceField(choices=["agree", "disagree", "cannot_assess"])
    cited_text = s.CharField(allow_blank=True, trim_whitespace=False)
    comment = s.CharField(allow_blank=True, trim_whitespace=False)
    created_at = s.DateTimeField()


class AnnotationPage(legacy.Page):
    results = Annotation(many=True)
