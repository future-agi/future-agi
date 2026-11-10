"""The evidence import contract: Observe sessions or calls frozen for an
environment, and the bundle a person reviews before a test is drafted from them."""

from rest_framework import serializers

from simulate.models import EvidenceImport, EvidenceImportCase
from simulate.selectors.evidence_import import MAX_SOURCES

# unreadable: recorded, but could not be read; missing: not recorded.
PART_STATUSES = ("present", "partial", "truncated", "missing", "unreadable")


class EvidenceImportCreateSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    source_kind = serializers.ChoiceField(choices=EvidenceImport.SourceKind.choices)
    ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=MAX_SOURCES,
        help_text="Session ids, or the trace ids of calls, in the order picked.",
    )

    def validate_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Each id may appear once.")
        return value


class EvidencePartSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=PART_STATUSES)
    reason = serializers.CharField(allow_blank=True)


class EvidenceTurnSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=("user", "agent"))
    text = serializers.CharField(allow_blank=True)
    truncated = serializers.BooleanField()
    trace_id = serializers.CharField()
    at = serializers.DateTimeField(allow_null=True)


class EvidenceTranscriptSerializer(EvidencePartSerializer):
    turns = EvidenceTurnSerializer(many=True)


class EvidenceToolCallSerializer(serializers.Serializer):
    span_id = serializers.CharField()
    trace_id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    at = serializers.DateTimeField()
    failed = serializers.BooleanField()
    error = serializers.CharField(allow_blank=True)
    input = serializers.CharField(allow_blank=True)
    output = serializers.CharField(allow_blank=True)
    input_truncated = serializers.BooleanField()
    output_truncated = serializers.BooleanField()


class EvidenceToolCallsSerializer(EvidencePartSerializer):
    calls = EvidenceToolCallSerializer(many=True)


class EvidenceLabelSerializer(serializers.Serializer):
    name = serializers.CharField()
    kind = serializers.CharField()
    value = serializers.JSONField(
        help_text="As the annotator recorded it: input evidence, not a verdict."
    )
    notes = serializers.CharField(allow_blank=True)
    target = serializers.ChoiceField(choices=("session", "trace", "span"))


class EvidenceLabelsSerializer(EvidencePartSerializer):
    labels = EvidenceLabelSerializer(many=True)


class EvidenceImportCaseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    source_id = serializers.CharField()
    status = serializers.ChoiceField(choices=EvidenceImportCase.Status.choices)
    reason = serializers.CharField(allow_blank=True)
    changed_since_import = serializers.BooleanField(
        allow_null=True,
        help_text="Null when unknown: the source is gone, or part of it could not "
        "be read now or when the import was made.",
    )
    trace_ids = serializers.ListField(child=serializers.CharField())
    started_at = serializers.DateTimeField(allow_null=True)
    ended_at = serializers.DateTimeField(allow_null=True)
    transcript = EvidenceTranscriptSerializer()
    tool_calls = EvidenceToolCallsSerializer()
    tool_inputs = EvidencePartSerializer(
        help_text="Whether the tool calls recorded their inputs."
    )
    tool_outputs = EvidencePartSerializer(
        help_text="Whether the tool calls recorded their outputs."
    )
    starting_state = EvidencePartSerializer(
        help_text="The records the agent's tools read before the conversation."
    )
    labels = EvidenceLabelsSerializer()


class EvidenceRedactionFindingSerializer(serializers.Serializer):
    case_id = serializers.UUIDField()
    part = serializers.ChoiceField(choices=("transcript", "tool_calls", "labels"))
    index = serializers.IntegerField(min_value=0)
    field = serializers.ChoiceField(
        choices=("text", "input", "output", "error", "notes", "value")
    )
    entity = serializers.CharField()
    start = serializers.IntegerField(min_value=0)
    end = serializers.IntegerField(min_value=0)
    value = serializers.CharField()
    replacement = serializers.CharField()


class EvidenceRedactionSerializer(serializers.Serializer):
    method = serializers.ChoiceField(
        choices=("presidio", "basic"),
        help_text="basic: a fixed-shape match, for a server without Presidio; it "
        "finds no names or addresses.",
    )
    reason = serializers.CharField(allow_blank=True)
    findings = EvidenceRedactionFindingSerializer(many=True)
    total = serializers.IntegerField(
        min_value=0, help_text="Every finding, including those past the listed ones."
    )


class EvidenceImportSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    environment_id = serializers.UUIDField()
    project_id = serializers.UUIDField()
    agent_version_id = serializers.UUIDField(allow_null=True)
    source_kind = serializers.ChoiceField(choices=EvidenceImport.SourceKind.choices)
    created_at = serializers.DateTimeField()
    cases = EvidenceImportCaseSerializer(many=True)
    redaction = EvidenceRedactionSerializer()
